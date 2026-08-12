"""Step 56 MySQL 连接器测试（V5 多数据源第三落点，ADR-056）。

以 FakeExecutor（惰性替换 handle._executor）驱动，不依赖真实 MySQL 服务；
凭据净化（URL 与 KV passwd 双形态）、setup 序列、schema 类型归一化、
内容指纹语义全覆盖。真实 MySQL 联调属 integration 标记场景（本地 compose 起库）。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pytest

from datasentry_core.connectors import (
    DataSourceSpec,
    DataSourceType,
    default_registry,
)
from datasentry_core.connectors.base import FrameBatch
from datasentry_core.connectors.errors import (
    ConnectorError,
    DataSourceNotFoundError,
    UnsafeSqlError,
)
from datasentry_core.connectors.mysql import (
    MySQLConnector,
    MySQLDataHandle,
    _RedactingExecutor,
    redact_credentials,
)
from datasentry_core.engine.base import SqlParams

DSN = "mysql://user:secret@localhost:3306/app"

_SCHEMA_TABLE = pa.table(
    {
        "column_name": ["id", "amount", "created_at"],
        "column_type": ["INTEGER", "DECIMAL(10,2)", "TIMESTAMP"],
    }
)

_FINGERPRINT_TABLE = pa.table({"n": [3], "h": ["rows-hash"]})


class FakeExecutor:
    """可编程执行器：按 SQL 前缀分发应答，记录调用序列，可注入 duckdb 错误。"""

    def __init__(
        self,
        responses: Mapping[str, pa.Table] | None = None,
        fail_on: Mapping[str, duckdb.Error] | None = None,
    ) -> None:
        self.responses = dict(responses or {})
        self.fail_on = dict(fail_on or {})
        self.calls: list[str] = []
        self.closed = False

    def _answer(self, sql: str) -> pa.Table:
        self.calls.append(sql)
        for prefix, error in self.fail_on.items():
            if sql.startswith(prefix):
                raise error
        for prefix, table in self.responses.items():
            if sql.startswith(prefix):
                return table
        if sql.startswith("SELECT 1 FROM"):
            return pa.table({})
        raise AssertionError(f"unexpected SQL: {sql}")

    def execute_setup(self, sql: str) -> None:
        self.calls.append(sql)
        for prefix, error in self.fail_on.items():
            if sql.startswith(prefix):
                raise error

    def register(self, name: str, obj: object) -> None:
        self.calls.append(f"REGISTER {name}")

    def execute(self, sql: str, params: SqlParams = None) -> pa.Table:
        return self._answer(sql)

    def execute_stream(self, sql: str, batch_size: int = 65536) -> Iterator[pa.RecordBatch]:
        self.calls.append(sql)
        table = self._answer(sql)
        yield from table.to_batches(max_chunksize=batch_size)

    def close(self) -> None:
        self.closed = True


def _handle(
    *,
    dsn: str = DSN,
    table: str = "payments",
    path: Any = None,
    fake: FakeExecutor | None = None,
) -> MySQLDataHandle:
    options: dict[str, Any] = {"dataset_id": "payments"}
    if dsn is not None:
        options["dsn"] = dsn
    handle = MySQLDataHandle(
        DataSourceSpec(
            source_type=DataSourceType.MYSQL,
            path=path,
            table_name=table,
            options=options,
        )
    )
    handle._executor = _RedactingExecutor(fake or FakeExecutor(), dsn or "mysql://***")
    return handle


def _base_spec(table: str = "payments") -> DataSourceSpec:
    return DataSourceSpec(
        source_type=DataSourceType.MYSQL,
        table_name=table,
        options={"dsn": DSN, "dataset_id": "payments"},
    )


class TestRedactCredentials:
    def test_full_dsn_replaced(self) -> None:
        assert redact_credentials(f"boom {DSN} oops", DSN) == "boom mysql://*** oops"

    def test_kv_password_replaced(self) -> None:
        # mysql 扩展错误回显 KV 形式：host=... passwd=secret
        text = "Failed to connect to MySQL database with host=db passwd=secret port=3306"
        assert "secret" not in redact_credentials(text, DSN)
        assert "passwd=***" in redact_credentials(text, DSN)

    def test_url_password_replaced(self) -> None:
        assert "secret" not in redact_credentials("password=secret in message", DSN)

    def test_without_secret_unchanged(self) -> None:
        assert redact_credentials("plain text", DSN) == "plain text"


class TestRedactingExecutor:
    def test_duckdb_error_redacted_to_connector_error(self) -> None:
        fake = FakeExecutor(fail_on={"SELECT": duckdb.Error(f"boom {DSN} secret")})
        with pytest.raises(ConnectorError) as exc:
            _RedactingExecutor(fake, DSN).execute("SELECT 1")
        assert "secret" not in str(exc.value)
        assert "mysql://***" in str(exc.value)

    def test_unsafe_sql_passthrough(self) -> None:
        def boom(_sql: str, _params: SqlParams = None) -> pa.Table:
            raise UnsafeSqlError("not read-only")

        fake = FakeExecutor()
        fake.execute = boom  # type: ignore[method-assign]
        with pytest.raises(UnsafeSqlError):
            _RedactingExecutor(fake, DSN).execute("DELETE FROM t")


class TestMySQLConnector:
    def test_supports_by_dsn_or_ref(self) -> None:
        connector = MySQLConnector()
        assert connector.supports(_base_spec()) is True
        assert (
            connector.supports(
                DataSourceSpec(
                    source_type=DataSourceType.MYSQL,
                    table_name="t",
                    connection_ref="DATASENTRY_MYSQL_DSN",
                )
            )
            is True
        )
        assert (
            connector.supports(DataSourceSpec(source_type=DataSourceType.MYSQL, table_name="t"))
            is False
        )
        assert (
            connector.supports(
                DataSourceSpec(
                    source_type=DataSourceType.CSV,
                    table_name="t",
                    options={"dsn": DSN},
                )
            )
            is False
        )

    def test_open_without_table_name_raises(self) -> None:
        spec = DataSourceSpec(source_type=DataSourceType.MYSQL, options={"dsn": DSN})
        with pytest.raises(DataSourceNotFoundError):
            MySQLConnector().open(spec)

    def test_registry_dispatch(self) -> None:
        handle = default_registry().open(_base_spec())
        assert isinstance(handle, MySQLDataHandle)
        handle.close()


class TestDsnResolution:
    def test_options_dsn_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATASENTRY_MYSQL_DSN", "mysql://env:ref@h:1/db")
        handle = _handle(dsn=DSN)
        assert handle._dsn == DSN

    def test_connection_ref_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATASENTRY_MYSQL_DSN", "mysql://env:ref@h:1/db")
        handle = MySQLDataHandle(
            DataSourceSpec(
                source_type=DataSourceType.MYSQL,
                table_name="t",
                connection_ref="DATASENTRY_MYSQL_DSN",
                options={"dataset_id": "x"},
            )
        )
        assert handle._dsn == "mysql://env:ref@h:1/db"
        handle.close()

    def test_connection_ref_secrets_file_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Step 59：connection_ref 统一解析链——env 缺位时回落 secrets.env。"""
        monkeypatch.delenv("DATASENTRY_MYSQL_DSN", raising=False)
        monkeypatch.setenv("DATASENTRY_CONFIG_HOME", str(tmp_path / "cfg"))
        from datasentry_core.secrets import set_secret

        set_secret("DATASENTRY_MYSQL_DSN", "mysql://file:ref@h:1/db")
        handle = MySQLDataHandle(
            DataSourceSpec(
                source_type=DataSourceType.MYSQL,
                table_name="t",
                connection_ref="DATASENTRY_MYSQL_DSN",
                options={"dataset_id": "x"},
            )
        )
        assert handle._dsn == "mysql://file:ref@h:1/db"
        handle.close()

    def test_connection_ref_secrets_file_env_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Step 59：env > secrets.env（统一解析链优先级）。"""
        monkeypatch.setenv("DATASENTRY_MYSQL_DSN", "mysql://env:ref@h:1/db")
        monkeypatch.setenv("DATASENTRY_CONFIG_HOME", str(tmp_path / "cfg"))
        from datasentry_core.secrets import set_secret

        set_secret("DATASENTRY_MYSQL_DSN", "mysql://file:ref@h:1/db")
        handle = MySQLDataHandle(
            DataSourceSpec(
                source_type=DataSourceType.MYSQL,
                table_name="t",
                connection_ref="DATASENTRY_MYSQL_DSN",
                options={"dataset_id": "x"},
            )
        )
        assert handle._dsn == "mysql://env:ref@h:1/db"
        handle.close()

    def test_ref_env_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DATASENTRY_MYSQL_DSN", raising=False)
        with pytest.raises(DataSourceNotFoundError):
            MySQLDataHandle(
                DataSourceSpec(
                    source_type=DataSourceType.MYSQL,
                    table_name="t",
                    connection_ref="DATASENTRY_MYSQL_DSN",
                )
            )

    def test_no_dsn_no_ref_raises(self) -> None:
        with pytest.raises(DataSourceNotFoundError):
            MySQLDataHandle(DataSourceSpec(source_type=DataSourceType.MYSQL, table_name="t"))


class TestSetupSequence:
    def test_requires_path_false_allows_pathless(self) -> None:
        handle = _handle(path=None)
        assert handle.source_path is None
        assert handle.source_type == DataSourceType.MYSQL

    def test_ensure_view_sequence(self) -> None:
        fake = FakeExecutor(responses={"DESCRIBE": _SCHEMA_TABLE})
        handle = _handle(fake=fake)
        handle._ensure_view()
        # 1.5.x 聚合下推绑定 bug 绕行（ADR-056）：SET 先于 LOAD/ATTACH
        assert fake.calls.index("SET mysql_aggregate_pushdown_enabled = false") < fake.calls.index(
            "LOAD mysql"
        )
        assert "LOAD mysql" in fake.calls
        assert any(c.startswith("ATTACH 'mysql://user:") for c in fake.calls)
        assert any(c.startswith('SELECT 1 FROM my."payments"') for c in fake.calls)
        assert any(
            c.startswith('CREATE OR REPLACE VIEW data AS SELECT * FROM my."payments"')
            for c in fake.calls
        )

    def test_table_name_identifier_escaped(self) -> None:
        fake = FakeExecutor(
            responses={"DESCRIBE": _SCHEMA_TABLE},
            fail_on={"SELECT 1": duckdb.Error("table does not exist")},
        )
        handle = _handle(table="orders; DROP TABLE x", fake=fake)
        with pytest.raises(DataSourceNotFoundError):
            handle._ensure_view()
        assert "DROP TABLE" not in fake.calls
        assert any('"orders; DROP TABLE x"' in c for c in fake.calls)

    def test_extension_load_failure_hints(self) -> None:
        fake = FakeExecutor(
            responses={"DESCRIBE": _SCHEMA_TABLE},
            fail_on={"LOAD mysql": duckdb.Error("extension not bundled")},
        )
        handle = _handle(fake=fake)
        with pytest.raises(ConnectorError) as exc:
            handle._ensure_view()
        assert "failed to load duckdb mysql extension" in str(exc.value)

    def test_attach_failure_hints_and_redacts(self) -> None:
        fake = FakeExecutor(
            responses={"DESCRIBE": _SCHEMA_TABLE},
            fail_on={"ATTACH": duckdb.Error(f"connect refused host=db passwd=secret {DSN}")},
        )
        handle = _handle(fake=fake)
        with pytest.raises(ConnectorError) as exc:
            handle._ensure_view()
        message = str(exc.value)
        assert "mysql connection failed (check host/port/database/credentials)" in message
        assert "secret" not in message

    def test_table_not_found(self) -> None:
        fake = FakeExecutor(
            responses={"DESCRIBE": _SCHEMA_TABLE},
            fail_on={"SELECT 1": duckdb.Error("table payments does not exist")},
        )
        handle = _handle(fake=fake)
        with pytest.raises(DataSourceNotFoundError) as exc:
            handle._ensure_view()
        assert "mysql table not found: payments" in str(exc.value)


class TestSchema:
    def test_type_normalization(self) -> None:
        fake = FakeExecutor(responses={"DESCRIBE": _SCHEMA_TABLE})
        handle = _handle(fake=fake)
        schema = handle.schema()
        assert [(c.name, c.physical_type) for c in schema.columns] == [
            ("id", "INTEGER"),
            ("amount", "DECIMAL"),
            ("created_at", "TIMESTAMP"),
        ]

    def test_ensure_view_called_once(self) -> None:
        fake = FakeExecutor(responses={"DESCRIBE": _SCHEMA_TABLE})
        handle = _handle(fake=fake)
        handle.schema()
        handle.schema()
        assert fake.calls.count("LOAD mysql") == 1


class TestContentFingerprint:
    def test_deterministic_and_change_sensitive(self) -> None:
        responses = {"DESCRIBE": _SCHEMA_TABLE, "SELECT count(*) AS n": _FINGERPRINT_TABLE}
        fake = FakeExecutor(responses=responses)
        handle = _handle(fake=fake)
        first = handle.content_fingerprint()
        second = handle.content_fingerprint()
        assert first == second
        changed_rows = pa.table({"n": [4], "h": ["rows-hash"]})
        rows_resp = {"DESCRIBE": _SCHEMA_TABLE, "SELECT count(*) AS n": changed_rows}
        handle2 = _handle(fake=FakeExecutor(responses=rows_resp))
        assert handle2.content_fingerprint() != first
        changed_hash = pa.table({"n": [3], "h": ["other"]})
        hash_resp = {"DESCRIBE": _SCHEMA_TABLE, "SELECT count(*) AS n": changed_hash}
        handle3 = _handle(fake=FakeExecutor(responses=hash_resp))
        assert handle3.content_fingerprint() != first

    def test_sql_contract_order_independent(self) -> None:
        responses = {"DESCRIBE": _SCHEMA_TABLE, "SELECT count(*) AS n": _FINGERPRINT_TABLE}
        fake = FakeExecutor(responses=responses)
        handle = _handle(fake=fake)
        handle.content_fingerprint()
        hash_sql = next(c for c in fake.calls if c.startswith("SELECT count(*) AS n"))
        assert "string_agg(rh, '' ORDER BY rh)" in hash_sql
        assert "concat_ws(chr(31)," in hash_sql
        assert "coalesce(cast(" in hash_sql


class TestFingerprint:
    def test_full_has_content_no_file_sha(self) -> None:
        fake = FakeExecutor(
            responses={
                "DESCRIBE": _SCHEMA_TABLE,
                "SELECT count(*) AS n": _FINGERPRINT_TABLE,
                "SELECT count(*)": pa.table({"c": [3]}),
            }
        )
        handle = _handle(fake=fake)
        fp = handle.fingerprint()
        assert fp.file_sha256 is None
        assert fp.content_sample_hash is not None
        assert fp.fingerprint_type == "full"
        assert fp.row_count == 3
        assert fp.column_count == 3

    def test_metadata_only_skips_content(self) -> None:
        fake = FakeExecutor(
            responses={
                "DESCRIBE": _SCHEMA_TABLE,
                "SELECT count(*)": pa.table({"c": [3]}),
            }
        )
        handle = _handle(fake=fake)
        fp = handle.fingerprint("metadata_only")
        assert fp.content_sample_hash is None


class TestReadAndCount:
    def test_read_batches_streams(self) -> None:
        table = pa.table({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        fake = FakeExecutor(responses={"DESCRIBE": _SCHEMA_TABLE, "SELECT * FROM data": table})
        handle = _handle(fake=fake)
        batches = list(handle.read_batches(batch_size=2))
        assert [b.row_offset for b in batches] == [0, 2]
        assert sum(b.table.num_rows for b in batches) == 3
        assert all(isinstance(b, FrameBatch) for b in batches)

    def test_count_rows(self) -> None:
        fake = FakeExecutor(
            responses={"DESCRIBE": _SCHEMA_TABLE, "SELECT count(*)": pa.table({"c": [7]})}
        )
        handle = _handle(fake=fake)
        assert handle.count_rows() == 7

    def test_close(self) -> None:
        fake = FakeExecutor(responses={"DESCRIBE": _SCHEMA_TABLE})
        handle = _handle(fake=fake)
        handle.close()
        assert fake.closed is True
        with pytest.raises(ValueError):
            handle.schema()

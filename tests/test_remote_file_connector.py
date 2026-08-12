"""Step 57 云存储文件源连接器测试（V5 多数据源第四落点，ADR-057）。

以 FakeExecutor（惰性替换 handle._executor）驱动，不依赖真实对象存储：
supports 分派、httpfs 会话序列（LOAD 先行、S3 SET 按 endpoint 条件注入、
探测→建视图）、URI 净化、快速失效内容指纹（size+last_modified 组合）、
fingerprint 全档语义（无文件字节）全覆盖。真实 MinIO 联调属 integration
标记场景（本地 docker compose 起对象存储）。
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
from datasentry_core.connectors.errors import (
    ConnectorError,
    DataSourceNotFoundError,
)
from datasentry_core.connectors.remote_file import (
    RemoteFileConnector,
    RemoteFileDataHandle,
    _is_cloud_uri,
    _RemoteRedactingExecutor,
    _s3_session_options,
    redact_uri,
)
from datasentry_core.engine.base import SqlParams

URI = "s3://test-bucket/orders.csv"

_SCHEMA_TABLE = pa.table(
    {
        "column_name": ["id", "amount"],
        "column_type": ["BIGINT", "DOUBLE"],
    }
)
_COUNT_TABLE = pa.table({"n": [3]})
_META_TABLE = pa.table({"size": [247], "last_modified": ["2026-08-12 10:00:00"]})
_DATA_TABLE = pa.table({"id": [1, 2, 3], "amount": [1.0, 2.0, 3.0]})


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
        if sql.startswith("SELECT 1 FROM") or sql.startswith("DESCRIBE"):
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


def _base_spec(source_type: DataSourceType = DataSourceType.CSV, uri: str = URI) -> DataSourceSpec:
    return DataSourceSpec(
        source_type=source_type,
        path=uri,
        options={"dataset_id": "orders"},
    )


def _handle(
    spec: DataSourceSpec | None = None,
    fake: FakeExecutor | None = None,
) -> RemoteFileDataHandle:
    handle = RemoteFileDataHandle(spec or _base_spec())
    handle._executor = _RemoteRedactingExecutor(
        fake
        or FakeExecutor(
            responses={
                "DESCRIBE": _SCHEMA_TABLE,
                "SELECT count(*)": _COUNT_TABLE,
                "SELECT size, last_modified": _META_TABLE,
                "SELECT * FROM data": _DATA_TABLE,
            }
        ),
        URI,
    )
    return handle


def _expected_quick_hash() -> str:
    import hashlib

    return hashlib.sha256(f"{URI}|247|2026-08-12 10:00:00".encode()).hexdigest()


class TestCloudUri:
    def test_prefix_recognition(self) -> None:
        assert _is_cloud_uri("s3://b/a.csv")
        assert _is_cloud_uri("gs://b/a.parquet")
        assert _is_cloud_uri("az://c/a.jsonl")
        assert not _is_cloud_uri("local.csv")
        assert not _is_cloud_uri("postgresql://db/t")
        assert not _is_cloud_uri("http://example.com/a.csv")

    def test_redact_uri(self) -> None:
        assert redact_uri(f"boom {URI} oops", URI) == "boom <remote-uri> oops"


class TestS3SessionOptions:
    def test_no_endpoint_no_sets(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AWS_ENDPOINT_URL_S3", raising=False)
        monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
        assert _s3_session_options(_base_spec()) == []

    def test_options_endpoint(self) -> None:
        spec = _base_spec()
        spec.options["s3_endpoint"] = "localhost:9000"
        statements = _s3_session_options(spec)
        assert any(s == "SET s3_endpoint = 'localhost:9000'" for s in statements)
        assert any(s == "SET s3_url_style = 'path'" for s in statements)
        assert any(s == "SET s3_use_ssl = 'false'" for s in statements)

    def test_env_endpoint_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "minio.internal:9000")
        statements = _s3_session_options(_base_spec())
        assert any("minio.internal:9000" in s for s in statements)


class TestRemoteRedactingExecutor:
    def test_duckdb_error_redacted(self) -> None:
        fake = FakeExecutor(fail_on={"SELECT": duckdb.Error(f"boom {URI} secret")})
        with pytest.raises(ConnectorError) as exc:
            _RemoteRedactingExecutor(fake, URI).execute("SELECT 1")
        assert URI not in str(exc.value)
        assert "<remote-uri>" in str(exc.value)


class TestRemoteFileConnector:
    def test_supports_cloud_only(self) -> None:
        connector = RemoteFileConnector()

        assert connector.supports(_base_spec()) is True
        assert connector.supports(_base_spec(DataSourceType.PARQUET, "s3://b/x.parquet")) is True
        assert connector.supports(_base_spec(DataSourceType.JSONL, "gs://b/x.jsonl")) is True
        assert (
            connector.supports(
                DataSourceSpec(source_type=DataSourceType.CSV, path=Path("local.csv"))
            )
            is False
        )
        assert (
            connector.supports(DataSourceSpec(source_type=DataSourceType.XLSX, path=URI)) is False
        )
        assert (
            connector.supports(DataSourceSpec(source_type=DataSourceType.CSV, path=None)) is False
        )

    def test_open_rejects_non_cloud_path(self) -> None:
        with pytest.raises(DataSourceNotFoundError):
            RemoteFileConnector().open(
                DataSourceSpec(source_type=DataSourceType.CSV, path="local.csv")
            )

    def test_registry_dispatch(self) -> None:
        handle = default_registry().open(_base_spec())
        assert isinstance(handle, RemoteFileDataHandle)
        handle.close()

    def test_registry_local_still_local(self, tmp_path: Any) -> None:
        local = tmp_path / "a.csv"
        local.write_text("id\n1\n")
        handle = default_registry().open(
            DataSourceSpec(source_type=DataSourceType.CSV, path=Path(local))
        )
        assert not isinstance(handle, RemoteFileDataHandle)
        handle.close()


class TestRemoteFileDataHandle:
    def test_view_setup_sequence(self) -> None:
        fake = FakeExecutor(responses={"DESCRIBE": _SCHEMA_TABLE})
        handle = _handle(fake=fake)
        handle._ensure_view()
        assert fake.calls[0] == "LOAD httpfs"
        assert any(c.startswith("SELECT 1 FROM read_csv_auto(") for c in fake.calls)
        assert any(c.startswith("CREATE OR REPLACE VIEW data") for c in fake.calls)

    def test_view_setup_with_s3_endpoint(self) -> None:
        spec = _base_spec()
        spec.options["s3_endpoint"] = "localhost:9000"
        fake = FakeExecutor(responses={"DESCRIBE": _SCHEMA_TABLE})
        handle = _handle(spec, fake=fake)
        handle._ensure_view()
        assert fake.calls[0] == "LOAD httpfs"
        set_index = fake.calls.index("SET s3_endpoint = 'localhost:9000'")
        load_index = fake.calls.index("LOAD httpfs")
        assert set_index > load_index

    def test_httpfs_load_failure_hint(self) -> None:
        fake = FakeExecutor(fail_on={"LOAD": duckdb.Error("no network")})
        handle = _handle(fake=fake)
        with pytest.raises(ConnectorError) as exc:
            handle._ensure_view()
        assert "httpfs extension" in str(exc.value)

    def test_probe_failure_not_found(self) -> None:
        fake = FakeExecutor(fail_on={"SELECT 1 FROM": duckdb.Error("NotFound: 404")})
        handle = _handle(fake=fake)
        with pytest.raises(DataSourceNotFoundError) as exc:
            handle._ensure_view()
        # 净化策略：URI（az:// 可含 SAS token）不出现在错误文本
        assert "remote file not found or unreadable" in str(exc.value)
        assert URI not in str(exc.value)

    def test_schema_via_view(self) -> None:
        handle = _handle()
        schema = handle.schema()
        assert [c.name for c in schema.columns] == ["id", "amount"]
        handle.close()

    def test_read_batches_streams_view(self) -> None:
        handle = _handle()
        batches = list(handle.read_batches())
        assert len(batches) == 1
        assert batches[0].table.column("id").to_pylist() == [1, 2, 3]
        assert batches[0].row_offset == 0
        handle.close()

    def test_count_rows(self) -> None:
        handle = _handle()
        assert handle.count_rows() == 3
        handle.close()

    def test_content_fingerprint_quick_layer(self) -> None:
        handle = _handle()
        assert handle.content_fingerprint() == _expected_quick_hash()
        handle.close()

    def test_content_fingerprint_meta_unavailable_not_found(self) -> None:
        fake = FakeExecutor(fail_on={"SELECT size": duckdb.Error("AccessDenied: 403")})
        handle = _handle(fake=fake)
        with pytest.raises(DataSourceNotFoundError):
            handle.content_fingerprint()

    def test_content_fingerprint_empty_meta_not_found(self) -> None:
        fake = FakeExecutor(responses={"SELECT size, last_modified": pa.table({})})
        handle = _handle(fake=fake)
        with pytest.raises(DataSourceNotFoundError) as exc:
            handle.content_fingerprint()
        assert "not found" in str(exc.value)

    def test_fingerprint_full_no_file_bytes(self) -> None:
        handle = _handle()
        fp = handle.fingerprint("full")
        assert fp.file_sha256 is None
        assert fp.content_sample_hash == _expected_quick_hash()
        assert fp.row_count == 3
        assert fp.schema_hash != ""
        handle.close()

    def test_read_sample(self) -> None:
        handle = _handle()
        batch = handle.read_sample(10)
        assert batch.table.column("id").to_pylist() == [1, 2, 3]
        handle.close()

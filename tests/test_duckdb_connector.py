"""Step 38 DuckDB 文件连接器测试（V1 数据库型数据源落地，ADR-038）。"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from datasentry_core.connectors import (
    DataSourceSpec,
    DataSourceType,
    DuckdbConnector,
    UnsupportedFormatError,
    default_registry,
)
from datasentry_core.connectors.errors import DataSourceNotFoundError
from datasentry_core.connectors.registry import ConnectorRegistry


def _write_duckdb(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE payments (id INTEGER, name VARCHAR, amount DOUBLE)")
        con.execute(
            "INSERT INTO payments VALUES (1, 'alice', 10.5), (2, 'bob', NULL), (3, 'carol', 99.9)"
        )
        con.execute("CREATE SCHEMA audit")
        con.execute("CREATE TABLE audit.log (ts TIMESTAMP, msg VARCHAR)")
        con.execute("INSERT INTO audit.log VALUES (TIMESTAMP '2026-08-01 10:00:00', 'ok')")
    finally:
        con.close()


def _spec(path: Path, *, table: str = "payments", schema: str | None = None) -> DataSourceSpec:
    options = (
        {"dataset_id": "payments"}
        if schema is None
        else {"dataset_id": "payments", "schema": schema}
    )
    return DataSourceSpec(
        source_type=DataSourceType.DUCKDB,
        path=path,
        table_name=table,
        options=options,
    )


class TestDuckdbConnector:
    def test_supports_by_type_only(self, tmp_path: Path) -> None:
        db = tmp_path / "t.duckdb"
        _write_duckdb(db)
        connector = DuckdbConnector()
        assert connector.supports(_spec(db, table="payments")) is True
        assert (
            connector.supports(DataSourceSpec(source_type=DataSourceType.DUCKDB, path=db)) is True
        )  # 表名校验在 open（缺表名是参数错误，非格式不支持）

    def test_open_missing_table_parameter_raises(self, tmp_path: Path) -> None:
        db = tmp_path / "t.duckdb"
        _write_duckdb(db)
        connector = DuckdbConnector()
        with pytest.raises(DataSourceNotFoundError, match="requires table_name"):
            connector.open(DataSourceSpec(source_type=DataSourceType.DUCKDB, path=db))

    def test_open_missing_file_raises(self, tmp_path: Path) -> None:
        connector = DuckdbConnector()
        with pytest.raises(DataSourceNotFoundError):
            connector.open(_spec(tmp_path / "missing.duckdb", table="payments"))

    def test_missing_table_fails_on_eval(self, tmp_path: Path) -> None:
        db = tmp_path / "t.duckdb"
        _write_duckdb(db)
        handle = DuckdbConnector().open(_spec(db, table="nope"))
        try:
            with pytest.raises((Exception,)):  # 视图惰性：首次求值才绑定失败
                handle.schema()
        finally:
            handle.close()

    def test_schema_read_sample_count_rows(self, tmp_path: Path) -> None:
        db = tmp_path / "t.duckdb"
        _write_duckdb(db)
        handle = DuckdbConnector().open(_spec(db))
        try:
            schema = handle.schema()
            assert schema.column_names == ["id", "name", "amount"]
            assert schema.columns[2].physical_type.upper() == "DOUBLE"
            sample = handle.read_sample(2)
            assert sample.table.num_rows == 2
            assert handle.count_rows() == 3
            agg = handle.sql_aggregate("SELECT count(*) AS n FROM data")
            assert agg.table.column(0).to_pylist() == [3]
        finally:
            handle.close()

    def test_read_batches_streams(self, tmp_path: Path) -> None:
        db = tmp_path / "t.duckdb"
        _write_duckdb(db)
        handle = DuckdbConnector().open(_spec(db))
        try:
            batches = list(handle.read_batches(batch_size=2))
            assert len(batches) == 2
            assert batches[0].row_count + batches[1].row_count == 3
            assert batches[1].row_offset == 2
            assert batches[0].column_names == ["id", "name", "amount"]
        finally:
            handle.close()

    def test_named_schema_table(self, tmp_path: Path) -> None:
        db = tmp_path / "t.duckdb"
        _write_duckdb(db)
        handle = DuckdbConnector().open(_spec(db, table="log", schema="audit"))
        try:
            assert handle.schema().column_names == ["ts", "msg"]
            assert handle.count_rows() == 1
        finally:
            handle.close()

    def test_fingerprint_and_warnings(self, tmp_path: Path) -> None:
        db = tmp_path / "t.duckdb"
        _write_duckdb(db)
        handle = DuckdbConnector().open(_spec(db))
        try:
            fp = handle.fingerprint(mode="sampled")
            assert fp.row_count == 3
            assert fp.column_count == 3
            assert handle.warnings() == []  # 无公式注入
        finally:
            handle.close()

    def test_identifier_injection_quoted(self, tmp_path: Path) -> None:
        db = tmp_path / "t.duckdb"
        _write_duckdb(db)
        connector = DuckdbConnector()
        handle = connector.open(_spec(db, table='payments"; DROP TABLE payments; --'))
        with pytest.raises((Exception,)):  # 注入标识符仅作为不存在的表名被拒绝
            handle.schema()
        # 注入表名仅被当作不存在的标识符，不执行 DROP
        verify = connector.open(_spec(db))
        try:
            assert verify.count_rows() == 3
        finally:
            verify.close()
        handle.close()

    def test_registry_open_and_unregistered_type(self, tmp_path: Path) -> None:
        db = tmp_path / "t.duckdb"
        _write_duckdb(db)
        handle = default_registry().open(_spec(db))
        try:
            assert handle.count_rows() == 3
        finally:
            handle.close()
        registry = ConnectorRegistry()
        with pytest.raises(UnsupportedFormatError):
            registry.open(_spec(db))


class TestDuckdbThroughClient:
    def test_scan_duckdb_with_table(self, tmp_path: Path) -> None:
        from datasentry import DataSentry

        db = tmp_path / "t.duckdb"
        _write_duckdb(db)
        client = DataSentry(project=tmp_path / "ws")
        try:
            scan, _, issues = client.scan_file(db, table_name="payments")
            assert scan.status == "completed"
            assert scan.fingerprint.row_count == 3
            assert scan.dataset_id == "t"
            assert len(issues) >= 1  # amount 有 NULL → 缺失类 issue
        finally:
            client.close()

    def test_scan_duckdb_without_table_fails(self, tmp_path: Path) -> None:
        from datasentry import DataSentry
        from datasentry_core.connectors.errors import DataSourceNotFoundError

        db = tmp_path / "t.duckdb"
        _write_duckdb(db)
        client = DataSentry(project=tmp_path / "ws")
        try:
            with pytest.raises(DataSourceNotFoundError):
                client.scan_file(db)
        finally:
            client.close()

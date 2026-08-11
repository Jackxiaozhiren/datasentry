"""Step 54 SQLite 数据源连接器测试（V3 多数据源第一落点，ADR-054）。

覆盖验收：真实 SQLite 库（含 NULL/重复/文本）经 sqlite_scan 只读
暴露——schema/batches/sample/count/fingerprint 可用；表名必填；
文件缺失报错；注册表发现；client.scan_file 端到端（检测器+评分）；
调度器联动（job path=.db + table_name）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from datasentry_core.connectors import (
    DataSourceSpec,
    DataSourceType,
    SqliteConnector,
    default_registry,
)
from datasentry_core.connectors.errors import DataSourceNotFoundError


def _write_sqlite(path: Path, *, table: str = "orders") -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"CREATE TABLE {table} (id INTEGER, name TEXT, amount REAL)")
        conn.executemany(
            f"INSERT INTO {table} VALUES (?,?,?)",
            [(1, "alice", 10.5), (2, "bob", None), (3, None, 99.9), (3, "bob", 5.0)],
        )
        conn.commit()
    finally:
        conn.close()


def _spec(path: Path, *, table: str = "orders") -> DataSourceSpec:
    return DataSourceSpec(
        source_type=DataSourceType.SQLITE,
        path=path,
        table_name=table,
        options={"dataset_id": "orders"},
    )


class TestSqliteConnector:
    def test_supports_by_type(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        _write_sqlite(db)
        connector = SqliteConnector()
        assert connector.supports(_spec(db)) is True
        assert (
            connector.supports(DataSourceSpec(source_type=DataSourceType.SQLITE, path=db)) is True
        )

    def test_open_requires_table_name(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        _write_sqlite(db)
        with pytest.raises(DataSourceNotFoundError, match="table_name"):
            SqliteConnector().open(DataSourceSpec(source_type=DataSourceType.SQLITE, path=db))

    def test_open_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(DataSourceNotFoundError, match="not found"):
            SqliteConnector().open(_spec(tmp_path / "nope.db"))

    def test_schema_and_rows(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        _write_sqlite(db)
        handle = SqliteConnector().open(_spec(db))
        try:
            assert handle.table_name == "orders"
            assert handle.schema().column_names == ["id", "name", "amount"]
            assert handle.count_rows() == 4
            sample = handle.read_sample(2, method="none")
            assert sample.row_count == 2
            batch = next(handle.read_batches())
            assert batch.row_count == 4
            values = batch.table.column("name").to_pylist()
            assert None in values
        finally:
            handle.close()

    def test_sql_aggregate(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        _write_sqlite(db)
        handle = SqliteConnector().open(_spec(db))
        try:
            agg = handle.sql_aggregate("SELECT COUNT(*) AS n FROM data WHERE amount IS NULL")
            assert agg.table.column("n").to_pylist() == [1]
        finally:
            handle.close()

    def test_fingerprint(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        _write_sqlite(db)
        handle = SqliteConnector().open(_spec(db))
        try:
            fp = handle.fingerprint()
            assert fp.schema_hash
            assert fp.row_count == 4
        finally:
            handle.close()

    def test_registry_discovers_sqlite(self, tmp_path: Path) -> None:
        db = tmp_path / "t.db"
        _write_sqlite(db)
        registry = default_registry()
        assert registry.open(_spec(db)) is not None


class TestSqliteClientScan:
    def test_scan_sqlite_file_end_to_end(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        from datasentry import DataSentry

        db = tmp_path / "orders.db"
        _write_sqlite(db)
        client = DataSentry(project=tmp_path)
        try:
            scan, _runs, issues = client.scan_file(db, table_name="orders")
            assert scan.quality_score is not None
            assert len(issues) >= 1
            assert scan.dataset_id == "orders"
        finally:
            client.close()

    def test_scan_sqlite_without_table_name_raises(self, tmp_path: Path) -> None:
        from datasentry import DataSentry

        db = tmp_path / "orders.db"
        _write_sqlite(db)
        client = DataSentry(project=tmp_path)
        try:
            with pytest.raises(Exception, match="table_name"):
                client.scan_file(db)
        finally:
            client.close()

    def test_scan_missing_sqlite_file_raises(self, tmp_path: Path) -> None:
        from datasentry import DataSentry

        client = DataSentry(project=tmp_path)
        try:
            with pytest.raises(FileNotFoundError):
                client.scan_file(tmp_path / "nope.db", table_name="orders")
        finally:
            client.close()


class TestSqliteSchedulerLink:
    def test_scheduled_job_scans_sqlite_and_skips_unchanged(
        self,
        tmp_path: Path,
    ) -> None:
        from datetime import timedelta

        from datasentry.scheduler.core import LocalScanExecutor, Scheduler
        from datasentry.scheduler.models import JobCommand, ScheduledJob, utcnow
        from datasentry.scheduler.store import SchedulerStore
        from datasentry_core.storage.paths import project_db_path

        db = tmp_path / "orders.db"
        _write_sqlite(db)
        store = SchedulerStore(project_db_path(tmp_path))
        now = utcnow()
        store.create_job(
            ScheduledJob(
                job_id="job_sql",
                name="sqlite nightly",
                project=str(tmp_path.resolve()),
                command=JobCommand(
                    project=str(tmp_path.resolve()), path=str(db), table_name="orders"
                ),
                cron="* * * * *",
                next_run_at=now - timedelta(seconds=5),
                created_at=now,
                updated_at=now,
            )
        )
        scheduler = Scheduler(store=store, executor=LocalScanExecutor())
        first_id = scheduler.trigger("job_sql")
        run = store.get_run(first_id)
        assert run is not None
        assert run.status.value == "completed"
        assert run.scan_run_id is not None
        assert run.file_hash is not None

        second_id = scheduler.trigger("job_sql")
        second = store.get_run(second_id)
        assert second is not None
        assert second.skipped is True
        assert second.file_hash == run.file_hash

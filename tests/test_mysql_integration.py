"""Step 56 MySQL 连接器集成测试（真实 MySQL 服务，integration marker）。

无 MySQL 服务时自动跳过（连接探测）：本地默认连 docker 映射端口 3307
（datasentry-mysql-test），CI 经 TEST_MYSQL_DSN 指向 mysql service。
fixture 数据经 DuckDB mysql 扩展读写 ATTACH 灌入（不引入 pymysql），
保持与连接器同路径。
"""

from __future__ import annotations

import os
from collections import Counter
from datetime import timedelta
from pathlib import Path

import duckdb
import pytest

from datasentry import DataSentry
from datasentry.scheduler.core import LocalScanExecutor, Scheduler
from datasentry.scheduler.models import JobCommand, ScheduledJob, utcnow
from datasentry.scheduler.store import SchedulerStore
from datasentry_core.connectors import DataSourceSpec, DataSourceType, default_registry
from datasentry_core.connectors.errors import DataSourceNotFoundError
from datasentry_core.storage.paths import project_db_path

TEST_MYSQL_DSN_ENV = "TEST_MYSQL_DSN"
_DEFAULT_DSN = "mysql://root:testpass@localhost:3307/testdb"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS w.{table} (
    order_id INT,
    customer_email VARCHAR(255),
    order_total DOUBLE,
    amount DECIMAL(10,2),
    status VARCHAR(32),
    created_at TIMESTAMP
)
"""

_DIRTY_ROWS: list[tuple[object, ...]] = [
    (1, "a@x.com", 12.5, "12.50", "completed", "2026-08-01 10:00:00"),
    (2, None, None, None, "pending", "2026-08-01 11:00:00"),
    (3, "bad@@", 7.25, "7.25", "completed", "2026-08-01 12:00:00"),
    (3, "a@x.com", 999999.99, "999999.99", "unknown", "2026-08-01 13:00:00"),
    (4, "test@example.com", 30.0, "30.00", "pending", "2026-08-01 14:00:00"),
    (5, "id@x.com", -5.0, "-5.00", "void", "2026-08-01 15:00:00"),
]


def _mysql_available(dsn: str) -> bool:
    """探测：mysql 扩展可加载且 DSN 可达（复用连接器同款 ATTACH）。"""
    con = duckdb.connect(database=":memory:")
    try:
        con.execute("LOAD mysql")
        con.execute(f"ATTACH '{dsn}' AS probe (TYPE mysql, READ_ONLY)")
        return True
    except Exception:
        return False
    finally:
        con.close()


def _seed(table: str, dsn: str, *, with_timestamps: bool = False) -> None:
    """重建脏数据表（DuckDB 读写 ATTACH，无 pymysql）。"""
    con = duckdb.connect(database=":memory:")
    try:
        con.execute("LOAD mysql")
        con.execute(f"ATTACH '{dsn}' AS w (TYPE mysql)")
        con.execute(f"DROP TABLE IF EXISTS w.{table}")
        con.execute(_SCHEMA_SQL.format(table=table))
        con.execute(f"DELETE FROM w.{table}")
        rows = list(_DIRTY_ROWS)
        if not with_timestamps:
            rows = [r[:-1] for r in rows]
        placeholders = ", ".join(["?"] * len(rows[0]))
        con.executemany(f"INSERT INTO w.{table} VALUES ({placeholders})", [list(r) for r in rows])
    finally:
        con.close()


@pytest.fixture(scope="module")
def mysql_dsn() -> str:
    dsn = os.environ.get(TEST_MYSQL_DSN_ENV) or _DEFAULT_DSN
    if not _mysql_available(dsn):
        pytest.skip("no reachable MySQL (set TEST_MYSQL_DSN to enable)")
    return dsn


@pytest.fixture(scope="module")
def orders_table(mysql_dsn: str) -> str:
    _seed("orders_integration", mysql_dsn, with_timestamps=True)
    return "orders_integration"


def _scan_mysql(mysql_dsn: str, table: str, workspace: Path) -> tuple[object, object, list[object]]:
    client = DataSentry(project=workspace)
    try:
        return client.scan_file(mysql_dsn, table_name=table, dataset_id=f"it_{table}")
    finally:
        client.close()


class TestMysqlIntegration:
    @pytest.mark.integration
    def test_schema_types_normalized(self, mysql_dsn: str, orders_table: str) -> None:
        spec = DataSourceSpec(
            source_type=DataSourceType.MYSQL,
            table_name=orders_table,
            options={"dsn": mysql_dsn},
        )
        handle = default_registry().open(spec)
        try:
            info = handle.schema()
            types = {c.name: c.physical_type for c in info.columns}
            assert types["amount"] == "DECIMAL"
            assert types["created_at"] == "TIMESTAMP"
            assert types["order_id"] == "INTEGER"
        finally:
            handle.close()

    @pytest.mark.integration
    def test_scan_matches_file_scan(
        self, mysql_dsn: str, orders_table: str, tmp_path: Path
    ) -> None:
        """验收：同一份脏数据灌 MySQL 后扫描，与文件扫描结果可比（同问题类型集合）。"""
        csv = tmp_path / "orders.csv"
        header = "order_id,customer_email,order_total,amount,status\n"
        rows = "\n".join(
            f"{r[0]},{r[1] if r[1] else ''},{r[2] if r[2] is not None else ''},"
            f"{r[3] if r[3] else ''},{r[4]}"
            for r in _DIRTY_ROWS
        )
        csv.write_text(header + rows, encoding="utf-8")

        mysql_scan, _, mysql_issues = _scan_mysql(mysql_dsn, orders_table, tmp_path / "ws_my")
        file_client = DataSentry(project=tmp_path / "ws_csv")
        try:
            file_scan, _, file_issues = file_client.scan_file(csv, dataset_id="it_csv")
        finally:
            file_client.close()

        mysql_types = Counter(i.issue_type for i in mysql_issues)
        file_types = Counter(i.issue_type for i in file_issues)
        assert set(mysql_types) == set(file_types), (mysql_types, file_types)
        assert (
            abs(
                (mysql_scan.quality_score.overall if mysql_scan.quality_score else 0)
                - (file_scan.quality_score.overall if file_scan.quality_score else 0)
            )
            <= 5
        )

    @pytest.mark.integration
    def test_content_fingerprint_change_and_order_sensitivity(
        self, mysql_dsn: str, orders_table: str
    ) -> None:
        spec = DataSourceSpec(
            source_type=DataSourceType.MYSQL,
            table_name=orders_table,
            options={"dsn": mysql_dsn},
        )
        handle = default_registry().open(spec)
        try:
            first = handle.content_fingerprint()
            second = handle.content_fingerprint()
            assert first == second

            con = duckdb.connect(database=":memory:")
            try:
                con.execute("LOAD mysql")
                con.execute(f"ATTACH '{mysql_dsn}' AS w (TYPE mysql)")
                con.execute(f"UPDATE w.{orders_table} SET status = 'cancelled' WHERE order_id = 1")
            finally:
                con.close()
            changed = default_registry().open(spec)
            try:
                assert changed.content_fingerprint() != first
            finally:
                changed.close()

            # 行序无关：按相反顺序重插同内容 → 指纹与初值一致（UPDATE 回滚前先还原）
            _seed(orders_table, mysql_dsn, with_timestamps=True)
            restored = default_registry().open(spec)
            try:
                assert restored.content_fingerprint() == first
            finally:
                restored.close()
        finally:
            handle.close()

    @pytest.mark.integration
    def test_scheduler_skip_on_unchanged_real_mysql(
        self, mysql_dsn: str, orders_table: str, tmp_path: Path
    ) -> None:
        """验收：MySQL 任务同内容二次触发 skipped；表内容变更后重扫。"""
        store = SchedulerStore(project_db_path(tmp_path))
        now = utcnow()
        store.create_job(
            ScheduledJob(
                job_id="job_mysql_it",
                name="mysql integration",
                project=str(tmp_path.resolve()),
                command=JobCommand(
                    project=str(tmp_path.resolve()), path=mysql_dsn, table_name=orders_table
                ),
                cron="* * * * *",
                next_run_at=now - timedelta(seconds=5),
                created_at=now,
                updated_at=now,
            )
        )
        scheduler = Scheduler(store=store, executor=LocalScanExecutor())
        first_id = scheduler.trigger("job_mysql_it")
        first = store.get_run(first_id)
        assert first is not None
        assert first.status.value == "completed"
        assert first.scan_run_id is not None
        assert first.file_hash is not None

        second_id = scheduler.trigger("job_mysql_it")
        second = store.get_run(second_id)
        assert second is not None
        assert second.skipped is True
        assert second.file_hash == first.file_hash

        con = duckdb.connect(database=":memory:")
        try:
            con.execute("LOAD mysql")
            con.execute(f"ATTACH '{mysql_dsn}' AS w (TYPE mysql)")
            con.execute(
                f"UPDATE w.{orders_table} SET order_total = order_total + 1 WHERE order_id = 4"
            )
        finally:
            con.close()
        third_id = scheduler.trigger("job_mysql_it")
        third = store.get_run(third_id)
        assert third is not None
        assert third.skipped is False
        assert third.scan_run_id is not None
        assert third.file_hash != first.file_hash

    @pytest.mark.integration
    def test_credentials_never_leak(
        self, mysql_dsn: str, orders_table: str, tmp_path: Path
    ) -> None:
        scan, _runs, issues = _scan_mysql(mysql_dsn, orders_table, tmp_path / "ws_leak")
        client = DataSentry(project=tmp_path / "ws_leak")
        try:
            report = client.export_report(scan.id)
        finally:
            client.close()
        assert mysql_dsn not in str(issues)
        assert mysql_dsn not in str(report)
        secret = mysql_dsn.split(":", 2)[2].split("@", 1)[0]
        assert secret not in str(issues)
        assert secret not in str(report)

    @pytest.mark.integration
    def test_missing_table_raises(self, mysql_dsn: str, tmp_path: Path) -> None:
        client = DataSentry(project=tmp_path)
        try:
            with pytest.raises(DataSourceNotFoundError):
                client.scan_file(mysql_dsn, table_name="no_such_table_xyz")
        finally:
            client.close()

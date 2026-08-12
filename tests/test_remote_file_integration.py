"""Step 57 云存储文件源集成测试（真实 MinIO 对象存储，integration marker）。

无 MinIO 服务时自动跳过（连接探测）：本地默认连 docker 映射端口 9000
（datasentry-minio-test），CI 经 TEST_MINIO_ENDPOINT 指向 minio service。
fixture 数据经 DuckDB httpfs COPY TO 灌入（与连接器同路径），不引入
boto3 等新依赖；桶需预建（本地 mc mb / CI setup 步骤）。
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import duckdb
import pytest

from datasentry import DataSentry
from datasentry_core.connectors import DataSourceSpec, DataSourceType, default_registry
from datasentry_core.connectors.errors import DataSourceNotFoundError

TEST_MINIO_ENDPOINT_ENV = "TEST_MINIO_ENDPOINT"
_DEFAULT_ENDPOINT = "localhost:9000"
_BUCKET = "test-bucket"
_KEYS = {
    "csv": f"s3://{_BUCKET}/orders_remote.csv",
    "parquet": f"s3://{_BUCKET}/orders_remote.parquet",
    "jsonl": f"s3://{_BUCKET}/orders_remote.jsonl",
}

#: 凭据走 httpfs 原生 env 读取（与连接器同路径）；测试默认 MinIO 官方
#: 开发凭据，CI 经 TEST_MINIO_ACCESS_KEY/SECRET 覆盖。
_ACCESS_KEY = os.environ.get("TEST_MINIO_ACCESS_KEY", "minioadmin")
_SECRET_KEY = os.environ.get("TEST_MINIO_SECRET_KEY", "minioadmin")

_DIRTY_ROWS = [
    (1, "a@x.com", 12.5, "12.50", "completed", "2026-08-01 10:00:00"),
    (2, None, None, None, "pending", "2026-08-01 11:00:00"),
    (3, "bad@@", 7.25, "7.25", "completed", "2026-08-01 12:00:00"),
    (3, "a@x.com", 999999.99, "999999.99", "unknown", "2026-08-01 13:00:00"),
    (4, "test@example.com", 30.0, "30.00", "pending", "2026-08-01 14:00:00"),
    (5, "id@x.com", -5.0, "-5.00", "void", "2026-08-01 15:00:00"),
]


def _session(con: duckdb.DuckDBPyConnection, endpoint: str) -> None:
    con.execute("LOAD httpfs")
    con.execute(f"SET s3_endpoint = '{endpoint}'")
    con.execute("SET s3_region = 'us-east-1'")
    con.execute("SET s3_url_style = 'path'")
    con.execute("SET s3_use_ssl = 'false'")


def _minio_available(endpoint: str) -> bool:
    """探测：httpfs 可加载且端点/凭据/桶可达（glob 桶内对象，无需预置 key）。"""
    con = duckdb.connect(database=":memory:")
    try:
        _session(con, endpoint)
        con.execute(f"SELECT * FROM glob('s3://{_BUCKET}/*')")
        return True
    except Exception:
        return False
    finally:
        con.close()


def _seed_csv_via_pyarrow(key: str, endpoint: str, rows: list[tuple[object, ...]]) -> None:
    """CSV：本地 csv 写临时文件 → COPY TO（httpfs PUT）。"""
    import csv as _csv

    tmp = Path(f"/tmp/_seed_remote_{rows[0][0]}.csv")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = _csv.writer(fh)
        writer.writerow(["order_id", "customer_email", "order_total", "amount", "status"])
        for r in rows:
            writer.writerow(
                [
                    r[0],
                    r[1] if r[1] is not None else "",
                    r[2] if r[2] is not None else "",
                    r[3] if r[3] is not None else "",
                    r[4],
                ]
            )
    con = duckdb.connect(database=":memory:")
    try:
        _session(con, endpoint)
        con.execute(f"COPY (SELECT * FROM read_csv_auto('{tmp}')) TO '{key}'")
    finally:
        con.close()
        tmp.unlink(missing_ok=True)


def _seed_parquet_jsonl(key: str, endpoint: str, rows: list[tuple[object, ...]]) -> None:
    """Parquet/JSONL：pyarrow 表 → 本地临时文件 → COPY TO（httpfs PUT）。"""
    import json as _json

    import pyarrow as pa
    import pyarrow.parquet as pq

    suffix = ".parquet" if key.endswith(".parquet") else ".jsonl"
    tmp = Path(f"/tmp/_seed_remote{suffix}")
    table = pa.table(
        {
            "order_id": pa.array([r[0] for r in rows], type=pa.int64()),
            "customer_email": pa.array([r[1] for r in rows], type=pa.string()),
            "order_total": pa.array([r[2] for r in rows], type=pa.float64()),
            "amount": pa.array([r[3] for r in rows], type=pa.string()),
            "status": pa.array([r[4] for r in rows], type=pa.string()),
        }
    )
    if suffix == ".parquet":
        pq.write_table(table, tmp)
    else:
        with tmp.open("w", encoding="utf-8") as fh:
            names = table.column_names
            for row in table.to_pylist():
                fh.write(_json.dumps({k: row[k] for k in names}) + "\n")
    con = duckdb.connect(database=":memory:")
    try:
        _session(con, endpoint)
        read_sql = (
            f"COPY (SELECT * FROM read_parquet('{tmp}')) TO '{key}'"
            if suffix == ".parquet"
            else f"COPY (SELECT * FROM read_json_auto('{tmp}')) TO '{key}'"
        )
        con.execute(read_sql)
    finally:
        con.close()
        tmp.unlink(missing_ok=True)


def _seed_all(endpoint: str, rows: list[tuple[object, ...]] | None = None) -> None:
    rows = list(rows if rows is not None else _DIRTY_ROWS)
    _seed_csv_via_pyarrow(_KEYS["csv"], endpoint, rows)
    _seed_parquet_jsonl(_KEYS["parquet"], endpoint, rows)
    _seed_parquet_jsonl(_KEYS["jsonl"], endpoint, rows)


@pytest.fixture(scope="module")
def minio_endpoint() -> str:
    endpoint = os.environ.get(TEST_MINIO_ENDPOINT_ENV) or _DEFAULT_ENDPOINT
    os.environ.setdefault("AWS_ACCESS_KEY_ID", _ACCESS_KEY)
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", _SECRET_KEY)
    os.environ.setdefault("AWS_ENDPOINT_URL_S3", endpoint)
    if not _minio_available(endpoint):
        pytest.skip(f"no reachable MinIO at {endpoint} (set {TEST_MINIO_ENDPOINT_ENV} to enable)")
    _seed_all(endpoint)
    return endpoint


def _scan_remote(uri: str, workspace: Path) -> tuple[object, object, list[object]]:
    client = DataSentry(project=workspace)
    try:
        return client.scan_file(uri, dataset_id=f"it_remote_{workspace.name}")
    finally:
        client.close()


class TestRemoteFileIntegration:
    @pytest.mark.integration
    def test_csv_schema_types(self, minio_endpoint: str) -> None:
        spec = DataSourceSpec(
            source_type=DataSourceType.CSV,
            path=_KEYS["csv"],
            options={"dataset_id": "it"},
        )
        handle = default_registry().open(spec)
        try:
            types = {c.name: c.physical_type for c in handle.schema().columns}
            assert types["order_id"] == "BIGINT"
            assert types["customer_email"] == "VARCHAR"
            assert types["order_total"] == "DOUBLE"
        finally:
            handle.close()

    @pytest.mark.integration
    def test_scan_remote_matches_local(self, minio_endpoint: str, tmp_path: Path) -> None:
        """验收：同一份脏数据在对象存储上扫描，与本地文件扫描可比（同问题类型集合）。"""
        local = tmp_path / "orders.csv"
        header = "order_id,customer_email,order_total,amount,status\n"
        body = "\n".join(
            f"{r[0]},{r[1] if r[1] else ''},{r[2] if r[2] is not None else ''},"
            f"{r[3] if r[3] else ''},{r[4]}"
            for r in _DIRTY_ROWS
        )
        local.write_text(header + body, encoding="utf-8")

        remote_scan, _, remote_issues = _scan_remote(_KEYS["csv"], tmp_path / "ws_remote")
        file_client = DataSentry(project=tmp_path / "ws_local")
        try:
            file_scan, _, file_issues = file_client.scan_file(local, dataset_id="it_local")
        finally:
            file_client.close()

        remote_types = Counter(i.issue_type for i in remote_issues)
        file_types = Counter(i.issue_type for i in file_issues)
        assert set(remote_types) == set(file_types), (remote_types, file_types)
        assert (
            abs(
                (remote_scan.quality_score.overall if remote_scan.quality_score else 0)
                - (file_scan.quality_score.overall if file_scan.quality_score else 0)
            )
            <= 5
        )

    @pytest.mark.integration
    def test_parquet_and_jsonl_scan(self, minio_endpoint: str, tmp_path: Path) -> None:
        parquet_scan, _, parquet_issues = _scan_remote(_KEYS["parquet"], tmp_path / "ws_pq")
        jsonl_scan, _, jsonl_issues = _scan_remote(_KEYS["jsonl"], tmp_path / "ws_js")
        assert parquet_scan.fingerprint.row_count == 6
        assert jsonl_scan.fingerprint.row_count == 6
        assert len(parquet_issues) > 0
        assert set(i.issue_type for i in parquet_issues) == set(i.issue_type for i in jsonl_issues)

    @pytest.mark.integration
    def test_content_fingerprint_quick_layer(self, minio_endpoint: str, tmp_path: Path) -> None:
        """快速失效层：元数据稳定则指纹稳定；覆盖写（Last-Modified 更新）则指纹变化。"""
        spec = DataSourceSpec(
            source_type=DataSourceType.PARQUET,
            path=_KEYS["parquet"],
            options={"dataset_id": "it"},
        )
        handle = default_registry().open(spec)
        try:
            first = handle.content_fingerprint()
            second = handle.content_fingerprint()
            assert first == second
        finally:
            handle.close()

        changed_rows = [tuple(r) for r in _DIRTY_ROWS[:3]]
        _seed_parquet_jsonl(_KEYS["parquet"], minio_endpoint, changed_rows)
        changed = default_registry().open(spec)
        try:
            assert changed.content_fingerprint() != first
        finally:
            changed.close()

    @pytest.mark.integration
    def test_fingerprint_full_no_file_bytes(self, minio_endpoint: str) -> None:
        spec = DataSourceSpec(
            source_type=DataSourceType.PARQUET,
            path=_KEYS["parquet"],
            options={"dataset_id": "it"},
        )
        handle = default_registry().open(spec)
        try:
            fp = handle.fingerprint("full")
            assert fp.file_sha256 is None
            assert fp.content_sample_hash is not None
            assert fp.row_count == 3  # changed_rows 覆盖后仅 3 行
        finally:
            handle.close()

    @pytest.mark.integration
    def test_missing_object_not_found(self, minio_endpoint: str) -> None:
        spec = DataSourceSpec(
            source_type=DataSourceType.PARQUET,
            path=f"s3://{_BUCKET}/does-not-exist.parquet",
            options={"dataset_id": "it"},
        )
        handle = default_registry().open(spec)
        try:
            with pytest.raises(DataSourceNotFoundError):
                handle.schema()
        finally:
            handle.close()

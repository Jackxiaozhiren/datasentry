"""Step 2 连接器测试：协议、注册表、CSV 加载/聚合/抽样/指纹/安全护栏。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from datasentry_core.connectors import (
    ConnectorRegistry,
    DataSourceSpec,
    DataSourceType,
    MySQLDataHandle,
    PostgresDataHandle,
    UnsupportedFormatError,
    default_registry,
)
from datasentry_core.connectors.csv import (
    CsvConnector,
    _schema_hash,
)
from datasentry_core.connectors.errors import DataSourceNotFoundError, UnsafeSqlError


@pytest.fixture
def basic_csv(tmp_path: Path) -> Path:
    p = tmp_path / "basic.csv"
    p.write_text(
        "order_id,customer_email,order_total,status\n"
        "1,a@x.com,12.5,completed\n"
        "2,b@x.com,30.0,pending\n"
        "3,c@x.com,7.25,completed\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def bom_csv(tmp_path: Path) -> Path:
    p = tmp_path / "bom.csv"
    p.write_bytes(b"\xef\xbb\xbfid,name\n1,alice\n2,bob\n")
    return p


@pytest.fixture
def latin1_csv(tmp_path: Path) -> Path:
    p = tmp_path / "latin1.csv"
    p.write_bytes("id,name\n1,café\n2,naïve\n".encode("latin-1"))
    return p


@pytest.fixture
def semicolon_csv(tmp_path: Path) -> Path:
    p = tmp_path / "semi.csv"
    p.write_text("a;b\n1;x\n2;y\n", encoding="utf-8")
    return p


@pytest.fixture
def formula_csv(tmp_path: Path) -> Path:
    p = tmp_path / "formula.csv"
    p.write_text(
        "id,note\n1,=SUM(A1:A9)\n2,plain text\n3,+1234\n4,@cmd\n5,-500\n",
        encoding="utf-8",
    )
    return p


class TestRegistry:
    def test_register_and_get(self) -> None:
        registry = ConnectorRegistry()
        connector = CsvConnector()
        registry.register(connector)
        assert registry.get("csv") is connector
        assert registry.list() == [connector]

    def test_duplicate_register_rejected(self) -> None:
        registry = ConnectorRegistry()
        registry.register(CsvConnector())
        with pytest.raises(ValueError):
            registry.register(CsvConnector())

    def test_default_registry_opens_csv(self, basic_csv: Path) -> None:
        registry = default_registry()
        spec = DataSourceSpec(source_type=DataSourceType.CSV, path=basic_csv)
        handle = registry.open(spec)
        try:
            assert handle.count_rows() == 3
        finally:
            handle.close()

    def test_unsupported_type_rejected(self) -> None:
        registry = default_registry()
        # Step 55/56 转正后 POSTGRESQL/MYSQL 已有连接器：无凭据的 URI 用例仍不
        # 被任何连接器支持（supports 要求 dsn/connection_ref）→ UnsupportedFormatError
        spec = DataSourceSpec(source_type=DataSourceType.POSTGRESQL, path=Path("x"), options={})
        with pytest.raises(UnsupportedFormatError):
            registry.get_for(spec)

    def test_mysql_with_dsn_dispatches(self) -> None:
        registry = default_registry()
        spec = DataSourceSpec(
            source_type=DataSourceType.MYSQL,
            table_name="t",
            options={"dsn": "mysql://u:p@localhost:3306/db"},
        )
        handle = registry.open(spec)
        try:
            assert isinstance(handle, MySQLDataHandle)
        finally:
            handle.close()

    def test_mysql_requires_table_name(self) -> None:
        registry = default_registry()
        spec = DataSourceSpec(
            source_type=DataSourceType.MYSQL,
            options={"dsn": "mysql://u:p@localhost:3306/db"},
        )
        with pytest.raises(DataSourceNotFoundError):
            registry.open(spec)

    def test_postgres_with_dsn_dispatches(self) -> None:
        registry = default_registry()
        spec = DataSourceSpec(
            source_type=DataSourceType.POSTGRESQL,
            table_name="t",
            options={"dsn": "postgresql://u:p@localhost:5432/db"},
        )
        handle = registry.open(spec)
        try:
            assert isinstance(handle, PostgresDataHandle)
        finally:
            handle.close()

    def test_postgres_requires_table_name(self) -> None:
        registry = default_registry()
        spec = DataSourceSpec(
            source_type=DataSourceType.POSTGRESQL,
            options={"dsn": "postgresql://u:p@localhost:5432/db"},
        )
        with pytest.raises(DataSourceNotFoundError):
            registry.open(spec)


class TestCsvHandle:
    def _open(self, path: Path, **options: object):
        spec = DataSourceSpec(source_type=DataSourceType.CSV, path=path, options=options)
        return CsvConnector().open(spec)

    def test_schema(self, basic_csv: Path) -> None:
        handle = self._open(basic_csv)
        try:
            info = handle.schema()
            assert info.column_names == [
                "order_id",
                "customer_email",
                "order_total",
                "status",
            ]
            assert info.columns[2].physical_type == "DOUBLE"
        finally:
            handle.close()

    def test_count_rows(self, basic_csv: Path) -> None:
        handle = self._open(basic_csv)
        try:
            assert handle.count_rows() == 3
        finally:
            handle.close()

    def test_read_batches_single(self, basic_csv: Path) -> None:
        handle = self._open(basic_csv)
        try:
            batches = list(handle.read_batches())
            assert len(batches) == 1
            assert batches[0].row_count == 3
            assert batches[0].row_offset == 0
            assert batches[0].column_names == handle.schema().column_names
        finally:
            handle.close()

    def test_read_batches_multiple(self, tmp_path: Path) -> None:
        p = tmp_path / "many.csv"
        p.write_text("id\n" + "".join(f"{i}\n" for i in range(20_000)), encoding="utf-8")
        handle = self._open(p)
        try:
            batches = list(handle.read_batches(batch_size=100))
            assert len(batches) >= 2
            total = sum(b.row_count for b in batches)
            assert total == 20_000
            offsets = [b.row_offset for b in batches]
            assert offsets == sorted(offsets)
        finally:
            handle.close()

    def test_bom_handled(self, bom_csv: Path) -> None:
        handle = self._open(bom_csv)
        try:
            assert handle.schema().column_names == ["id", "name"]
            assert handle.count_rows() == 2
            table = handle.read_sample(10, method="none").table
            assert table.column("name").to_pylist() == ["alice", "bob"]
        finally:
            handle.close()

    def test_latin1_encoding(self, latin1_csv: Path) -> None:
        handle = self._open(latin1_csv, encoding="latin-1")
        try:
            table = handle.read_sample(10, method="none").table
            assert table.column("name").to_pylist() == ["café", "naïve"]
        finally:
            handle.close()

    def test_delimiter_sniffing(self, semicolon_csv: Path) -> None:
        handle = self._open(semicolon_csv)
        try:
            assert handle.schema().column_names == ["a", "b"]
            assert handle.count_rows() == 2
        finally:
            handle.close()

    def test_sql_aggregate(self, basic_csv: Path) -> None:
        handle = self._open(basic_csv)
        try:
            # 无 ORDER BY 时 GROUP BY 行序不稳定，按集合断言
            batch = handle.sql_aggregate("SELECT status, count(*) AS n FROM data GROUP BY status")
            result = sorted(batch.table.to_pylist(), key=lambda r: r["status"])
            assert result == [
                {"status": "completed", "n": 2},
                {"status": "pending", "n": 1},
            ]
        finally:
            handle.close()

    def test_sql_aggregate_named_params(self, basic_csv: Path) -> None:
        handle = self._open(basic_csv)
        try:
            batch = handle.sql_aggregate("SELECT * FROM data WHERE order_id = $1", params={"1": 2})
            assert batch.table.num_rows == 1
        finally:
            handle.close()

    def test_sql_guard_rejects_writes(self, basic_csv: Path) -> None:
        handle = self._open(basic_csv)
        try:
            with pytest.raises(UnsafeSqlError):
                handle.sql_aggregate("UPDATE data SET status='x'")
            with pytest.raises(UnsafeSqlError):
                handle.sql_aggregate("SELECT 1; DROP TABLE data")
        finally:
            handle.close()

    def test_read_sample_reservoir_reproducible(self, tmp_path: Path) -> None:
        p = tmp_path / "big.csv"
        p.write_text("id\n" + "".join(f"{i}\n" for i in range(5000)), encoding="utf-8")
        h1 = self._open(p, seed=7)
        h2 = self._open(p, seed=7)
        try:
            s1 = h1.read_sample(200, method="reservoir").table.column("id").to_pylist()
            s2 = h2.read_sample(200, method="reservoir").table.column("id").to_pylist()
            assert s1 == s2
            assert len(s1) == 200
            assert len(set(s1)) == 200
        finally:
            h1.close()
            h2.close()

    def test_read_sample_none(self, basic_csv: Path) -> None:
        handle = self._open(basic_csv)
        try:
            batch = handle.read_sample(2, method="none")
            assert batch.table.num_rows == 2
        finally:
            handle.close()


class TestFingerprint:
    def _open(self, path: Path, **options: object):
        spec = DataSourceSpec(
            source_type=DataSourceType.CSV,
            path=path,
            options={"dataset_id": "ds_1", **options},
        )
        return CsvConnector().open(spec)

    def test_full_deterministic(self, basic_csv: Path) -> None:
        h1 = self._open(basic_csv)
        h2 = self._open(basic_csv)
        try:
            f1 = h1.fingerprint("full")
            f2 = h2.fingerprint("full")
            assert f1.dataset_id == f2.dataset_id
            assert f1.content_sample_hash == f2.content_sample_hash
            assert f1.schema_hash == f2.schema_hash
            assert len(f1.file_sha256 or "") == 64
            assert f1.row_count == 3
            assert f1.column_count == 4
        finally:
            h1.close()
            h2.close()

    def test_full_changes_with_content(self, tmp_path: Path) -> None:
        p = tmp_path / "fp.csv"
        p.write_text("a\n1\n", encoding="utf-8")
        h1 = self._open(p)
        f1 = h1.fingerprint("full")
        h1.close()
        p.write_text("a\n2\n", encoding="utf-8")
        h2 = self._open(p)
        f2 = h2.fingerprint("full")
        h2.close()
        assert f1.file_sha256 != f2.file_sha256
        assert f1.schema_hash == f2.schema_hash

    def test_sampled_reproducible_and_hashed(self, tmp_path: Path) -> None:
        p = tmp_path / "s.csv"
        p.write_text("id\n" + "".join(f"{i}\n" for i in range(3000)), encoding="utf-8")
        h1 = self._open(p)
        h2 = self._open(p)
        try:
            s1 = h1.fingerprint("sampled")
            s2 = h2.fingerprint("sampled")
            assert s1.content_sample_hash == s2.content_sample_hash
            assert s1.file_sha256 is None
        finally:
            h1.close()
            h2.close()

    def test_metadata_only(self, basic_csv: Path) -> None:
        handle = self._open(basic_csv)
        try:
            fp = handle.fingerprint("metadata_only")
            assert fp.fingerprint_type == "metadata_only"
            assert fp.file_sha256 is None
            assert fp.content_sample_hash is None
            assert fp.schema_hash == _schema_hash(
                [(c.name, c.physical_type) for c in handle.schema().columns]
            )
        finally:
            handle.close()


class TestFormulaInjectionWarnings:
    def test_detects_prefixes(self, formula_csv: Path) -> None:
        spec = DataSourceSpec(source_type=DataSourceType.CSV, path=formula_csv)
        handle = CsvConnector().open(spec)
        try:
            warnings = handle.warnings()
            assert len(warnings) == 4
            prefixes = {w.value_preview[0] for w in warnings}
            assert prefixes == {"=", "+", "@", "-"}
            assert all(w.column == "note" for w in warnings)
            assert all(w.row >= 0 for w in warnings)
        finally:
            handle.close()

    def test_cached(self, formula_csv: Path) -> None:
        spec = DataSourceSpec(source_type=DataSourceType.CSV, path=formula_csv)
        handle = CsvConnector().open(spec)
        try:
            assert handle.warnings() is not handle.warnings()
            assert handle.warnings() == handle.warnings()
        finally:
            handle.close()

    def test_no_false_positive(self, basic_csv: Path) -> None:
        spec = DataSourceSpec(source_type=DataSourceType.CSV, path=basic_csv)
        handle = CsvConnector().open(spec)
        try:
            assert handle.warnings() == []
        finally:
            handle.close()


class TestHandleLifecycle:
    def test_use_after_close_rejected(self, basic_csv: Path) -> None:
        spec = DataSourceSpec(source_type=DataSourceType.CSV, path=basic_csv)
        handle = CsvConnector().open(spec)
        handle.close()
        with pytest.raises(ValueError):
            handle.count_rows()

    def test_missing_file(self, tmp_path: Path) -> None:
        from datasentry_core.connectors.errors import DataSourceNotFoundError

        spec = DataSourceSpec(source_type=DataSourceType.CSV, path=tmp_path / "nope.csv")
        with pytest.raises(DataSourceNotFoundError):
            CsvConnector().open(spec)


_CSV_SAFE_TEXT = st.text(
    # 仅小写字母：规避 CSV 类型推断（数字/布尔/日期/空值）干扰字符串断言
    alphabet=st.characters(whitelist_categories=("Ll",)),
    min_size=1,
    max_size=10,
).filter(lambda s: s.lower() not in {"true", "false", "null", "none", "nan", "inf"})

_SIMPLE_ROWS = st.lists(
    st.tuples(st.integers(min_value=-(10**6), max_value=10**6), _CSV_SAFE_TEXT),
    min_size=1,
    max_size=100,
)


@given(_SIMPLE_ROWS)
def test_property_count_matches_source(rows: list[tuple[int, str]]) -> None:
    """属性测试：任意合法 CSV 内容，count/read 与源数据一致（29.2 起点）。"""
    p = Path(tempfile.mkdtemp()) / "prop.csv"
    p.write_text("id,name\n" + "".join(f"{a},{b}\n" for a, b in rows), encoding="utf-8")
    spec = DataSourceSpec(source_type=DataSourceType.CSV, path=p)
    handle = CsvConnector().open(spec)
    try:
        assert handle.count_rows() == len(rows)
        names = [
            row for batch in handle.read_batches() for row in batch.table.column("name").to_pylist()
        ]
        assert names == [b for _, b in rows]
    finally:
        handle.close()


@given(_SIMPLE_ROWS)
def test_property_schema_hash_stable(rows: list[tuple[int, str]]) -> None:
    """属性测试：同内容同 schema，schema_hash 恒定。"""
    p = Path(tempfile.mkdtemp()) / "prop2.csv"
    p.write_text("id,name\n" + "".join(f"{a},{b}\n" for a, b in rows), encoding="utf-8")
    spec = DataSourceSpec(source_type=DataSourceType.CSV, path=p)
    h1 = CsvConnector().open(spec)
    h2 = CsvConnector().open(spec)
    try:
        assert h1.fingerprint("full").schema_hash == h2.fingerprint("full").schema_hash
    finally:
        h1.close()
        h2.close()

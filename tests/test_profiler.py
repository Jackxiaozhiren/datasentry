"""Step 4 Profiling engine 测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from datasentry_core.connectors import CsvConnector, DataSourceSpec, DataSourceType
from datasentry_core.engine import Profiler

_CSV_SAFE_TEXT = st.text(
    alphabet=st.characters(whitelist_categories=("Ll",)),
    min_size=1,
    max_size=10,
).filter(lambda s: s.lower() not in {"true", "false", "null", "none", "nan", "inf"})


@pytest.fixture
def profiler_csv(tmp_path: Path) -> Path:
    p = tmp_path / "profile.csv"
    p.write_text(
        "id,amount,label\n1,10.5,a\n2,,b\n3,7.5,a\n4,10.5,c\n,5.0,\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def empty_csv(tmp_path: Path) -> Path:
    p = tmp_path / "empty.csv"
    p.write_text("id,amount\n", encoding="utf-8")
    return p


def _profile(path: Path, dataset_id: str = "ds_p"):
    spec = DataSourceSpec(
        source_type=DataSourceType.CSV, path=path, options={"dataset_id": dataset_id}
    )
    handle = CsvConnector().open(spec)
    try:
        return Profiler(handle, dataset_id).profile()
    finally:
        handle.close()


class TestProfiler:
    def test_row_and_column_counts(self, profiler_csv: Path) -> None:
        p = _profile(profiler_csv)
        assert p.row_count == 5
        assert p.column_count == 3
        assert set(p.column_profiles) == {"id", "amount", "label"}

    def test_null_ratio(self, profiler_csv: Path) -> None:
        p = _profile(profiler_csv)
        # amount: 1 个空值（第 2 行）；id: 1 个空值（第 5 行）
        assert p.column_profiles["amount"].null_ratio == pytest.approx(0.2)
        assert p.column_profiles["id"].null_ratio == pytest.approx(0.2)
        assert p.column_profiles["label"].null_ratio == pytest.approx(0.2)

    def test_distinct_and_unique_ratio(self, profiler_csv: Path) -> None:
        p = _profile(profiler_csv)
        label = p.column_profiles["label"]
        assert label.distinct_count == 3
        # unique_ratio = distinct / 非空值（label 4 个非空，3 个不同）
        assert label.unique_ratio == pytest.approx(0.75)

    def test_numeric_stats(self, profiler_csv: Path) -> None:
        p = _profile(profiler_csv)
        amount = p.column_profiles["amount"]
        assert amount.min == 5.0
        assert amount.max == 10.5
        assert amount.mean == pytest.approx((10.5 + 7.5 + 10.5 + 5.0) / 4)
        assert amount.median == pytest.approx((7.5 + 10.5) / 2)
        assert amount.std is not None

    def test_string_column_has_no_numeric_stats(self, profiler_csv: Path) -> None:
        p = _profile(profiler_csv)
        label = p.column_profiles["label"]
        assert label.mean is None
        assert label.q25 is None
        assert label.median is None
        assert label.std is None

    def test_top_categories(self, profiler_csv: Path) -> None:
        p = _profile(profiler_csv)
        label = p.column_profiles["label"]
        assert label.top_categories is not None
        assert label.top_categories[0] == ("a", 2)

    def test_empty_table(self, empty_csv: Path) -> None:
        p = _profile(empty_csv)
        assert p.row_count == 0
        assert p.column_profiles["id"].distinct_count == 0
        assert p.column_profiles["id"].null_ratio == 0.0
        assert p.column_profiles["id"].top_categories is None

    def test_high_cardinality_skips_top_categories(self, tmp_path: Path) -> None:
        p = tmp_path / "high.csv"
        p.write_text("v\n" + "".join(f"x{i}\n" for i in range(2000)), encoding="utf-8")
        profile = _profile(p)
        assert profile.column_profiles["v"].top_categories is None

    def test_identifier_quoting(self, tmp_path: Path) -> None:
        p = tmp_path / "weird.csv"
        p.write_text('"weird col","n"\n"a",1\n"b",2\n', encoding="utf-8")
        profile = _profile(p)
        assert profile.column_profiles["weird col"].distinct_count == 2

    def test_min_max_on_string(self, profiler_csv: Path) -> None:
        p = _profile(profiler_csv)
        label = p.column_profiles["label"]
        assert label.min == "a"
        assert label.max == "c"

    def test_performance_smoke(self, tmp_path: Path) -> None:
        """1e5 行画像冒烟：不严格断言耗时（基准套件在 Step 20 落地）。"""
        p = tmp_path / "perf.csv"
        p.write_text(
            "id,v\n" + "".join(f"{i},v{i % 100}\n" for i in range(100_000)),
            encoding="utf-8",
        )
        profile = _profile(p)
        assert profile.row_count == 100_000
        assert profile.column_profiles["id"].distinct_count == 100_000


@settings(max_examples=50)
@given(
    st.lists(
        st.tuples(st.integers(min_value=0, max_value=1000), _CSV_SAFE_TEXT), min_size=1, max_size=50
    ),
)
def test_property_profile_counts_match_source(rows: list[tuple[int, str]]) -> None:
    """属性测试：画像计数与源数据一致。"""
    p = Path(tempfile.mkdtemp()) / "prop_profile.csv"
    p.write_text("id,label\n" + "".join(f"{a},{b}\n" for a, b in rows), encoding="utf-8")
    profile = _profile(p)
    assert profile.row_count == len(rows)
    id_col = profile.column_profiles["id"]
    assert id_col.null_ratio == 0.0
    assert id_col.distinct_count == len({a for a, _ in rows})
    assert id_col.min == min(a for a, _ in rows)
    assert id_col.max == max(a for a, _ in rows)
    label_col = profile.column_profiles["label"]
    assert label_col.distinct_count == len({b for _, b in rows})

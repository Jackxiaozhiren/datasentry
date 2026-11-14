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


class TestNonFiniteNumbersAreProfiledNotFatal:
    """G-2：数值列里有一个 NaN 就让整个画像崩，四个面都把一次合法扫描读成 500。

    本机实测触发点比预想的宽：一份普通 CSV 里写 `nan` 字样就够了——列被推断成 DOUBLE，
    `stddev` 直接抛 `OutOfRangeException: STDDEV_SAMP is out of range!`，而 pandas 导出的
    缺省形态正是这样。崩之前 `min`/`max`/`avg` 也不报错，只是把 NaN 当数字参与运算：
    `[1.0, nan, 3.0]` 的 `max` 报 `nan`、`median` 报 `3.0`（真值 2.0）——不崩的那些数同样是假的。
    画像的语义因此定为：**统计量只在有限值上算**，NaN 既不是数也不是空值，
    它作为"值"仍计入 `count`/`distinct`，作为"数"不进 min/max/mean/std/分位。
    """

    def test_a_nan_token_in_a_numeric_column_is_profiled_instead_of_crashing(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "nan.csv"
        p.write_text("id,v\n1,1.0\n2,nan\n3,3.0\n", encoding="utf-8")
        profile = _profile(p)
        assert profile.row_count == 3
        assert profile.column_profiles["v"].std is not None

    def test_nan_is_not_counted_as_a_number_in_the_summary(self, tmp_path: Path) -> None:
        p = tmp_path / "nan.csv"
        p.write_text("id,v\n1,1.0\n2,nan\n3,3.0\n", encoding="utf-8")
        col = _profile(p).column_profiles["v"]
        assert col.min == pytest.approx(1.0), col.min
        assert col.max == pytest.approx(3.0), f"max returned NaN as the largest number: {col.max}"
        assert col.mean == pytest.approx(2.0), col.mean
        assert col.median == pytest.approx(2.0), f"median ordered NaN as a value: {col.median}"
        assert col.std == pytest.approx(1.4142135623730951), col.std
        # NaN 仍然是一个"值"，不是 NULL：缺失率必须保持 0
        assert col.null_ratio == pytest.approx(0.0), col.null_ratio

    def test_an_all_nan_column_yields_null_statistics_not_a_crash(self, tmp_path: Path) -> None:
        p = tmp_path / "allnan.csv"
        p.write_text("id,v\n1,nan\n2,nan\n", encoding="utf-8")
        col = _profile(p).column_profiles["v"]
        assert col.mean is None
        assert col.std is None
        assert col.min is None and col.max is None

    def test_integer_and_decimal_columns_are_unaffected_by_the_finite_guard(
        self, tmp_path: Path
    ) -> None:
        """守卫必须对不可含 NaN 的类型也成立（`isfinite` 在 DuckDB 对整数/小数返回 True）。"""
        p = tmp_path / "plain.csv"
        p.write_text("id,amount\n1,10.5\n2,7.5\n3,12.0\n", encoding="utf-8")
        col = _profile(p).column_profiles["amount"]
        assert col.min == pytest.approx(7.5)
        assert col.max == pytest.approx(12.0)
        assert col.mean == pytest.approx(30.0 / 3.0)
        assert col.std is not None
        ids = _profile(p).column_profiles["id"]
        assert ids.min == 1 and ids.max == 3 and ids.mean == pytest.approx(2.0)

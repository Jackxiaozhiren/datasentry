"""Step 41：模糊重复检测器（Level 3，归一化分组）。"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from datasentry import DataSentry
from datasentry_core.connectors.registry import default_registry
from datasentry_core.connectors.spec import DataSourceSpec
from datasentry_core.detectors.base import DetectionContext
from datasentry_core.detectors.initial.uniqueness import FuzzyDuplicateDetector
from datasentry_core.models.enums import QualityDimension
from datasentry_core.models.scan import ScanConfig

DETECTOR = FuzzyDuplicateDetector()


def _write_csv(path: Path, columns: list[str], rows: list[list[object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        writer.writerows(rows)


def _make_context(path: Path) -> DetectionContext:
    spec = DataSourceSpec(source_type="csv", path=path, options={})
    handle = default_registry().open(spec)
    return DetectionContext(
        dataset_id="people",
        table_name=None,
        columns=handle.schema().column_names,
        handle=handle,
        config=ScanConfig(),
    )


@pytest.fixture()
def case_variant(tmp_path: Path) -> Path:
    path = tmp_path / "people.csv"
    _write_csv(
        path,
        ["id", "name", "email"],
        [
            [1, "Alice", "a@x.com"],
            [2, "alice", "a@x.com"],
            [3, "BOB", "b@x.com"],
            [4, "bob", "b@x.com"],
            [5, "Carol", "c@x.com"],
        ],
    )
    return path


@pytest.fixture()
def whitespace_cjk(tmp_path: Path) -> Path:
    path = tmp_path / "names.csv"
    _write_csv(
        path,
        ["id", "name"],
        [
            [1, " 张三 "],
            [2, "张三"],
            [3, "李四"],
            [4, "李四!"],
            [5, "王五"],
        ],
    )
    return path


def test_case_and_whitespace_variants(case_variant: Path) -> None:
    context = _make_context(case_variant)
    try:
        assert DETECTOR.supports(context)
        candidates = DETECTOR.detect(context)
        name_candidates = [c for c in candidates if c.columns == ["name"]]
        assert len(name_candidates) == 1
        issue = name_candidates[0]
        assert issue.issue_type == "fuzzy_duplicate"
        assert issue.affected_count == 2  # alice/ bob 两组各 1 行冗余
        groups = issue.evidence[0].data["groups"]
        keys = {g["normalized"] for g in groups}
        assert keys == {"alice", "bob"}
        email_groups = [c for c in candidates if c.columns == ["email"]]
        assert email_groups == []
    finally:
        context.handle.close()


def test_cjk_names(whitespace_cjk: Path) -> None:
    context = _make_context(whitespace_cjk)
    try:
        candidates = DETECTOR.detect(context)
        assert len(candidates) == 1
        issue = candidates[0]
        assert issue.columns == ["name"]
        groups = issue.evidence[0].data["groups"]
        assert {g["normalized"] for g in groups} == {"张三", "李四"}
        assert issue.affected_count == 2
    finally:
        context.handle.close()


def test_no_variants_no_issue(tmp_path: Path) -> None:
    path = tmp_path / "clean.csv"
    _write_csv(
        path,
        ["id", "city"],
        [[1, "beijing"], [2, "shanghai"], [3, "guangzhou"]],
    )
    context = _make_context(path)
    try:
        assert DETECTOR.detect(context) == []
    finally:
        context.handle.close()


def test_numeric_column_skipped(tmp_path: Path) -> None:
    path = tmp_path / "nums.csv"
    _write_csv(
        path,
        ["id", "score"],
        [[1, 10], [2, 10], [3, 10]],
    )
    context = _make_context(path)
    try:
        assert not DETECTOR.supports(context)
        assert DETECTOR.detect(context) == []
    finally:
        context.handle.close()


def test_short_keys_filtered(tmp_path: Path) -> None:
    path = tmp_path / "short.csv"
    _write_csv(
        path,
        ["id", "code"],
        [[1, "A"], [2, "a"], [3, "B"], [4, "b"]],
    )
    context = _make_context(path)
    try:
        assert DETECTOR.detect(context) == []
    finally:
        context.handle.close()


def test_end_to_end_through_client(tmp_path: Path, case_variant: Path) -> None:
    client = DataSentry(project=tmp_path / "ws")
    try:
        scan, _, issues = client.scan_file(case_variant)
        assert scan.status == "completed"
        fuzzy = [i for i in issues if i.issue_type == "uniqueness"]
        assert any(i.title == "Duplicate values in name" for i in fuzzy)
        assert any(QualityDimension.UNIQUENESS in i.quality_dimensions for i in fuzzy)
    finally:
        client.close()

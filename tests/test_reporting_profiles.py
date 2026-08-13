"""Step 61（ADR-061）Column Profiles 交互节测试：序列化、排序、渲染、PII 掩码。

覆盖契约：profile_rows（模型 dump → 视图行，top_categories 掩码）；
sort_profiles（默认 null 降序 / name 文本比较 / 非法键报错）；
render_column_profiles（#profiles 标记、data-key 表头、json_script 转义、
迷你空值条、语义/PII 徽标、chips）；render_html(profiles=...) 节插入与导航。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from datasentry_core import __version__
from datasentry_core.models.enums import QualityDimension, Severity
from datasentry_core.models.issue import Issue
from datasentry_core.models.quality import QualityScore
from datasentry_core.models.scan import (
    DetectorRun,
    ReproducibilityInfo,
    ScanConfig,
    ScanRun,
)
from datasentry_core.reporting import build_report
from datasentry_core.reporting.column_profiles import (
    SORTABLE_KEYS,
    profile_rows,
    render_column_profiles,
    sort_profiles,
)
from datasentry_core.reporting.html import render_html


def _profiles() -> dict:
    """最小 DatasetProfile model_dump(mode="json") 形状。"""
    return {
        "dataset_id": "orders",
        "row_count": 100,
        "column_count": 2,
        "profiler_version": "0.1.0",
        "column_profiles": {
            "email": {
                "dataset_id": "orders",
                "column_name": "email",
                "physical_type": "VARCHAR",
                "semantic_type": "email",
                "contains_pii": True,
                "null_ratio": 0.05,
                "unique_ratio": 0.95,
                "distinct_count": 95,
                "min": None,
                "q25": None,
                "median": None,
                "q75": None,
                "max": None,
                "mean": None,
                "std": None,
                "top_categories": [("alice@example.com", 3), ("bob@example.com", 2)],
            },
            "amount": {
                "dataset_id": "orders",
                "column_name": "amount",
                "physical_type": "DOUBLE",
                "semantic_type": "unknown",
                "contains_pii": False,
                "null_ratio": 0.3,
                "unique_ratio": 0.4,
                "distinct_count": 40,
                "min": 1.5,
                "q25": 2.0,
                "median": 3.25,
                "q75": 5.0,
                "max": 99.9,
                "mean": 10.25,
                "std": 2.5,
                "top_categories": None,
            },
        },
    }


def _report() -> dict:
    scan = ScanRun(
        id="scan_abc",
        dataset_id="orders",
        status="completed",
        config=ScanConfig(),
        fingerprint={
            "dataset_id": "orders",
            "fingerprint_type": "full",
            "schema_hash": "h1",
            "row_count": 100,
            "column_count": 2,
            "column_signature": [["id", "BIGINT"], ["email", "VARCHAR"]],
        },
        issues_count={s: 0 for s in Severity},
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        reproducibility=ReproducibilityInfo(
            datasentry_version=__version__,
            detector_versions={"numeric_outlier": "1.0.0"},
            seed=42,
            scanned_at=datetime.now(UTC),
        ),
    )
    runs = [
        DetectorRun(
            id="dr_1",
            scan_run_id="scan_abc",
            detector_id="numeric_outlier",
            detector_version="1.0.0",
            status="completed",
            rows_scanned=100,
            duration_ms=5,
            issues_candidates=1,
        )
    ]
    issues = [
        Issue(
            id="iss_1",
            scan_run_id="scan_abc",
            issue_type="numeric_outlier",
            title="Outlier in amount",
            dataset_id="orders",
            columns=["amount"],
            quality_dimensions=[QualityDimension.VALIDITY],
            severity=Severity.HIGH,
            confidence=0.9,
            priority_score=78.5,
            affected_count=2,
            affected_ratio=0.02,
            detector_ids=["numeric_outlier"],
        )
    ]
    quality = QualityScore(
        overall=88.2,
        dimensions={"validity": 76.6},
        weights={"validity": 1.0},
        calculation_notes="dimension = 100 * (1 - ...)",
        dimension_contributions={"validity": {"iss_1": 0.375}},
    )
    return build_report(scan, runs, issues, quality, generated_at=datetime(2026, 8, 2))


class TestProfileRows:
    def test_masks_top_categories_text(self) -> None:
        rows = profile_rows(_profiles())
        email = next(r for r in rows if r["name"] == "email")
        assert email["topCategories"] == [
            {"value": "[REDACTED]", "count": 3},
            {"value": "[REDACTED]", "count": 2},
        ]

    def test_numeric_columns_carry_sortable_values(self) -> None:
        rows = profile_rows(_profiles())
        amount = next(r for r in rows if r["name"] == "amount")
        assert amount["null"] == 0.3
        assert amount["unique"] == 0.4
        assert amount["distinct"] == 40
        assert amount["mean"] == "10.25"
        assert amount["median"] == "3.25"
        assert amount["min"] == "1.5"
        assert amount["max"] == "99.9"
        assert amount["std"] == "2.5"
        assert amount["containsPii"] is False
        assert amount["semanticType"] == "unknown"

    def test_absent_categories_become_empty(self) -> None:
        data = _profiles()
        data["column_profiles"]["email"].pop("top_categories", None)
        rows = profile_rows(data)
        email = next(r for r in rows if r["name"] == "email")
        assert email["topCategories"] == []
        assert email["min"] is None


class TestSortProfiles:
    def test_default_sorts_null_desc(self) -> None:
        rows = profile_rows(_profiles())
        order = [r["name"] for r in sort_profiles(rows)]
        assert order == ["amount", "email"]

    def test_sort_by_distinct_asc(self) -> None:
        rows = profile_rows(_profiles())
        order = [r["name"] for r in sort_profiles(rows, key="distinct", reverse=False)]
        assert order == ["amount", "email"]

    def test_sort_by_name_case_insensitive(self) -> None:
        rows = profile_rows(_profiles())
        order = [r["name"] for r in sort_profiles(rows, key="name", reverse=False)]
        assert order == ["amount", "email"]

    def test_unknown_key_raises(self) -> None:
        with pytest.raises(ValueError, match="unsupported sort key"):
            sort_profiles([], key="bogus")

    def test_sortable_keys_cover_contract(self) -> None:
        assert {"name", "null", "unique", "distinct", "mean", "median", "std"} == SORTABLE_KEYS


class TestRenderColumnProfiles:
    def test_markers_and_headers(self) -> None:
        html = render_column_profiles(_profiles())
        assert '<div id="profiles">' in html
        assert 'id="profiles-table"' in html
        assert 'id="profiles-tbody"' in html
        for key in ("name", "null", "unique", "distinct", "mean", "median", "std"):
            assert f'data-key="{key}"' in html
        assert 'data-key="min"' not in html

    def test_payload_escapes_script_close(self) -> None:
        data = _profiles()
        data["column_profiles"]["email"]["top_categories"] = [
            ("</script><script>alert(1)</script>", 1)
        ]
        html = render_column_profiles(data)
        payload = html.split('id="profiles-data">')[1].split("</script>")[0]
        assert "</script>" not in payload
        decoded = json.loads(payload)
        assert decoded["columns"][0]["topCategories"][0]["value"] == (
            "</script><script>alert(1)</script>"
        )

    def test_null_bars_use_percent_width(self) -> None:
        html = render_column_profiles(_profiles())
        assert 'className = "profiles-bar-track"' in html
        assert 'className = "profiles-bar"' in html
        assert '(c.null * 100).toFixed(1) + "%"' in html

    def test_badges(self) -> None:
        html = render_column_profiles(_profiles())
        assert '"badge-semantic"' in html
        assert '"badge-pii"' in html
        assert 'className = "chip"' in html
        assert "\u00d7" in html


class TestRenderHtmlIntegration:
    def test_profiles_section_and_nav_link(self) -> None:
        html = render_html(_report(), profiles=_profiles())
        assert '<h2 id="column_profiles">Column Profiles</h2>' in html
        assert 'href="#column_profiles"' in html
        assert '<div id="profiles">' in html
        assert html.index('id="column_profiles"') < html.index("Column Profiles")

    def test_without_profiles_no_section(self) -> None:
        html = render_html(_report())
        assert "column_profiles" not in html

    def test_empty_profiles_dict_skips_section(self) -> None:
        html = render_html(_report(), profiles={})
        assert "column_profiles" not in html


@pytest.mark.parametrize(
    "name",
    ["profile_rows", "render_column_profiles", "sort_profiles"],
)
def test_public_api(name: str) -> None:
    from datasentry_core.reporting import column_profiles as module

    assert callable(getattr(module, name))

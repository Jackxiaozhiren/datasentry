"""Step 49 交互式 HTML 报告测试（V2-B，ADR-049：筛选/排序/分页纯函数 + 快照 + 注入防护）。"""

from __future__ import annotations

import json

import pytest

from datasentry_core.reporting.interactive import (
    filter_issues,
    issue_rows,
    json_script,
    paginate,
    render_interactive_issue_table,
    render_trend_svg,
    sort_issues,
)

_SEV_ORDER = ("critical", "high", "medium", "low", "info")


def _row(issue_id: str, **overrides) -> dict:
    base = {
        "id": issue_id,
        "issue_type": "numeric_outlier",
        "title": f"Issue {issue_id}",
        "description": f"Description of {issue_id}",
        "severity": "medium",
        "priority": 50.0,
        "confidence": 0.8,
        "falsePositiveRisk": "medium",
        "affected": 3,
        "affectedRatio": 0.03,
        "affectedRowIds": ["1", "2"],
        "columns": ["amount"],
        "detectors": ["numeric_outlier"],
        "dimensions": ["validity"],
    }
    base.update(overrides)
    return base


def _rows() -> list[dict]:
    return [
        _row(
            "a",
            severity="critical",
            priority=95.0,
            affected=10,
            title="Duplicate rows",
            columns=["id"],
            detectors=["uniqueness_violation"],
            dimensions=["uniqueness"],
        ),
        _row(
            "b",
            severity="high",
            priority=78.5,
            affected=5,
            title="Outlier in amount",
            dimensions=["validity", "completeness"],
        ),
        _row(
            "c",
            severity="low",
            priority=30.0,
            affected=1,
            title="minor whitespace",
            columns=["name"],
            detectors=["whitespace_detector"],
        ),
    ]


def _report() -> dict:
    return {
        "report_schema_version": "1.0",
        "datasentry_version": "0.2.0",
        "scan_run_id": "scan_abc",
        "generated_at": "2026-08-02T00:00:00+00:00",
        "reproducible": True,
        "llm_used": False,
        "scan": {"id": "scan_abc", "dataset_id": "orders", "status": "completed"},
        "detector_runs": [],
        "issues": [
            {
                "id": "iss_1",
                "issue_type": "uniqueness_violation",
                "title": "Duplicate rows",
                "description": "found 10 duplicate rows",
                "dataset_id": "orders",
                "columns": ["id"],
                "quality_dimensions": ["uniqueness"],
                "severity": "critical",
                "confidence": 0.95,
                "priority_score": 95.0,
                "false_positive_risk": "low",
                "affected_count": 10,
                "affected_ratio": 0.1,
                "affected_row_ids": ["1", "2", "3"],
                "detector_ids": ["uniqueness_violation"],
            },
            {
                "id": "iss_2",
                "issue_type": "numeric_outlier",
                "title": "bad email alice@example.com",
                "description": "",
                "dataset_id": "orders",
                "columns": ["amount"],
                "quality_dimensions": ["validity"],
                "severity": "high",
                "confidence": 0.8,
                "priority_score": 78.5,
                "false_positive_risk": "medium",
                "affected_count": 5,
                "affected_ratio": 0.05,
                "affected_row_ids": None,
                "detector_ids": ["numeric_outlier"],
            },
        ],
        "quality": None,
    }


class TestIssueRows:
    def test_view_model_fields_and_pii_masking(self) -> None:
        rows = issue_rows(_report())
        assert len(rows) == 2
        assert rows[0]["id"] == "iss_1"
        assert rows[0]["severity"] == "critical"
        assert rows[0]["priority"] == 95.0
        assert rows[0]["dimensions"] == ["uniqueness"]
        assert rows[1]["affectedRowIds"] == []
        assert "alice@example.com" not in rows[1]["title"]
        assert "[REDACTED]" in rows[1]["title"]

    def test_affected_row_ids_truncated(self) -> None:
        rows = _report()["issues"]
        rows[0]["affected_row_ids"] = [str(i) for i in range(30)]
        truncated = issue_rows({"scan_run_id": "s", "issues": rows})
        assert len(truncated[0]["affectedRowIds"]) <= 10


class TestFilterIssues:
    def test_no_filters_returns_all(self) -> None:
        assert filter_issues(_rows()) == _rows()

    def test_by_severity(self) -> None:
        assert [r["id"] for r in filter_issues(_rows(), severity="high")] == ["b"]
        assert filter_issues(_rows(), severity="critical")[0]["id"] == "a"

    def test_by_dimension(self) -> None:
        assert [r["id"] for r in filter_issues(_rows(), dimension="completeness")] == ["b"]

    def test_by_search_title_and_columns(self) -> None:
        assert [r["id"] for r in filter_issues(_rows(), search="outlier")] == ["b"]
        assert [r["id"] for r in filter_issues(_rows(), search="id")] == ["a"]
        assert filter_issues(_rows(), search="  ") == _rows()  # 空白搜索 = 不过滤

    def test_combined(self) -> None:
        assert filter_issues(_rows(), severity="medium", search="amount") == []

    def test_search_case_insensitive(self) -> None:
        assert [r["id"] for r in filter_issues(_rows(), search="OUTLIER")] == ["b"]


class TestSortIssues:
    def test_default_priority_desc(self) -> None:
        rows = sort_issues(_rows())
        assert [r["id"] for r in rows] == ["a", "b", "c"]

    def test_priority_asc(self) -> None:
        rows = sort_issues(_rows(), reverse=False)
        assert [r["id"] for r in rows] == ["c", "b", "a"]

    def test_by_severity_rank(self) -> None:
        rows = sort_issues(_rows(), key="severity", reverse=False)
        assert [r["id"] for r in rows] == ["a", "b", "c"]

    def test_by_affected(self) -> None:
        rows = sort_issues(_rows(), key="affected", reverse=False)
        assert [r["id"] for r in rows] == ["c", "b", "a"]

    def test_by_title(self) -> None:
        rows = sort_issues(_rows(), key="title", reverse=False)
        assert [r["id"] for r in rows] == ["a", "b", "c"]

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValueError):
            sort_issues(_rows(), key="nope")

    def test_stable_and_unknown_severity_ranked_last(self) -> None:
        rows = [*_rows(), _row("x", severity="weird")]
        sorted_rows = sort_issues(rows, key="severity", reverse=False)
        assert [r["id"] for r in sorted_rows] == ["a", "b", "c", "x"]


class TestPaginate:
    def test_page_slices(self) -> None:
        page, total_pages, total = paginate(_rows(), page=1, page_size=2)
        assert [r["id"] for r in page] == ["a", "b"]
        assert total_pages == 2
        assert total == 3

    def test_last_page(self) -> None:
        page, _total_pages, total = paginate(_rows(), page=2, page_size=2)
        assert [r["id"] for r in page] == ["c"]
        assert total == 3

    def test_page_clamped(self) -> None:
        page, total_pages, _ = paginate(_rows(), page=0, page_size=2)
        assert [r["id"] for r in page] == ["a", "b"]
        assert total_pages == 2
        page, _pages, _total = paginate(_rows(), page=99, page_size=2)
        assert [r["id"] for r in page] == ["c"]

    def test_empty_rows(self) -> None:
        page, total_pages, total = paginate([], page=1, page_size=25)
        assert page == [] and total_pages == 1 and total == 0

    def test_exact_fit(self) -> None:
        page, total_pages, total = paginate(_rows(), page_size=3)
        assert len(page) == 3 and total_pages == 1 and total == 3


class TestJsonScript:
    def test_script_close_injection_escaped(self) -> None:
        payload = {"title": "</script><script>alert(1)</script>"}
        out = json_script(payload)
        assert "</script>" not in out
        assert "\\u003c/script\\u003e" in out
        assert "\\u003cscript\\u003e" in out

    def test_roundtrip_preserves_values(self) -> None:
        payload = {"a": "<b>&</b>", "n": 1.5, "ok": True}
        assert json.loads(json_script(payload)) == payload

    def test_compact_and_ascii(self) -> None:
        out = json_script({"t": "中文 <tag>"})
        assert "中文" not in out  # ensure_ascii
        assert "\\u003ctag\\u003e" in out


class TestTrendSvg:
    def test_renders_polyline_and_dots(self) -> None:
        trend = {
            "dataset_id": "orders",
            "points": [
                {"run_id": "r1", "score": 70.0, "issues_total": 5, "finished_at": "2026-07-01"},
                {"run_id": "r2", "score": 88.2, "issues_total": 2, "finished_at": "2026-08-01"},
            ],
        }
        svg = render_trend_svg(trend)
        assert '<svg class="trend-svg"' in svg
        assert "<polyline" in svg
        assert svg.count("<circle") == 2
        assert "orders" in svg and "2 completed scans" in svg

    def test_flat_series_no_division_by_zero(self) -> None:
        trend = {
            "dataset_id": "flat",
            "points": [{"score": 80.0}, {"score": 80.0}, {"score": 80.0}],
        }
        svg = render_trend_svg(trend)
        assert svg.count("<circle") == 3
        assert '<polyline points="5.0,55.0 130.0,55.0 255.0,55.0"' in svg

    def test_less_than_two_points_returns_empty(self) -> None:
        assert render_trend_svg({"dataset_id": "solo", "points": [{"score": 90.0}]}) == ""

    def test_dataset_id_escaped(self) -> None:
        trend = {"dataset_id": "<b>evil</b>", "points": [{"score": 1}, {"score": 2}]}
        svg = render_trend_svg(trend)
        assert "<b>evil</b>" not in svg
        assert "&lt;b&gt;evil&lt;/b&gt;" in svg


class TestInteractiveTable:
    def test_controls_and_container(self) -> None:
        html = render_interactive_issue_table(_report())
        assert 'id="issue_breakdown"' in html
        assert 'id="issue-table"' in html and 'id="issue-tbody"' in html
        assert 'id="f-severity"' in html and 'id="f-dimension"' in html
        assert 'id="f-search"' in html
        assert 'id="pg-prev"' in html and 'id="pg-next"' in html

    def test_dimension_options_derived_from_issues(self) -> None:
        html = render_interactive_issue_table(_report())
        assert '<option value="uniqueness">' in html
        assert '<option value="validity">' in html

    def test_embedded_data_json_and_js(self) -> None:
        html = render_interactive_issue_table(_report())
        assert 'type="application/json" id="issue-data"' in html
        assert '"severity":"critical"' in html
        assert "JSON.parse(document.getElementById" in html

    def test_server_base_url_wired_into_payload(self) -> None:
        html = render_interactive_issue_table(_report(), server_base_url="http://localhost:8000")
        assert '"serverBaseUrl":"http://localhost:8000"' in html

    def test_empty_issues_still_renders_container(self) -> None:
        report = _report()
        report["issues"] = []
        html = render_interactive_issue_table(report)
        assert 'id="issue-table"' in html
        assert '"issues":[]' in html


class TestPiiAndInjection:
    def test_pii_masked_in_payload(self) -> None:
        html = render_interactive_issue_table(_report())
        assert "alice@example.com" not in html
        assert "[REDACTED]" in html

    def test_script_tag_injection_not_in_html(self) -> None:
        report = _report()
        report["issues"][0]["title"] = "<script>alert(1)</script>"
        html = render_interactive_issue_table(report)
        assert "<script>alert(1)</script>" not in html
        assert "\\u003cscript\\u003ealert(1)" in html


class TestHtmlRenderIntegration:
    def _full_report(self) -> dict:
        from datetime import UTC, datetime

        from datasentry_core import __version__
        from datasentry_core.models.issue import Issue
        from datasentry_core.models.quality import QualityScore
        from datasentry_core.models.scan import ReproducibilityInfo, ScanConfig, ScanRun
        from datasentry_core.reporting import build_report

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
            issues_count={},
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            reproducibility=ReproducibilityInfo(
                datasentry_version=__version__,
                detector_versions={"numeric_outlier": "1.0.0"},
                seed=42,
                scanned_at=datetime.now(UTC),
            ),
        )
        issues = [
            Issue(
                id="iss_1",
                scan_run_id="scan_abc",
                issue_type="uniqueness_violation",
                title="Duplicate rows",
                dataset_id="orders",
                columns=["id"],
                quality_dimensions=["uniqueness"],
                severity="critical",
                confidence=0.95,
                priority_score=95.0,
                affected_count=10,
                affected_ratio=0.1,
                detector_ids=["uniqueness_violation"],
            ),
            Issue(
                id="iss_2",
                scan_run_id="scan_abc",
                issue_type="numeric_outlier",
                title="bad email alice@example.com",
                dataset_id="orders",
                columns=["amount"],
                quality_dimensions=["validity"],
                severity="high",
                confidence=0.8,
                priority_score=78.5,
                affected_count=5,
                affected_ratio=0.05,
                detector_ids=["numeric_outlier"],
            ),
        ]
        quality = QualityScore(
            overall=88.2,
            dimensions={"validity": 76.6, "uniqueness": 100.0},
            weights={"validity": 0.5, "uniqueness": 0.5},
            calculation_notes="n/a",
            dimension_contributions={},
        )
        return build_report(scan, [], issues, quality)

    def test_render_html_wires_interactive_table(self) -> None:
        from datasentry_core.reporting.html import render_html

        html = render_html(self._full_report())
        assert 'id="issue-table"' in html
        assert 'id="issue-data"' in html
        assert '"serverBaseUrl":null' in html  # 默认离线模式无 server 联动

    def test_trends_section_present_only_when_supplied(self) -> None:
        from datasentry_core.reporting.html import render_html

        trend = {
            "dataset_id": "orders",
            "points": [{"score": 70.0}, {"score": 88.2}, {"score": 90.0}],
        }
        assert 'id="quality_trends"' not in render_html(self._full_report())
        html = render_html(self._full_report(), trends=[trend])
        assert 'id="quality_trends"' in html
        assert '<svg class="trend-svg"' in html
        assert "Quality Trends" in html

    def test_server_base_url_enables_workbench_link(self) -> None:
        from datasentry_core.reporting.html import render_html

        html = render_html(self._full_report(), server_base_url="http://localhost:8000")
        assert '"serverBaseUrl":"http://localhost:8000"' in html

    def test_page_size_passed_through(self) -> None:
        from datasentry_core.reporting.html import render_html

        html = render_html(self._full_report(), page_size=10)
        assert '"pageSize":10' in html

    def test_report_remains_audit_artifact(self) -> None:
        from datasentry_core.reporting.html import render_html

        html = render_html(self._full_report())
        assert 'id="reproducibility"' in html
        assert 'id="methodology"' in html
        assert "scan_abc" in html
        assert "report_schema_version" in html

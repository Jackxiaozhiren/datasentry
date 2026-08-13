"""Step 12 报告引擎测试（26 章 + ADR-014：报告头、格式渲染、自包含、转义）。"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from datasentry_core import __version__
from datasentry_core.models.enums import QualityDimension, Severity
from datasentry_core.models.issue import Issue
from datasentry_core.models.quality import QualityScore
from datasentry_core.models.scan import DetectorRun, ReproducibilityInfo, ScanConfig, ScanRun
from datasentry_core.reporting import (
    CRITICAL_FINDINGS_LIMIT,
    HTML_SECTIONS,
    REPORT_SCHEMA_VERSION,
    build_report,
    critical_findings,
)
from datasentry_core.reporting.html import render_html
from datasentry_core.reporting.junit import render_junit
from datasentry_core.reporting.markdown import render_markdown
from datasentry_core.reporting.sarif import render_sarif


def _scan() -> ScanRun:
    return ScanRun(
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


def _runs() -> list[DetectorRun]:
    return [
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


def _issues() -> list[Issue]:
    return [
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
        ),
        Issue(
            id="iss_2",
            scan_run_id="scan_abc",
            issue_type="uniqueness_violation",
            title="Duplicate rows",
            dataset_id="orders",
            columns=["id"],
            quality_dimensions=[QualityDimension.UNIQUENESS],
            severity=Severity.CRITICAL,
            confidence=0.95,
            priority_score=95.0,
            affected_count=1,
            affected_ratio=0.01,
            detector_ids=["uniqueness_violation"],
        ),
    ]


def _quality() -> QualityScore:
    return QualityScore(
        overall=88.2,
        dimensions={"validity": 76.6, "uniqueness": 100.0},
        weights={"validity": 0.5, "uniqueness": 0.5},
        calculation_notes="dimension = 100 * (1 - ...)",
        dimension_contributions={"validity": {"iss_1": 0.375}},
    )


def _report() -> dict:
    return build_report(_scan(), _runs(), _issues(), _quality(), generated_at=datetime(2026, 8, 2))


class TestReportContract:
    def test_header_262(self) -> None:
        report = _report()
        assert report["report_schema_version"] == REPORT_SCHEMA_VERSION == "1.0"
        assert report["datasentry_version"] == __version__
        assert report["scan_run_id"] == "scan_abc"
        assert report["generated_at"].startswith("2026-08-02")
        assert report["reproducible"] is True
        assert report["llm_used"] is False

    def test_json_consumable_structure(self) -> None:
        report = _report()
        assert report["scan"]["id"] == "scan_abc"
        assert len(report["detector_runs"]) == 1
        assert len(report["issues"]) == 2
        assert report["quality"]["overall"] == 88.2

    def test_critical_findings_ordering_and_limit(self) -> None:
        report = _report()
        findings = critical_findings(report)
        assert len(findings) <= CRITICAL_FINDINGS_LIMIT
        assert findings[0]["id"] == "iss_2"  # critical 优先于 high
        assert [i["severity"] for i in findings] == ["critical", "high"]


class TestMarkdown:
    def test_sections_and_tables(self) -> None:
        md = render_markdown(_report())
        assert md.startswith("# DataSentry Data Quality Report")
        assert "## Executive Summary" in md
        assert "## Quality Score" in md and "| Dimension | Score | Weight |" in md
        assert "## Issue Breakdown" in md
        assert "## Critical Findings" in md
        assert "**critical**" in md or "[critical]" in md
        assert "## Reproducibility" in md

    def test_cell_escaping(self) -> None:
        report = _report()
        report["issues"][0]["title"] = "a|b\nc"
        md = render_markdown(report)
        assert "a\\|b c" in md
        assert "\n| a|b" not in md


class TestHtml:
    def test_self_contained_single_file(self) -> None:
        html = render_html(_report())
        assert html.startswith("<!DOCTYPE html>")
        assert "DataSentry Data Quality Report" in html
        assert "<link" not in html  # 内嵌 CSS，无外部资源
        assert "<style>" in html
        assert "<script src=" not in html  # 内联 JS 允许（Step 49），外部脚本不允许
        assert "<script>" in html

    def test_quality_score_bar_and_tooltip(self) -> None:
        html = render_html(_report())
        assert 'class="score-bar"' in html
        assert "score_version" in html
        assert "iss_1" in html and "0.3750" in html  # 27.3 悬停扣分构成

    def test_html_escapes_issue_content(self) -> None:
        report = _report()
        report["issues"][0]["title"] = "<script>alert(1)</script>"
        html = render_html(report)
        assert "<script>alert(1)</script>" not in html
        assert "\\u003cscript\\u003e" in html  # 内联数据 JSON 的 \\u003c 转义

    def test_sections_anchors(self) -> None:
        html = render_html(_report())
        for section in HTML_SECTIONS:
            assert f'id="{section}"' in html


class TestNavAndLinkage:
    def test_sticky_nav_lists_all_sections(self) -> None:
        html = render_html(_report())
        assert '<nav class="report-nav" id="report-nav"' in html
        for section in HTML_SECTIONS:
            assert f'href="#{section}"' in html

    def test_score_bar_dimensions_clickable(self) -> None:
        html = render_html(_report())
        for dim in ("validity", "uniqueness"):
            assert f'data-dim-link="{dim}"' in html
        assert 'role="button"' in html and 'tabindex="0"' in html
        assert 'class="score-dim"' in html

    def test_critical_findings_link_to_issue_rows(self) -> None:
        html = render_html(_report())
        findings = critical_findings(_report())
        assert findings
        for issue in findings:
            assert (
                f'class="finding-link" href="#issue_breakdown" '
                f'data-issue-id="{issue["id"]}"' in html
            )

    def test_linkage_script_and_back_to_top(self) -> None:
        html = render_html(_report())
        assert 'id="back-to-top"' in html
        assert 'closest("[data-dim-link]")' in html
        assert 'closest(".finding-link")' in html
        assert 'byId("report-nav")' in html
        assert "issues._render" in html


class TestPiiMasking:
    def test_mask_text_pii_basic(self) -> None:
        from datasentry_core.reporting import mask_text_pii

        masked = mask_text_pii("mail alice@example.com phone 13800138000")
        assert "alice@example.com" not in masked
        assert "13800138000" not in masked
        assert masked.count("[REDACTED]") == 2

    def test_mask_text_pii_no_pii_unchanged(self) -> None:
        from datasentry_core.reporting import mask_text_pii

        assert mask_text_pii("missing values in column price") == "missing values in column price"
        assert mask_text_pii("") == ""

    def test_html_report_redacts_pii_title(self) -> None:
        report = _report()
        report["issues"][0]["title"] = "bad email alice@example.com in column"
        html = render_html(report)
        assert "alice@example.com" not in html
        assert "[REDACTED]" in html

    def test_markdown_report_redacts_pii_title(self) -> None:
        report = _report()
        report["issues"][0]["title"] = "bad email bob@corp.io in column"
        md = render_markdown(report)
        assert "bob@corp.io" not in md
        assert "[REDACTED]" in md

    def test_json_report_keeps_full_evidence(self) -> None:
        report = _report()
        report["issues"][0]["title"] = "bad email alice@example.com in column"
        assert "alice@example.com" in json.dumps(report)


class TestJunit:
    def test_suite_and_testcases(self) -> None:
        root = ET.fromstring(render_junit(_report()))
        assert root.tag == "testsuite"
        assert root.get("name") == "datasentry:orders"
        assert root.get("tests") == "2"
        assert root.get("failures") == "2"
        assert root.get("errors") == "0"
        cases = root.findall("testcase")
        assert len(cases) == 2
        assert cases[0].get("name") == "numeric_outlier"
        assert cases[0].get("classname") == "amount"
        assert cases[0].get("file") == "orders"
        failure = cases[0].find("failure")
        assert failure is not None
        assert failure.get("type") == "high"
        assert failure.get("message") == "Outlier in amount"
        assert "affected: 2 rows" in (failure.text or "")
        assert "detectors: numeric_outlier" in (failure.text or "")
        overview = root.find("properties/property")
        assert overview is not None
        assert "rows=100" in overview.get("value", "")

    def test_xml_escaping(self) -> None:
        report = _report()
        report["issues"][0]["title"] = "a <b> & \"c\" 'd'"
        xml = render_junit(report)
        assert "<b>" not in xml
        root = ET.fromstring(xml)
        assert root.findall("testcase")[0].find("failure").get("message") == "a <b> & \"c\" 'd'"

    def test_empty_report_is_green_suite(self) -> None:
        report = build_report(_scan(), _runs(), [], None)
        root = ET.fromstring(render_junit(report))
        assert root.get("tests") == "0"
        assert root.get("failures") == "0"
        assert root.findall("testcase") == []


class TestSarif:
    def test_run_structure(self) -> None:
        sarif = render_sarif(_report())
        assert sarif["version"] == "2.1.0"
        assert sarif["$schema"].endswith("sarif-2.1.0.json")
        run = sarif["runs"][0]
        assert run["automationDetails"]["id"] == "scan_abc"
        assert run["tool"]["driver"]["name"] == "DataSentry"
        assert run["tool"]["driver"]["version"]
        assert run["properties"]["dataset_id"] == "orders"

    def test_rules_and_results_mapping(self) -> None:
        sarif = render_sarif(_report())
        run = sarif["runs"][0]
        rule_ids = {r["id"] for r in run["tool"]["driver"]["rules"]}
        assert rule_ids == {"numeric_outlier", "uniqueness_violation"}
        results = run["results"]
        assert len(results) == 2
        by_rule = {r["ruleId"]: r for r in results}
        assert by_rule["uniqueness_violation"]["level"] == "error"  # critical
        assert by_rule["numeric_outlier"]["level"] == "error"  # high
        loc = by_rule["numeric_outlier"]["locations"][0]["physicalLocation"]
        assert loc["artifactLocation"]["uri"] == "orders"
        props = by_rule["numeric_outlier"]["properties"]
        assert props["columns"] == ["amount"]
        assert props["scan_run_id"] == "scan_abc"
        assert "amount" in by_rule["numeric_outlier"]["message"]["text"]

    def test_medium_maps_to_warning(self) -> None:
        report = _report()
        report["issues"][0]["severity"] = Severity.MEDIUM
        sarif = render_sarif(report)
        result = next(r for r in sarif["runs"][0]["results"] if r["ruleId"] == "numeric_outlier")
        assert result["level"] == "warning"
        rule = next(
            r for r in sarif["runs"][0]["tool"]["driver"]["rules"] if r["id"] == "numeric_outlier"
        )
        assert rule["defaultConfiguration"]["level"] == "warning"

    def test_empty_issues(self) -> None:
        report = build_report(_scan(), _runs(), [], None)
        sarif = render_sarif(report)
        assert sarif["runs"][0]["results"] == []
        assert sarif["runs"][0]["tool"]["driver"]["rules"] == []


class TestReportWithNoQuality:
    def test_unscored_report(self) -> None:
        report = build_report(_scan(), _runs(), _issues(), None)
        assert report["quality"] is None
        assert "not scored" in render_markdown(report)
        assert "not scored" in render_html(report)

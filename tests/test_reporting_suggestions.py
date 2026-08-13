"""Step 62（ADR-062）修复建议预览测试：确定性映射、掩码、渲染接线。

覆盖契约：suggest_repairs（detector → 操作去重/排序/上限、未知检测器空、
rationale PII 掩码）；与 repair/engine.py 映射表的一致性漂移防护
（每条引擎映射检测器必有建议）；issue_rows 行内 suggestions 键；
JS 详情行渲染标记（建议块 / 无建议文案）。
"""

from __future__ import annotations

import json
import re

import pytest

from datasentry_core.reporting.interactive import issue_rows, render_interactive_issue_table
from datasentry_core.reporting.suggestions import _MAX_SUGGESTIONS, suggest_repairs


def _issue(**overrides: object) -> dict:
    base = {
        "id": "iss_1",
        "issue_type": "iqr_outlier",
        "title": "Outlier in amount",
        "description": "",
        "severity": "high",
        "priority_score": 90.0,
        "confidence": 0.9,
        "false_positive_risk": "low",
        "affected_count": 2,
        "affected_ratio": 0.02,
        "columns": ["amount"],
        "detector_ids": ["iqr_outlier"],
        "quality_dimensions": ["validity"],
    }
    base.update(overrides)
    return base


class TestSuggestRepairs:
    def test_maps_detector_to_operation(self) -> None:
        out = suggest_repairs(_issue())
        assert out[0]["operation"] == "clip_value"
        assert out[0]["label"] == "clip to outlier bounds"
        assert out[0]["risk"] == "medium"
        assert out[0]["targetColumns"] == ["amount"]

    def test_dedupe_and_order(self) -> None:
        out = suggest_repairs(_issue(detector_ids=["invalid_date", "iqr_outlier", "invalid_date"]))
        assert [s["operation"] for s in out] == ["set_null", "clip_value"]

    def test_caps_at_three(self) -> None:
        out = suggest_repairs(
            _issue(
                detector_ids=[
                    "leading_or_trailing_whitespace",
                    "inconsistent_case",
                    "iqr_outlier",
                    "invalid_date",
                ]
            )
        )
        assert len(out) == _MAX_SUGGESTIONS == 3

    def test_unknown_detector_returns_empty(self) -> None:
        assert suggest_repairs(_issue(detector_ids=["no_such_detector"])) == []
        assert suggest_repairs(_issue(detector_ids=[])) == []

    def test_rationale_pii_masked(self) -> None:
        from datasentry_core.reporting.suggestions import _SUGGESTION_TABLE

        issue = _issue(detector_ids=["iqr_outlier"])
        _SUGGESTION_TABLE["iqr_outlier"]["rationale"] = "mail alice@example.com now"
        out = suggest_repairs(issue)
        assert "alice@example.com" not in out[0]["rationale"]
        assert "[REDACTED]" in out[0]["rationale"]
        _SUGGESTION_TABLE["iqr_outlier"]["rationale"] = (
            "clip values outside the detected IQR bounds (bounds in evidence)"
        )

    def test_missing_detector_ids_key(self) -> None:
        assert suggest_repairs(_issue(detector_ids=[]).pop("detector_ids") or {}) == []


class TestEngineMappingConsistency:
    """漂移防护：引擎可修的检测器，预览建议必须非空（Step 62 契约）。"""

    @pytest.mark.parametrize(
        "detector_id",
        sorted(
            {
                "leading_or_trailing_whitespace",
                "inconsistent_case",
                "suspicious_missing_token",
                "invalid_date",
                "impossible_date",
                "iqr_outlier",
                "percentile_outlier",
                "modified_zscore",
            }
        ),
    )
    def test_engine_repairable_detectors_have_suggestion(self, detector_id: str) -> None:
        out = suggest_repairs(_issue(detector_ids=[detector_id]))
        assert out, f"{detector_id} 应有内置建议"
        assert out[0]["operation"]


class TestIssueRowsIntegration:
    def test_rows_carry_suggestions(self) -> None:
        rows = issue_rows({"issues": [_issue()]})
        assert rows[0]["suggestions"][0]["operation"] == "clip_value"

    def test_unknown_type_empty_suggestions(self) -> None:
        rows = issue_rows({"issues": [_issue(detector_ids=["drift_detector"])]})
        assert rows[0]["suggestions"] == []


class TestRenderMarkers:
    def test_detail_row_renders_suggestions_block(self) -> None:
        report = {
            "issues": [_issue()],
            "scan_run_id": "scan_x",
            "scan": {"id": "scan_x"},
            "quality": None,
        }
        html = render_interactive_issue_table(report, page_size=25)
        assert "Repair suggestions" in html
        assert "No built-in repair suggestion" in html
        match = re.search(r'id="issue-data">(.*?)</script>', html, re.S)
        payload = json.loads(match.group(1))
        assert payload["issues"][0]["suggestions"][0]["operation"] == "clip_value"
        assert payload["issues"][0]["suggestions"][0]["risk"] == "medium"

    def test_payload_escapes_script_close(self) -> None:
        from datasentry_core.reporting.suggestions import _SUGGESTION_TABLE

        original = _SUGGESTION_TABLE["iqr_outlier"]["rationale"]
        _SUGGESTION_TABLE["iqr_outlier"]["rationale"] = "</script><script>alert(1)</script>"
        try:
            report = {
                "issues": [_issue()],
                "scan_run_id": "scan_x",
                "scan": {"id": "scan_x"},
                "quality": None,
            }
            html = render_interactive_issue_table(report, page_size=25)
            match = re.search(r'id="issue-data">(.*?)</script>', html, re.S)
            payload = match.group(1)
            assert "</script>" not in payload
            assert "\\u003c/script\\u003e" in payload
        finally:
            _SUGGESTION_TABLE["iqr_outlier"]["rationale"] = original

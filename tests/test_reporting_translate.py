"""Step 75（ADR-075）报告正文翻译测试：渲染层映射 + en 逐字不变。

覆盖契约：translate.py 三函数（title/description/suggestion：zh 查表、
非 zh 短路、无法识别回退原文）；渲染接线（interactive issue_rows /
markdown / html / ui：zh 出中文标题，en 出原文）；en 快照逐字不变
（en 报告标题仍为英文原文格式）。
"""

from __future__ import annotations

from pathlib import Path

from datasentry_core.reporting.interactive import issue_rows
from datasentry_core.reporting.markdown import render_markdown
from datasentry_core.reporting.translate import (
    translate_description,
    translate_suggestion,
    translate_title,
)


def _issue(**overrides: object) -> dict:
    base = {
        "id": "iss_1",
        "issue_type": "numeric_outlier",
        "title": "Numeric outlier in amount",
        "description": "[iqr_outlier v1.0] iqr_outlier: 2",
        "severity": "high",
        "priority_score": 90.0,
        "confidence": 0.9,
        "false_positive_risk": "low",
        "affected_count": 2,
        "affected_ratio": 0.5,
        "affected_row_ids": ["r1"],
        "columns": ["amount"],
        "detector_ids": ["iqr_outlier"],
        "quality_dimensions": ["validity"],
    }
    base.update(overrides)
    return base


def _report() -> dict:
    return {
        "report_schema_version": "1",
        "datasentry_version": "1.0.0",
        "scan_run_id": "run_1",
        "dataset_id": "ds_1",
        "generated_at": "2026-01-01T00:00:00Z",
        "reproducible": True,
        "llm_used": False,
        "detector_runs": [],
        "scan": {
            "dataset_id": "ds_1",
            "status": "completed",
            "fingerprint": {
                "row_count": 4,
                "column_count": 3,
                "schema_hash": "h",
                "column_signature": [],
            },
            "reproducibility": {
                "datasentry_version": "1.0.0",
                "detector_versions": [],
                "seed": 42,
                "scanned_at": "2026-01-01T00:00:00Z",
            },
            "config": {},
        },
        "quality": None,
        "issues": [_issue()],
    }


class TestTranslateFunctions:
    def test_title_zh(self) -> None:
        assert (
            translate_title("zh", "Numeric outlier in amount", "numeric_outlier")
            == "数值异常值（amount）"
        )

    def test_title_en_returns_original(self) -> None:
        title = "Numeric outlier in amount"
        assert translate_title("en", title, "numeric_outlier") == title

    def test_title_unknown_phrase_returns_original(self) -> None:
        title = "Custom issue in cols"
        assert translate_title("zh", title, "unknown_family") == "Custom issue（cols）"

    def test_title_no_in_pattern_returns_original(self) -> None:
        title = "no pattern here"
        assert translate_title("zh", title) == title

    def test_description_zh(self) -> None:
        assert (
            translate_description("zh", "[iqr_outlier v1.0] iqr_outlier: 2")
            == "[iqr_outlier v1.0] IQR 离群值：2"
        )

    def test_description_en_returns_original(self) -> None:
        desc = "[iqr_outlier v1.0] iqr_outlier: 2"
        assert translate_description("en", desc) == desc

    def test_description_unknown_itype_falls_back_en(self) -> None:
        desc = "[weird v1.0] custom_type: 5"
        assert translate_description("zh", desc) == "[weird v1.0] custom_type：5"

    def test_suggestion_zh(self) -> None:
        sug = {
            "operation": "clip_value",
            "label": "clip to outlier bounds",
            "rationale": "x",
            "risk": "low",
        }
        out = translate_suggestion("zh", sug)
        assert out["label"] == "裁剪到异常边界"
        assert out["risk"] == "low"

    def test_suggestion_en_returns_original_dict(self) -> None:
        sug = {
            "operation": "clip_value",
            "label": "clip to outlier bounds",
            "rationale": "x",
            "risk": "low",
        }
        assert translate_suggestion("en", sug) is sug


class TestRendering:
    def test_issue_rows_zh_translates_body(self) -> None:
        rows = issue_rows(_report(), lang="zh")
        assert rows[0]["title"] == "数值异常值（amount）"
        assert "IQR 离群值" in rows[0]["description"]

    def test_issue_rows_en_byte_identical(self) -> None:
        rows = issue_rows(_report(), lang="en")
        assert rows[0]["title"] == "Numeric outlier in amount"
        assert rows[0]["description"] == "[iqr_outlier v1.0] iqr_outlier: 2"

    def test_markdown_zh_title_translated(self, tmp_path: Path) -> None:
        out = render_markdown(_report(), lang="zh")
        assert "数值异常值（amount）" in out

    def test_markdown_en_title_original(self) -> None:
        out = render_markdown(_report(), lang="en")
        assert "Numeric outlier in amount" in out

    def test_html_zh_critical_findings_translated(self) -> None:
        from datasentry_core.reporting.html import render_html

        out = render_html(_report(), lang="zh")
        assert "数值异常值（amount）" in out

    def test_html_en_critical_findings_original(self) -> None:
        from datasentry_core.reporting.html import render_html

        out = render_html(_report(), lang="en")
        assert "Numeric outlier in amount" in out

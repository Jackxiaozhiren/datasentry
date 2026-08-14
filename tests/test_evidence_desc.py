"""Step 78（ADR-078）证据级动态描述：模板化 + zh 镜像。

覆盖契约：ev() en 渲染与原 f-string 逐字一致（快照）；make_evidence
自动把 base + _text_key/_params 并入 data（原 data 语义零变化）；
translate_evidence_desc zh 渲染同源参数（en/zh 数值逐字一致）；
历史数据（无 meta）回退原文；模板/参数缺失回退原文（诚实降级）；
en 短路原文；JSON 契约面（description 字段）始终 en 原文。
"""

from __future__ import annotations

from datasentry_core.models.enums import EvidenceType
from datasentry_core.reporting.evidence_desc import ev, translate_evidence_desc


def _mk_evidence(desc: str, data: dict | None = None):
    from datasentry_core.detectors.common import make_evidence

    return make_evidence(
        detector_id="iqr_outlier",
        detector_version="1.0",
        evidence_type=EvidenceType.STATISTICAL_MEASURE,
        description=desc,
        data=data,
    )


class TestEv:
    def test_en_text_byte_identical_to_legacy_fstring(self) -> None:
        desc = ev("numeric.range", {"q25": 1.0}, count=2, lower=-51.5, upper=226.25)
        assert desc == "2 values outside [-51.5, 226]"

    def test_missing_standin_en(self) -> None:
        desc = ev("missing.standin", {"count": 3}, count=3, ratio=0.1234)
        assert desc == "3 values are missing stand-ins (0.1234)"

    def test_unknown_key_falls_back_to_key_name(self) -> None:
        assert ev("no.such.key", {}, count=1) == "no.such.key"

    def test_make_evidence_merges_base_and_meta(self) -> None:
        evidence = _mk_evidence(
            ev("numeric.range", {"q25": 1.0, "lower": -51.5}, count=2, lower=-51.5, upper=226.25)
        )
        assert evidence.data["q25"] == 1.0
        assert evidence.data["lower"] == -51.5
        assert evidence.data["_text_key"] == "numeric.range"
        assert evidence.data["_params"] == {"count": 2, "lower": -51.5, "upper": 226.25}

    def test_make_evidence_explicit_data_kept(self) -> None:
        evidence = _mk_evidence(
            ev("numeric.mad", {"median": 1.0}, count=2, z_threshold=3.5),
            data={"extra": "x"},
        )
        assert evidence.data["extra"] == "x"
        assert evidence.data["median"] == 1.0


class TestTranslateEvidenceDesc:
    def test_zh_renders_same_numbers_as_en(self) -> None:
        desc = ev("numeric.range", {}, count=2, lower=-51.5, upper=226.25)
        data = {"_text_key": desc.key, "_params": desc.params, **desc.base}
        assert translate_evidence_desc("zh", data, desc) == "2 个值超出 [-51.5, 226]"

    def test_en_returns_original(self) -> None:
        desc = ev("numeric.range", {}, count=2, lower=-51.5, upper=226.25)
        data = {"_text_key": desc.key, "_params": desc.params, **desc.base}
        assert translate_evidence_desc("en", data, desc) == desc

    def test_legacy_data_without_meta_returns_original(self) -> None:
        assert translate_evidence_desc("zh", {"count": 2}, "2 values outside [-51.5, 226]") == (
            "2 values outside [-51.5, 226]"
        )

    def test_missing_template_key_returns_original(self) -> None:
        desc = ev("no.such.key", {}, count=1)
        data = {"_text_key": desc.key, "_params": desc.params, **desc.base}
        assert translate_evidence_desc("zh", data, desc) == "no.such.key"

    def test_bad_params_falls_back_gracefully(self) -> None:
        desc = ev("numeric.range", {}, count=2, lower=-51.5, upper=226.25)
        broken = {"_text_key": desc.key, "_params": {"count": 2}}
        assert translate_evidence_desc("zh", broken, desc) == desc

    def test_zh_issue_row_evidence_translated(self) -> None:
        from datasentry_core.reporting.interactive import issue_rows

        issue = {
            "id": "iss_1",
            "issue_type": "numeric_outlier",
            "title": "Numeric outlier in amount",
            "description": "[iqr_outlier v1.0] iqr_outlier: 2",
            "severity": "medium",
            "priority_score": 5.0,
            "confidence": 0.9,
            "false_positive_risk": "low",
            "affected_count": 2,
            "affected_ratio": 0.1,
            "affected_row_ids": [1, 2],
            "columns": ["amount"],
            "detector_ids": ["iqr_outlier"],
            "quality_dimensions": ["validity"],
            "evidence": [
                {
                    "description": "2 values outside [-51.5, 226]",
                    "data": {
                        "lower": -51.5,
                        "upper": 226.25,
                        "_text_key": "numeric.range",
                        "_params": {"count": 2, "lower": -51.5, "upper": 226.25},
                    },
                }
            ],
        }
        rows = issue_rows({"issues": [issue]}, lang="zh")
        assert rows[0]["evidence"] == ["2 个值超出 [-51.5, 226]"]
        rows_en = issue_rows({"issues": [issue]}, lang="en")
        assert rows_en[0]["evidence"] == ["2 values outside [-51.5, 226]"]

    def test_issue_row_legacy_evidence_kept_original(self) -> None:
        from datasentry_core.reporting.interactive import issue_rows

        issue = {
            "id": "iss_1",
            "issue_type": "numeric_outlier",
            "title": "Numeric outlier in amount",
            "description": "[iqr_outlier v1.0] iqr_outlier: 2",
            "severity": "medium",
            "priority_score": 5.0,
            "confidence": 0.9,
            "false_positive_risk": "low",
            "affected_count": 2,
            "affected_ratio": 0.1,
            "affected_row_ids": [1, 2],
            "columns": ["amount"],
            "detector_ids": ["iqr_outlier"],
            "quality_dimensions": ["validity"],
            "evidence": [{"description": "2 values outside [-51.5, 226]", "data": {}}],
        }
        rows = issue_rows({"issues": [issue]}, lang="zh")
        assert rows[0]["evidence"] == ["2 values outside [-51.5, 226]"]


class TestDetectorIntegration:
    def test_scan_produces_ev_text_data(self, tmp_path) -> None:
        from datasentry import DataSentry

        ws = tmp_path / "ws"
        ws.mkdir()
        path = tmp_path / "data.csv"
        rows = [str(i) for i in range(100)] + ["5000", "-5000"]
        path.write_text("amount\n" + "\n".join(rows) + "\n", encoding="utf-8")
        client = DataSentry(project=ws)
        try:
            _, _, issues = client.scan_file(str(path))
            found = False
            for issue in issues:
                for evidence in issue.evidence:
                    if evidence.detector_id == "iqr_outlier":
                        found = True
                        assert evidence.description.startswith("2 values outside")
                        assert evidence.data["_text_key"] == "numeric.range"
                        assert "lower" in evidence.data["_params"]
            assert found
        finally:
            client.close()

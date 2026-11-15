"""PRD R-08: verify 结论成为报告的一行。

刻意不放 `test_reporting_html.py`(§0 基线仪器), 也不放进 R-07 的文件:
R-08 的判据与 R-07 的证据链不同, 混在一起会让"哪条门禁会红"变得难辨。

PRD 的两条硬要求在这里逐条钉住:
1. 明写 referee 是同一个重扫器, **不宣称"质量提升"**(Goodhart 陷阱);
2. 分数必须带"哪一份检测器代码"的标注 —— 但不假装版本号能区分未提交的
   本地改动(本仓库实测同一 demo 在 HEAD 与脏树上读出两套分数)。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from datasentry import DataSentry, report_evidence
from datasentry.cli import EXIT_OK, main
from datasentry_core.reporting.html import render_html
from test_reporting_html import _report

_VERIFY_LINE = re.compile(r'<p class="verify-line">(.*?)</p>', re.S)
_UPWARD_WORDS = (
    "improved",
    "improvement",
    "better quality",
    "higher quality",
    "质量提升",
    "提升质量",
)
# 报告里唯一合法的 "improvement" 字样是这句自我否定; 门禁把它抠掉再查禁词。
_ALLOWED_DISCLAIMER = "not a quality-improvement claim"


def _export(ws: Path, scan_id: str) -> str:
    code = main(
        ["--project", str(ws), "--format", "json", "report", "export", scan_id, "--as", "html"]
    )
    assert code == EXIT_OK
    return (ws / ".datasentry" / "reports" / f"{scan_id}.html").read_text(encoding="utf-8")


def _lines(html: str) -> list[str]:
    return _VERIFY_LINE.findall(html)


def _verify(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "verify_scan_run_id": "scan_verify_unit",
        "before_score": 96.8,
        "after_score": 96.2,
        "new_issue_types": [],
        "source_code": {"version": "0.8.6", "detector_count": 39},
        "verify_code": {"version": "0.8.6", "detector_count": 39},
        "same_code": True,
    }
    base.update(overrides)
    return base


def _code(version: str, count: int = 39) -> dict[str, Any]:
    return {"version": version, "detector_count": count}


@pytest.fixture(scope="module")
def verified(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """真实 scan -> apply -> verify -> report export, 全在 scratch 里。"""
    base = tmp_path_factory.mktemp("verify-line")
    csv = base / "customers.csv"
    rows = [f"user{i},active,{i * 10},2024-01-01" for i in range(30)]
    rows.append(" alice@example.com ,Active,n/a,2024-02-30")
    rows.append("  bob@example.com  ,active,5000,2024-01-02")
    csv.write_text("name,status,price,event_date\n" + "\n".join(rows) + "\n", encoding="utf-8")
    ws = base / "ws"
    ws.mkdir()
    client = DataSentry(project=ws)
    try:
        scan, _, issues = client.scan_file(csv)
        issue = next(i for i in issues if "leading_or_trailing_whitespace" in i.detector_ids)
        run = client.repair_apply(issue.id, csv)
        verify_scan, verify_report = client.repair_verify(run.id)
        source_score = scan.quality_score.overall if scan.quality_score else None
        after_score = verify_scan.quality_score.overall if verify_scan.quality_score else None
        version = scan.reproducibility.datasentry_version
        detector_count = len(scan.reproducibility.detector_versions)
    finally:
        client.close()
    return {
        "ws": ws,
        "scan_id": scan.id,
        "run_id": run.id,
        "before": source_score,
        "after": after_score,
        "version": version,
        "detectors": detector_count,
        "new_types": verify_report["new_types"],
        "html": _export(ws, scan.id),
    }


class TestVerifyLine:
    def test_verify_line_shows_before_and_after_scores(self, verified: dict[str, Any]) -> None:
        lines = _lines(verified["html"])
        assert len(lines) == 1, "每条 repair 记录应恰好一行 verify 结论"
        assert f"{verified['before']}" in lines[0]
        assert f"{verified['after']}" in lines[0]

    def test_verify_line_labels_the_detector_code(self, verified: dict[str, Any]) -> None:
        line = _lines(verified["html"])[0]
        assert verified["version"] in line, "分数没带 DataSentry 版本标注"
        assert str(verified["detectors"]) in line, "分数没带检测器族标注"
        assert "uncommitted" in line.lower(), "没写明版本字符串不区分未提交改动"

    def test_verify_line_counts_new_issue_types(self, verified: dict[str, Any]) -> None:
        line = _lines(verified["html"])[0]
        assert re.search(r"\b0 new issue types\b", line), line

    def test_verify_line_names_the_same_rescan_engine(self, verified: dict[str, Any]) -> None:
        line = _lines(verified["html"])[0]
        assert "same rescan engine" in line.lower(), line
        assert "not a quality-improvement claim" in line.lower(), line

    def test_verify_line_never_claims_improvement(self, verified: dict[str, Any]) -> None:
        line = _lines(verified["html"])[0].lower()
        probed = line.replace(_ALLOWED_DISCLAIMER, "")
        for word in _UPWARD_WORDS:
            assert word not in probed, f"Goodhart 陷阱: 出现了 {word!r}"

    def test_rest_report_html_carries_the_same_verify_line(self, verified: dict[str, Any]) -> None:
        """api.py::ui_report_html 接了同一个组装函数, 不能只在 CLI 面生效。"""
        from fastapi.testclient import TestClient

        from datasentry.api import create_app

        resp = TestClient(create_app(project=verified["ws"])).get(
            f"/scans/{verified['scan_id']}/report.html"
        )
        assert resp.status_code == 200
        lines = _lines(resp.text)
        assert len(lines) == 1
        assert f"{verified['before']}" in lines[0]
        assert f"{verified['after']}" in lines[0]

    def test_verify_line_absent_when_verify_was_not_run(
        self, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        """棘轮: 只 apply 不 verify 时不许出现 verify 结论行。"""
        base = tmp_path_factory.mktemp("verify-absent")
        csv = base / "a.csv"
        csv.write_text("name,x\n alice ,1\n bob ,2\n", encoding="utf-8")
        ws = base / "ws"
        ws.mkdir()
        client = DataSentry(project=ws)
        try:
            scan, _, issues = client.scan_file(csv)
            issue = next(i for i in issues if "leading_or_trailing_whitespace" in i.detector_ids)
            client.repair_apply(issue.id, csv)
        finally:
            client.close()
        assert _lines(_export(ws, scan.id)) == []

    def test_zh_verify_line_is_translated(self) -> None:
        record = {
            "run_id": "rep_unit",
            "status": "applied",
            "issue_type": "x",
            "operations": [],
            "rows_changed": 1,
            "rollback_command": "datasentry repair rollback rep_unit",
            "verify": _verify(),
        }
        html = render_html(_report(), repair_records=[record], lang="zh")
        assert len(_lines(html)) == 1
        assert "同一个重扫器" in _lines(html)[0]
        assert "新增问题类型 0 个" in _lines(html)[0]

    def test_code_mismatch_is_named_not_silently_compared(self) -> None:
        """两次扫描的检测器代码不同 => 明写不可直接比较, 不许并排摆两个分数当作可比的。"""
        record = {
            "run_id": "rep_unit",
            "status": "applied",
            "issue_type": "x",
            "operations": [],
            "rows_changed": 1,
            "rollback_command": "datasentry repair rollback rep_unit",
            "verify": _verify(verify_code=_code("0.8.7"), same_code=False),
        }
        line = _lines(render_html(_report(), repair_records=[record]))[0]
        assert "not directly comparable" in line.lower()

    def test_scores_are_live_not_hardcoded(self) -> None:
        """换一组分数, 输出必须跟着换: 证明数字来自组装层而非写死在模板里。"""
        record = {
            "run_id": "rep_unit",
            "status": "applied",
            "issue_type": "x",
            "operations": [],
            "rows_changed": 1,
            "rollback_command": "datasentry repair rollback rep_unit",
            "verify": _verify(before_score=11.1, after_score=22.2),
        }
        line = _lines(render_html(_report(), repair_records=[record]))[0]
        assert "11.1" in line and "22.2" in line
        assert "96.8" not in line and "96.2" not in line


class TestVerifyEvidenceIsReadOnly:
    """报告导出不能带副作用, 也不能长成第二套领域逻辑。"""

    def test_verify_evidence_never_triggers_a_rescan(
        self, verified: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`repair_verify()` 会 `scan_file()` 产生新 ScanRun; 组装层只许读已有证据。"""

        def _boom(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("报告导出路径不许重扫")

        monkeypatch.setattr(DataSentry, "scan_file", _boom)
        client = DataSentry(project=verified["ws"])
        try:
            report = client.export_report(verified["scan_id"])
            records = report_evidence.build_verify_records(client, report)
        finally:
            client.close()
        assert verified["run_id"] in records

    def test_new_types_match_the_cli_verify_report(self, verified: dict[str, Any]) -> None:
        """报告里的"新增问题类型"必须等于 `repair verify` 的 new_types, 不许各算一套。"""
        client = DataSentry(project=verified["ws"])
        try:
            report = client.export_report(verified["scan_id"])
            records = report_evidence.build_verify_records(client, report)
        finally:
            client.close()
        assert records[verified["run_id"]]["new_issue_types"] == sorted(verified["new_types"])


class TestVerifyLineGatesCanGoRed:
    """§6 的反证臂: 证明上面那些断言不是恒真的。"""

    def test_gate_flips_when_verify_evidence_is_dropped(
        self, verified: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert len(_lines(verified["html"])) == 1, "未注入时必须有这一行, 否则下面是空断言"
        monkeypatch.setattr(report_evidence, "build_verify_records", lambda *a, **k: {})
        assert _lines(_export(verified["ws"], verified["scan_id"])) == []

    def test_gate_flips_on_upward_language(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """把一句框架文案换成"质量提升"话术, 禁词门禁必须抓到。"""
        from datasentry_core.reporting import i18n as i18n_module

        record = {
            "run_id": "rep_unit",
            "status": "applied",
            "issue_type": "x",
            "operations": [],
            "rows_changed": 1,
            "rollback_command": "datasentry repair rollback rep_unit",
            "verify": _verify(),
        }
        original = dict(i18n_module.L10N["en"])
        original["verify.same_engine"] = "quality improved by the repair"
        monkeypatch.setattr(i18n_module, "L10N", {**i18n_module.L10N, "en": original})
        line = _lines(render_html(_report(), repair_records=[record]))[0].lower()
        assert any(word in line for word in _UPWARD_WORDS), "禁词门禁没抓到注入的话术"

"""PRD R-07: 报告里的 Repair Record 章节必须来自既有修复证据链。

刻意不放在 `test_reporting_html.py`: 那是 §0 前置检查的基线仪器(9 passed),
往里加测试会让交接文档的前置读数失去意义。

主验收走端到端 CLI(scan -> apply -> report export --as html), 而不是只给
`render_html` 传一个新参数就断言: 后者只能证明我加的参数能用, 证明不了
`rows_changed` / 工件 / diff 真的来自库里那条 repair run。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from datasentry import DataSentry
from datasentry.cli import EXIT_OK, main
from datasentry_core.reporting.html import render_html
from test_reporting_html import _EXTERNAL_LOADERS, _report, _table_parent_classes

_EMAIL = "alice@example.com"
_DSN = "postgresql://user:s3cr3t@db.internal/prod"


def _export(ws: Path, scan_id: str) -> str:
    code = main(
        ["--project", str(ws), "--format", "json", "report", "export", scan_id, "--as", "html"]
    )
    assert code == EXIT_OK
    return (ws / ".datasentry" / "reports" / f"{scan_id}.html").read_text(encoding="utf-8")


def _section(html: str) -> str:
    """只取 Repair Record 章节: PRD R-07 的判据是章节内不泄露, 不是全文。

    B3-e 曾在此记下一处全文既存泄露(column_profiles 的 max 写原值进内嵌 JSON);
    该缺陷已在 B3-h 修复, 全报告路径另由
    tests/test_reporting_profile_redaction.py 独立断言。这里保留章节级判据,
    不靠放大断言掩盖新回归, 也不再声称缺陷仍在。
    """
    start = html.find('id="repair_record"')
    assert start >= 0, "章节不在, 无法取章节片段"
    rest = html[start:]
    end = rest.find('<h2 id="', 1)
    return rest if end < 0 else rest[:end]


def _record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": "rep_unit",
        "status": "applied",
        "issue_id": None,
        "issue_type": "legacy_issue",
        "operations": [],
        "rows_changed": 1,
        "artifact_before": "rep_unit.before.csv",
        "artifact_copy": "rep_unit.csv",
        "diff": None,
        "rollback_command": "datasentry repair rollback rep_unit",
    }
    base.update(overrides)
    return base


@pytest.fixture(scope="module")
def applied(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """一次真实扫描 + 一次真实 apply + 一次真实导出, 全在 scratch 里。

    name 列两行带前后空格 => trim_whitespace 修复; 其中一行是 email, 另一行的
    event_date 是 DSN, 于是 diff 表格里既有 PII 又有 DSN, 脱敏断言有真实分母。
    """
    base = tmp_path_factory.mktemp("repair-record")
    csv = base / "customers.csv"
    rows = [f"user{i},active,{i * 10},2024-01-01" for i in range(30)]
    rows.append(f" {_EMAIL} ,Active,n/a,2024-02-30")
    rows.append(f"  bob@example.com  ,active,5000,{_DSN}")
    csv.write_text("name,status,price,event_date\n" + "\n".join(rows) + "\n", encoding="utf-8")
    ws = base / "ws"
    ws.mkdir()
    client = DataSentry(project=ws)
    try:
        scan, _, issues = client.scan_file(csv)
        issue = next(i for i in issues if "leading_or_trailing_whitespace" in i.detector_ids)
        run = client.repair_apply(issue.id, csv)
    finally:
        client.close()
    return {
        "ws": ws,
        "scan_id": scan.id,
        "run_id": run.id,
        "rows_changed": run.rows_changed,
        "issue_type": issue.issue_type,
        "html": _export(ws, scan.id),
    }


class TestRepairRecord:
    def test_repair_record_renders_when_applied(self, applied: dict[str, Any]) -> None:
        html = applied["html"]
        assert 'id="repair_record"' in html
        assert applied["run_id"] in html
        assert applied["issue_type"] in html
        assert "trim_whitespace" in html
        assert f"{applied['run_id']}.before.csv" in html
        assert f"{applied['run_id']}.csv" in html
        assert f"datasentry repair rollback {applied['run_id']}" in html
        assert f"<strong>Rows changed</strong> {applied['rows_changed']}" in html
        assert "<th>before</th>" in html and "<th>after</th>" in html
        parents = _table_parent_classes(html)
        assert parents, "章节里没有表格, 内联 diff 可能没渲染"
        assert all("table-scroll" in cls for cls in parents), parents

    def test_repair_record_absent_when_no_repair(self) -> None:
        """棘轮: 首轮即绿, 不是修复成果。没有修复时章节整节不许出现。"""
        html = render_html(_report())
        assert 'id="repair_record"' not in html
        assert 'href="#repair_record"' not in html
        assert "Repair Record" not in html

    def test_repair_record_never_leaks_secret(self, applied: dict[str, Any]) -> None:
        html = applied["html"]
        section = _section(html)
        assert _EMAIL not in section
        assert _DSN not in section
        assert "s3cr3t" not in section
        assert "[REDACTED]" in section, "脱敏没生效或 diff 没渲染"
        assert str(applied["ws"]) not in section, "工件绝对路径进了报告"
        assert _EXTERNAL_LOADERS.findall(html) == []

    def test_unreadable_artifact_says_so_and_keeps_the_link(
        self, applied: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """照 ui.py:1167-1193 的口径: 明文写读不到 + 仍保留链接 + 不渲染 diff 表。"""

        def _boom(*args: object, **kwargs: object) -> None:
            raise FileNotFoundError("repaired copy missing")

        monkeypatch.setattr(DataSentry, "repair_diff", _boom)
        section = _section(_export(applied["ws"], applied["scan_id"]))
        assert "could not be read from this repair" in section
        assert f"{applied['run_id']}.before.csv" in section
        assert f"/ui/repairs/{applied['run_id']}/artifact" in section
        assert "<th>before</th>" not in section, "读不到工件却渲染了 diff 表"
        assert "[REDACTED]" not in section, "读不到工件却渲染了单元格值"

    def test_rows_changed_none_is_unknown_not_zero(self) -> None:
        """旧记录的 rows_changed=None 语义是未知, 不是 0(见 models/repair.py:90-92)。"""
        record = _record(rows_changed=None)
        html = render_html(_report(), repair_records=[record])
        assert "unknown" in html
        assert "<strong>Rows changed</strong> 0" not in html

    def test_diff_hidden_rows_are_named_not_silently_dropped(self) -> None:
        """报告有体积上界, 截断的行数必须报出来, 不许悄悄丢证据。"""
        record = _record(
            diff={
                "columns": ["email"],
                "rows": [
                    {
                        "line": 2,
                        "before": {"email": "old@example.com"},
                        "after": {"email": "new@example.com"},
                    }
                ],
                "hidden": 2,
            }
        )
        html = render_html(_report(), repair_records=[record])
        assert "2 more changed row(s)" in html
        assert "[REDACTED]" in html

    def test_absent_key_and_null_value_are_named_not_invented(self) -> None:
        """照 json_cell_map 的口径: 文件没这个键就是没这个键, 不写成 null。"""
        record = _record(
            diff={
                "columns": ["email", "note"],
                "rows": [
                    {"line": 2, "before": {"email": None}, "after": {"email": "x"}},
                    {"line": 3, "before": {}, "after": {"note": "y"}},
                ],
                "hidden": 0,
            }
        )
        html = render_html(_report(), repair_records=[record])
        assert "(no such key)" in html
        assert "&#8709;" in html
        assert "<th>before</th>" in html

    def test_rest_report_html_carries_the_same_evidence(self, applied: dict[str, Any]) -> None:
        """api.py 的 report.html 接了同一个组装函数, 不能只在 CLI 面生效。"""
        from fastapi.testclient import TestClient

        from datasentry.api import create_app

        resp = TestClient(create_app(project=applied["ws"])).get(
            f"/scans/{applied['scan_id']}/report.html"
        )
        assert resp.status_code == 200
        section = _section(resp.text)
        assert applied["run_id"] in section
        assert f"{applied['run_id']}.before.csv" in section

    def test_zh_report_translates_the_heading(self) -> None:
        record = _record(rows_changed=3)
        assert '<h2 id="repair_record">' in render_html(_report(), repair_records=[record])
        assert "修复记录" in render_html(_report(), repair_records=[record], lang="zh")


class TestRepairRecordGatesCanGoRed:
    """§6 的反证臂: 证明上面那些断言不是恒真的。"""

    def test_gate_flips_when_evidence_is_dropped(
        self, applied: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import datasentry.cli as cli_module

        monkeypatch.setattr(cli_module, "build_repair_records", lambda *a, **k: [])
        html = _export(applied["ws"], applied["scan_id"])
        assert 'id="repair_record"' not in html
        assert applied["run_id"] not in html

    def test_gate_flips_when_masking_is_removed(
        self, applied: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import datasentry_core.reporting.html as html_module

        monkeypatch.setattr(html_module, "mask_text_pii", lambda text: text)
        section = _section(_export(applied["ws"], applied["scan_id"]))
        assert _EMAIL in section, "去掉掩码后 PII 仍没出现, 脱敏断言是空转"
        assert "s3cr3t" in section

    def test_gate_flips_when_diff_becomes_readable(
        self, applied: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """不可读分支的反面: 正常导出必须渲染 diff 表, 否则那条分支恒真。"""
        section = _section(applied["html"])
        assert "could not be read from this repair" not in section
        assert "<th>before</th>" in section
        assert "[REDACTED]" in section

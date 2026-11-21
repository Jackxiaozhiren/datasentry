"""B4 批 A：本机采纳日志（R-14）与 `issues confirm` 人工判定（R-16）。

PRD 点名的两条 failing-first 测试（``test_adoption_log_records_approve_and_reject``
与 ``test_adoption_log_scrubs_secrets_and_pii``）都在这里。日志是 **opt-in**
（D-1 决策，见 ``AUDIT/FIX_PRD.md`` B0），字段走严格白名单，任何列值、绝对路径、
DSN、token 或 PII 都不会落盘（不变量 5）。

同批的 R-17 按 D-2 明确不做；最后一个类是一道棘轮，防止有人日后悄悄加回
``--verbose``。R-15 的四阶段墙钟见 ``tests/test_stage_timings.py``。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from datasentry import DataSentry
from datasentry.cli import main

_REPO = Path(__file__).resolve().parents[1]
_CLI_SOURCE = _REPO / "src" / "datasentry" / "cli.py"
_DSN = "postgresql://user:s3cr3t@db.internal/prod"
_EMAIL = "alice@example.com"
_PHONE = "13800138000"

_LOG_FIELDS = frozenset(
    {"ts", "action", "issue_type", "rows_changed", "surface", "issue_id", "severity", "verdict"}
)


@pytest.fixture()
def adoption() -> Any:
    """Import on demand so a missing module fails with a visible ModuleNotFoundError."""
    import datasentry.adoption as module

    return module


@pytest.fixture()
def scanned(tmp_path: Path) -> tuple[Path, Path, DataSentry]:
    ws = tmp_path / "ws"
    ws.mkdir()
    csv = ws / "customers.csv"
    rows = [f"user{i},active,{i * 10},2024-01-01" for i in range(30)]
    rows.append(" user30 ,Active,n/a,2024-02-30")
    csv.write_text("name,status,price,event_date\n" + "\n".join(rows) + "\n", encoding="utf-8")
    client = DataSentry(project=ws)
    try:
        client.scan_file(csv)
    finally:
        client.close()
    return ws, csv, DataSentry(project=ws)


def _log_lines(ws: Path) -> list[dict[str, Any]]:
    path = ws / ".datasentry" / "adoption.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _assert_no_secret(text: str) -> None:
    for needle in (_DSN, "s3cr3t", _EMAIL, _PHONE, "db.internal", str(_REPO)):
        assert needle not in text, f"secret/PII leaked into the adoption log: {needle!r}"


@pytest.fixture()
def enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-1 是 opt-in：闸门装在 `adoption.record` 这一个入口上（不是各调用点），
    所以凡是要真的落盘的用例都必须显式开启；不开启的用例另有 `off_by_default`。"""
    monkeypatch.setenv("DATASENTRY_ADOPTION_LOG", "1")


class TestAdoptionLog:
    def test_log_is_opt_in_and_off_by_default(
        self, scanned: tuple[Path, Path, DataSentry], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws, csv, client = scanned
        monkeypatch.delenv("DATASENTRY_ADOPTION_LOG", raising=False)
        try:
            _, _, issues = client.scan_file(csv)
            issue = next(i for i in issues if "leading_or_trailing_whitespace" in i.detector_ids)
            client.repair_propose(issue.id, csv)
        finally:
            client.close()
        assert _log_lines(ws) == [], "opt-in decision (D-1) was silently flipped to default-on"

    def test_adoption_log_records_approve_and_reject(
        self,
        scanned: tuple[Path, Path, DataSentry],
        adoption: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ws, csv, client = scanned
        monkeypatch.setenv("DATASENTRY_ADOPTION_LOG", "1")
        try:
            _, _, issues = client.scan_file(csv)
            issue = next(i for i in issues if "leading_or_trailing_whitespace" in i.detector_ids)
            client.repair_propose(issue.id, csv)
            run = client.repair_apply(issue.id, csv)
            client.repair_rollback(run.id)
            adoption.record(
                ws, action="reject", issue_type=issue.issue_type, rows_changed=0, surface="cli"
            )
        finally:
            client.close()
        lines = _log_lines(ws)
        assert len(lines) == 4, lines
        assert {line["action"] for line in lines} == {"propose", "approve", "rollback", "reject"}
        assert all(set(line) == _LOG_FIELDS for line in lines), lines
        assert all(isinstance(line["rows_changed"], int) for line in lines)
        assert all(line["surface"] == "cli" for line in lines)

    def test_adoption_log_scrubs_secrets_and_pii(
        self, tmp_path: Path, adoption: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        monkeypatch.setenv("DATASENTRY_ADOPTION_LOG", "1")
        # Stuff secrets into every free-text field of the recorded context.
        adoption.record(
            ws,
            action="propose",
            issue_type=_DSN,
            rows_changed=7,
            surface="cli",
            issue_id=f"{_EMAIL}/{_PHONE}",
            severity="high",
        )
        text = (ws / ".datasentry" / "adoption.jsonl").read_text(encoding="utf-8")
        _assert_no_secret(text)
        lines = _log_lines(ws)
        assert len(lines) == 1
        assert set(lines[0]) == _LOG_FIELDS
        assert lines[0]["issue_type"] != _DSN, "unbounded free text must be replaced, not stored"
        assert lines[0]["issue_id"] == "", "a malformed issue id must be dropped"

    def test_identity_sanitizer_is_detected(self, tmp_path: Path, adoption: Any) -> None:
        """Counter-proof: the leak assertion really fires on unfiltered content."""
        ws = tmp_path / "ws"
        ws.mkdir()
        with pytest.raises(AssertionError, match="secret/PII leaked"):
            _assert_no_secret(
                json.dumps({"issue_type": _DSN, "issue_id": _EMAIL, "rows_changed": 1})
            )

    def test_summary_reports_denominator_and_scope(
        self, tmp_path: Path, adoption: Any, enabled: None
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        for action in ("propose", "propose", "approve"):
            adoption.record(
                ws, action=action, issue_type="missingness", rows_changed=2, surface="cli"
            )
        summary = adoption.summarize(ws)
        assert summary["proposals"] == 2
        assert summary["approvals"] == 1
        assert summary["adoption_rate"] == 0.5
        assert "proposals" in summary["scope"].lower()
        assert "unconfirmed" in summary["scope"].lower()

    def test_four_surfaces_are_distinguishable(
        self, tmp_path: Path, adoption: Any, enabled: None
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        for surface in ("cli", "ui", "rest", "mcp"):
            adoption.record(
                ws, action="propose", issue_type="uniqueness", rows_changed=1, surface=surface
            )
        assert [line["surface"] for line in _log_lines(ws)] == ["cli", "ui", "rest", "mcp"]


class TestConfirmationFeedback:
    def test_confirm_writes_to_the_same_log_with_verdict(
        self, tmp_path: Path, adoption: Any, enabled: None
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        adoption.record(ws, action="propose", issue_type="uniqueness", rows_changed=1, surface="ui")
        adoption.record(
            ws,
            action="confirm",
            issue_type="uniqueness",
            rows_changed=0,
            surface="ui",
            issue_id="iss_a8af3fd95e",
            severity="high",
            verdict="false",
        )
        lines = _log_lines(ws)
        assert len(lines) == 2
        confirm = lines[1]
        assert confirm["verdict"] == "false"
        assert confirm["issue_id"] == "iss_a8af3fd95e"
        assert confirm["severity"] == "high"

    def test_unconfirmed_issues_are_excluded_from_the_denominator(
        self, tmp_path: Path, adoption: Any, enabled: None
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        for i in range(3):
            adoption.record(
                ws, action="propose", issue_type="uniqueness", rows_changed=1, surface="cli"
            )
            if i < 2:
                adoption.record(
                    ws,
                    action="confirm",
                    issue_type="uniqueness",
                    rows_changed=0,
                    surface="cli",
                    issue_id=f"iss_{i:012x}",
                    severity="high",
                    verdict="false" if i == 0 else "true",
                )
        summary = adoption.summarize(ws)
        assert summary["confirmed"] == 2, "unjudged issues must not enter the denominator"
        assert summary["false_positive_rate"] == 0.5
        assert "unconfirmed" in summary["scope"].lower()

    def test_injected_unconfirmed_denominator_is_detected(
        self, tmp_path: Path, adoption: Any
    ) -> None:
        """Counter-proof: a denominator that counts unjudged issues must fail the scope check."""
        ws = tmp_path / "ws"
        ws.mkdir()
        adoption.record(
            ws, action="propose", issue_type="uniqueness", rows_changed=1, surface="cli"
        )
        summary = adoption.summarize(ws)
        drifted = dict(summary, confirmed=1, false_positive_rate=0.0)
        assert drifted["confirmed"] != summary["confirmed"]
        with pytest.raises(AssertionError):
            assert drifted["confirmed"] == 0, "unconfirmed issues were counted"


class TestCliSurfaces:
    """R-14 要求「纯本地聚合命令」，R-16 要求人工判定落进同一条日志。"""

    def test_adoption_summary_reports_denominator_and_scope(
        self, tmp_path: Path, adoption: Any, enabled: None, capsys: Any
    ) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        for action in ("propose", "propose", "approve"):
            adoption.record(
                ws, action=action, issue_type="uniqueness", rows_changed=1, surface="cli"
            )
        assert main(["--project", str(ws), "adoption", "summary"]) == 0
        out = capsys.readouterr().out
        assert "proposals=2" in out, out
        assert "adoption_rate=0.50" in out, out
        assert "unconfirmed" in out, "the scope statement must name the excluded population"

    def test_cli_confirm_refuses_when_the_log_is_off_then_records(
        self,
        scanned: tuple[Path, Path, DataSentry],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ws, _, client = scanned
        issue = next(
            i for i in client.list_issues() if "leading_or_trailing_whitespace" in i.detector_ids
        )
        client.close()
        argv = ["--project", str(ws), "issues", "confirm", issue.id, "--verdict", "false"]
        monkeypatch.delenv("DATASENTRY_ADOPTION_LOG", raising=False)
        assert main(argv) != 0, "a disabled log must fail loudly, not swallow the judgement"
        assert _log_lines(ws) == []
        monkeypatch.setenv("DATASENTRY_ADOPTION_LOG", "1")
        assert main(argv) == 0
        lines = _log_lines(ws)
        assert len(lines) == 1, lines
        assert lines[0]["action"] == "confirm"
        assert lines[0]["verdict"] == "false"
        assert lines[0]["issue_id"] == issue.id
        assert lines[0]["severity"] == issue.severity.value


class TestVerboseDecisionGuard:
    def _assert_no_verbose(self, text: str) -> None:
        assert '"--verbose"' not in text, "D-2 was decided NO; --verbose must not be invented"

    def test_no_verbose_flag_exists(self) -> None:
        self._assert_no_verbose(_CLI_SOURCE.read_text(encoding="utf-8"))

    def test_guard_fires_if_someone_adds_it(self) -> None:
        with pytest.raises(AssertionError, match="D-2"):
            self._assert_no_verbose('parser.add_argument("--verbose", action="store_true")')

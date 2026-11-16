"""R-09: the MCP diff tool is the same structure as REST/CLI, not a third spelling.

PRD R-09 pins one thing: `repair_diff` over MCP returns the same structure as
`GET /repairs/{id}/diff`, assembled through the shared `json_cell_map()` path.
The trap named in the PRD is a missing key: writing `null` for a key the
record never carried invents data. `tools/call` runs `json.dumps` on the result
*outside* its try block, so any non-JSON object escaping into the payload also
kills the stdio loop. The `ABSENT` sentinel that distinguishes missing from
null belongs to a later slice; on this baseline a short row is the same case.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from datasentry import DataSentry
from datasentry.api import create_app
from datasentry.cli import main
from datasentry.mcp_server import McpServer
from datasentry_core.repair import RepairEngine


@pytest.fixture
def applied(tmp_path: Path) -> tuple[Path, str]:
    """One shared workspace, one applied repair, readable by all three faces."""
    ws = tmp_path / "ws"
    ws.mkdir()
    csv = ws / "customers.csv"
    rows = [f"user{i},active,{i * 10},2024-01-01" for i in range(30)]
    rows.append(" user30 ,Active,n/a,2024-02-30")
    rows.append("  user31  ,active,5000,2024-13-01")
    csv.write_text("name,status,price,event_date\n" + "\n".join(rows) + "\n", encoding="utf-8")
    client = DataSentry(project=ws)
    try:
        _, _, issues = client.scan_file(csv)
        issue = next(i for i in issues if "leading_or_trailing_whitespace" in i.detector_ids)
        run = client.repair_apply(issue.id, csv)
        return ws, run.id
    finally:
        client.close()


def _rpc(ws: Path, message_id: int, name: str, arguments: dict[str, str]) -> dict[str, Any]:
    server = McpServer(project=ws)
    try:
        response = server._handle_message(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        assert response is not None
        return response
    finally:
        server.close()


def _mcp_tools(ws: Path) -> dict[str, dict[str, Any]]:
    server = McpServer(project=ws)
    try:
        response = server._handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert response is not None and "result" in response
        return {t["name"]: t for t in response["result"]["tools"]}
    finally:
        server.close()


def _mcp_diff(ws: Path, run_id: str) -> dict[str, Any]:
    response = _rpc(ws, 2, "repair_diff", {"repair_run_id": run_id})
    assert "error" not in response, response.get("error")
    return dict(json.loads(response["result"]["content"][0]["text"]))


def _rest_diff(ws: Path, run_id: str) -> dict[str, Any]:
    with TestClient(create_app(project=ws)) as client:
        response = client.get(f"/repairs/{run_id}/diff")
        assert response.status_code == 200, response.text
        return dict(response.json())


def _cli_diff(ws: Path, run_id: str, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert main(["--project", str(ws), "--format", "json", "repair", "diff", run_id]) == 0
    return dict(json.loads(capsys.readouterr().out)["data"])


class TestMcpRepairDiff:
    def test_advertised_as_read_only_with_one_required_argument(
        self, applied: tuple[Path, str]
    ) -> None:
        ws, _ = applied
        tools = _mcp_tools(ws)
        assert "repair_diff" in tools
        tool = tools["repair_diff"]
        assert tool["inputSchema"]["required"] == ["repair_run_id"]
        assert set(tool["inputSchema"]["properties"]) == {"repair_run_id"}
        assert tool["annotations"]["readOnlyHint"] is True

    def test_payload_equals_rest(self, applied: tuple[Path, str]) -> None:
        ws, run_id = applied
        assert _mcp_diff(ws, run_id) == _rest_diff(ws, run_id)

    def test_payload_equals_cli_json(
        self, applied: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        ws, run_id = applied
        rest = _rest_diff(ws, run_id)
        assert _mcp_diff(ws, run_id) == rest == _cli_diff(ws, run_id, capsys)

    def test_equality_is_not_vacuous(self, applied: tuple[Path, str]) -> None:
        ws, run_id = applied
        payload = _mcp_diff(ws, run_id)
        assert set(payload) == {"run_id", "status", "columns", "changed_rows"}
        assert payload["run_id"] == run_id
        assert payload["columns"]
        rows = payload["changed_rows"]
        assert rows, "two empty dicts would make the parity assertion pass vacuously"
        assert set(rows[0]) == {"line", "before", "after"}
        assert rows[0]["line"] >= 2
        assert rows[0]["before"] != rows[0]["after"]

    def test_missing_key_is_omitted_and_payload_stays_json_serialisable(
        self, applied: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Injection control: a missing key must be absent, not invented as null."""
        ws, run_id = applied
        rows = (["name", "note"], [[" alice "]], [["alice", None]], [0])
        monkeypatch.setattr(RepairEngine, "table_diff", staticmethod(lambda *_a: rows))
        response = _rpc(ws, 3, "repair_diff", {"repair_run_id": run_id})
        assert "error" not in response, response.get("error")
        # _handle_message calls json.dumps here, outside its try block: a leaked
        # non-JSON object would take the stdio loop down with it.
        text = response["result"]["content"][0]["text"]
        payload = json.loads(text)
        before = payload["changed_rows"][0]["before"]
        after = payload["changed_rows"][0]["after"]
        assert "note" not in before
        assert after["note"] is None
        assert "object at 0x" not in text
        assert _rest_diff(ws, run_id)["changed_rows"] == payload["changed_rows"]

    def test_unknown_run_is_an_error_envelope_not_a_crash(self, applied: tuple[Path, str]) -> None:
        ws, _ = applied
        response = _rpc(ws, 4, "repair_diff", {"repair_run_id": "rep_no_such_run"})
        assert "error" not in response, response.get("error")
        payload = json.loads(response["result"]["content"][0]["text"])
        assert payload.get("error")
        # The server must still answer the next request: one bad argument cannot end stdio.
        assert "repair_diff" in _mcp_tools(ws)

    def test_repair_diff_writes_nothing_in_steady_state(self, applied: tuple[Path, str]) -> None:
        """Measured, not guessed: back the readOnlyHint with a workspace snapshot.

        `tests/test_mcp_server.py::_READ_ONLY_TOOLS` is a measured set, so adding
        a tool to it needs an observation, not a claim about the handler body.
        The first call on a fresh workspace bumps metadata.db's mtime (size
        unchanged: connection/schema init), so this uses one long-lived server
        and a warm-up call, matching the probe described above that set.
        """
        ws, run_id = applied

        def snapshot() -> dict[str, tuple[int, int]]:
            return {
                str(p.relative_to(ws)): (p.stat().st_size, p.stat().st_mtime_ns)
                for p in sorted(ws.rglob("*"))
                if p.is_file()
            }

        def call(message_id: int) -> dict[str, Any]:
            response = server._handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "method": "tools/call",
                    "params": {"name": "repair_diff", "arguments": {"repair_run_id": run_id}},
                }
            )
            assert response is not None and "error" not in response, response
            return response

        server = McpServer(project=ws)
        try:
            call(1)
            before = snapshot()
            assert before, "the shared workspace must contain the applied repair artefacts"
            call(2)
            assert snapshot() == before
        finally:
            server.close()

    def test_injected_drift_is_visible_on_the_mcp_face(
        self, applied: tuple[Path, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Injection control: MCP must call the shared builder, not a private copy.

        Drift the builder on the MCP module alone. If MCP had its own inline
        spelling, its payload would not move and the equality above would be a
        coincidence rather than shared code.
        """
        import datasentry.mcp_server as mcp_module

        ws, run_id = applied
        real = mcp_module.build_repair_diff_payload

        def drifted(*args: Any, **kwargs: Any) -> dict[str, Any]:
            payload = real(*args, **kwargs)
            payload["changed_rows"] = [
                {"row": row["line"], "before": row["before"], "after": row["after"]}
                for row in payload["changed_rows"]
            ]
            return payload

        monkeypatch.setattr(mcp_module, "build_repair_diff_payload", drifted)
        mcp = _mcp_diff(ws, run_id)
        rest = _rest_diff(ws, run_id)
        assert mcp != rest, "parity would be vacuous if MCP ignored the shared builder"
        assert mcp["changed_rows"] and "row" in mcp["changed_rows"][0]


_REPO = Path(__file__).resolve().parents[1]
_BLOGS = (
    _REPO / ".growth" / "blog-4-verify-loop-zh.md",
    _REPO / ".growth" / "blog-4-verify-loop-en.md",
)


def _assert_blog_claim(text: str, tools: dict[str, dict[str, Any]]) -> None:
    """Every surface the blog names must exist, and the old vague claim must be gone."""
    assert "repair_diff" in tools, "the blog names a tool the server does not advertise"
    assert "GET /repairs/{id}/diff" in text
    assert "repair_diff_payload.py" in text
    assert "test_mcp_repair_diff_parity.py" in text
    assert "Four surfaces, one implementation" not in text
    assert "四个入口\uff0c一套实现" not in text


class TestGrowthClaim:
    """R-09 also asks that the `.growth/` claim be brought to measured wording."""

    def test_named_surfaces_exist(self, tmp_path: Path) -> None:
        tools = _mcp_tools(tmp_path)
        assert (_REPO / "src" / "datasentry" / "repair_diff_payload.py").is_file()
        assert (_REPO / "tests" / "test_mcp_repair_diff_parity.py").is_file()
        with TestClient(create_app(project=tmp_path)) as client:
            routes = {route.path for route in client.app.routes}
            assert "/repairs/{repair_run_id}/diff" in routes
        for blog in _BLOGS:
            _assert_blog_claim(blog.read_text(encoding="utf-8"), tools)

    def test_claim_guard_fires_on_a_drifted_copy(self, tmp_path: Path) -> None:
        """Injection control: a blog naming an endpoint we did not build must go red."""
        tools = _mcp_tools(tmp_path)
        for blog in _BLOGS:
            text = blog.read_text(encoding="utf-8")
            _assert_blog_claim(text, tools)
            drifted = text.replace("GET /repairs/{id}/diff", "GET /repairs/{id}/delta")
            assert drifted != text
            with pytest.raises(AssertionError):
                _assert_blog_claim(drifted, tools)

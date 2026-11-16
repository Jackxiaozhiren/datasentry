"""B5: MCP Apps negotiation (R-10), read-only card (R-11), no second logic (R-13).

The gate that matters most is the one that protects existing users: a client that
does not declare `mcp-apps` must get **byte-identical** responses. The snapshot in
`tests/fixtures/mcp_report_export_legacy.json` was captured from the server before
the feature existed, so it can only pass if nothing was added to the legacy path.

R-12 (a real MCP Apps client rendering the card) is **not** verified here, and
`docs/MCP_APPS.md` is asserted to say so rather than imply otherwise.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from datasentry import mcp_apps
from datasentry.mcp_server import McpServer

_REPO = Path(__file__).resolve().parents[1]
_SNAPSHOT = _REPO / "tests" / "fixtures" / "mcp_report_export_legacy.json"
_DOC = _REPO / "docs" / "MCP_APPS.md"

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_DEVICE_WIDTH_META = '<meta name="viewport" content="width=device-width, initial-scale=1">'


def _head_contract(html: str) -> bool:
    """Structural half of the narrow-viewport check; geometry needs a browser."""
    return "</head>" in html and _DEVICE_WIDTH_META in html[: html.index("</head>")]


def _card_css_contract(css: str) -> bool:
    rule = re.search(r"\.card\{([^}]*)\}", css)
    return bool(
        rule
        and "box-sizing:border-box" in rule.group(1)
        and "width:100%" in rule.group(1)
        and "max-width:520px" in rule.group(1)
    )


_FIXED_REPORT: dict[str, Any] = {
    "report_schema_version": "1.0",
    "datasentry_version": "0.0.0-fixture",
    "scan_run_id": "scan_000000000000",
    "generated_at": "2024-01-01T00:00:00+00:00",
    "reproducible": True,
    "llm_used": False,
    "scan": {
        "id": "scan_000000000000",
        "dataset_id": "fixture_orders",
        "issues_count": {"critical": 1, "high": 2, "medium": 3, "low": 4, "info": 5},
    },
    "detector_runs": [],
    "issues": [],
    "quality": {"overall": 96.75},
}


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    srv = McpServer(project=tmp_path / "ws")
    monkeypatch.setattr(srv._client, "export_report", lambda scan_run_id: dict(_FIXED_REPORT))
    try:
        yield srv
    finally:
        srv.close()


def _call(
    server: McpServer, message_id: int, method: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": message_id, "method": method}
    if params is not None:
        message["params"] = params
    response = server._handle_message(message)
    assert isinstance(response, dict)
    return response


def _initialize(server: McpServer, capabilities: object) -> dict[str, Any]:
    return _call(server, 1, "initialize", {"capabilities": capabilities})


def _export(server: McpServer, message_id: int = 1) -> dict[str, Any]:
    return _call(
        server,
        message_id,
        "tools/call",
        {"name": "report_export", "arguments": {"scan_run_id": "scan_000000000000"}},
    )


def _assert_matches_snapshot(response: dict[str, Any], snapshot_text: str) -> None:
    actual = json.dumps(response, ensure_ascii=False)
    assert actual == snapshot_text.strip(), "the legacy response bytes changed"


class TestLegacyClientIsUntouched:
    def test_initialize_capabilities_are_unchanged(self, server: McpServer) -> None:
        result = _initialize(server, {"roots": {"listChanged": False}})["result"]
        assert result["capabilities"] == {"tools": {"listChanged": False}}
        assert "extensions" not in result["capabilities"]

    def test_initialize_without_capabilities_is_unchanged(self, server: McpServer) -> None:
        result = _call(server, 1, "initialize")["result"]
        assert result["capabilities"] == {"tools": {"listChanged": False}}

    def test_report_export_is_byte_identical_to_the_pre_feature_snapshot(
        self, server: McpServer
    ) -> None:
        response = _export(server)
        _assert_matches_snapshot(response, _SNAPSHOT.read_text(encoding="utf-8"))
        assert len(response["result"]["content"]) == 1

    def test_malformed_capabilities_do_not_unlock_the_card(self, server: McpServer) -> None:
        for capabilities in ("mcp-apps", ["mcp-apps"], {"extensions": []}, {"extensions": {}}, 4):
            result = _initialize(server, capabilities)["result"]
            assert "extensions" not in result["capabilities"], capabilities
            assert len(_export(server)["result"]["content"]) == 1, capabilities


class TestDeclaringClientGetsTheCard:
    def test_extensions_declaration_adds_one_resource_block(self, server: McpServer) -> None:
        declared = _initialize(server, {"extensions": {"mcp-apps": {}}})
        assert declared["result"]["capabilities"]["extensions"]["mcp-apps"]["resourceUri"] == (
            mcp_apps.REPORT_CARD_URI
        )
        content = _export(server)["result"]["content"]
        assert len(content) == 2, content
        assert content[0]["type"] == "text"
        block = content[1]
        assert block["type"] == "resource"
        assert block["resource"]["mimeType"] == "text/html"
        assert block["resource"]["uri"] == mcp_apps.REPORT_CARD_URI
        assert "<article" in block["resource"]["text"]

    def test_experimental_declaration_is_accepted_too(self, server: McpServer) -> None:
        _initialize(server, {"experimental": {"mcp-apps": {"version": 1}}})
        assert len(_export(server)["result"]["content"]) == 2

    def test_the_json_payload_is_identical_with_and_without_the_card(
        self, server: McpServer
    ) -> None:
        _initialize(server, {"extensions": {}})
        plain = _export(server)["result"]["content"]
        _initialize(server, {"extensions": {"mcp-apps": {}}})
        with_card = _export(server)["result"]["content"]
        assert plain[0] == with_card[0], "the payload a script parses must not change"
        assert len(with_card) == 2

    def test_no_new_tool_was_invented(self, server: McpServer) -> None:
        tools = _call(server, 3, "tools/list")["result"]["tools"]
        names = {tool["name"] for tool in tools}
        assert "report_export" in names
        assert "scan_summary" not in names, "R-11 says reuse report_export, do not add a tool"
        assert len(names) >= 20


class TestCardHasNoActionSurface:
    def _html(self) -> str:
        return mcp_apps.report_card_html(_FIXED_REPORT)

    def test_no_form_button_or_link(self) -> None:
        html = self._html().lower()
        for forbidden in ("<form", "<button", "<input", "onclick", "href="):
            assert forbidden not in html, forbidden

    def test_no_approval_vocabulary(self) -> None:
        html = self._html().lower()
        for word in ("approve", "apply", "reject", "rollback"):
            assert word not in html, word

    def test_no_script(self) -> None:
        assert "<script" not in self._html().lower()


class TestNoSecondDomainLogic:
    def _body(self, html: str) -> str:
        start = html.index("<body>") + len("<body>")
        return html[start : html.index("</body>")]

    def test_every_number_in_the_card_comes_from_the_payload(self) -> None:
        html = mcp_apps.report_card_html(_FIXED_REPORT)
        body = self._body(html)
        payload = set(_NUMBER.findall(json.dumps(_FIXED_REPORT, ensure_ascii=False)))
        body_numbers = set(_NUMBER.findall(body))
        assert body_numbers, "a card with no numbers would make this gate vacuous"
        assert body_numbers <= payload, f"invented numbers: {sorted(body_numbers - payload)}"

    def test_the_card_tracks_the_payload_instead_of_constants(self) -> None:
        other = json.loads(json.dumps(_FIXED_REPORT))
        other["quality"]["overall"] = 71.5
        other["scan"]["issues_count"] = {"critical": 7}
        first = mcp_apps.report_card_html(_FIXED_REPORT)
        second = mcp_apps.report_card_html(other)
        assert "96.75" in first
        assert "96.75" not in second
        assert "71.5" in second
        assert "71.5" not in first
        assert ">7<" in second

    def test_card_numbers_helper_matches_the_rendered_body(self) -> None:
        html = mcp_apps.report_card_html(_FIXED_REPORT)
        body = self._body(html)
        for number in mcp_apps.card_numbers(_FIXED_REPORT):
            assert number in body, f"{number} is offered by the helper but not rendered"


class TestGateCanGoRed:
    def test_a_snapshot_with_an_extra_key_is_detected(self, server: McpServer) -> None:
        response = _export(server)
        drifted = json.loads(json.dumps(response))
        drifted["result"]["structuredContent"] = {"ok": True}
        snapshot = _SNAPSHOT.read_text(encoding="utf-8")
        with pytest.raises(AssertionError, match="legacy response bytes changed"):
            _assert_matches_snapshot(drifted, snapshot)

    def test_a_card_leaking_into_the_legacy_path_is_detected(self, server: McpServer) -> None:
        response = _export(server)
        drifted = json.loads(json.dumps(response))
        drifted["result"]["content"].append({"type": "resource", "resource": {}})
        snapshot = _SNAPSHOT.read_text(encoding="utf-8")
        with pytest.raises(AssertionError, match="legacy response bytes changed"):
            _assert_matches_snapshot(drifted, snapshot)

    def test_an_invented_number_in_the_card_is_detected(self) -> None:
        html = mcp_apps.report_card_html(_FIXED_REPORT).replace(
            "</article>", '<p class="note">42</p></article>'
        )
        body = html[html.index("<body>") :]
        payload = set(_NUMBER.findall(json.dumps(_FIXED_REPORT, ensure_ascii=False)))
        assert set(_NUMBER.findall(body)) - payload, "the R-13 comparison must have a denominator"


class TestCardFitsANarrowViewport:
    """R-01's invariant applied to the card introduced in B5.

    Real geometry is measured outside CI with CDP device emulation at 390 CSS px;
    these two gates stop the fix from being quietly reverted. See docs/MCP_APPS.md.
    """

    def test_head_declares_a_device_width_viewport(self) -> None:
        html = mcp_apps.report_card_html(_FIXED_REPORT)
        assert _head_contract(html), (
            "without width=device-width a mobile browser lays out at 980 CSS px, "
            "so the per-severity count column is off-screen"
        )

    def test_card_width_is_fluid_but_capped(self) -> None:
        assert _card_css_contract(mcp_apps.card_css()), (
            "border-box + width:100% is what keeps the counts inside 390px; "
            "the max-width cap still applies on wide screens"
        )


class TestNarrowViewportGateCanGoRed:
    def test_no_viewport_meta_is_rejected(self) -> None:
        broken = mcp_apps.report_card_html(_FIXED_REPORT).replace(_DEVICE_WIDTH_META, "")
        assert not _head_contract(broken), "the head check must have a denominator"

    def test_a_fixed_width_card_is_rejected(self) -> None:
        broken = mcp_apps.card_css().replace("box-sizing:border-box;width:100%", "")
        assert not _card_css_contract(broken), "the css check must have a denominator"


class TestRealClientStatusIsHonest:
    def test_the_document_says_it_was_not_verified_in_a_real_client(self) -> None:
        text = _DOC.read_text(encoding="utf-8")
        assert "未在真客户端验证" in text
        lowered = text.lower()
        assert "verified in a real client" not in lowered.replace(
            "not verified in a real client", ""
        )

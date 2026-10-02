"""Step 100 MCP PII vault 工具（V17，ADR-100）：pii_sessions /
pii_restore / pii_delete_session——显式授权语义。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datasentry.mcp_server import McpServer
from datasentry.pii_vault import PIIVault

_MAPPING = {"email": ["alice@example.com", "bob@corp.io"], "cn_phone": ["13800138000"]}

# D5-12: these four tools mutate, so the transport now refuses them without `confirm=true`.
# Everything below is about what a tool does *after* confirmation, so the acknowledgement is
# injected in one place here instead of at each of the ~20 call sites.
PII_MUTATING_TOOLS = ["pii_delete_session", "pii_purge_sessions", "pii_restore", "pii_rotate_key"]


def _with_confirmation(params: dict | None) -> dict | None:
    if not isinstance(params, dict) or params.get("name") not in PII_MUTATING_TOOLS:
        return params
    arguments = dict(params.get("arguments") or {})
    arguments.setdefault("confirm", True)
    return {**params, "arguments": arguments}


def _call(server: McpServer, message_id: int, method: str, params: dict | None = None) -> dict:
    message: dict = {"jsonrpc": "2.0", "id": message_id, "method": method}
    if params is not None:
        message["params"] = _with_confirmation(params)
    response = server._handle_message(message)
    assert response is not None
    return response


def _tool_text(server: McpServer, message_id: int, name: str, args: dict) -> dict:
    response = _call(server, message_id, "tools/call", {"name": name, "arguments": args})
    return json.loads(response["result"]["content"][0]["text"])


def _server_with_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> McpServer:
    monkeypatch.setenv("DATASENTRY_ENCRYPTION_KEY", "mcp-test-key-0001")
    monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
    return McpServer(project=tmp_path / "ws")


class TestPiiToolsShape:
    def test_tools_list_includes_pii(self, tmp_path: Path) -> None:
        server = McpServer(project=tmp_path / "ws")
        try:
            result = _call(server, 4, "tools/list")["result"]
            by_name = {t["name"]: t for t in result["tools"]}
            assert {"pii_sessions", "pii_restore", "pii_delete_session", "pii_rotate_key"} <= set(
                by_name
            )
            restore = by_name["pii_restore"]
            assert "Explicit authorization" in restore["description"]
            assert restore["inputSchema"]["type"] == "object"
            # D5-12: the description's "explicit authorization" used to be prose only; `confirm`
            # is now the mechanism, required like every other mutating tool's acknowledgement.
            assert set(restore["inputSchema"]["properties"]) == {"session_id", "text", "confirm"}
            assert restore["inputSchema"]["required"] == ["session_id", "text", "confirm"]
            assert by_name["pii_sessions"]["inputSchema"]["required"] == []
            rotate = by_name["pii_rotate_key"]
            assert set(rotate["inputSchema"]["properties"]) == {"newKey", "confirm"}
            assert rotate["inputSchema"]["required"] == ["confirm"]
            assert "newKey" in rotate["description"]
            purge = by_name["pii_purge_sessions"]
            assert set(purge["inputSchema"]["properties"]) == {"olderThanDays", "confirm"}
            assert purge["inputSchema"]["required"] == ["olderThanDays", "confirm"]
        finally:
            server.close()


class TestPiiToolsNoKey:
    def test_all_tools_error_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DATASENTRY_ENCRYPTION_KEY", raising=False)
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        server = McpServer(project=tmp_path / "ws")
        try:
            listed = _tool_text(server, 20, "pii_sessions", {})
            assert listed["ok"] is False
            assert "no encryption key configured" in listed["error"]
            restored = _tool_text(
                server, 21, "pii_restore", {"session_id": "pii_x", "text": "{{REDACTED:email:0}}"}
            )
            assert restored["ok"] is False
            assert "no encryption key configured" in restored["error"]
            rotated = _tool_text(server, 22, "pii_rotate_key", {})
            assert rotated["ok"] is False
            assert "no encryption key configured" in rotated["error"]
        finally:
            server.close()

    def test_delete_works_without_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DATASENTRY_ENCRYPTION_KEY", raising=False)
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        server = McpServer(project=tmp_path / "ws")
        try:
            removed = _tool_text(server, 22, "pii_delete_session", {"session_id": "pii_x"})
            assert removed["ok"] is False
            assert "not found" in removed["error"]
        finally:
            server.close()


class TestPiiToolsEndToEnd:
    def test_sessions_list_and_restore(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = _server_with_key(tmp_path, monkeypatch)
        try:
            vault = PIIVault(server._client._store)
            session_id = vault.save_mapping(_MAPPING)

            listed = _tool_text(server, 30, "pii_sessions", {})
            assert listed["ok"] is True
            assert listed["key_source"] == "env"
            assert [s["sessionId"] for s in listed["sessions"]] == [session_id]
            assert listed["sessions"][0]["keyVersion"] == "env"
            assert "createdAt" in listed["sessions"][0]

            restored = _tool_text(
                server,
                31,
                "pii_restore",
                {
                    "session_id": session_id,
                    "text": "mail {{REDACTED:email:0}} {{REDACTED:cn_phone:0}}",
                },
            )
            assert restored["ok"] is True
            assert restored["restored"] == "mail alice@example.com 13800138000"
        finally:
            server.close()

    def test_restore_unknown_session_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = _server_with_key(tmp_path, monkeypatch)
        try:
            restored = _tool_text(
                server, 32, "pii_restore", {"session_id": "pii_nope", "text": "x"}
            )
            assert restored["ok"] is False
            assert "not found" in restored["error"]
        finally:
            server.close()

    def test_delete_session(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        server = _server_with_key(tmp_path, monkeypatch)
        try:
            vault = PIIVault(server._client._store)
            session_id = vault.save_mapping(_MAPPING)
            removed = _tool_text(server, 33, "pii_delete_session", {"session_id": session_id})
            assert removed["ok"] is True
            assert removed["deleted"] is True
            gone = _tool_text(server, 34, "pii_delete_session", {"session_id": session_id})
            assert gone["ok"] is False
            assert "not found" in gone["error"]
            listed = _tool_text(server, 35, "pii_sessions", {})
            assert listed["sessions"] == []
        finally:
            server.close()

    def test_rotate_key_auto_generates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = _server_with_key(tmp_path, monkeypatch)
        try:
            vault = PIIVault(server._client._store)
            session_id = vault.save_mapping(_MAPPING)
            rotated = _tool_text(server, 36, "pii_rotate_key", {})
            assert rotated["ok"] is True
            assert rotated["keyVersion"] == "file"
            assert rotated["rotated"] == 1
            assert rotated["keyFile"] == str(tmp_path / "vault.key")
            assert "newKey" not in rotated  # 远程面不泄露密钥材料
            # 旧 env key 失效 → 缺文件 key 时解密失败错误
            stale = _tool_text(
                server,
                37,
                "pii_restore",
                {"session_id": session_id, "text": "{{REDACTED:email:0}}"},
            )
            assert stale["ok"] is False
        finally:
            server.close()

    def test_rotate_key_with_new_key_material(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = _server_with_key(tmp_path, monkeypatch)
        try:
            vault = PIIVault(server._client._store)
            vault.save_mapping(_MAPPING)
            rotated = _tool_text(server, 38, "pii_rotate_key", {"newKey": "mcp-known-material"})
            assert rotated["ok"] is True
            assert rotated["rotated"] == 1
            assert (tmp_path / "vault.key").read_text(
                encoding="utf-8"
            ).strip() == "mcp-known-material"
        finally:
            server.close()

    def test_purge_sessions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from datetime import timedelta

        from datasentry_core.models.evidence import utcnow

        server = _server_with_key(tmp_path, monkeypatch)
        try:
            store = server._client._store
            store.save_pii_mapping(
                "pii_old", "ct", key_version="env", created_at=utcnow() - timedelta(days=60)
            )
            store.save_pii_mapping(
                "pii_new", "ct2", key_version="env", created_at=utcnow() - timedelta(days=2)
            )
            purged = _tool_text(server, 39, "pii_purge_sessions", {"olderThanDays": 30})
            assert purged["ok"] is True
            assert purged["purged"] == 1
            listed = _tool_text(server, 40, "pii_sessions", {})
            assert [s["sessionId"] for s in listed["sessions"]] == ["pii_new"]
        finally:
            server.close()

    def test_purge_invalid_days_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = _server_with_key(tmp_path, monkeypatch)
        try:
            purged = _tool_text(server, 41, "pii_purge_sessions", {"olderThanDays": 0})
            assert purged["ok"] is False
            assert "must be >= 1" in purged["error"]
        finally:
            server.close()

    def test_purge_works_without_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from datetime import timedelta

        from datasentry_core.models.evidence import utcnow

        monkeypatch.delenv("DATASENTRY_ENCRYPTION_KEY", raising=False)
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        server = McpServer(project=tmp_path / "ws")
        try:
            server._client._store.save_pii_mapping(
                "pii_old", "ct", key_version="env", created_at=utcnow() - timedelta(days=60)
            )
            purged = _tool_text(server, 42, "pii_purge_sessions", {"olderThanDays": 30})
            assert purged["ok"] is True
            assert purged["purged"] == 1
        finally:
            server.close()


class TestPiiConfirmationRefusals:
    """D5-12 on the PII face: an agent must not be able to rotate, purge or delete in one call."""

    @staticmethod
    def _raw(server: McpServer, message_id: int, name: str, arguments: dict) -> dict:
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

    def test_unconfirmed_pii_writes_are_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        server = _server_with_key(tmp_path, monkeypatch)
        vault = PIIVault(server._client._store)
        session_id = vault.save_mapping(_MAPPING)
        try:
            cases = (
                ("pii_rotate_key", {}),
                ("pii_purge_sessions", {"olderThanDays": 30}),
                ("pii_delete_session", {"session_id": session_id}),
                ("pii_restore", {"session_id": session_id, "text": "x"}),
            )
            for name, args in cases:
                response = self._raw(server, 60, name, args)
                assert response["error"]["code"] == -32602, name
                assert "confirm" in response["error"]["message"], name
            # Nothing above may have taken effect: the session is still there and restorable.
            listed = _tool_text(server, 61, "pii_sessions", {})
            assert [s["sessionId"] for s in listed["sessions"]] == [session_id]
            restored = _tool_text(
                server,
                62,
                "pii_restore",
                {"session_id": session_id, "text": "{{REDACTED:email:0}}"},
            )
            assert restored["ok"] is True, restored
            assert restored["restored"] == "alice@example.com"
        finally:
            server.close()

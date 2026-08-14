"""Step 101 Web UI /ui/pii（V17，ADR-101）：会话列表 + 还原表单 + 缺 key 提示。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from datasentry.api import create_app
from datasentry.pii_vault import PIIVault

_MAPPING = {"email": ["alice@example.com"], "cn_phone": ["13800138000"]}


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(project=tmp_path))


class TestPiiPageNoKey:
    def test_page_shows_key_missing_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DATASENTRY_ENCRYPTION_KEY", raising=False)
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        resp = _client(tmp_path).get("/ui/pii")
        assert resp.status_code == 200
        assert "Encryption key not configured" in resp.text
        assert "DATASENTRY_ENCRYPTION_KEY" in resp.text

    def test_page_zh_hint(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DATASENTRY_ENCRYPTION_KEY", raising=False)
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        resp = _client(tmp_path).get("/ui/pii", params={"lang": "zh"})
        assert resp.status_code == 200
        assert "未配置加密密钥" in resp.text

    def test_no_restore_form_without_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DATASENTRY_ENCRYPTION_KEY", raising=False)
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        resp = _client(tmp_path).get("/ui/pii")
        assert 'name="session_id"' not in resp.text

    def test_restore_post_without_key_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DATASENTRY_ENCRYPTION_KEY", raising=False)
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        resp = _client(tmp_path).post(
            "/ui/pii", data={"session_id": "pii_x", "text": "{{REDACTED:email:0}}"}
        )
        assert resp.status_code == 200
        assert "Encryption key not configured" in resp.text


class TestPiiPageWithKey:
    def test_empty_list_and_restore_form(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATASENTRY_ENCRYPTION_KEY", "ui-test-key-0001")
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        resp = _client(tmp_path).get("/ui/pii")
        assert resp.status_code == 200
        assert "No PII sessions yet" in resp.text
        assert 'name="session_id"' in resp.text
        assert 'name="text"' in resp.text

    def test_lists_sessions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATASENTRY_ENCRYPTION_KEY", "ui-test-key-0001")
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        client = _client(tmp_path)
        app_store = client.app.state.client._store
        session_id = PIIVault(app_store).save_mapping(_MAPPING)
        resp = client.get("/ui/pii")
        assert resp.status_code == 200
        assert session_id in resp.text
        assert "env" in resp.text

    def test_restore_post_shows_plaintext_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATASENTRY_ENCRYPTION_KEY", "ui-test-key-0001")
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        client = _client(tmp_path)
        session_id = PIIVault(client.app.state.client._store).save_mapping(_MAPPING)
        resp = client.post(
            "/ui/pii",
            data={"session_id": session_id, "text": "mail {{REDACTED:email:0}}"},
        )
        assert resp.status_code == 200
        assert "alice@example.com" in resp.text
        assert "Restored" in resp.text

    def test_restore_unknown_session_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATASENTRY_ENCRYPTION_KEY", "ui-test-key-0001")
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        resp = _client(tmp_path).post(
            "/ui/pii", data={"session_id": "pii_nope", "text": "{{REDACTED:email:0}}"}
        )
        assert resp.status_code == 200
        assert "not found" in resp.text

    def test_restore_escaped_in_html(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATASENTRY_ENCRYPTION_KEY", "ui-test-key-0001")
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        client = _client(tmp_path)
        session_id = PIIVault(client.app.state.client._store).save_mapping(_MAPPING)
        resp = client.post(
            "/ui/pii", data={"session_id": session_id, "text": "x <script>alert(1)</script>"}
        )
        assert resp.status_code == 200
        assert "<script>alert(1)</script>" not in resp.text
        assert "&lt;script&gt;" in resp.text


class TestPiiNav:
    def test_nav_links_pii(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/ui/")
        assert resp.status_code == 200
        assert 'href="/ui/pii"' in resp.text

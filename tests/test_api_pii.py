"""Step 99 REST API PII vault 管理面（V17，ADR-099）：/pii/* 五端点。

key 未配置 → 503（与 /rpc/execute disabled 语义一致）；session
不存在 → 404；还原缺 text → 422；轮换后旧 key 解密失败 → 503。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from datasentry.api import create_app
from datasentry.pii_vault import PIIVault
from datasentry_core.storage.store import MetadataStore

_MAPPING = {"email": ["alice@example.com", "bob@corp.io"], "cn_phone": ["13800138000"]}


@pytest.fixture()
def key_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DATASENTRY_ENCRYPTION_KEY", "api-test-key-0001")
    monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")


@pytest.fixture()
def no_key_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DATASENTRY_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")


def _client(tmp_path: Path) -> tuple[TestClient, PIIVault]:
    app = create_app(project=tmp_path)
    client = TestClient(app)
    vault = PIIVault(app.state.client._store)
    return client, vault


class TestPiiEndpointsUnconfigured:
    """无 env key 且无 key 文件 → key_source=dev（key_configured=False）→ 503。"""

    def test_list_503(self, tmp_path: Path, no_key_env: None) -> None:
        client, _ = _client(tmp_path)
        resp = client.get("/pii/sessions")
        assert resp.status_code == 503
        assert "DATASENTRY_ENCRYPTION_KEY" in resp.json()["detail"]

    def test_summary_503(self, tmp_path: Path, no_key_env: None) -> None:
        client, _ = _client(tmp_path)
        assert client.get("/pii/sessions/pii_x").status_code == 503

    def test_restore_503(self, tmp_path: Path, no_key_env: None) -> None:
        client, _ = _client(tmp_path)
        resp = client.post("/pii/sessions/pii_x/restore", json={"text": "x"})
        assert resp.status_code == 503

    def test_rotate_503(self, tmp_path: Path, no_key_env: None) -> None:
        client, _ = _client(tmp_path)
        assert client.post("/pii/rotate-key").status_code == 503

    def test_delete_works_without_key(self, tmp_path: Path, no_key_env: None) -> None:
        """删除密文行不需要密钥（与 CLI llm restore --delete 一致）；不存在仍 404。"""
        client, _ = _client(tmp_path)
        assert client.delete("/pii/sessions/pii_x").status_code == 404


class TestPiiEndpointList:
    def test_root_lists_new_endpoints(self, tmp_path: Path, key_env: None) -> None:
        client, _ = _client(tmp_path)
        endpoints = client.get("/").json()["endpoints"]
        for ep in (
            "GET /pii/sessions",
            "GET /pii/sessions/{session_id}",
            "POST /pii/sessions/{session_id}/restore",
            "DELETE /pii/sessions/{session_id}",
            "POST /pii/rotate-key",
        ):
            assert ep in endpoints


class TestPiiFullLifecycle:
    def test_list_summary_restore(self, tmp_path: Path, key_env: None) -> None:
        client, vault = _client(tmp_path)
        session_id = vault.save_mapping(_MAPPING)

        listed = client.get("/pii/sessions")
        assert listed.status_code == 200
        body = listed.json()
        assert body["key_source"] == "env"
        assert [s["session_id"] for s in body["sessions"]] == [session_id]
        assert body["sessions"][0]["key_version"] == "env"

        summary = client.get(f"/pii/sessions/{session_id}")
        assert summary.status_code == 200
        mapping = summary.json()["mapping"]
        assert mapping["email"]["count"] == 2
        assert mapping["email"]["preview"][0]["original"] == "alice@example.com"
        assert mapping["cn_phone"]["count"] == 1

        restored = client.post(
            f"/pii/sessions/{session_id}/restore",
            json={"text": "mail {{REDACTED:email:0}} phone {{REDACTED:cn_phone:0}}"},
        )
        assert restored.status_code == 200
        assert restored.json()["restored"] == "mail alice@example.com phone 13800138000"

    def test_restore_matches_cli_semantics(self, tmp_path: Path, key_env: None) -> None:
        """同 vault 同映射：API 还原结果与 vault.restore_text 一致（CLI 同源）。"""
        client, vault = _client(tmp_path)
        session_id = vault.save_mapping(_MAPPING)
        text = "contact {{REDACTED:email:1}}"
        resp = client.post(f"/pii/sessions/{session_id}/restore", json={"text": text})
        assert resp.status_code == 200
        assert (
            resp.json()["restored"] == vault.restore_text(text, session_id) == "contact bob@corp.io"
        )

    def test_delete_session(self, tmp_path: Path, key_env: None) -> None:
        client, vault = _client(tmp_path)
        session_id = vault.save_mapping(_MAPPING)
        assert client.delete(f"/pii/sessions/{session_id}").status_code == 204
        assert client.get(f"/pii/sessions/{session_id}").status_code == 404
        assert client.delete(f"/pii/sessions/{session_id}").status_code == 404

    def test_missing_session_404(self, tmp_path: Path, key_env: None) -> None:
        client, _ = _client(tmp_path)
        assert client.get("/pii/sessions/pii_nope").status_code == 404
        resp = client.post("/pii/sessions/pii_nope/restore", json={"text": "x"})
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_restore_missing_text_422(self, tmp_path: Path, key_env: None) -> None:
        client, vault = _client(tmp_path)
        session_id = vault.save_mapping(_MAPPING)
        assert client.post(f"/pii/sessions/{session_id}/restore", json={}).status_code == 422

    def test_rotate_key_full_chain(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATASENTRY_ENCRYPTION_KEY", "api-test-key-0001")
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        client, vault = _client(tmp_path)
        session_id = vault.save_mapping(_MAPPING)

        rotated = client.post("/pii/rotate-key")
        assert rotated.status_code == 200
        body = rotated.json()
        assert body["key_version"] == "file"
        assert body["rotated"] == 1
        assert body["key_file"] == str(tmp_path / "vault.key")

        # 轮换后旧 env key 无法解密 → 503（VaultKeyMissingError → EXIT_CONFIG 对齐）
        stale = client.post(
            f"/pii/sessions/{session_id}/restore", json={"text": "{{REDACTED:email:0}}"}
        )
        assert stale.status_code == 503
        assert client.get(f"/pii/sessions/{session_id}").status_code == 503

        # 去掉 env key 后 vault 改读 key 文件（新密钥）→ 还原恢复
        monkeypatch.delenv("DATASENTRY_ENCRYPTION_KEY", raising=False)
        restored = client.post(
            f"/pii/sessions/{session_id}/restore", json={"text": "{{REDACTED:email:0}}"}
        )
        assert restored.status_code == 200
        assert restored.json()["restored"] == "alice@example.com"


class TestPiiRotateKeyV18:
    """Step 102（V18，ADR-102）：POST /pii/rotate-key 可选 body {"new_key"}。

    无 body / 空对象行为与 v0.19.0 完全一致（向后兼容）；带
    new_key 与 CLI rotate-key --new-key 同源。
    """

    def test_no_body_backward_compat(self, tmp_path: Path, key_env: None) -> None:
        client, vault = _client(tmp_path)
        vault.save_mapping(_MAPPING)
        body = client.post("/pii/rotate-key").json()
        assert body == {
            "key_version": "file",
            "rotated": 1,
            "key_file": str(tmp_path / "vault.key"),
        }

    def test_empty_object_backward_compat(self, tmp_path: Path, key_env: None) -> None:
        client, vault = _client(tmp_path)
        vault.save_mapping(_MAPPING)
        body = client.post("/pii/rotate-key", json={}).json()
        assert body["key_version"] == "file"
        assert body["rotated"] == 1

    def test_explicit_new_key_chain(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """带 new_key 轮换：旧 env key 失效 503，去 env 后新 key 可还原（与 CLI 同源）。"""
        monkeypatch.setenv("DATASENTRY_ENCRYPTION_KEY", "api-test-key-0001")
        monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
        client, vault = _client(tmp_path)
        session_id = vault.save_mapping(_MAPPING)

        rotated = client.post("/pii/rotate-key", json={"new_key": "v18-explicit-material"})
        assert rotated.status_code == 200
        body = rotated.json()
        assert body["key_version"] == "file"
        assert body["rotated"] == 1
        assert "new_key" not in body  # 远程面不泄露密钥材料

        stale = client.post(
            f"/pii/sessions/{session_id}/restore", json={"text": "{{REDACTED:email:0}}"}
        )
        assert stale.status_code == 503

        monkeypatch.delenv("DATASENTRY_ENCRYPTION_KEY", raising=False)
        restored = client.post(
            f"/pii/sessions/{session_id}/restore", json={"text": "{{REDACTED:email:0}}"}
        )
        assert restored.status_code == 200
        assert restored.json()["restored"] == "alice@example.com"

    def test_explicit_new_key_roundtrip_uses_it(self, tmp_path: Path, key_env: None) -> None:
        """文件密钥内容即轮换时指定材料（可被 CLI/API 复用，非随机）。"""
        client, vault = _client(tmp_path)
        vault.save_mapping(_MAPPING)
        client.post("/pii/rotate-key", json={"new_key": "v18-known-material"})
        assert (tmp_path / "vault.key").read_text(encoding="utf-8").strip() == "v18-known-material"


class TestPiiPurgeV18:
    """Step 103（V18，ADR-103）：POST /pii/sessions/purge 按龄清理。

    无需密钥（与 DELETE 同语义）；older_than_days < 1 → 422。
    """

    def _seed(self, store: MetadataStore) -> None:
        from datetime import timedelta

        from datasentry_core.models.evidence import utcnow

        store.save_pii_mapping(
            "pii_old", "ct", key_version="env", created_at=utcnow() - timedelta(days=60)
        )
        store.save_pii_mapping(
            "pii_new", "ct2", key_version="env", created_at=utcnow() - timedelta(days=2)
        )

    def test_purge_removes_old_keeps_new(self, tmp_path: Path, key_env: None) -> None:
        client, _ = _client(tmp_path)
        self._seed(client.app.state.client._store)
        resp = client.post("/pii/sessions/purge", json={"older_than_days": 30})
        assert resp.status_code == 200
        assert resp.json() == {"purged": 1}
        remaining = [s["session_id"] for s in client.get("/pii/sessions").json()["sessions"]]
        assert remaining == ["pii_new"]

    def test_purge_invalid_days_422(self, tmp_path: Path, key_env: None) -> None:
        client, _ = _client(tmp_path)
        assert client.post("/pii/sessions/purge", json={"older_than_days": 0}).status_code == 422
        assert client.post("/pii/sessions/purge", json={}).status_code == 422

    def test_purge_works_without_key(self, tmp_path: Path, no_key_env: None) -> None:
        client, _ = _client(tmp_path)
        self._seed(client.app.state.client._store)
        resp = client.post("/pii/sessions/purge", json={"older_than_days": 30})
        assert resp.status_code == 200
        assert resp.json() == {"purged": 1}

    def test_purge_endpoint_listed(self, tmp_path: Path, key_env: None) -> None:
        client, _ = _client(tmp_path)
        assert "POST /pii/sessions/purge" in client.get("/").json()["endpoints"]

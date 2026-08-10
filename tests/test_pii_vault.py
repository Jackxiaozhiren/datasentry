"""PII 加密还原保险库（Step 48，V2-A）：AES-GCM 加密映射 + 还原 + 轮换 + 审计。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datasentry.pii_vault import (
    PIIVault,
    VaultKeyMissingError,
    format_mapping_summary,
)
from datasentry_core.storage.store import MetadataStore

_MAPPING = {"email": ["alice@example.com", "bob@corp.io"], "cn_phone": ["13800138000"]}


@pytest.fixture()
def store(tmp_path: Path) -> MetadataStore:
    s = MetadataStore.for_workspace(tmp_path)
    yield s
    s.close()


@pytest.fixture()
def vault(store: MetadataStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> PIIVault:
    monkeypatch.setattr("datasentry.pii_vault._key_file", lambda: tmp_path / "vault.key")
    return PIIVault(store, key="test-key-material")


class TestVaultEncryption:
    def test_save_load_roundtrip(self, vault: PIIVault, store: MetadataStore) -> None:
        session_id = vault.save_mapping(_MAPPING)
        assert session_id.startswith("pii_")
        loaded = vault.load_mapping(session_id)
        assert loaded == _MAPPING

    def test_ciphertext_contains_no_plaintext(self, vault: PIIVault, store: MetadataStore) -> None:
        session_id = vault.save_mapping(_MAPPING)
        row = store.get_pii_mapping(session_id)
        assert row is not None
        assert "alice@example.com" not in row["ciphertext"]
        assert "13800138000" not in row["ciphertext"]
        assert "email" not in row["ciphertext"]

    def test_deterministic_session_id(self, vault: PIIVault) -> None:
        first = vault.save_mapping(_MAPPING)
        second = vault.save_mapping(_MAPPING)
        assert first == second

    def test_missing_session_raises_key_error(self, vault: PIIVault) -> None:
        with pytest.raises(KeyError):
            vault.load_mapping("pii_nope")

    def test_wrong_key_denied_gracefully(self, vault: PIIVault, store: MetadataStore) -> None:
        session_id = vault.save_mapping(_MAPPING)
        other = PIIVault(store, key="different-key")
        with pytest.raises(VaultKeyMissingError):
            other.load_mapping(session_id)

    def test_delete_mapping(self, vault: PIIVault, store: MetadataStore) -> None:
        session_id = vault.save_mapping(_MAPPING)
        assert store.delete_pii_mapping(session_id) is True
        assert store.delete_pii_mapping(session_id) is False
        assert store.count_pii_mappings() == 0


class TestRestore:
    def test_restore_text_and_audit(self, vault: PIIVault, store: MetadataStore) -> None:
        session_id = vault.save_mapping(_MAPPING)
        text = "contact {{REDACTED:email:0}} or {{REDACTED:email:1}}"
        restored = vault.restore_text(text, session_id)
        assert restored == "contact alice@example.com or bob@corp.io"
        invocations = store.list_llm_invocations()
        assert any(
            i.task_type == "pii_restore" and i.pii_session_id == session_id for i in invocations
        )

    def test_restore_unknown_placeholder_idempotent(self, vault: PIIVault) -> None:
        session_id = vault.save_mapping(_MAPPING)
        assert vault.restore_text("{{REDACTED:email:99}}", session_id) == "{{REDACTED:email:99}}"

    def test_restore_value_recursive(self, vault: PIIVault) -> None:
        session_id = vault.save_mapping(_MAPPING)
        value = {"allowed": ["{{REDACTED:email:1}}"], "nested": {"k": "{{REDACTED:cn_phone:0}}"}}
        restored = vault.restore_value(value, session_id)
        assert restored == {"allowed": ["bob@corp.io"], "nested": {"k": "13800138000"}}

    def test_restore_no_mapping_session(self, vault: PIIVault) -> None:
        with pytest.raises(KeyError):
            vault.restore_text("x", "pii_missing")


class TestRotateKey:
    def test_rotate_reencrypts_and_old_key_denied(
        self, vault: PIIVault, store: MetadataStore, tmp_path: Path
    ) -> None:
        session_id = vault.save_mapping(_MAPPING)
        result = vault.rotate_key(new_key="new-key-material")
        assert result["rotated"] == 1
        assert result["key_file"] == str(tmp_path / "vault.key")
        assert vault.key_source == "file"
        assert vault.load_mapping(session_id) == _MAPPING
        old = PIIVault(store, key="test-key-material")
        with pytest.raises(VaultKeyMissingError):
            old.load_mapping(session_id)

    def test_rotate_writes_key_file(self, vault: PIIVault, tmp_path: Path) -> None:
        result = vault.rotate_key(new_key="new-key-material")
        key_path = tmp_path / "vault.key"
        assert key_path.is_file()
        assert key_path.read_text(encoding="utf-8").strip() == "new-key-material"
        assert (key_path.stat().st_mode & 0o777) == 0o600
        assert result["rotated"] == 0

    def test_rotate_generates_new_key(self, vault: PIIVault) -> None:
        result = vault.rotate_key()
        assert len(result["new_key"]) >= 32

    def test_rotate_audited(self, vault: PIIVault, store: MetadataStore) -> None:
        vault.save_mapping(_MAPPING)
        vault.rotate_key(new_key="rotated-key")
        invocations = store.list_llm_invocations()
        assert any(i.task_type == "pii_key_rotate" for i in invocations)


class TestKeySource:
    def test_explicit_key(self, store: MetadataStore) -> None:
        vault = PIIVault(store, key="abc")
        assert vault.key_source == "explicit"
        assert vault.key_configured is True

    def test_env_key(self, store: MetadataStore, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATASENTRY_ENCRYPTION_KEY", "env-key")
        vault = PIIVault(store)
        assert vault.key_source == "env"

    def test_dev_key_fallback(self, store: MetadataStore) -> None:
        vault = PIIVault(store)
        assert vault.key_source == "dev"
        assert vault.key_configured is False
        session_id = vault.save_mapping(_MAPPING)
        assert vault.load_mapping(session_id) == _MAPPING


class TestSummary:
    def test_format_mapping_summary(self) -> None:
        summary = format_mapping_summary(_MAPPING, preview=1)
        assert summary["email"]["count"] == 2
        assert summary["email"]["preview"][0]["masked"] == "{{REDACTED:email:0}}"
        assert summary["email"]["preview"][0]["original"] == "alice@example.com"

    def test_json_serializable(self, vault: PIIVault) -> None:
        session_id = vault.save_mapping(_MAPPING)
        mapping = vault.load_mapping(session_id)
        assert json.loads(json.dumps(mapping)) == mapping

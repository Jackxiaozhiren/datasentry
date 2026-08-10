"""PII 加密映射保险库（Step 48，V2-A：AES-GCM 可逆脱敏）。

在 38 章确定性脱敏（redactor.py，映射表进程内不落盘）之上增加可逆层：
脱敏时生成的映射用 AES-GCM 加密后持久化到 SQLite（pii_mappings 表），
LLM 回复中的 {{REDACTED:kind:n}} 占位符可经 vault.restore_text() 还原
为原文——「不出机器 + 信息不丢 + 可审计还原」。

密钥来源优先级（key_source）：
    env   DATASENTRY_ENCRYPTION_KEY 环境变量（优先）
    file  <user-config>/datasentry/vault.key（0600，rotate 时写入）
    dev   内置开发密钥（仅本机开发，CLI 显式告警）

安全边界（ADR-048）：
    - 密文 = base64(AESGCM.nonce || ciphertext || tag)，密钥经 sha256
      派生为 32 字节（AES-256-GCM）
    - 明文映射只存在于进程内存；每次还原/轮换动作写审计
      （llm_invocations，task_type=pii_restore / pii_key_rotate）
    - 缺密钥拒绝解密（VaultKeyMissingError），不静默降级、不泄露提示
    - session_id 由映射内容确定性派生：同一映射复用同一加密会话
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import uuid
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from datasentry_core.models.evidence import utcnow
from datasentry_core.models.llm import LLMInvocation
from datasentry_core.privacy.redactor import restore
from datasentry_core.storage.store import MetadataStore

ENV_KEY = "DATASENTRY_ENCRYPTION_KEY"

#: 内置开发密钥：仅当 env 与 key 文件均缺失时使用（CLI 告警）
_DEV_KEY = "datasentry-dev-key-do-not-use-in-production"
_NONCE_BYTES = 12


class VaultKeyMissingError(RuntimeError):
    """解密所需密钥缺失：拒绝还原/轮换，提示用户配置密钥。"""


def _derive_key(key_material: str) -> bytes:
    """任意长度密钥材料 → 32 字节（AES-256-GCM）。"""
    return hashlib.sha256(key_material.encode("utf-8")).digest()


def _key_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(base) / "datasentry" / "vault.key"


class PIIVault:
    """加密映射管理：save（加密落库）/ load（解密）/ restore（还原+审计）/ rotate（轮换）。"""

    def __init__(
        self,
        store: MetadataStore,
        key: str | None = None,
        project: str | None = None,
    ) -> None:
        self._store = store
        self._project = project
        self._key_material = key
        self._key_source: str | None = None
        self._key_bytes: bytes | None = None
        if key is not None:
            self._key_source = "explicit"
        else:
            env_value = os.environ.get(ENV_KEY)
            if env_value:
                self._key_source = "env"
                self._key_material = env_value
            else:
                key_path = _key_file()
                if key_path.is_file():
                    self._key_source = "file"
                    self._key_material = key_path.read_text(encoding="utf-8").strip()
        if self._key_material is not None:
            self._key_bytes = _derive_key(self._key_material)

    @property
    def key_source(self) -> str:
        """当前密钥来源（env / file / explicit / dev）。"""
        if self._key_source is not None:
            return self._key_source
        return "dev"

    @property
    def key_configured(self) -> bool:
        return self._key_bytes is not None

    # ---- 加密原语 ---------------------------------------------------------

    def _effective_key(self) -> bytes:
        """当前密钥；dev 模式下用内置开发密钥兜底（开发闭环可用）。"""
        if self._key_bytes is not None:
            return self._key_bytes
        return _derive_key(_DEV_KEY)

    def _encrypt(self, payload: str) -> tuple[str, str]:
        nonce = secrets.token_bytes(_NONCE_BYTES)
        ct = AESGCM(self._effective_key()).encrypt(nonce, payload.encode("utf-8"), None)
        return base64.b64encode(nonce + ct).decode("ascii"), self.key_source

    def _decrypt(self, ciphertext: str) -> str:
        try:
            raw = base64.b64decode(ciphertext.encode("ascii"))
            nonce, ct = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
            plaintext = AESGCM(self._effective_key()).decrypt(nonce, ct, None)
            return plaintext.decode("utf-8")
        except (ValueError, InvalidTag, UnicodeDecodeError) as exc:
            raise VaultKeyMissingError(
                f"cannot decrypt mapping (missing or wrong key?): {exc} — "
                f"set {ENV_KEY} or run 'datasentry llm rotate-key'"
            ) from exc

    # ---- 会话管理 ---------------------------------------------------------

    @staticmethod
    def _session_id_for(mapping: dict[str, list[str]]) -> str:
        canonical = json.dumps(mapping, sort_keys=True, ensure_ascii=False)
        return f"pii_{hashlib.sha256(canonical.encode()).hexdigest()[:16]}"

    def save_mapping(self, mapping: dict[str, list[str]]) -> str:
        """加密映射落库（确定性 session_id：同映射复用同会话）。返回 session_id。"""
        session_id = self._session_id_for(mapping)
        ciphertext, version = self._encrypt(json.dumps(mapping, ensure_ascii=False))
        self._store.save_pii_mapping(session_id, ciphertext, key_version=version)
        return session_id

    def load_mapping(self, session_id: str) -> dict[str, list[str]]:
        """解密会话映射；缺密钥/解密失败抛 VaultKeyMissingError。"""
        row = self._store.get_pii_mapping(session_id)
        if row is None:
            raise KeyError(f"pii mapping session not found: {session_id}")
        plaintext = self._decrypt(row["ciphertext"])
        mapping: dict[str, list[str]] = json.loads(plaintext)
        return mapping

    # ---- 还原（审计） -----------------------------------------------------

    def restore_text(self, text: str, session_id: str) -> str:
        """把文本中的占位符还原为原文；每次还原写审计记录。"""
        mapping = self.load_mapping(session_id)
        restored = restore(text, mapping)
        self._audit("pii_restore", session_id, restored_count=len(mapping))
        return restored

    def restore_value(self, value: Any, session_id: str) -> Any:
        """递归还原任意值（str/dict/list）：用于规则 when.value 等结构化字段。"""
        mapping = self.load_mapping(session_id)
        restored = _restore_value(value, mapping)
        self._audit("pii_restore", session_id, restored_count=len(mapping))
        return restored

    # ---- 密钥轮换 ---------------------------------------------------------

    def rotate_key(self, new_key: str | None = None) -> dict[str, Any]:
        """用新密钥重加密全部映射；新密钥写入本地 key 文件。

        缺当前密钥（无法解密存量映射）抛 VaultKeyMissingError。
        返回 {"new_key", "rotated", "key_file"}。
        """
        rows = self._store.get_all_pii_mappings()
        new_material = new_key or secrets.token_urlsafe(32)
        new_bytes = _derive_key(new_material)
        old_bytes = self._effective_key()
        rotated = 0
        for row in rows:
            try:
                raw = base64.b64decode(row["ciphertext"].encode("ascii"))
                nonce, ct = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
                plaintext = AESGCM(old_bytes).decrypt(nonce, ct, None)
            except (ValueError, InvalidTag) as exc:
                raise VaultKeyMissingError(
                    f"cannot decrypt session {row['session_id']}: {exc}"
                ) from exc
            nonce2 = secrets.token_bytes(_NONCE_BYTES)
            ct2 = AESGCM(new_bytes).encrypt(nonce2, plaintext, None)
            new_ct = base64.b64encode(nonce2 + ct2).decode("ascii")
            self._store.save_pii_mapping(row["session_id"], new_ct, key_version="file")
            rotated += 1
        key_path = _key_file()
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(new_material + "\n", encoding="utf-8")
        key_path.chmod(0o600)
        self._key_material = new_material
        self._key_bytes = new_bytes
        self._key_source = "file"
        self._audit("pii_key_rotate", "-", rotated_count=rotated)
        return {"new_key": new_material, "rotated": rotated, "key_file": str(key_path)}

    # ---- 审计 -------------------------------------------------------------

    def _audit(self, task_type: str, session_id: str, **meta: Any) -> None:
        self._store.record_llm_invocation(
            LLMInvocation(
                invocation_id=f"inv_{uuid.uuid4().hex[:12]}",
                task_type=task_type,
                template_version="vault-1",
                provider_id="local-vault",
                model="-",
                input_tokens=0,
                output_tokens=0,
                cache_hit=False,
                latency_ms=0,
                status="ok",
                prompt_hash=session_id,
                masked_sample_count=0,
                pii_session_id=session_id if session_id != "-" else None,
                error_message=None,
                created_at=utcnow(),
            )
        )


def _restore_value(value: Any, mapping: dict[str, list[str]]) -> Any:
    if isinstance(value, str):
        return restore(value, mapping)
    if isinstance(value, list):
        return [_restore_value(item, mapping) for item in value]
    if isinstance(value, dict):
        return {k: _restore_value(v, mapping) for k, v in value.items()}
    return value


def format_mapping_summary(mapping: dict[str, list[str]], preview: int = 2) -> dict[str, Any]:
    """映射摘要（llm restore 展示）：kind → 条目数 + 掩码→原文预览。"""
    summary: dict[str, Any] = {}
    for kind, bucket in sorted(mapping.items()):
        previews = []
        for index, original in enumerate(bucket[:preview]):
            previews.append(
                {
                    "masked": f"{{{{REDACTED:{kind}:{index}}}}}",
                    "original": original,
                }
            )
        summary[kind] = {"count": len(bucket), "preview": previews}
    return summary

"""Step 27 测试：脱敏管线 + LLM Provider 抽象（38 章安全子集）。"""

from __future__ import annotations

import json

import httpx
import pytest

from datasentry.llm_providers import (
    LLMConfig,
    NullProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    create_provider,
    load_llm_config,
)
from datasentry_core.llm.provider import (
    LLMError,
    LLMNotConfiguredError,
    LLMRequest,
    LLMSchemaError,
)
from datasentry_core.models.profile import ColumnProfile, DatasetProfile
from datasentry_core.privacy.redactor import (
    mask_profile,
    mask_rows,
    redact,
    restore,
)
from datasentry_core.storage.store import MetadataStore

REQUEST = LLMRequest(task_type="explain", prompt="summarize: alice@example.com 13812345678")


# ---- 脱敏管线 --------------------------------------------------------------


def test_redact_email_phone_and_determinism() -> None:
    text = "contact alice@example.com or bob@corp.io, phone 13812345678"
    r1 = redact(text)
    r2 = redact(text)
    assert r1.masked == r2.masked  # 确定性
    assert "alice@example.com" not in r1.masked
    assert "13812345678" not in r1.masked
    assert "contact" in r1.masked  # 非 PII 保留
    assert r1.mapping["email"] == ["alice@example.com", "bob@corp.io"]
    assert r1.mapping["cn_phone"] == ["13812345678"]


def test_redact_chinese_id_and_ipv4() -> None:
    text = "id=11010519491231002X ip=192.168.1.1"
    r = redact(text)
    assert "11010519491231002X" not in r.masked
    assert "192.168.1.1" not in r.masked
    assert r.mapping["cn_id"] == ["11010519491231002X"]
    assert r.mapping["ipv4"] == ["192.168.1.1"]


def test_redact_url() -> None:
    text = "see https://example.com/path?token=abc123 for details"
    r = redact(text)
    assert "https://example.com" not in r.masked
    assert r.mapping["url"] == ["https://example.com/path?token=abc123"]


def test_redact_shared_mapping_consistent() -> None:
    text_a = "user a@x.com"
    text_b = "user a@x.com"
    r1 = redact(text_a)
    r2 = redact(text_b, r1.mapping)
    assert r1.mapping == r2.mapping
    assert r1.masked == r2.masked


def test_restore_roundtrip() -> None:
    text = "mail alice@example.com or call 13812345678"
    r = redact(text)
    restored = restore(r.masked, r.mapping)
    assert restored == text


def test_restore_unknown_placeholder_idempotent() -> None:
    masked = "value {{REDACTED:email:99}}"
    assert restore(masked, {"email": ["a@x.com"]}) == masked


def test_redact_no_pii_unchanged() -> None:
    text = "plain text with numbers 123 and words"
    r = redact(text)
    assert r.masked == text
    assert r.mapping == {}


def test_redact_overlap_email_inside_text() -> None:
    text = "prefix alice@example.com suffix"
    r = redact(text)
    assert "alice@example.com" not in r.masked
    assert r.mapping["email"] == ["alice@example.com"]


def test_mask_rows_batch() -> None:
    rows = [
        {"name": "alice@example.com", "age": 30},
        {"name": "bob@corp.io", "age": 25},
    ]
    masked, mapping = mask_rows(rows)
    assert masked[0]["name"] != rows[0]["name"]
    assert masked[0]["age"] == 30  # 非字符串原样保留
    assert masked[1]["name"] != rows[1]["name"]
    restored = [{"name": restore(r["name"], mapping), "age": r["age"]} for r in masked]
    assert restored == rows


def test_mask_profile_examples_and_categories() -> None:
    profile = DatasetProfile(
        dataset_id="d1",
        row_count=10,
        column_count=1,
        profiler_version="1.0",
        column_profiles={
            "email": ColumnProfile(
                dataset_id="d1",
                column_name="email",
                physical_type="VARCHAR",
                examples=["alice@example.com", "bob@corp.io"],
                top_categories=[("alice@example.com", 3), ("bob@corp.io", 2)],
            )
        },
    )
    masked, mapping = mask_profile(profile)
    col = masked.column_profiles["email"]
    assert "alice@example.com" not in " ".join(str(e) for e in col.examples)
    assert col.top_categories is not None
    assert col.top_categories[0][0] != "alice@example.com"
    assert col.top_categories[0][1] == 3  # 计数保留
    assert restore(col.examples[0], mapping) == "alice@example.com"


# ---- LLM Provider -----------------------------------------------------------


def test_null_provider_raises_not_configured() -> None:
    with pytest.raises(LLMNotConfiguredError):
        NullProvider().complete(REQUEST)


def test_openai_provider_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "gpt-test"
        assert body["messages"][1]["content"] == REQUEST.prompt
        assert request.headers["Authorization"] == "Bearer key-123"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok: issue found"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "model": "gpt-test",
            },
        )

    provider = OpenAICompatibleProvider(
        LLMConfig(provider="openai", model="gpt-test", base_url="http://x", api_key="key-123"),
        transport=httpx.MockTransport(handler),
    )
    response = provider.complete(REQUEST)
    assert response.text == "ok: issue found"
    assert response.input_tokens == 10
    assert response.output_tokens == 5
    assert response.status == "ok"


def test_openai_provider_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    provider = OpenAICompatibleProvider(
        LLMConfig(provider="openai", model="gpt-test", base_url="http://x", max_retries=0),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMError):
        provider.complete(REQUEST)


def test_openai_provider_malformed_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    provider = OpenAICompatibleProvider(
        LLMConfig(provider="openai", model="gpt-test", base_url="http://x", max_retries=0),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMSchemaError):
        provider.complete(REQUEST)


def test_ollama_provider_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "llama3"
        assert body["stream"] is False
        return httpx.Response(
            200,
            json={
                "model": "llama3",
                "response": "generated text",
                "prompt_eval_count": 8,
                "eval_count": 3,
            },
        )

    provider = OllamaProvider(
        LLMConfig(provider="ollama", model="llama3"), transport=httpx.MockTransport(handler)
    )
    response = provider.complete(REQUEST)
    assert response.text == "generated text"
    assert response.input_tokens == 8
    assert response.output_tokens == 3
    assert response.status == "ok"


def test_ollama_provider_missing_response_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "llama3"})

    provider = OllamaProvider(
        LLMConfig(provider="ollama", model="llama3"), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(LLMSchemaError):
        provider.complete(REQUEST)


def test_create_provider_defaults_null() -> None:
    assert isinstance(create_provider(LLMConfig()), NullProvider)


def test_create_provider_openai_and_ollama() -> None:
    openai = create_provider(LLMConfig(provider="openai", model="m", base_url="http://x"))
    assert isinstance(openai, OpenAICompatibleProvider)
    ollama = create_provider(LLMConfig(provider="ollama", model="m"))
    assert isinstance(ollama, OllamaProvider)


def test_create_provider_missing_model_raises() -> None:
    with pytest.raises(ValueError):
        create_provider(LLMConfig(provider="openai", base_url="http://x"))
    with pytest.raises(ValueError):
        create_provider(LLMConfig(provider="ollama"))


def test_load_llm_config_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATASENTRY_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("DATASENTRY_LLM_MODEL", "llama3.1")
    config = load_llm_config()
    assert config.provider == "ollama"
    assert config.model == "llama3.1"


def test_load_llm_config_invalid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATASENTRY_LLM_PROVIDER", "bogus")
    with pytest.raises(ValueError):
        load_llm_config()


# ---- LLM 审计持久化 ----------------------------------------------------------


def test_record_and_list_llm_invocations(tmp_path) -> None:
    from datasentry_core.models.llm import LLMInvocation

    store = MetadataStore(tmp_path / "m.db")
    inv = LLMInvocation(
        invocation_id="inv-1",
        task_type="explain",
        template_version="1.0",
        provider_id="ollama",
        model="llama3",
        input_tokens=10,
        output_tokens=5,
        cache_hit=True,
        latency_ms=120,
        status="ok",
        prompt_hash="abc123",
        masked_sample_count=4,
        injection_flagged=False,
    )
    store.record_llm_invocation(inv)
    rows = store.list_llm_invocations()
    assert len(rows) == 1
    loaded = rows[0]
    assert loaded.invocation_id == "inv-1"
    assert loaded.provider_id == "ollama"
    assert loaded.cache_hit is True
    assert loaded.status == "ok"
    assert loaded.masked_sample_count == 4
    assert loaded.created_at == inv.created_at


def test_llm_invocations_order_and_limit(tmp_path) -> None:
    from datasentry_core.models.llm import LLMInvocation

    store = MetadataStore(tmp_path / "m.db")
    for i in range(3):
        store.record_llm_invocation(
            LLMInvocation(
                invocation_id=f"inv-{i}",
                task_type="explain",
                template_version="1.0",
                provider_id="null",
                model="none",
                input_tokens=0,
                output_tokens=0,
                latency_ms=0,
                status="ok",
                prompt_hash="h",
            )
        )
    rows = store.list_llm_invocations(limit=2)
    assert len(rows) == 2
    assert rows[0].invocation_id == "inv-2"  # 倒序
    assert rows[1].invocation_id == "inv-1"

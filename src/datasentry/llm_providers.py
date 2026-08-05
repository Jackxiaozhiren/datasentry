"""LLM Provider 应用层实现（Step 27，ADR-027 分层）。

- `OpenAICompatibleProvider`：OpenAI Chat Completions 兼容 API
  （OpenAI / 任意兼容网关），Bearer 认证
- `OllamaProvider`：本地 Ollama /api/generate（默认零密钥部署）
- `NullProvider`：未配置时的显式降级（抛 LLMNotConfiguredError）
- `create_provider()`：按 LLMConfig 选择实现；配置来源：环境变量
  DATASENTRY_LLM_* 优先，其次全局 config.json（paths.global_data_dir）

安全：调用方必须先在 privacy/redactor 层完成脱敏再构造 prompt；
本层只负责传输与重试，不校验内容敏感性（38 章边界在调用方）。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from datasentry_core.llm.provider import (
    LLMError,
    LLMNotConfiguredError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMSchemaError,
)
from datasentry_core.storage.paths import global_data_dir

ProviderKind = Literal["null", "openai", "ollama"]


class LLMConfig(BaseModel):
    """LLM 提供方配置（env 优先，其次全局 config.json）。"""

    provider: ProviderKind = "null"
    model: str = ""
    base_url: str = ""
    api_key: str | None = None
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=5)
    default_max_tokens: int = Field(default=512, ge=1)


def _env_config() -> dict[str, Any]:
    """环境变量覆盖层（DATASENTRY_LLM_*）。"""
    prefix = "DATASENTRY_LLM_"
    overrides: dict[str, Any] = {}
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        field = key[len(prefix) :].lower()
        if field in {"provider", "model", "base_url", "api_key"}:
            overrides[field] = value
    if "provider" in overrides and overrides["provider"] not in (
        "null",
        "openai",
        "ollama",
    ):
        raise ValueError(f"unsupported DATASENTRY_LLM_PROVIDER: {overrides['provider']}")
    return overrides


def load_llm_config() -> LLMConfig:
    """合并配置：env > 全局 config.json > 默认（null）。"""
    base: dict[str, Any] = {}
    config_file = global_data_dir() / "config.json"
    if config_file.exists():
        try:
            base = json.loads(config_file.read_text(encoding="utf-8")).get("llm", {})
        except (json.JSONDecodeError, OSError, AttributeError):
            base = {}
    merged = {**base, **_env_config()}
    return LLMConfig.model_validate(merged)


class NullProvider:
    """未配置提供方：任何调用都显式失败（调用方按降级路径处理）。"""

    provider_id = "null"
    model = "none"

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise LLMNotConfiguredError(
            "LLM not configured: set DATASENTRY_LLM_PROVIDER / config.json (provider=openai|ollama)"
        )


class OpenAICompatibleProvider:
    """OpenAI Chat Completions 兼容提供方。"""

    provider_id = "openai"

    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None) -> None:
        if not config.model or not config.base_url:
            raise ValueError("openai provider requires model and base_url")
        self.config = config
        self.model = config.model
        self._transport = transport

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": "You are a data quality analysis assistant."},
                {"role": "user", "content": request.prompt},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        data, latency_ms = self._post(url, payload, headers)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMSchemaError(f"unexpected chat completion shape: {exc}") from exc
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            cache_hit=bool(data.get("system_fingerprint") or usage.get("cached_tokens")),
            model=data.get("model", self.config.model),
            status="ok",
            latency_ms=latency_ms,
        )

    def _post(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> tuple[dict[str, Any], int]:
        attempt = 0
        while True:
            started = time.monotonic()
            try:
                with httpx.Client(
                    timeout=self.config.timeout_seconds, headers=headers, transport=self._transport
                ) as client:
                    response = client.post(url, json=payload)
                response.raise_for_status()
                return response.json(), int((time.monotonic() - started) * 1000)
            except httpx.TimeoutException as exc:
                attempt += 1
                if attempt > self.config.max_retries:
                    raise LLMError(f"timeout after {attempt} attempts: {exc}") from exc
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                raise LLMError(f"provider error: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise LLMSchemaError(f"non-JSON response: {exc}") from exc


class OllamaProvider:
    """本地 Ollama /api/generate（默认 http://localhost:11434）。"""

    provider_id = "ollama"

    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None) -> None:
        if not config.model:
            raise ValueError("ollama provider requires model")
        self.config = config
        self.model = config.model
        self._transport = transport

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload = {
            "model": self.config.model,
            "prompt": request.prompt,
            "stream": False,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }
        base = self.config.base_url or "http://localhost:11434"
        url = f"{base.rstrip('/')}/api/generate"
        started = time.monotonic()
        try:
            with httpx.Client(
                timeout=self.config.timeout_seconds, transport=self._transport
            ) as client:
                response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise LLMError(f"ollama timeout: {exc}") from exc
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            raise LLMError(f"ollama error: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LLMSchemaError(f"non-JSON response: {exc}") from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        if "response" not in data:
            raise LLMSchemaError("ollama response missing 'response' field")
        text = data["response"]
        if not isinstance(text, str):
            raise LLMSchemaError("ollama response 'response' field not a string")
        return LLMResponse(
            text=text,
            input_tokens=int(data.get("prompt_eval_count", 0)),
            output_tokens=int(data.get("eval_count", 0)),
            model=data.get("model", self.config.model),
            status="ok",
            latency_ms=latency_ms,
        )


def create_provider(config: LLMConfig | None = None) -> LLMProvider:
    """按配置实例化提供方（未配置 → NullProvider 显式降级）。"""
    cfg = config or load_llm_config()
    if cfg.provider == "null":
        return NullProvider()
    if cfg.provider == "openai":
        return OpenAICompatibleProvider(cfg)
    return OllamaProvider(cfg)

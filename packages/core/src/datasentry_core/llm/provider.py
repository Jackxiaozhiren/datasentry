"""LLM Provider 抽象（Step 27，13.7/13.11 落地）。

core 层只定义接口与数据结构，**零网络依赖**；具体 HTTP 实现位于
应用层（src/datasentry/llm_providers.py，ADR-027 分层理由）。

契约：
- `LLMProvider.complete()` 必须同步返回 LLMResponse 或抛 LLMError；
  状态机归调用方（审计状态：ok/retried/schema_failed/failed/degraded）
- 所有入参文本假定**已脱敏**（38 章：AI 不接收未经授权的完整数据；
  脱敏由调用方在构造 prompt 前强制执行，见 privacy/redactor）
- 输出解析（JSON Schema 校验、说明生成）不在此层，归任务层
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

ResponseStatus = Literal["ok", "degraded"]


@runtime_checkable
class LLMProvider(Protocol):
    """LLM 提供方协议：仅同步 complete() 一个能力（MVP/V1 起点）。"""

    provider_id: str
    model: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...


@dataclass(frozen=True)
class LLMRequest:
    """单次补全请求（审计字段见 LLMInvocation，13.11）。"""

    task_type: str
    prompt: str
    template_version: str = "1.0"
    max_tokens: int = 512
    temperature: float = 0.0
    masked_sample_count: int = 0


@dataclass(frozen=True)
class LLMResponse:
    """单次补全响应；status='degraded' 表示提供方降级（如缓存未命中）。"""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit: bool = False
    model: str = ""
    status: ResponseStatus = "ok"
    latency_ms: int = 0


class LLMError(Exception):
    """LLM 调用失败（不可重试或重试耗尽后抛出）。"""


class LLMNotConfiguredError(LLMError):
    """未配置 LLM（NullProvider 直抛，调用方按降级路径处理）。"""


class LLMSchemaError(LLMError):
    """响应不符合输出 Schema（任务层校验失败前由本层先做形态校验）。"""

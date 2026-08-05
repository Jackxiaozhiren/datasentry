"""自然语言 → 规则候选生成（Step 28，14.4 + 38 章安全子集）。

流程（14.4 强制「先预运行 + 用户批准」）：
    1. propose：打开数据 → profiler 画像 → 脱敏（38 章，映射表不落盘）
       → 模板 prompt → llm_cache 命中检查 → provider 调用 → JSON
       严格校验（RuleCandidate）→ 语义校验（列存在/operator 白名单）
       → llm_invocations 审计（masked_sample_count / prompt_hash）→
       对每个候选跑 run_preflight（14.3），**只展示不落库**
    2. approve：用户确认后把 Rule 落库（source=llm_candidate）
    3. 未配置 LLM：抛 LLMNotConfiguredError，CLI 清晰提示且不崩溃

预算（13.9）：LLMRequest.max_tokens 由调用方预算参数控制，默认
不超过 ScanConfig.llm_budget_tokens（20000）。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from datasentry.client import _source_type_for_path
from datasentry.llm_providers import NullProvider, create_provider
from datasentry_core.connectors import DataSourceSpec, default_registry
from datasentry_core.engine.profiler import Profiler
from datasentry_core.llm.provider import (
    LLMError,
    LLMNotConfiguredError,
    LLMProvider,
    LLMRequest,
)
from datasentry_core.models.enums import RuleType, Severity
from datasentry_core.models.evidence import utcnow
from datasentry_core.models.llm import LLMInvocation
from datasentry_core.models.rules import Condition, Rule, RuleCandidate, RulePreflightReport
from datasentry_core.privacy.redactor import mask_profile
from datasentry_core.rules.engine import run_preflight
from datasentry_core.storage.store import MetadataStore

_TEMPLATE_VERSION = "1.0"
_MAX_CANDIDATES = 5

_OUTPUT_SCHEMA_PROMPT = (
    'Respond with ONLY strict JSON of shape: {"rules": [{"type": '
    '"value_range|allowed_values|regex|uniqueness|not_null|conditional_value|'
    'conditional_not_null|column_comparison|aggregate", '
    '"severity": "info|low|medium|high|critical", "description": str, '
    '"when": {"column": str, "operator": "equals|not_equals|gt|gte|lt|lte|in|not_in|'
    'not_null|is_null|matches|between|not_between", "value": str|number|list|null}, '
    '"columns": [str], "confidence": number 0..1, "paraphrase": str, "notes": [str]}]}'
    " Do not include markdown fences."
)


class _RuleJson(BaseModel):
    """LLM 输出行的严格 Schema（14.4 输出校验）。"""

    type: str
    severity: str
    description: str
    when: dict[str, Any]
    columns: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    paraphrase: str = ""
    notes: list[str] = Field(default_factory=list)


class _RulesJson(BaseModel):
    rules: list[_RuleJson] = Field(default_factory=list)


@dataclass
class ProposedRule:
    """候选 + 预运行报告（展示给用户审批；未落库）。"""

    candidate: RuleCandidate | None = None
    preflight: RulePreflightReport | None = None
    rejected_reason: str | None = None


@dataclass
class ProposeResult:
    """propose 输出：候选列表 + 元信息。"""

    rules: list[ProposedRule] = field(default_factory=list)
    masked_sample_count: int = 0
    cache_hit: bool = False
    llm_error: str | None = None


def _new_rule_id() -> str:
    return f"rule_{uuid.uuid4().hex[:12]}"


class RuleProposalService:
    """自然语言规则提案服务（绑定工作区 store + 可注入 provider）。"""

    def __init__(
        self,
        store: MetadataStore,
        provider: LLMProvider | None = None,
        project: str | None = None,
    ) -> None:
        self._store = store
        self._project = project
        self._provider = provider

    # ---- propose：NL → 候选（脱敏 → LLM → 校验 → 预运行） ---------------

    def propose(self, description: str, path: str, budget_tokens: int = 20000) -> ProposeResult:
        provider = self._provider or create_provider()
        if isinstance(provider, NullProvider):
            raise LLMNotConfiguredError(
                "LLM not configured: set DATASENTRY_LLM_PROVIDER=openai|ollama "
                "(see `datasentry llm status`)"
            )
        handle = self._open(path)
        try:
            profile = Profiler(handle, dataset_id="rule-proposal").profile()
            self._fill_examples(profile, handle)
            masked_profile, mapping = mask_profile(profile)
            prompt = self._build_prompt(description, masked_profile)
            masked_count = sum(len(bucket) for bucket in mapping.values())
            return self._complete(provider, prompt, masked_count, budget_tokens, path)
        finally:
            handle.close()

    @staticmethod
    def _fill_examples(profile: Any, handle: Any, n: int = 5) -> None:
        """从数据源采样填充 examples（38 章：随后整体脱敏后才入 prompt）。"""
        sample = handle.read_sample(n=n)
        table = sample.table
        for column_name, column in profile.column_profiles.items():
            if column_name not in table.column_names:
                continue
            values: list[str] = []
            for row in table.select([column_name]).to_pylist():
                value = row[column_name]
                if value is None:
                    continue
                text = str(value)
                if text and text not in values:
                    values.append(text)
                if len(values) >= 3:
                    break
            column.examples = values

    def _open(self, path: str) -> Any:
        source_type = _source_type_for_path(Path(path))
        if source_type is None:
            raise ValueError(f"unsupported data source format: {path}")
        return default_registry().open(
            DataSourceSpec(
                source_type=source_type,
                path=Path(path),
                options={"dataset_id": "rule-proposal"},
            )
        )

    def _complete(
        self,
        provider: LLMProvider,
        prompt: str,
        masked_count: int,
        budget_tokens: int,
        path: str,
    ) -> ProposeResult:
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        cache_hit = False
        cached = self._store.get_llm_cache(prompt_hash)
        if cached is not None:
            cache_hit = True
            raw_text = cached
        else:
            request = LLMRequest(
                task_type="rule_candidate",
                prompt=prompt,
                template_version=_TEMPLATE_VERSION,
                max_tokens=budget_tokens,
                masked_sample_count=masked_count,
            )
            try:
                response = provider.complete(request)
            except LLMError as exc:
                self._record(
                    provider,
                    request,
                    status="failed",
                    prompt_hash=prompt_hash,
                    error=str(exc),
                )
                return ProposeResult(llm_error=str(exc), masked_sample_count=masked_count)
            raw_text = response.text
            self._record(
                provider,
                request,
                status="ok",
                prompt_hash=prompt_hash,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cache_hit=response.cache_hit,
                latency_ms=response.latency_ms,
                masked_sample_count=masked_count,
            )
            self._store.put_llm_cache(prompt_hash, raw_text)

        rules = self._parse_and_validate(raw_text, path)
        return ProposeResult(rules=rules, masked_sample_count=masked_count, cache_hit=cache_hit)

    def _record(
        self,
        provider: LLMProvider,
        request: LLMRequest,
        *,
        status: Literal["ok", "retried", "schema_failed", "failed", "degraded"],
        prompt_hash: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_hit: bool = False,
        latency_ms: int = 0,
        masked_sample_count: int = 0,
        error: str | None = None,
    ) -> None:
        self._store.record_llm_invocation(
            LLMInvocation(
                invocation_id=f"inv_{uuid.uuid4().hex[:12]}",
                task_type=request.task_type,
                template_version=request.template_version,
                provider_id=provider.provider_id,
                model=provider.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_hit=cache_hit,
                latency_ms=latency_ms,
                status=status,
                prompt_hash=prompt_hash,
                masked_sample_count=masked_sample_count,
                injection_flagged=False,
                error_message=error,
                created_at=utcnow(),
            )
        )

    # ---- 解析与校验 -------------------------------------------------------

    def _parse_and_validate(self, raw_text: str, path: str) -> list[ProposedRule]:
        try:
            parsed = _RulesJson.model_validate_json(raw_text)
        except ValidationError as exc:
            return [ProposedRule(rejected_reason=f"output schema failed: {exc}")]
        results: list[ProposedRule] = []
        for item in parsed.rules[:_MAX_CANDIDATES]:
            rule = self._to_rule(item)
            if rule is None:
                results.append(
                    ProposedRule(rejected_reason="rule shape invalid (type/severity/operator)")
                )
                continue
            handle = None
            try:
                handle = self._open(path)
                preflight = run_preflight(rule, handle)
            except Exception as exc:  # 预运行失败不影响其余候选
                results.append(ProposedRule(rejected_reason=str(exc)))
                continue
            finally:
                if handle is not None:
                    handle.close()
            results.append(
                ProposedRule(
                    candidate=RuleCandidate(
                        rule=rule,
                        paraphrase=item.paraphrase,
                        confidence=item.confidence,
                        notes=item.notes,
                    ),
                    preflight=preflight,
                )
            )
            rule.enabled = False  # 候选未批准不生效（14.4）
            self._store.save_rule(rule)  # 候选落库待批准
        return results

    def _to_rule(self, item: _RuleJson) -> Rule | None:
        try:
            rule_type = RuleType(item.type)
            severity = Severity(item.severity)
            condition = Condition.model_validate(item.when)
        except (ValueError, ValidationError):
            return None
        return Rule(
            id=_new_rule_id(),
            type=rule_type,
            severity=severity,
            description=item.description,
            when=condition,
            columns=item.columns or [condition.column],
            source="llm_candidate",
        )

    # ---- 审批落库 ---------------------------------------------------------

    def approve(self, rule_id: str) -> Rule | None:
        """用户批准：候选转正（enabled 0→1，14.4 批准语义）。

        候选在 propose 时已落库（enabled=0，未生效）；approve 只是
        翻转 enabled，不重新构造规则（保持审计一致性）。
        """
        return self._store.activate_rule(rule_id)

    # ---- prompt 模板 ------------------------------------------------------

    def _build_prompt(self, description: str, profile: Any) -> str:
        columns_desc: list[str] = []
        for column in profile.column_profiles.values():
            parts = [
                f"- {column.column_name} ({column.physical_type})",
                f"null={column.null_ratio:.2f} unique={column.unique_ratio:.2f}",
            ]
            if column.examples:
                parts.append(f"examples={column.examples[:3]}")
            columns_desc.append(" ".join(parts))
        return (
            "You are a data quality rule designer for a dataset.\n"
            "The dataset profile (sensitive values already masked as "
            "{{REDACTED:kind:n}} placeholders) is:\n"
            + "\n".join(columns_desc)
            + f"\n\nUser request: {description}\n\n"
            + _OUTPUT_SCHEMA_PROMPT
        )

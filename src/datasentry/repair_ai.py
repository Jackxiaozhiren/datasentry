"""Issue → AI 修复候选（Step 44，15 章扩展：规则引擎兜底 + 参数/理由生成）。

流程（与 rules_ai 同构，38 章安全子集）：
    1. propose：读取 issue → 打开数据 → profiler 画像 + 样例 → 整体脱敏
       （mask_profile，映射表不落盘）→ 模板 prompt（含 evidence 摘要，
       str 值就地遮蔽）→ llm_cache 命中检查 → provider 调用 → JSON
       严格校验（_RepairJson）→ 上下文白名单校验（按 detector_ids
       派生允许操作，AI 只能在规则引擎同款操作集内选择，杜绝任意
       SQL/表达式注入）→ llm_invocations 审计 → 候选落库
       （save_repair_proposal，status=PROPOSED）
    2. 未配置 LLM：抛 LLMNotConfiguredError，CLI 清晰提示且不崩溃

参数面：仅 clip_value 接受数值 lower/upper（其余操作必须空参数，
与 RepairEngine._after_expr 支持面一致——AI 不引入新表达式）。
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from datasentry.llm_providers import NullProvider, create_provider
from datasentry_core.connectors import DataSourceSpec, default_registry
from datasentry_core.detectors.common import quote_ident
from datasentry_core.engine.profiler import Profiler
from datasentry_core.llm.provider import (
    LLMError,
    LLMNotConfiguredError,
    LLMProvider,
    LLMRequest,
)
from datasentry_core.models.enums import RepairOperation, RepairProposalStatus, RiskLevel
from datasentry_core.models.evidence import utcnow
from datasentry_core.models.llm import LLMInvocation
from datasentry_core.models.repair import RepairProposal
from datasentry_core.privacy.redactor import mask_profile
from datasentry_core.repair.engine import _after_expr
from datasentry_core.storage.store import MetadataStore

_TEMPLATE_VERSION = "1.0"

# AI 可选操作集（与 RepairEngine._PROPOSAL_MAP 同款：上下文感知，
# 只允许修复该 issue 检测器对应的操作）
_CONTEXT_OPS: dict[str, set[RepairOperation]] = {
    "leading_or_trailing_whitespace": {RepairOperation.TRIM_WHITESPACE},
    "inconsistent_case": {RepairOperation.NORMALIZE_CASE},
    "suspicious_missing_token": {RepairOperation.REPLACE_MISSING_TOKEN},
    "invalid_date": {RepairOperation.SET_NULL},
    "impossible_date": {RepairOperation.SET_NULL},
    "iqr_outlier": {RepairOperation.CLIP_VALUE},
    "percentile_outlier": {RepairOperation.CLIP_VALUE},
    "modified_zscore": {RepairOperation.CLIP_VALUE},
}
_CLIP_ISSUE_TYPES = frozenset({"iqr_outlier", "percentile_outlier", "modified_zscore"})


class _RepairJson(BaseModel):
    """LLM 输出的严格 Schema（与 14.4 同风格输出校验）。"""

    operation: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


@dataclass
class RepairProposalResult:
    """propose 输出：候选（含拒绝原因）+ 元信息。"""

    proposal: RepairProposal | None = None
    rejected_reason: str | None = None
    masked_sample_count: int = 0
    cache_hit: bool = False
    llm_error: str | None = None


def _new_proposal_id() -> str:
    return f"prop_{uuid.uuid4().hex[:12]}"


def _redact_str(value: Any) -> Any:
    """evidence 摘要就地遮蔽：str 值替换为占位符，数值/布尔保留。"""
    if isinstance(value, str):
        return "{{REDACTED}}"
    return value


class AIRepairService:
    """AI 修复候选服务（绑定工作区 store + 可注入 provider）。"""

    def __init__(
        self,
        store: MetadataStore,
        provider: LLMProvider | None = None,
        project: str | None = None,
    ) -> None:
        self._store = store
        self._project = project
        self._provider = provider

    # ---- propose：issue → AI 修复候选（脱敏 → LLM → 校验 → 落库） --------

    def propose(self, issue_id: str, path: str, budget_tokens: int = 20000) -> RepairProposalResult:
        provider = self._provider or create_provider()
        if isinstance(provider, NullProvider):
            raise LLMNotConfiguredError(
                "LLM not configured: set DATASENTRY_LLM_PROVIDER=openai|ollama "
                "(see `datasentry llm status`)"
            )
        issue = self._store.get_issue_by_id(issue_id)
        if issue is None:
            raise KeyError(f"issue not found: {issue_id}")
        allowed_ops = self._allowed_operations(issue)
        if not allowed_ops:
            return RepairProposalResult(rejected_reason="no AI-repairable detector in issue")
        handle = self._open(path)
        try:
            profile = Profiler(handle, dataset_id="ai-repair").profile()
            self._fill_examples(profile, handle)
            masked_profile, mapping = mask_profile(profile)
            prompt = self._build_prompt(issue, masked_profile, allowed_ops)
            masked_count = sum(len(bucket) for bucket in mapping.values())
            return self._complete(
                provider, issue, prompt, masked_count, budget_tokens, allowed_ops, path
            )
        finally:
            handle.close()

    @staticmethod
    def _allowed_operations(issue: Any) -> set[RepairOperation]:
        ops: set[RepairOperation] = set()
        for detector_id in issue.detector_ids:
            ops |= _CONTEXT_OPS.get(detector_id, set())
        return ops

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
        from datasentry.client import _source_type_for_path

        source_type = _source_type_for_path(Path(path))
        if source_type is None:
            raise ValueError(f"unsupported data source format: {path}")
        return default_registry().open(
            DataSourceSpec(
                source_type=source_type,
                path=Path(path),
                options={"dataset_id": "ai-repair"},
            )
        )

    def _complete(
        self,
        provider: LLMProvider,
        issue: Any,
        prompt: str,
        masked_count: int,
        budget_tokens: int,
        allowed_ops: set[RepairOperation],
        path: str,
    ) -> RepairProposalResult:
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        cache_hit = False
        cached = self._store.get_llm_cache(prompt_hash)
        if cached is not None:
            cache_hit = True
            raw_text = cached
        else:
            request = LLMRequest(
                task_type="repair_candidate",
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
                return RepairProposalResult(llm_error=str(exc), masked_sample_count=masked_count)
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

        proposal, reason = self._parse_and_validate(raw_text, issue, allowed_ops, path)
        if proposal is not None:
            self._store.save_repair_proposal(proposal)
        return RepairProposalResult(
            proposal=proposal,
            rejected_reason=reason,
            masked_sample_count=masked_count,
            cache_hit=cache_hit,
        )

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

    def _parse_and_validate(
        self,
        raw_text: str,
        issue: Any,
        allowed_ops: set[RepairOperation],
        path: str,
    ) -> tuple[RepairProposal | None, str | None]:
        try:
            parsed = _RepairJson.model_validate_json(raw_text)
        except ValidationError as exc:
            return None, f"output schema failed: {exc}"
        try:
            operation = RepairOperation(parsed.operation)
        except ValueError:
            return None, f"operation not in allowed set: {parsed.operation}"
        if operation not in allowed_ops:
            return None, f"operation not allowed for this issue: {operation.value}"
        if not parsed.rationale.strip():
            return None, "rationale is empty"
        if not issue.columns:
            return None, "issue has no target columns"
        parameters = self._validate_parameters(operation, parsed.parameters, issue)
        if parameters is None:
            return None, f"invalid parameters for operation: {operation.value}"
        columns = list(issue.columns)
        handle = None
        try:
            handle = self._open(path)
            affected = self._affected_rows(handle, operation, columns, parameters)
        except Exception as exc:
            return None, f"affected-rows estimation failed: {exc}"
        finally:
            if handle is not None:
                handle.close()
        if affected <= 0:
            return None, "no rows would change"
        proposal = RepairProposal(
            proposal_id=_new_proposal_id(),
            issue_id=issue.id,
            issue_type=operation.value,
            operation=operation,
            target_columns=columns,
            parameters=parameters,
            rationale=parsed.rationale.strip(),
            evidence_ids=[e.evidence_id for e in issue.evidence],
            risk_level=self._risk_level(operation),
            reversibility=(
                "partially_reversible"
                if operation == RepairOperation.SET_NULL
                else "fully_reversible"
            ),
            estimated_rows_changed=affected,
            status=RepairProposalStatus.PROPOSED,
        )
        return proposal, None

    def _validate_parameters(
        self,
        operation: RepairOperation,
        parameters: dict[str, Any],
        issue: Any,
    ) -> dict[str, Any] | None:
        """参数白名单：仅 clip_value 接受数值边界，其余必须空参数。"""
        if operation == RepairOperation.CLIP_VALUE:
            try:
                lower = float(parameters["lower"])
                upper = float(parameters["upper"])
            except (KeyError, TypeError, ValueError):
                return None
            if not math.isfinite(lower) or not math.isfinite(upper) or lower > upper:
                return None
            return {"lower": lower, "upper": upper}
        if parameters:
            return None
        return {}

    @staticmethod
    def _risk_level(operation: RepairOperation) -> RiskLevel:
        if operation in (
            RepairOperation.SET_NULL,
            RepairOperation.CLIP_VALUE,
            RepairOperation.REPLACE_MISSING_TOKEN,
        ):
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    @staticmethod
    def _affected_rows(
        handle: Any, operation: RepairOperation, columns: list[str], params: dict[str, Any]
    ) -> int:
        """估算受影响行数（与 RepairEngine._affected_rows 同 SQL，直接对 handle 执行）。"""
        total = 0
        for column in columns:
            q = quote_ident(column)
            expr = _after_expr(operation, column, params)
            table = handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data WHERE {q} IS DISTINCT FROM ({expr})"
            ).table
            total += int(table.column("n").to_pylist()[0])
        return total

    # ---- prompt 模板 ------------------------------------------------------

    def _build_prompt(
        self,
        issue: Any,
        profile: Any,
        allowed_ops: set[RepairOperation],
    ) -> str:
        columns_desc: list[str] = []
        for column in profile.column_profiles.values():
            parts = [
                f"- {column.column_name} ({column.physical_type})",
                f"null={column.null_ratio:.2f} unique={column.unique_ratio:.2f}",
            ]
            if column.examples:
                parts.append(f"examples={column.examples[:3]}")
            columns_desc.append(" ".join(parts))
        evidence: dict[str, Any] = {}
        for e in issue.evidence:
            evidence[e.evidence_id] = {k: _redact_str(v) for k, v in e.data.items()}
        allowed_names = [op.value for op in sorted(allowed_ops, key=lambda op: op.value)]
        return (
            "You are a data repair advisor. Suggest a repair for a detected "
            "data quality issue. Sensitive values are masked as "
            "{{REDACTED:kind:n}} placeholders.\n"
            "Dataset column profile:\n"
            + "\n".join(columns_desc)
            + "\n\nIssue:\n"
            + json.dumps(
                {
                    "detector_ids": list(issue.detector_ids),
                    "issue_type": issue.issue_type,
                    "severity": issue.severity,
                    "columns": list(issue.columns),
                    "evidence": evidence,
                },
                ensure_ascii=False,
            )
            + f"\n\nChoose operation ONLY from: {allowed_names}\n"
            + _OUTPUT_SCHEMA_PROMPT
        )


_OUTPUT_SCHEMA_PROMPT = (
    'Respond with ONLY strict JSON of shape: {"operation": str, "parameters": {}, '
    '"rationale": str} '
    "(parameters must be empty except clip_value which needs numeric "
    '"lower" and "upper"). Do not include markdown fences.'
)

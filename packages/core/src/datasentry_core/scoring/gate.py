"""质量门禁（Step 12 / 22 章场景 C + ADR-014）。

Gate 在扫描结果上求值（MVP 无契约规则执行，ADR-004 归 V1）：

- fail_on：严重度达到列表内任一等级（默认 [critical]）即视为失败项
- maximum_failed_rows_ratio：失败项受影响行比例（max over issues，上限近似）
  超过阈值 → 失败（默认 0.01）
- maximum_issues：按严重度的 Issue 数上限（提供时生效）
- require_repair_validation：MVP 不支持，设为 True 时 Gate 直接失败（显式拒绝静默忽略）

CLI 通过 `scan --fail-on SEV --max-failure-ratio R` 激活，失败退出码 1
（EXIT_GATE_FAILED，22 章退出码契约）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from datasentry_core.models.contract import QualityGate
from datasentry_core.models.issue import Issue


class GateResult(BaseModel):
    """门禁结果：passed + 失败明细（供 CLI/报告展示）。"""

    passed: bool
    failed_issues: list[str] = Field(default_factory=list)  # issue ids
    reasons: list[str] = Field(default_factory=list)

    @property
    def failed_count(self) -> int:
        return len(self.failed_issues)


class QualityGateEvaluator:
    """质量门禁求值器（纯函数式，无状态）。"""

    def evaluate(self, issues: list[Issue], gate: QualityGate) -> GateResult:
        if gate.require_repair_validation:
            return GateResult(
                passed=False,
                reasons=["require_repair_validation is not supported in MVP (V1)"],
            )
        failed: list[Issue] = []
        reasons: list[str] = []
        severe = [i for i in issues if i.severity in set(gate.fail_on)]
        if severe:
            worst_ratio = max(i.affected_ratio for i in severe)
            if worst_ratio > gate.maximum_failed_rows_ratio:
                failed.extend(severe)
                reasons.append(
                    f"{len(severe)} issues at {[s.value for s in gate.fail_on]} severity "
                    f"affect {worst_ratio:.4f} of rows, exceeding "
                    f"maximum_failed_rows_ratio={gate.maximum_failed_rows_ratio}"
                )
        if gate.maximum_issues:
            for severity, limit in gate.maximum_issues.items():
                count = sum(1 for i in issues if i.severity is severity)
                if count > limit:
                    failed.extend(i for i in issues if i.severity is severity)
                    reasons.append(
                        f"{count} {severity.value} issues exceed maximum_issues limit {limit}"
                    )
        return GateResult(
            passed=not failed,
            failed_issues=[i.id for i in failed],
            reasons=reasons,
        )

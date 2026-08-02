"""跨字段规则检测器（11.10）：安全表达式求值（读操作子集，ADR-015）。

内置规则按列名语义对自动绑定（MVP 确定性子集，ADR-004 契约 DSL 归 V1）：
- {name}_start / {name}_begin / {name}_from  <=  {name}_end / {name}_finish / {name}_to
- {name}_min / {name}_lower  <=  {name}_max / {name}_upper
仅同类型（数值/日期）配对；表达式须通过 AST 白名单校验。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from datasentry_core.detectors.base import DetectionContext
from datasentry_core.detectors.common import (
    DetectorBase,
    make_candidate,
    make_evidence,
    quote_ident,
)
from datasentry_core.detectors.safe_eval import ExpressionSecurityError, SafeExpressionEvaluator
from datasentry_core.models.detector import DetectorCapabilities, IssueCandidate
from datasentry_core.models.enums import EvidenceType, QualityDimension, Severity

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PREFIX_PAIRS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("start", "begin", "from"), ("end", "finish", "to")),
    (("min", "lower"), ("max", "upper")),
)
_MAX_EXAMPLES = 20


@dataclass(frozen=True)
class BoundRule:
    """绑定到具体列对的一条规则。"""

    rule_id: str
    expression: str
    left: str
    right: str


class CrossFieldRuleDetector(DetectorBase):
    """跨字段规则（11.10）：start<=end / min<=max 等内置语义对，行级安全求值。"""

    detector_id = "cross_field_rule"
    display_name = "Cross-Field Rule"
    description = "Evaluates whitelisted cross-column expressions (e.g. start <= end)."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)
    default_thresholds: ClassVar[dict[str, float | int | str]] = {"timeout_s": 10.0}

    def __init__(self, timeout_s: float = 10.0) -> None:
        self._evaluator = SafeExpressionEvaluator(timeout_s=timeout_s)

    def supports(self, context: DetectionContext) -> bool:
        return bool(self._bind_rules(context))

    def _bind_rules(self, context: DetectionContext) -> list[BoundRule]:
        rules: list[BoundRule] = []
        physical = {c.name: c.physical_type.upper() for c in context.handle.schema().columns}
        for left_prefixes, right_prefixes in _PREFIX_PAIRS:
            for left in context.columns:
                m = re.match(rf"^({'|'.join(left_prefixes)})_(.+)$", left)
                if not m or left not in physical:
                    continue
                name = m.group(2)
                for right in context.columns:
                    r = re.match(rf"^({'|'.join(right_prefixes)})_{name}$", right)
                    if not r or right == left:
                        continue
                    if not (_IDENT_RE.fullmatch(left) and _IDENT_RE.fullmatch(right)):
                        continue
                    if self._same_family(physical.get(left), physical.get(right)):
                        rules.append(
                            BoundRule(
                                rule_id=f"{name}_range_order",
                                expression=f"{left} <= {right}",
                                left=left,
                                right=right,
                            )
                        )
        return rules

    def _same_family(self, left: str | None, right: str | None) -> bool:
        if left is None or right is None:
            return False
        if left == right:
            return True
        numeric = {
            "INTEGER",
            "BIGINT",
            "SMALLINT",
            "TINYINT",
            "FLOAT",
            "DOUBLE",
            "DECIMAL",
            "HUGEINT",
        }
        temporal = {"DATE", "TIMESTAMP", "TIME"}
        return (left in numeric and right in numeric) or (left in temporal and right in temporal)

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for rule in self._bind_rules(context):
            try:
                self._evaluator.validate(rule.expression)
            except ExpressionSecurityError:
                # 内置规则不应失败；失败视为规则无效并跳过（不中断扫描）
                continue
            rows = self._load_rows(context, rule)
            result = self._evaluator.evaluate(
                rule.expression, [rule.left, rule.right], [r[:2] for r in rows]
            )
            if result.timed_out:
                continue
            bad_rows = [r[2] for r, ok in zip(rows, result.values, strict=True) if ok is False]
            if not bad_rows:
                continue
            candidates.append(
                make_candidate(
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    context=context,
                    issue_type="cross_field_violation",
                    columns=[rule.left, rule.right],
                    affected_count=len(bad_rows),
                    affected_rows=[str(i) for i in bad_rows[:_MAX_EXAMPLES]],
                    evidence=[
                        make_evidence(
                            detector_id=self.detector_id,
                            detector_version=self.detector_version,
                            evidence_type=EvidenceType.CONSTRAINT_VIOLATION,
                            description=(f"{len(bad_rows)} rows violate {rule.expression}"),
                            data={"rule_id": rule.rule_id, "expression": rule.expression},
                        )
                    ],
                    raw_score=len(bad_rows),
                    confidence=0.95,
                    severity=Severity.MEDIUM,
                )
            )
        return candidates

    def _load_rows(
        self, context: DetectionContext, rule: BoundRule
    ) -> list[tuple[object, object, int]]:
        """取左右列值 + 行号（1 基，ROW_NUMBER 稳定序）。"""
        ql, qr = quote_ident(rule.left), quote_ident(rule.right)
        table = context.handle.sql_aggregate(
            f"SELECT {ql} AS l, {qr} AS r, ROW_NUMBER() OVER () AS rn FROM data"
        ).table
        lefts = table.column("l").to_pylist()
        rights = table.column("r").to_pylist()
        rns = table.column("rn").to_pylist()
        return list(zip(lefts, rights, rns, strict=True))

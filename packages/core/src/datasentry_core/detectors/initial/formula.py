"""公式注入检测器（11.7）：值以 = + - @ \\t \\r 开头（导出风险）。"""

from __future__ import annotations

from typing import ClassVar

from datasentry_core.detectors.base import DetectionContext
from datasentry_core.detectors.initial.common import (
    DetectorBase,
    make_candidate,
    make_evidence,
    quote_ident,
    quote_literal,
    string_columns,
)
from datasentry_core.models.detector import DetectorCapabilities, IssueCandidate
from datasentry_core.models.enums import EvidenceType, QualityDimension, Severity

FORMULA_INJECTION_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")


class FormulaInjectionDetector(DetectorBase):
    """公式注入（11.7）：CSV 导出时可能被电子表格执行。"""

    detector_id = "suspicious_formula_injection"
    display_name = "Formula Injection"
    description = "Reports values starting with spreadsheet formula prefixes."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        prefixes = ", ".join(
            f"chr({ord(p)})" if ord(p) < 32 else quote_literal(p)
            for p in FORMULA_INJECTION_PREFIXES
        )
        for col in string_columns(context):
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data WHERE left({q}, 1) IN ({prefixes})"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="suspicious_formula_injection",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.PATTERN_MATCH,
                                description=f"{count} values start with formula-injection prefixes",
                                data={"count": count},
                            )
                        ],
                        raw_score=count,
                        confidence=0.95,
                        severity=Severity.LOW,
                    )
                )
        return candidates

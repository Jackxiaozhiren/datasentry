"""缺失类检测器（11.3）：excessive_null_rate / suspicious_missing_token。"""

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

_MISSING_TOKENS = (
    "na",
    "n/a",
    "null",
    "none",
    "-",
    "?",
    "unknown",
    "missing",
    "todo",
    "tbd",
    "n.a.",
)


class ExcessiveNullRateDetector(DetectorBase):
    """缺失率过高（11.3）：null_rate > threshold（默认 0.05，critical 列收紧至 0.01）。"""

    detector_id = "excessive_null_rate"
    display_name = "Excessive Null Rate"
    description = "Reports columns whose null ratio exceeds the configured threshold."
    quality_dimension = QualityDimension.COMPLETENESS
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)
    default_thresholds: ClassVar[dict[str, float | int | str]] = {
        "threshold": 0.05,
        "critical_threshold": 0.01,
    }

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        # MVP 固定默认阈值；可配置化经 YAML 契约（Step 13）与 detector options 接入
        threshold = 0.05
        candidates: list[IssueCandidate] = []
        for col in context.columns:
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n, sum({q} IS NULL) AS nulls FROM data"
            ).table
            total = int(table.column("n").to_pylist()[0])
            nulls = int(table.column("nulls").to_pylist()[0])
            if total <= 0:
                continue
            ratio = nulls / total
            if ratio > threshold:
                severity = (
                    Severity.HIGH
                    if ratio > 0.3
                    else Severity.MEDIUM
                    if ratio > 0.1
                    else Severity.LOW
                )
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="excessive_null_rate",
                        columns=[col],
                        affected_count=nulls,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.STATISTICAL_MEASURE,
                                description=f"null_ratio={ratio:.4f} exceeds threshold={threshold}",
                                data={
                                    "null_ratio": round(ratio, 6),
                                    "threshold": threshold,
                                    "nulls": nulls,
                                    "total": total,
                                },
                            )
                        ],
                        raw_score=ratio,
                        confidence=0.95,
                        severity=severity,
                    )
                )
        return candidates


class SuspiciousMissingTokenDetector(DetectorBase):
    """特殊缺失标记（11.3）：标记占比 > 0.005。"""

    detector_id = "suspicious_missing_token"
    display_name = "Suspicious Missing Token"
    description = "Reports values that are common stand-ins for missing data."
    quality_dimension = QualityDimension.COMPLETENESS
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)
    default_thresholds: ClassVar[dict[str, float | int | str]] = {"ratio_threshold": 0.005}

    def supports(self, context: DetectionContext) -> bool:
        return bool(string_columns(context))

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        threshold = 0.005
        candidates: list[IssueCandidate] = []
        for col in string_columns(context):
            q = quote_ident(col)
            tokens = ", ".join(quote_literal(t) for t in _MISSING_TOKENS)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data WHERE lower(trim({q})) IN ({tokens})"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count == 0:
                continue
            table_total = context.handle.sql_aggregate("SELECT count(*) AS n FROM data").table
            total = int(table_total.column("n").to_pylist()[0])
            ratio = count / total if total else 0.0
            if ratio > threshold:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="suspicious_missing_token",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.PATTERN_MATCH,
                                description=f"{count} values are missing stand-ins ({ratio:.4f})",
                                data={"count": count, "ratio": round(ratio, 6)},
                            )
                        ],
                        raw_score=ratio,
                        confidence=0.9,
                        severity=Severity.LOW,
                    )
                )
        return candidates

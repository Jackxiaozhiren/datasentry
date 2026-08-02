"""类别异常检测器（11.6）：suspicious_placeholder / rare_category / category_explosion。"""

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

_PLACEHOLDERS = ("test", "xxx", "foo", "abc", "12345", "dummy", "example")
_IDENTIFIER_HINTS = ("_id", "id_", " id", "key", "code", "uuid", "hash")


class SuspiciousPlaceholderDetector(DetectorBase):
    """占位符值（11.6）：匹配即报告。"""

    detector_id = "suspicious_placeholder"
    display_name = "Suspicious Placeholder"
    description = "Reports values like 'test', 'xxx', 'foo' that are likely placeholders."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)
    default_thresholds: ClassVar[dict[str, float | int | str]] = {
        "placeholder_count": len(_PLACEHOLDERS)
    }

    def supports(self, context: DetectionContext) -> bool:
        return bool(string_columns(context))

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in string_columns(context):
            q = quote_ident(col)
            tokens = ", ".join(quote_literal(t) for t in _PLACEHOLDERS)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data WHERE lower(trim({q})) IN ({tokens})"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="suspicious_placeholder",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.PATTERN_MATCH,
                                description=f"{count} placeholder values",
                                data={"count": count},
                            )
                        ],
                        raw_score=count,
                        confidence=0.95,
                        severity=Severity.LOW,
                    )
                )
        return candidates


class RareCategoryDetector(DetectorBase):
    """稀有类别（11.6）：频数 < 5 且占比 < 0.001；仅高频字段（distinct 2~1000）启用。"""

    detector_id = "rare_category"
    display_name = "Rare Category"
    description = "Reports categories with very low frequency in high-cardinality fields."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)
    default_thresholds: ClassVar[dict[str, float | int | str]] = {
        "min_count": 5,
        "min_ratio": 0.001,
    }

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in context.columns:
            q = quote_ident(col)
            meta = context.handle.sql_aggregate(
                f"SELECT count(*) AS n, count(DISTINCT {q}) AS d, count({q}) AS nn FROM data"
            ).table
            row = meta.to_pylist()[0]
            total = int(row["n"])
            distinct = int(row["d"])
            non_null = int(row["nn"])
            if total <= 0:
                continue
            unique_ratio = distinct / non_null if non_null else 1.0
            if not (2 <= distinct <= 1000) or unique_ratio > 0.9:
                continue  # 单值/爆炸列不适用（category_explosion 处理后者）
            table = context.handle.sql_aggregate(
                f"SELECT {q} AS v, count(*) AS n FROM data "
                f"WHERE {q} IS NOT NULL GROUP BY {q} "
                f"HAVING count(*) < 5 AND count(*)::DOUBLE * 1000.0 < {total} "
                f"ORDER BY n LIMIT 20"
            ).table
            values = table.column("v").to_pylist()
            counts = table.column("n").to_pylist()
            if not values:
                continue
            candidates.append(
                make_candidate(
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    context=context,
                    issue_type="rare_category",
                    columns=[col],
                    affected_count=sum(int(c) for c in counts),
                    evidence=[
                        make_evidence(
                            detector_id=self.detector_id,
                            detector_version=self.detector_version,
                            evidence_type=EvidenceType.STATISTICAL_MEASURE,
                            description=f"{len(values)} rare categories",
                            data={"categories": list(zip(values, counts, strict=True))},
                        )
                    ],
                    raw_score=len(values),
                    confidence=0.85,
                    severity=Severity.LOW,
                )
            )
        return candidates


class CategoryExplosionDetector(DetectorBase):
    """类别爆炸（11.6）：唯一值比例 > 0.9 且列名不像标识符。"""

    detector_id = "category_explosion"
    display_name = "Category Explosion"
    description = "Flags near-unique columns that may be free-text masquerading as categories."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)
    default_thresholds: ClassVar[dict[str, float | int | str]] = {"unique_ratio_threshold": 0.9}

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in context.columns:
            lowered = col.lower()
            if any(hint in lowered for hint in _IDENTIFIER_HINTS):
                continue
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count({q}) AS nn, count(DISTINCT {q}) AS d FROM data"
            ).table
            row = table.to_pylist()[0]
            non_null = int(row["nn"])
            distinct = int(row["d"])
            if non_null <= 0:
                continue
            ratio = distinct / non_null
            if ratio > 0.9:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="category_explosion",
                        columns=[col],
                        affected_count=distinct,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.STATISTICAL_MEASURE,
                                description=f"unique_ratio={ratio:.3f} > 0.9",
                                data={"unique_ratio": round(ratio, 6), "distinct": distinct},
                            )
                        ],
                        raw_score=ratio,
                        confidence=0.8,
                        severity=Severity.LOW,
                    )
                )
        return candidates

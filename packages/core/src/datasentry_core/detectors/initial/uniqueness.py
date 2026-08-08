"""唯一性检测器（11.4）：uniqueness_violation。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, ClassVar

from datasentry_core.detectors.base import DetectionContext
from datasentry_core.detectors.common import (
    DetectorBase,
    make_candidate,
    make_evidence,
    quote_ident,
)
from datasentry_core.models.detector import DetectorCapabilities, IssueCandidate
from datasentry_core.models.enums import EvidenceType, QualityDimension, Severity

_CAP_EXAMPLES = 20


class UniquenessViolationDetector(DetectorBase):
    """重复值检测（11.4）：按列 GROUP BY，输出重复频次最高的前 _CAP_EXAMPLES 个值。"""

    detector_id = "uniqueness_violation"
    display_name = "Uniqueness Violation"
    description = "Reports duplicate values in columns that are expected to be unique."
    quality_dimension = QualityDimension.UNIQUENESS
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in context.columns:
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT {q} AS v, count(*) AS n FROM data "
                f"WHERE {q} IS NOT NULL GROUP BY {q} HAVING count(*) > 1 "
                f"ORDER BY n DESC, v LIMIT {_CAP_EXAMPLES}"
            ).table
            values = table.column("v").to_pylist()
            counts = table.column("n").to_pylist()
            if not values:
                continue
            duplicate_rows = sum(int(c) - 1 for c in counts)

            def _json_safe(value: Any) -> Any:
                if isinstance(value, (date, datetime)):
                    return value.isoformat()
                return value

            examples = [(_json_safe(v), int(c)) for v, c in zip(values, counts, strict=True)]
            candidates.append(
                make_candidate(
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    context=context,
                    issue_type="uniqueness_violation",
                    columns=[col],
                    affected_count=duplicate_rows,
                    evidence=[
                        make_evidence(
                            detector_id=self.detector_id,
                            detector_version=self.detector_version,
                            evidence_type=EvidenceType.DUPLICATE_MATCH,
                            description=f"{len(values)} duplicated values, "
                            f"{duplicate_rows} duplicate rows",
                            data={"examples": examples[:10]},
                        )
                    ],
                    raw_score=duplicate_rows,
                    confidence=0.98,
                    severity=Severity.MEDIUM,
                )
            )
        return candidates

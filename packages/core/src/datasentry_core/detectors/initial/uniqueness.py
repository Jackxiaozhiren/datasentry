"""唯一性检测器（11.4）：uniqueness_violation + fuzzy_duplicate（Level 3 模糊重复）。"""

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

_STRING_TYPES = frozenset({"VARCHAR", "CHAR", "TEXT", "BPCHAR"})


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


#: 归一化分组上限（防长尾组数爆炸）
_MAX_FUZZY_GROUPS = 50


class FuzzyDuplicateDetector(DetectorBase):
    """模糊重复（Level 3，11.4）：归一化后相同但原始值不同的组。

    SQL 下推做归一化分组：lower + 去除空白/标点/控制字符
    （保留字母数字与 CJK），组大小 ≥ 2 且组内原始值 ≥ 2 种。
    按列输出一条 issue：affected_count = 组内行数 − 组数（可去重
    行数），evidence 携带各组样例（归一化键 + 原始值列表）。
    """

    detector_id = "fuzzy_duplicate"
    display_name = "Fuzzy Duplicate"
    description = (
        "Finds near-duplicate values that only differ by case, whitespace "
        "or punctuation after normalization."
    )
    quality_dimension = QualityDimension.UNIQUENESS
    # Step 73/ADR-073：支持抽样——20 万行 groupby/string_agg 是大内存点
    # （bench 抽样档峰值主因），抽样下大组仍可检出（generalizable 语义）
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(
        supports_sql_pushdown=True, supports_sampling=True
    )
    default_thresholds: ClassVar[dict[str, float | int | str]] = {
        "min_group_size": 2,
        "min_norm_length": 2,
        "max_groups": _MAX_FUZZY_GROUPS,
    }

    def supports(self, context: DetectionContext) -> bool:
        return any(
            col.physical_type.upper() in _STRING_TYPES for col in context.handle.schema().columns
        )

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for column in context.handle.schema().columns:
            if column.physical_type.upper() not in _STRING_TYPES:
                continue
            q = quote_ident(column.name)
            table = context.handle.sql_aggregate(
                "SELECT norm AS k, count(*) AS n, count(DISTINCT "
                + q
                + ") AS raw_n, string_agg(DISTINCT "
                + q
                + ", ' || ') AS samples FROM ("
                "SELECT lower(regexp_replace("
                + q
                + ", '[^0-9A-Za-z\u4e00-\u9fff]', '', 'g')) AS norm, "
                + q
                + " FROM data WHERE "
                + q
                + " IS NOT NULL) WHERE length(norm) >= 2 GROUP BY norm "
                "HAVING count(*) >= 2 AND count(DISTINCT "
                + q
                + ") >= 2 ORDER BY n DESC LIMIT "
                + str(_MAX_FUZZY_GROUPS)
            ).table
            keys = table.column("k").to_pylist()
            counts = table.column("n").to_pylist()
            raw_counts = table.column("raw_n").to_pylist()
            sample_lists = table.column("samples").to_pylist()
            if not keys:
                continue
            groups: list[dict[str, Any]] = []
            redundant_rows = 0
            for key, n, raw_n, joined in zip(keys, counts, raw_counts, sample_lists, strict=True):
                group_rows = int(n)
                redundant_rows += group_rows - 1
                samples = str(joined).split(" || ") if joined is not None else []
                groups.append(
                    {
                        "normalized": key,
                        "row_count": group_rows,
                        "distinct_raw": int(raw_n),
                        "examples": samples[:3],
                    }
                )
            if not groups:
                continue
            candidates.append(
                make_candidate(
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    context=context,
                    issue_type="fuzzy_duplicate",
                    columns=[column.name],
                    affected_count=redundant_rows,
                    evidence=[
                        make_evidence(
                            detector_id=self.detector_id,
                            detector_version=self.detector_version,
                            evidence_type=EvidenceType.DUPLICATE_MATCH,
                            description=(
                                f"{len(groups)} normalized groups with "
                                f"{redundant_rows} redundant rows"
                            ),
                            data={"groups": groups[:10]},
                        )
                    ],
                    raw_score=redundant_rows,
                    confidence=0.9,
                    severity=Severity.MEDIUM,
                    fpr=0.15,
                )
            )
        return candidates

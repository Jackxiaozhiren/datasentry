"""缺失模式检测器（11.4 核心，Step 16）：sudden/conditional/group/correlated。

四个检测器全部 SQL pushdown、列级统计证据（双列/分组/时间桶场景不取行级证据，
ADR-017）：
- sudden_missingness：时间桶缺失率突变（3× 整体或 ≥0.2，桶样本 ≥10）
- group_missingness：按类别列分组的目标列缺失率异常组
- conditional_missingness：定向级联缺失（A 缺失时 B 缺失率 ≥0.8）
- correlated_missingness：双列缺失高共现（共现率 ≥0.5）
"""

from __future__ import annotations

from typing import ClassVar

from datasentry_core.detectors.base import DetectionContext
from datasentry_core.detectors.common import (
    DetectorBase,
    datetime_columns,
    make_candidate,
    make_evidence,
    quote_ident,
    string_columns,
)
from datasentry_core.models.detector import DetectorCapabilities, IssueCandidate
from datasentry_core.models.enums import EvidenceType, QualityDimension, Severity

# 列对/列预算：防组合爆炸（性能预算见 ADR-017）
_NULL_RATE_MIN = 0.02
_PAIR_COL_CAP = 15
_PAIR_CAP = 50
_GROUP_COL_CAP = 10
_TARGET_COL_CAP = 5

# 时间桶粒度自适应：桶数不足则升级粒度
_BUCKET_GRANULARITIES = (("%Y-%m-%d", "day"), ("%Y-%m", "month"), ("%Y", "year"))
_BUCKET_MIN_COUNT = 5
_BUCKET_MIN_ROWS = 10

# 字符串时间列名特征（created_at / event_date 等）
_TIME_STR_HINTS = ("_at", "_time", "date", "time")

_ISO_DT_RE = r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}:\d{2})?$"

# 阈值固定为规格默认值；可配置化经契约引擎（V1，ADR-004）接入
# 异常判定 = 桶/组缺失率 ≥ max(0.2, 整体 + 0.2)：
# 相对 3× 在整体缺失率 >1/3 时会超 1 永不触发，故用绝对差（ADR-017）
_GROUP_MIN_RATIO = 0.2
_GROUP_MIN_GAP = 0.2
_CONDITIONAL_COVERAGE = 0.8
_CORRELATED_COVERAGE = 0.5
_MIN_COOCCURRENCE = 5


def _row_count(context: DetectionContext) -> int:
    table = context.handle.sql_aggregate("SELECT count(*) AS n FROM data").table
    return int(table.column("n").to_pylist()[0])


def _column_nulls(context: DetectionContext, column: str) -> tuple[int, int]:
    q = quote_ident(column)
    table = context.handle.sql_aggregate(
        f"SELECT count(*) AS n, sum({q} IS NULL) AS nulls FROM data"
    ).table
    total = int(table.column("n").to_pylist()[0])
    nulls = int(table.column("nulls").to_pylist()[0])
    return total, nulls


def _null_rates(context: DetectionContext) -> dict[str, float]:
    """全列 null 率（总行数统一）。"""
    total = _row_count(context)
    if total <= 0:
        return {}
    rates: dict[str, float] = {}
    for col in context.columns:
        _, nulls = _column_nulls(context, col)
        rates[col] = nulls / total
    return rates


def _sparse_columns(context: DetectionContext, rates: dict[str, float]) -> list[str]:
    """缺失率 ≥ _NULL_RATE_MIN 的列（截断至 _PAIR_COL_CAP，按缺失率降序）。"""
    ordered = [c for c in context.columns if rates.get(c, 0.0) >= _NULL_RATE_MIN]
    ordered.sort(key=lambda c: rates[c], reverse=True)
    return ordered[:_PAIR_COL_CAP]


def _anomalous_threshold(overall: float) -> float:
    """桶/组缺失率异常阈值（绝对差判定，见模块 docstring）。"""
    return max(_GROUP_MIN_RATIO, overall + _GROUP_MIN_GAP)


def _column_pairs(columns: list[str]) -> list[tuple[str, str]]:
    """有序列对（按缺失率降序的列序），截断至 _PAIR_CAP。"""
    pairs = [(a, b) for i, a in enumerate(columns) for b in columns[i + 1 :]]
    return pairs[:_PAIR_CAP]


class CorrelatedMissingnessDetector(DetectorBase):
    """缺失共现（11.4）：两列同时缺失的行显著（对称，共现率 ≥0.5）。"""

    detector_id = "correlated_missingness"
    display_name = "Correlated Missingness"
    description = "Reports column pairs that are missing together."
    quality_dimension = QualityDimension.COMPLETENESS
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def supports(self, context: DetectionContext) -> bool:
        return len(context.columns) >= 2

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        rates = _null_rates(context)
        candidates: list[IssueCandidate] = []
        for a, b in _column_pairs(_sparse_columns(context, rates)):
            qa, qb = quote_ident(a), quote_ident(b)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n, "
                f"sum({qa} IS NULL AND {qb} IS NULL) AS both_null, "
                f"sum({qa} IS NULL) AS a_null, "
                f"sum({qb} IS NULL) AS b_null "
                f"FROM data"
            ).table
            both_null = int(table.column("both_null").to_pylist()[0])
            a_null = int(table.column("a_null").to_pylist()[0])
            b_null = int(table.column("b_null").to_pylist()[0])
            if both_null < _MIN_COOCCURRENCE:
                continue
            denominator = min(a_null, b_null)
            coverage = both_null / denominator if denominator else 0.0
            if coverage >= _CORRELATED_COVERAGE:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="correlated_missingness",
                        columns=[a, b],
                        affected_count=both_null,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.STATISTICAL_MEASURE,
                                description=(
                                    f"{both_null} rows are missing in both {a} and {b} "
                                    f"(coverage={coverage:.2f})"
                                ),
                                data={
                                    "column_a": a,
                                    "column_b": b,
                                    "both_null": both_null,
                                    "a_null": a_null,
                                    "b_null": b_null,
                                    "coverage": round(coverage, 6),
                                },
                            )
                        ],
                        raw_score=coverage,
                        confidence=0.85,
                        severity=Severity.LOW,
                        fpr=0.15,
                    )
                )
        return candidates


class ConditionalMissingnessDetector(DetectorBase):
    """条件性缺失（11.4）：A 缺失 ⟹ B 缺失 的级联（定向覆盖率 ≥0.8）。"""

    detector_id = "conditional_missingness"
    display_name = "Conditional Missingness"
    description = "Reports columns whose values are missing whenever another is."
    quality_dimension = QualityDimension.COMPLETENESS
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def supports(self, context: DetectionContext) -> bool:
        return len(context.columns) >= 2

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        rates = _null_rates(context)
        candidates: list[IssueCandidate] = []
        for a, b in _column_pairs(_sparse_columns(context, rates)):
            qa, qb = quote_ident(a), quote_ident(b)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n, "
                f"sum({qa} IS NULL AND {qb} IS NULL) AS both_null, "
                f"sum({qa} IS NULL) AS a_null "
                f"FROM data"
            ).table
            both_null = int(table.column("both_null").to_pylist()[0])
            a_null = int(table.column("a_null").to_pylist()[0])
            if a_null < 10:
                continue
            coverage = both_null / a_null
            if coverage >= _CONDITIONAL_COVERAGE:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="conditional_missingness",
                        columns=[a, b],
                        affected_count=both_null,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.STATISTICAL_MEASURE,
                                description=(
                                    f"{both_null} of {a_null} missing rows of {a} are also "
                                    f"missing in {b} (coverage={coverage:.2f})"
                                ),
                                data={
                                    "condition_column": a,
                                    "target_column": b,
                                    "both_null": both_null,
                                    "condition_null": a_null,
                                    "coverage": round(coverage, 6),
                                },
                            )
                        ],
                        raw_score=coverage,
                        confidence=0.85,
                        severity=Severity.MEDIUM,
                        fpr=0.15,
                    )
                )
        return candidates


def _distinct_counts(context: DetectionContext, columns: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for col in columns:
        q = quote_ident(col)
        table = context.handle.sql_aggregate(
            f"SELECT count(DISTINCT {q}) AS n FROM data WHERE {q} IS NOT NULL"
        ).table
        counts[col] = int(table.column("n").to_pylist()[0])
    return counts


def _group_columns(context: DetectionContext) -> list[str]:
    """类别分组列候选：字符串列中 distinct ∈ [2, 50]（截断 _GROUP_COL_CAP）。"""
    cols = [c for c in string_columns(context) if len(c) <= 64]
    if len(cols) > _GROUP_COL_CAP:
        cols = cols[:_GROUP_COL_CAP]
    counts = _distinct_counts(context, cols)
    return [c for c in cols if 2 <= counts[c] <= 50]


class GroupMissingnessDetector(DetectorBase):
    """分组缺失（11.4）：按类别列分组后目标列缺失率异常组（≥max(0.2, 3×整体)）。"""

    detector_id = "group_missingness"
    display_name = "Grouped Missingness"
    description = "Reports groups whose values are missing much more than usual."
    quality_dimension = QualityDimension.COMPLETENESS
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def supports(self, context: DetectionContext) -> bool:
        return len(context.columns) >= 2

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        rates = _null_rates(context)
        targets = [c for c in context.columns if rates.get(c, 0.0) > 0.0]
        group_cols = [c for c in _group_columns(context) if c not in targets]
        candidates: list[IssueCandidate] = []
        for group_col in group_cols:
            for target in targets:
                if len(candidates) >= _TARGET_COL_CAP * len(group_cols):
                    break
                qg, qt = quote_ident(group_col), quote_ident(target)
                overall = rates.get(target, 0.0)
                table = context.handle.sql_aggregate(
                    f"SELECT {qg} AS gv, count(*) AS n, sum({qt} IS NULL) AS nulls "
                    f"FROM data GROUP BY 1"
                ).table
                for row in table.to_pylist():
                    n = int(row["n"])
                    nulls = int(row["nulls"])
                    ratio = nulls / n if n else 0.0
                    if n < _BUCKET_MIN_ROWS or ratio < _anomalous_threshold(overall):
                        continue
                    candidates.append(
                        make_candidate(
                            detector_id=self.detector_id,
                            detector_version=self.detector_version,
                            context=context,
                            issue_type="group_missingness",
                            columns=[target],
                            affected_count=nulls,
                            evidence=[
                                make_evidence(
                                    detector_id=self.detector_id,
                                    detector_version=self.detector_version,
                                    evidence_type=EvidenceType.STATISTICAL_MEASURE,
                                    description=(
                                        f"{target} missing in {nulls}/{n} rows where "
                                        f"{group_col}={row['gv']!r} (overall {overall:.4f})"
                                    ),
                                    data={
                                        "group_column": group_col,
                                        "group_value": str(row["gv"]),
                                        "target_column": target,
                                        "nulls": nulls,
                                        "group_size": n,
                                        "null_ratio": round(ratio, 6),
                                        "overall_ratio": round(overall, 6),
                                    },
                                )
                            ],
                            raw_score=ratio,
                            confidence=0.85,
                            severity=Severity.MEDIUM,
                            fpr=0.15,
                        )
                    )
        return candidates


def _time_string_columns(context: DetectionContext) -> list[str]:
    """字符串时间列候选（列名含 _at/_time/date 特征）。"""
    return [
        col for col in string_columns(context) if any(h in col.lower() for h in _TIME_STR_HINTS)
    ]


def _bucket_sql(column: str, granularity: str) -> str:
    q = quote_ident(column)
    return f"strftime({q}, '{granularity}') AS bucket"


def _time_bucket_counts(
    context: DetectionContext, column: str, target: str, granularity: str
) -> tuple[list[dict[str, int | str]], int]:
    """按桶统计（target 缺失行数），返回 (桶列表, 有效桶数)。"""
    qt = quote_ident(target)
    bucket_expr = _bucket_sql(column, granularity)
    table = context.handle.sql_aggregate(
        f"SELECT {bucket_expr}, count(*) AS n, sum({qt} IS NULL) AS nulls FROM data GROUP BY 1"
    ).table
    buckets: list[dict[str, int | str]] = []
    for row in table.to_pylist():
        if row["bucket"] is None:
            continue
        buckets.append(
            {"bucket": str(row["bucket"]), "n": int(row["n"]), "nulls": int(row["nulls"])}
        )
    return buckets, len(buckets)


class SuddenMissingnessDetector(DetectorBase):
    """突变缺失（11.4）：时间桶缺失率骤增（≥max(0.2, 3×整体)，桶样本 ≥10）。"""

    detector_id = "sudden_missingness"
    display_name = "Sudden Missingness"
    description = "Reports time buckets where missingness spikes abruptly."
    quality_dimension = QualityDimension.COMPLETENESS
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def supports(self, context: DetectionContext) -> bool:
        return bool(datetime_columns(context) or _time_string_columns(context))

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        rates = _null_rates(context)
        targets = [c for c in context.columns if rates.get(c, 0.0) >= 0.01]
        targets = targets[:_TARGET_COL_CAP] if targets else []
        time_cols = datetime_columns(context) + _time_string_columns(context)
        if not time_cols or not targets:
            return []
        candidates: list[IssueCandidate] = []
        for time_col in time_cols:
            for target in targets:
                overall = rates.get(target, 0.0)
                granularity = _BUCKET_GRANULARITIES[0][0]
                buckets, count = _time_bucket_counts(context, time_col, target, granularity)
                if count < _BUCKET_MIN_COUNT:
                    granularity = _BUCKET_GRANULARITIES[1][0]
                    buckets, count = _time_bucket_counts(context, time_col, target, granularity)
                if count < _BUCKET_MIN_COUNT:
                    granularity = _BUCKET_GRANULARITIES[2][0]
                    buckets, count = _time_bucket_counts(context, time_col, target, granularity)
                threshold = _anomalous_threshold(overall)
                for b in buckets:
                    n, nulls = int(b["n"]), int(b["nulls"])
                    ratio = nulls / n if n else 0.0
                    if n < _BUCKET_MIN_ROWS or ratio < threshold:
                        continue
                    candidates.append(
                        make_candidate(
                            detector_id=self.detector_id,
                            detector_version=self.detector_version,
                            context=context,
                            issue_type="sudden_missingness",
                            columns=[target],
                            affected_count=nulls,
                            evidence=[
                                make_evidence(
                                    detector_id=self.detector_id,
                                    detector_version=self.detector_version,
                                    evidence_type=EvidenceType.STATISTICAL_MEASURE,
                                    description=(
                                        f"{target} missing in {nulls}/{n} rows in "
                                        f"bucket {b['bucket']} (overall {overall:.4f})"
                                    ),
                                    data={
                                        "time_column": time_col,
                                        "bucket": str(b["bucket"]),
                                        "granularity": granularity,
                                        "target_column": target,
                                        "nulls": nulls,
                                        "bucket_size": n,
                                        "null_ratio": round(ratio, 6),
                                        "overall_ratio": round(overall, 6),
                                    },
                                )
                            ],
                            raw_score=ratio,
                            confidence=0.9,
                            severity=Severity.MEDIUM,
                            fpr=0.15,
                        )
                    )
        return candidates

"""证据融合引擎（12.7）：IssueCandidate 聚类合并 → Issue。

规则：
1. 按 (dataset_id, table, columns_set, issue_family) 聚类
   （MVP 无行级主键，不做 affected_row_key 合并）
2. 同簇合并：evidence 全部保留（provenance 可追溯）
3. issue_family 由 issue_type 归一化（family_map）
4. 行级影响并集（仅候选携带行级证据时），
   affected_count = 列级时取 max（保守估计，文档约定）
5. confidence = 1 − Π(1−cᵢ)（C-17 固化写法；factor/sampling 后续步骤接入）
6. severity/false_positive_risk 取簇内最高
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping

from datasentry_core.models.detector import IssueCandidate
from datasentry_core.models.enums import QualityDimension, RiskLevel, Severity
from datasentry_core.models.issue import Issue
from datasentry_core.scoring.weights import SEVERITY_WEIGHTS

FAMILY_MAP: Mapping[str, str] = {
    "iqr_outlier": "numeric_outlier",
    "modified_zscore": "numeric_outlier",
    "tail_probability": "numeric_outlier",
    "percentile_outlier": "numeric_outlier",
    "histogram_rarity": "numeric_outlier",
    "excessive_null_rate": "missingness",
    "suspicious_missing_token": "missingness",
    "sudden_missingness": "missingness",
    "group_missingness": "missingness",
    "conditional_missingness": "missingness",
    "correlated_missingness": "missingness",
    "uniqueness_violation": "uniqueness",
    "suspicious_placeholder": "categorical_anomaly",
    "rare_category": "categorical_anomaly",
    "category_explosion": "categorical_anomaly",
    "inconsistent_case": "categorical_anomaly",
    "leading_or_trailing_whitespace": "string_format",
    "repeated_whitespace": "string_format",
    "hidden_control_character": "string_format",
    "unusual_length": "string_format",
    "invalid_email": "string_format",
    "invalid_phone": "string_format",
    "invalid_url": "string_format",
    "invalid_ip": "string_format",
    "suspicious_formula_injection": "string_format",
    "spelling_variant": "string_format",
    "fullwidth_character": "string_format",
    "mojibake_character": "string_format",
    "invalid_numeric": "string_format",
    "cross_field_violation": "cross_field_constraint",
    "invalid_date": "datetime_anomaly",
    "impossible_date": "datetime_anomaly",
    "future_date": "datetime_anomaly",
    "stale_date": "datetime_anomaly",
    "mixed_date_format": "datetime_anomaly",
    "duplicate_timestamp": "datetime_anomaly",
    "foreign_key_violation": "integrity_constraint",
}

FAMILY_DIMENSIONS: Mapping[str, QualityDimension] = {
    "numeric_outlier": QualityDimension.VALIDITY,
    "missingness": QualityDimension.COMPLETENESS,
    "uniqueness": QualityDimension.UNIQUENESS,
    "categorical_anomaly": QualityDimension.VALIDITY,
    "string_format": QualityDimension.VALIDITY,
    "cross_field_constraint": QualityDimension.VALIDITY,
    "datetime_anomaly": QualityDimension.VALIDITY,
    "integrity_constraint": QualityDimension.INTEGRITY,
}

FAMILY_TITLES: Mapping[str, str] = {
    "numeric_outlier": "Numeric outlier",
    "missingness": "Missing values",
    "uniqueness": "Duplicate values",
    "categorical_anomaly": "Categorical anomaly",
    "string_format": "String format issue",
    "cross_field_constraint": "Cross-field rule violation",
    "datetime_anomaly": "Datetime anomaly",
    "integrity_constraint": "Integrity constraint violation",
}


def issue_family(issue_type: str) -> str:
    """issue_type 归一化为 issue_family（12.7 family_map）。"""
    return FAMILY_MAP.get(issue_type, issue_type)


def _fuse_confidence(confidences: Iterable[float]) -> float:
    """C-17：confidence = 1 − Π(1−cᵢ)。"""
    product = 1.0
    for c in confidences:
        product *= 1.0 - c
    return round(1.0 - product, 6)


class EvidenceFusionEngine:
    """候选融合引擎（12.7）。"""

    def fuse(
        self,
        candidates: list[IssueCandidate],
        scan_run_id: str,
        row_count: int | None = None,
    ) -> list[Issue]:
        clusters: dict[tuple[str, str | None, frozenset[str], str], list[IssueCandidate]] = (
            defaultdict(list)
        )
        for candidate in candidates:
            key = (
                candidate.dataset_id,
                candidate.table_name,
                frozenset(candidate.columns),
                issue_family(candidate.issue_type),
            )
            clusters[key].append(candidate)

        issues: list[Issue] = []
        for (dataset_id, table_name, columns, family), cluster in clusters.items():
            severity = max(
                (Severity(c.suggested_severity) for c in cluster),
                key=lambda s: SEVERITY_WEIGHTS[s],
            )
            affected_rows_union: set[str] = set()
            has_row_level = any(c.affected_rows for c in cluster)
            for c in cluster:
                if c.affected_rows:
                    affected_rows_union.update(c.affected_rows)
            affected_count = (
                len(affected_rows_union)
                if has_row_level
                else max(c.affected_count for c in cluster)
            )
            all_evidence = [ev for c in cluster for ev in c.evidence]
            worst_fpr = max(c.estimated_false_positive_risk for c in cluster)
            issues.append(
                Issue(
                    id=f"iss_{uuid.uuid4().hex[:12]}",
                    scan_run_id=scan_run_id,
                    issue_type=family,
                    title=_title(family, sorted(columns)),
                    description=_description(cluster),
                    dataset_id=dataset_id,
                    table_name=table_name,
                    columns=sorted(columns),
                    quality_dimensions=[FAMILY_DIMENSIONS.get(family, QualityDimension.VALIDITY)],
                    severity=severity,
                    confidence=_fuse_confidence(c.confidence for c in cluster),
                    priority_score=0.0,  # Step 8 评分引擎填充
                    false_positive_risk=_risk_level(worst_fpr),
                    affected_count=affected_count,
                    affected_ratio=_safe_ratio(affected_count, row_count),
                    affected_row_ids=sorted(affected_rows_union) if has_row_level else None,
                    evidence=all_evidence,
                    detector_ids=[c.detector_id for c in cluster],
                )
            )
        return issues


def _title(family: str, columns: list[str]) -> str:
    cols = ", ".join(columns[:3]) + ("..." if len(columns) > 3 else "")
    return f"{FAMILY_TITLES.get(family, family)} in {cols}"


def _description(cluster: list[IssueCandidate]) -> str:
    parts = []
    for c in cluster:
        parts.append(f"[{c.detector_id} v{c.detector_version}] {c.issue_type}: {c.affected_count}")
    return " | ".join(parts)


def _risk_level(fpr: float) -> RiskLevel:
    """误报风险浮点 → 风险等级（<0.3 low / <0.6 medium / else high）。"""
    if fpr < 0.3:
        return RiskLevel.LOW
    if fpr < 0.6:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


def _safe_ratio(affected: int, row_count: int | None) -> float:
    if not row_count or row_count <= 0:
        return 0.0
    return round(min(1.0, affected / row_count), 6)

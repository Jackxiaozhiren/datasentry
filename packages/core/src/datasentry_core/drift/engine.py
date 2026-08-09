"""漂移引擎（18.2，V1）：同数据集历史扫描的版本间比较。

纯函数式：两个 ScanRun（+各自 issues）→ DriftReport。
- schema 变更：column_signature 逐列 diff（added/removed/
  dtype_changed/order_changed；renamed 需相似度启发式，MVP 不伪造）
- 行数漂移：变化率超阈值（默认 20%）
- 质量分数漂移：overall 变化超阈值（默认 5 分）
- issue 分布漂移：issue_type 计数增减（新增问题/已解决问题）
阈值可传参；比较只读，不落库（落库归调用方/UI）。
"""

from __future__ import annotations

import uuid

from datasentry_core.models.drift import ColumnDrift, DriftReport, SchemaChange
from datasentry_core.models.enums import Severity
from datasentry_core.models.issue import Issue
from datasentry_core.models.scan import ScanRun

DEFAULT_ROW_RATIO_THRESHOLD = 0.20
DEFAULT_SCORE_THRESHOLD = 5.0

_DRIFT_SEVERITY = {
    SchemaChange: Severity.HIGH,
    ColumnDrift: Severity.MEDIUM,
}


def _id() -> str:
    return f"drift_{uuid.uuid4().hex[:12]}"


def _schema_changes(reference: ScanRun, current: ScanRun) -> list[SchemaChange]:
    """column_signature 逐列 diff；无法确证的变更不报告。"""
    ref_cols = {name: dtype for name, dtype in reference.fingerprint.column_signature}
    cur_cols = {name: dtype for name, dtype in current.fingerprint.column_signature}
    changes: list[SchemaChange] = []
    for name in cur_cols:
        if name not in ref_cols:
            changes.append(SchemaChange(change_type="added", column=name, after=cur_cols[name]))
    for name in ref_cols:
        if name not in cur_cols:
            changes.append(SchemaChange(change_type="removed", column=name, before=ref_cols[name]))
    for name in sorted(set(ref_cols) & set(cur_cols)):
        if ref_cols[name] != cur_cols[name]:
            changes.append(
                SchemaChange(
                    change_type="dtype_changed",
                    column=name,
                    before=ref_cols[name],
                    after=cur_cols[name],
                )
            )
    ref_order = [n for n, _ in reference.fingerprint.column_signature]
    cur_order = [n for n, _ in current.fingerprint.column_signature]
    if ref_order and ref_order != cur_order and not changes:
        changes.append(SchemaChange(change_type="order_changed", column=ref_order[0]))
    return changes


def _row_drift(reference: ScanRun, current: ScanRun, threshold: float) -> ColumnDrift | None:
    ref_rows = reference.fingerprint.row_count
    cur_rows = current.fingerprint.row_count
    if ref_rows <= 0:
        return None
    ratio = abs(cur_rows - ref_rows) / ref_rows
    if ratio < threshold:
        return None
    return ColumnDrift(
        column="__dataset__",
        drift_type="numeric",
        metric="row_count",
        value=round(ratio, 4),
        threshold=threshold,
        direction="increase" if cur_rows > ref_rows else "decrease",
        sample_sizes=(ref_rows, cur_rows),
    )


def _score_drift(reference: ScanRun, current: ScanRun, threshold: float) -> ColumnDrift | None:
    ref_score = reference.quality_score
    cur_score = current.quality_score
    if ref_score is None or cur_score is None:
        return None
    delta = round(abs(cur_score.overall - ref_score.overall), 2)
    if delta < threshold:
        return None
    return ColumnDrift(
        column="__dataset__",
        drift_type="numeric",
        metric="quality_overall",
        value=delta,
        threshold=threshold,
        direction="decrease" if cur_score.overall < ref_score.overall else "increase",
        sample_sizes=(0, 0),
    )


def _issue_drifts(
    reference: list[Issue],
    current: list[Issue],
    ref_rows: int,
    cur_rows: int,
) -> list[ColumnDrift]:
    """issue_type 分布增减：出现=新问题，消失=已解决。"""
    ref_counts: dict[str, int] = {}
    for issue in reference:
        ref_counts[issue.issue_type] = ref_counts.get(issue.issue_type, 0) + 1
    cur_counts: dict[str, int] = {}
    for issue in current:
        cur_counts[issue.issue_type] = cur_counts.get(issue.issue_type, 0) + 1
    drifts: list[ColumnDrift] = []
    for issue_type in sorted(set(cur_counts) | set(ref_counts)):
        before = ref_counts.get(issue_type, 0)
        after = cur_counts.get(issue_type, 0)
        if before == after:
            continue
        drifts.append(
            ColumnDrift(
                column="__issues__",
                drift_type="categorical",
                metric=f"issue_count.{issue_type}",
                value=float(abs(after - before)),
                threshold=1.0,
                direction=(
                    "new_category"
                    if before == 0
                    else "gone_category"
                    if after == 0
                    else "increase"
                    if after > before
                    else "decrease"
                ),
                severity=Severity.HIGH if before == 0 else Severity.MEDIUM,
                sample_sizes=(ref_rows, cur_rows),
            )
        )
    return drifts


def compare_scans(
    reference: ScanRun,
    current: ScanRun,
    reference_issues: list[Issue],
    current_issues: list[Issue],
    *,
    row_ratio_threshold: float = DEFAULT_ROW_RATIO_THRESHOLD,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> DriftReport:
    """两个历史扫描 → 漂移报告（纯函数，不落库）。"""
    changes = _schema_changes(reference, current)
    drifts: list[ColumnDrift] = []
    row = _row_drift(reference, current, row_ratio_threshold)
    if row is not None:
        drifts.append(row)
    score = _score_drift(reference, current, score_threshold)
    if score is not None:
        drifts.append(score)
    drifts.extend(
        _issue_drifts(
            reference_issues,
            current_issues,
            reference.fingerprint.row_count,
            current.fingerprint.row_count,
        )
    )
    return DriftReport(
        id=_id(),
        reference_dataset_id=reference.dataset_id,
        current_dataset_id=current.dataset_id,
        schema_changes=changes,
        column_drifts=drifts,
        issue_ids=[i.id for i in current_issues],
    )


__all__ = [
    "DEFAULT_ROW_RATIO_THRESHOLD",
    "DEFAULT_SCORE_THRESHOLD",
    "compare_scans",
]

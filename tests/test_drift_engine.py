"""Step 39 漂移引擎测试（18.2 历史版本比较，V1）。"""

from __future__ import annotations

from datasentry_core.drift import compare_scans
from datasentry_core.models.enums import QualityDimension, Severity
from datasentry_core.models.issue import Issue
from datasentry_core.models.quality import QualityScore
from datasentry_core.models.scan import ReproducibilityInfo, ScanConfig, ScanRun


def _scan(
    dataset_id: str,
    *,
    signature: list[tuple[str, str]],
    rows: int,
    overall: float,
    issues_count: dict[Severity, int],
) -> ScanRun:
    from datetime import UTC, datetime

    from datasentry_core import __version__

    return ScanRun(
        id=f"scan_{dataset_id}",
        dataset_id=dataset_id,
        status="completed",
        config=ScanConfig(),
        fingerprint={
            "dataset_id": dataset_id,
            "fingerprint_type": "full",
            "schema_hash": "h",
            "row_count": rows,
            "column_count": len(signature),
            "column_signature": signature,
        },
        quality_score=QualityScore(
            overall=overall,
            dimensions={},
            weights={},
        ),
        issues_count=issues_count,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        reproducibility=ReproducibilityInfo(
            datasentry_version=__version__,
            detector_versions={},
            seed=42,
        ),
    )


def _issue(scan_run_id: str, issue_type: str, severity: Severity = Severity.HIGH) -> Issue:
    return Issue(
        id=f"iss_{issue_type}",
        scan_run_id=scan_run_id,
        issue_type=issue_type,
        title=issue_type,
        dataset_id="orders",
        columns=["amount"],
        quality_dimensions=[QualityDimension.VALIDITY],
        severity=severity,
        confidence=0.9,
        priority_score=70.0,
        affected_count=1,
        affected_ratio=0.01,
        detector_ids=[issue_type],
    )


class TestSchemaChanges:
    def test_added_removed_dtype_changed(self) -> None:
        ref = _scan(
            "orders",
            signature=[("id", "BIGINT"), ("amount", "DOUBLE")],
            rows=100,
            overall=90.0,
            issues_count={s: 0 for s in Severity},
        )
        cur = _scan(
            "orders",
            signature=[("id", "BIGINT"), ("amount", "VARCHAR"), ("note", "VARCHAR")],
            rows=100,
            overall=90.0,
            issues_count={s: 0 for s in Severity},
        )
        report = compare_scans(ref, cur, [], [])
        changes = {(c.change_type, c.column) for c in report.schema_changes}
        assert changes == {("added", "note"), ("dtype_changed", "amount")}

    def test_removed_column(self) -> None:
        ref = _scan(
            "orders",
            signature=[("id", "BIGINT"), ("old", "VARCHAR")],
            rows=10,
            overall=90.0,
            issues_count={s: 0 for s in Severity},
        )
        cur = _scan(
            "orders",
            signature=[("id", "BIGINT")],
            rows=10,
            overall=90.0,
            issues_count={s: 0 for s in Severity},
        )
        report = compare_scans(ref, cur, [], [])
        assert report.schema_changes[0].change_type == "removed"
        assert report.schema_changes[0].column == "old"

    def test_order_change_only_when_columns_equal(self) -> None:
        ref = _scan(
            "orders",
            signature=[("a", "BIGINT"), ("b", "VARCHAR")],
            rows=10,
            overall=90.0,
            issues_count={s: 0 for s in Severity},
        )
        cur = _scan(
            "orders",
            signature=[("b", "VARCHAR"), ("a", "BIGINT")],
            rows=10,
            overall=90.0,
            issues_count={s: 0 for s in Severity},
        )
        report = compare_scans(ref, cur, [], [])
        assert [(c.change_type, c.column) for c in report.schema_changes] == [
            ("order_changed", "a")
        ]


class TestNumericDrift:
    def test_row_count_ratio(self) -> None:
        ref = _scan(
            "orders",
            signature=[("id", "BIGINT")],
            rows=1000,
            overall=90.0,
            issues_count={s: 0 for s in Severity},
        )
        cur = _scan(
            "orders",
            signature=[("id", "BIGINT")],
            rows=1500,
            overall=90.0,
            issues_count={s: 0 for s in Severity},
        )
        report = compare_scans(ref, cur, [], [])
        drift = next(d for d in report.column_drifts if d.metric == "row_count")
        assert drift.value == 0.5
        assert drift.direction == "increase"
        assert drift.sample_sizes == (1000, 1500)

    def test_row_below_threshold_silent(self) -> None:
        ref = _scan(
            "orders",
            signature=[("id", "BIGINT")],
            rows=1000,
            overall=90.0,
            issues_count={s: 0 for s in Severity},
        )
        cur = _scan(
            "orders",
            signature=[("id", "BIGINT")],
            rows=1100,
            overall=90.0,
            issues_count={s: 0 for s in Severity},
        )
        report = compare_scans(ref, cur, [], [])
        assert not any(d.metric == "row_count" for d in report.column_drifts)

    def test_score_drop_above_threshold(self) -> None:
        ref = _scan(
            "orders",
            signature=[("id", "BIGINT")],
            rows=10,
            overall=95.0,
            issues_count={s: 0 for s in Severity},
        )
        cur = _scan(
            "orders",
            signature=[("id", "BIGINT")],
            rows=10,
            overall=80.0,
            issues_count={s: 0 for s in Severity},
        )
        report = compare_scans(ref, cur, [], [])
        drift = next(d for d in report.column_drifts if d.metric == "quality_overall")
        assert drift.value == 15.0
        assert drift.direction == "decrease"

    def test_no_quality_scores_silent(self) -> None:
        ref = _scan(
            "orders",
            signature=[("id", "BIGINT")],
            rows=10,
            overall=90.0,
            issues_count={s: 0 for s in Severity},
        )
        cur = ScanRun(
            **{
                **ref.model_dump(),
                "id": "scan_orders_2",
                "quality_score": None,
            }
        )
        report = compare_scans(ref, cur, [], [])
        assert not any(d.metric == "quality_overall" for d in report.column_drifts)


class TestIssueDrift:
    def test_new_and_gone_issue_types(self) -> None:
        ref = _scan(
            "orders",
            signature=[("id", "BIGINT")],
            rows=10,
            overall=90.0,
            issues_count={s: 0 for s in Severity},
        )
        cur = _scan(
            "orders",
            signature=[("id", "BIGINT")],
            rows=10,
            overall=90.0,
            issues_count={s: 0 for s in Severity},
        )
        report = compare_scans(
            ref,
            cur,
            [_issue(ref.id, "null_issue")],
            [_issue(cur.id, "outlier_issue")],
        )
        drifts = {d.metric: d for d in report.column_drifts}
        assert drifts["issue_count.outlier_issue"].direction == "new_category"
        assert drifts["issue_count.outlier_issue"].severity == Severity.HIGH
        assert drifts["issue_count.null_issue"].direction == "gone_category"

    def test_count_change_direction(self) -> None:
        ref = _scan(
            "orders",
            signature=[("id", "BIGINT")],
            rows=10,
            overall=90.0,
            issues_count={s: 0 for s in Severity},
        )
        cur = _scan(
            "orders",
            signature=[("id", "BIGINT")],
            rows=10,
            overall=90.0,
            issues_count={s: 0 for s in Severity},
        )
        report = compare_scans(
            ref,
            cur,
            [_issue(ref.id, "x")],
            [_issue(cur.id, "x"), _issue(cur.id, "x"), _issue(cur.id, "x")],
        )
        drift = next(d for d in report.column_drifts if d.metric == "issue_count.x")
        assert drift.value == 2.0
        assert drift.direction == "increase"

    def test_no_changes_means_clean_report(self) -> None:
        ref = _scan(
            "orders",
            signature=[("id", "BIGINT")],
            rows=10,
            overall=90.0,
            issues_count={s: 0 for s in Severity},
        )
        cur = _scan(
            "orders",
            signature=[("id", "BIGINT")],
            rows=11,
            overall=91.0,
            issues_count={s: 0 for s in Severity},
        )
        report = compare_scans(ref, cur, [], [])
        assert report.schema_changes == []
        assert report.column_drifts == []
        assert report.issue_ids == []
        assert report.reference_dataset_id == "orders"

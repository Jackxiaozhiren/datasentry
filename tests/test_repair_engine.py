"""Step 19 修复引擎测试（12.5/15 章 MVP 子集 + ADR-020，M6 验收）。"""

from __future__ import annotations

from pathlib import Path

from datasentry_core.connectors import CsvConnector, DataSourceSpec, DataSourceType
from datasentry_core.detectors import DetectionContext, DetectorRegistry
from datasentry_core.detectors.initial import register_default_detectors
from datasentry_core.detectors.runner import ScanRunner
from datasentry_core.models.enums import (
    RepairOperation,
    RepairRunStatus,
)
from datasentry_core.repair import RepairEngine

RULES = (
    "name,status,price,event_date\n"
    + "\n".join(f"user{i},active,{i * 10},2024-01-01" for i in range(20))
    + "\n"
    + " user9 ,Active,n/a,2024-02-30\n"
    + "  user8  ,active,300,2024-13-01\n"
)


def _ctx(tmp_path: Path, csv_text: str) -> tuple[DetectionContext, Path, DetectorRegistry]:
    p = tmp_path / "data.csv"
    p.write_text(csv_text, encoding="utf-8")
    spec = DataSourceSpec(source_type=DataSourceType.CSV, path=p, options={"dataset_id": "t"})
    handle = CsvConnector().open(spec)
    context = DetectionContext(
        dataset_id="t",
        table_name=None,
        columns=handle.schema().column_names,
        handle=handle,
    )
    registry = DetectorRegistry()
    register_default_detectors(registry)
    return context, p, registry


def _close(context: DetectionContext) -> None:
    context.handle.close()


def _run_scan(context: DetectionContext, registry: DetectorRegistry) -> list:
    runner = ScanRunner(registry)
    _, _, issues = runner.run_scan(context, None)
    return issues


def _issue_with_detector(issues: list, detector_id: str):
    """家族化 issue 里挑出含指定原始检测器（与 issue_type 同名）的。"""
    return next(i for i in issues if detector_id in i.detector_ids)


class TestPropose:
    def test_proposes_trim_for_whitespace(self, tmp_path: Path) -> None:
        ctx, _, registry = _ctx(tmp_path, RULES)
        try:
            issues = _run_scan(ctx, registry)
            whitespace = _issue_with_detector(issues, "leading_or_trailing_whitespace")
            proposal = RepairEngine().propose(whitespace, ctx)
            assert proposal is not None
            assert proposal.operation == RepairOperation.TRIM_WHITESPACE
            assert proposal.issue_type == "leading_or_trailing_whitespace"
            assert proposal.target_columns == ["name"]
            assert proposal.estimated_rows_changed == 2
        finally:
            _close(ctx)

    def test_proposes_token_replacement(self, tmp_path: Path) -> None:
        ctx, _, registry = _ctx(tmp_path, RULES)
        try:
            issues = _run_scan(ctx, registry)
            token = _issue_with_detector(issues, "suspicious_missing_token")
            proposal = RepairEngine().propose(token, ctx)
            assert proposal is not None
            assert proposal.operation == RepairOperation.REPLACE_MISSING_TOKEN
        finally:
            _close(ctx)

    def test_no_proposal_for_unmapped_issue(self, tmp_path: Path) -> None:
        ctx, _, registry = _ctx(tmp_path, RULES)
        try:
            issues = _run_scan(ctx, registry)
            unmatched = _issue_with_detector(issues, "uniqueness_violation")
            proposal = RepairEngine().propose(unmatched, ctx)
            assert proposal is None
        finally:
            _close(ctx)


class TestPreview:
    def test_preview_reports_deltas_and_rule_failures(self, tmp_path: Path) -> None:
        ctx, _, registry = _ctx(tmp_path, RULES)
        try:
            issues = _run_scan(ctx, registry)
            whitespace = _issue_with_detector(issues, "leading_or_trailing_whitespace")
            engine = RepairEngine()
            proposal = engine.propose(whitespace, ctx)
            assert proposal is not None
            preview = engine.preview(proposal, ctx, registry)
            assert preview.rows_changed == 2
            assert preview.rule_failures_before["leading_or_trailing_whitespace"] > 0
            assert preview.rule_failures_after["leading_or_trailing_whitespace"] == 0
            assert (
                preview.rule_failures_after["leading_or_trailing_whitespace"]
                < (preview.rule_failures_before["leading_or_trailing_whitespace"])
            )
            assert preview.changed_examples
            example = preview.changed_examples[0]
            assert example.column == "name"
            assert str(example.before) != str(example.after)
        finally:
            _close(ctx)


class TestApplyRollback:
    def test_apply_creates_repaired_copy(self, tmp_path: Path) -> None:
        ctx, _, registry = _ctx(tmp_path, RULES)
        workspace = tmp_path / "ws"
        try:
            issues = _run_scan(ctx, registry)
            whitespace = _issue_with_detector(issues, "leading_or_trailing_whitespace")
            engine = RepairEngine()
            proposal = engine.propose(whitespace, ctx)
            assert proposal is not None
            run = engine.apply(proposal, ctx, workspace)
            assert run.status == RepairRunStatus.APPLIED
            assert run.fingerprint_before != run.fingerprint_after
            output = workspace / ".datasentry" / "repairs" / f"{run.id}.csv"
            assert output.exists()
            artifact = Path(run.rollback_artifact or "")
            assert artifact.exists()
            # 修复副本上重跑检测器：0 候选
            after_spec = DataSourceSpec(
                source_type=DataSourceType.CSV, path=output, options={"dataset_id": "t"}
            )
            after_handle = CsvConnector().open(after_spec)
            try:
                after_ctx = DetectionContext(
                    dataset_id="t",
                    table_name=None,
                    columns=after_handle.schema().column_names,
                    handle=after_handle,
                )
                detector = registry.get("leading_or_trailing_whitespace")
                assert detector.detect(after_ctx) == []
            finally:
                after_handle.close()
        finally:
            _close(ctx)

    def test_rollback_restores_before_state(self, tmp_path: Path) -> None:
        ctx, _, registry = _ctx(tmp_path, RULES)
        workspace = tmp_path / "ws"
        try:
            issues = _run_scan(ctx, registry)
            whitespace = _issue_with_detector(issues, "leading_or_trailing_whitespace")
            engine = RepairEngine()
            proposal = engine.propose(whitespace, ctx)
            assert proposal is not None
            run = engine.apply(proposal, ctx, workspace)
            rolled = engine.rollback(run, workspace)
            assert rolled.status == RepairRunStatus.ROLLED_BACK
            rolled_path = workspace / ".datasentry" / "repairs" / f"{run.id}.rolled_back.csv"
            assert rolled_path.exists()
            # 回滚副本指纹 = before 指纹（M6：回滚后状态一致）
            before = ctx.handle.fingerprint()
            rolled_spec = DataSourceSpec(
                source_type=DataSourceType.CSV,
                path=rolled_path,
                options={"dataset_id": "t"},
            )
            rolled_handle = CsvConnector().open(rolled_spec)
            try:
                after_fp = rolled_handle.fingerprint()
                assert after_fp.file_sha256 == before.file_sha256
            finally:
                rolled_handle.close()
        finally:
            _close(ctx)


class TestClip:
    def test_clip_proposal_from_outlier_bounds(self, tmp_path: Path) -> None:
        rows = [f"{i}" for i in range(100)] + ["5000", "-5000"]
        ctx, _, registry = _ctx(tmp_path, "value\n" + "\n".join(rows) + "\n")
        try:
            issues = _run_scan(ctx, registry)
            outlier = _issue_with_detector(issues, "iqr_outlier")
            engine = RepairEngine()
            proposal = engine.propose(outlier, ctx)
            assert proposal is not None
            assert proposal.operation == RepairOperation.CLIP_VALUE
            assert "lower" in proposal.parameters and "upper" in proposal.parameters
            preview = engine.preview(proposal, ctx, registry)
            assert preview.rule_failures_after["iqr_outlier"] == 0
        finally:
            _close(ctx)

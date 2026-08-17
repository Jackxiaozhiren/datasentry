"""扫描调度（Step 7 配套）：跑检测器 → DetectorRun 记录 → 融合 → 评分 → ScanRun 组装。

Step 35：ScanConfig.custom_rules（契约 rules）接入扫描主流程——逐规则
run_preflight，违规行生成 IssueCandidate 参与融合（issue_type=rule.id）。
"""

from __future__ import annotations

import time
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from datasentry_core import __version__
from datasentry_core.connectors.base import FingerprintMode
from datasentry_core.connectors.sampling import SampledDataHandle
from datasentry_core.detectors.base import (
    DetectionContext,
    Detector,
    DetectorRegistry,
    filter_by_config,
)
from datasentry_core.detectors.common import make_candidate, make_evidence
from datasentry_core.engine.fusion import EvidenceFusionEngine
from datasentry_core.models.detector import IssueCandidate
from datasentry_core.models.enums import EvidenceType, Severity
from datasentry_core.models.issue import Issue
from datasentry_core.models.profile import SamplingInfo
from datasentry_core.models.rules import Rule
from datasentry_core.models.scan import DetectorRun, ReproducibilityInfo, ScanConfig, ScanRun
from datasentry_core.rules.engine import run_preflight
from datasentry_core.scoring import ScoringEngine
from datasentry_core.scoring.quality import QualityScoreEngine


def _count_by_severity(issues: list[Issue]) -> dict[Severity, int]:
    """各严重度 Issue 计数（ScanRun.issues_count，18.2）。"""
    counts = Counter(issue.severity for issue in issues)
    return {severity: counts.get(severity, 0) for severity in Severity}


def _resolve_sample_size(config: ScanConfig, full_rows: int) -> int | None:
    """有效抽样大小：method != none 且 (sample_size 或 ratio) 给定，否则不抽样。"""
    sampling = config.sampling
    if sampling.method == "none":
        return None
    if sampling.sample_size is not None:
        return sampling.sample_size
    if sampling.ratio is not None:
        return max(1, int(full_rows * sampling.ratio))
    return None


class ScanRunner:
    """单数据集扫描器：支持过滤 → detect → DetectorRun → 融合 → 评分。"""

    def __init__(self, registry: DetectorRegistry) -> None:
        self._registry = registry
        self._fusion = EvidenceFusionEngine()
        self._scoring = ScoringEngine()
        self._quality = QualityScoreEngine()

    def run(
        self,
        context: DetectionContext,
        config: ScanConfig,
        scan_run_id: str,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> tuple[list[DetectorRun], list[Issue]]:
        """执行扫描。

        on_progress(done, total, name)：每个检测器执行前回调（TUI 实时进度）。
        """
        detectors = filter_by_config(self._registry.list_active(), config.detectors)
        total = len(detectors)
        full_count = context.handle.count_rows()
        sample_size = _resolve_sample_size(config, full_count)
        sampled_handle: SampledDataHandle | None = None
        runs: list[DetectorRun] = []
        candidates: list[IssueCandidate] = []
        for i, detector in enumerate(detectors):
            if on_progress is not None:
                meta = detector.metadata()
                on_progress(i, total, meta.display_name or meta.detector_id)
            det_context = context
            sampling_info: SamplingInfo | None = None
            rows_scanned = full_count
            if sample_size is not None and detector.metadata().capabilities.supports_sampling:
                if sampled_handle is None:
                    sampled_handle = SampledDataHandle(
                        context.handle,
                        sample_size,
                        seed=config.sampling.seed,
                        method=config.sampling.method,
                    )
                det_context = context.with_handle(sampled_handle)
                rows_scanned = min(sample_size, full_count)
                sampling_info = SamplingInfo(
                    sampled=True,
                    method=config.sampling.method,
                    sample_size=rows_scanned,
                    full_size=full_count,
                    generalizable=config.sampling.generalizable,
                )
            run = self._run_detector(detector, det_context, scan_run_id, rows_scanned)
            run.sampling = sampling_info
            runs.append(run)
            if run.status == "completed":
                candidates.extend(detector.detect(det_context))
        if config.custom_rules:
            runs.append(
                self._run_contract_rules(
                    context, config.custom_rules, candidates, scan_run_id, full_count
                )
            )
        fused = self._fusion.fuse(candidates, scan_run_id, row_count=full_count)
        issues = [self._scoring.apply(issue) for issue in fused]
        return runs, issues

    def _run_contract_rules(
        self,
        context: DetectionContext,
        rules: list[Rule],
        candidates: list[IssueCandidate],
        scan_run_id: str,
        rows_scanned: int,
    ) -> DetectorRun:
        """契约规则执行（Step 35）：逐规则 run_preflight，违规进融合。

        schema/表达式无效的规则记为该 DetectorRun 失败（scan 随之 failed）。
        """
        started = time.monotonic()
        issues_candidates = len(rules)
        failed_rules: list[str] = []
        for rule in rules:
            report = run_preflight(rule, context.handle)
            if not report.valid:
                reason = "missing columns" if not report.schema_valid else "invalid expression"
                failed_rules.append(f"{rule.id}: {reason}")
                continue
            sample = report.sample_run
            if sample is None or sample.failures <= 0:
                continue
            evidence = [
                make_evidence(
                    detector_id="contract_rule",
                    detector_version=str(rule.version),
                    evidence_type=EvidenceType.RULE_VIOLATION,
                    description=(
                        f"rule {rule.id}: {sample.failures} of {sample.rows_tested} rows "
                        f"violate ({sample.failure_ratio:.4f})"
                    ),
                    data={
                        "rule_id": rule.id,
                        "rule_version": rule.version,
                        "failures": sample.failures,
                        "rows_tested": sample.rows_tested,
                        "examples": sample.example_rows[:5],
                    },
                )
            ]
            candidates.append(
                make_candidate(
                    detector_id="contract_rule",
                    detector_version=str(rule.version),
                    context=context,
                    issue_type=f"rule:{rule.id}",
                    columns=[rule.when.column] if rule.when else list(rule.columns),
                    affected_count=sample.failures,
                    affected_rows=None,
                    evidence=evidence,
                    raw_score=sample.failure_ratio,
                    confidence=0.95,
                    severity=rule.severity,
                )
            )
        status: Literal["completed", "failed"] = "failed" if failed_rules else "completed"
        error = "; ".join(failed_rules) if failed_rules else None
        return DetectorRun(
            id=f"det_{uuid.uuid4().hex[:12]}",
            scan_run_id=scan_run_id,
            detector_id="contract_rule",
            detector_version="1.0",
            status=status,
            rows_scanned=rows_scanned,
            duration_ms=int((time.monotonic() - started) * 1000),
            issues_candidates=issues_candidates,
            error=error,
        )

    def run_scan(
        self,
        context: DetectionContext,
        config: ScanConfig | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> tuple[ScanRun, list[DetectorRun], list[Issue]]:
        """完整扫描入口：跑检测器 → 融合 → 评分 → 组装 ScanRun（18.2）。

        返回 (scan_run, detector_runs, issues)，便于调用方持久化与展示。
        """
        config = config or ScanConfig()
        scan_run_id = f"scan_{uuid.uuid4().hex[:12]}"
        started_at = datetime.now(UTC)
        runs, issues = self.run(context, config, scan_run_id, on_progress)
        # Step 73/ADR-073：抽样扫描用 sampled 档指纹（变更检测语义），
        # 避免整文件 SHA-256（原 full 档在 1e6 行 CSV 上 ~1s 量级）
        fp_mode: FingerprintMode = (
            "sampled"
            if config.sampling.method != "none"
            and (config.sampling.sample_size is not None or config.sampling.ratio is not None)
            else "full"
        )
        fingerprint = context.handle.fingerprint(mode=fp_mode)
        failed = [r for r in runs if r.status == "failed"]
        status: Literal["completed", "failed"] = "failed" if failed else "completed"
        error = "; ".join(f"{r.detector_id}: {r.error}" for r in failed) if failed else None
        ran_dimensions = set()
        for run in runs:
            if run.status != "completed":
                continue
            try:
                detector = self._registry.get(run.detector_id)
            except KeyError:
                continue  # 契约规则 run（非注册检测器）不参与维度统计
            ran_dimensions.add(detector.quality_dimension)
        try:
            quality_score = self._quality.score(issues, ran_dimensions=ran_dimensions)
        except ValueError:  # 无任何检测器运行（空白名单）→ 未评分
            quality_score = None
        source_path = context.handle.source_path
        scan_run = ScanRun(
            id=scan_run_id,
            dataset_id=context.dataset_id,
            source_path=str(source_path) if source_path else None,
            status=status,
            config=config,
            fingerprint=fingerprint,
            issues_count=_count_by_severity(issues),
            started_at=started_at,
            finished_at=datetime.now(UTC),
            error=error,
            quality_score=quality_score,
            reproducibility=ReproducibilityInfo(
                datasentry_version=__version__,
                detector_versions={r.detector_id: r.detector_version for r in runs},
                seed=config.seed,
                scanned_at=datetime.now(UTC),
            ),
        )
        return scan_run, runs, issues

    def _run_detector(
        self, detector: Detector, context: DetectionContext, scan_run_id: str, rows_scanned: int
    ) -> DetectorRun:
        started = time.perf_counter()
        try:
            if not detector.supports(context):
                return DetectorRun(
                    id=f"dr_{uuid.uuid4().hex[:12]}",
                    scan_run_id=scan_run_id,
                    detector_id=detector.detector_id,
                    detector_version=detector.detector_version,
                    status="skipped",
                    rows_scanned=0,
                    duration_ms=0,
                    issues_candidates=0,
                )
            candidates = detector.detect(context)
            return DetectorRun(
                id=f"dr_{uuid.uuid4().hex[:12]}",
                scan_run_id=scan_run_id,
                detector_id=detector.detector_id,
                detector_version=detector.detector_version,
                status="completed",
                rows_scanned=rows_scanned,
                duration_ms=int((time.perf_counter() - started) * 1000),
                issues_candidates=len(candidates),
            )
        except Exception as exc:  # 单检测器失败不中断整次扫描
            return DetectorRun(
                id=f"dr_{uuid.uuid4().hex[:12]}",
                scan_run_id=scan_run_id,
                detector_id=detector.detector_id,
                detector_version=detector.detector_version,
                status="failed",
                rows_scanned=0,
                duration_ms=int((time.perf_counter() - started) * 1000),
                issues_candidates=0,
                error=str(exc),
            )

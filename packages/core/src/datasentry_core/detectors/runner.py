"""扫描调度（Step 7 配套）：跑检测器 → DetectorRun 记录 → 融合 → 评分 → ScanRun 组装。

Step 35：ScanConfig.custom_rules（契约 rules）接入扫描主流程——逐规则
run_preflight，违规行生成 IssueCandidate 参与融合（issue_type=rule.id）。
"""

from __future__ import annotations

import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Literal

from datasentry_core import __version__
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
from datasentry_core.models.rules import Rule
from datasentry_core.models.scan import DetectorRun, ReproducibilityInfo, ScanConfig, ScanRun
from datasentry_core.rules.engine import run_preflight
from datasentry_core.scoring import ScoringEngine
from datasentry_core.scoring.quality import QualityScoreEngine


def _count_by_severity(issues: list[Issue]) -> dict[Severity, int]:
    """各严重度 Issue 计数（ScanRun.issues_count，18.2）。"""
    counts = Counter(issue.severity for issue in issues)
    return {severity: counts.get(severity, 0) for severity in Severity}


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
    ) -> tuple[list[DetectorRun], list[Issue]]:
        detectors = filter_by_config(self._registry.list_active(), config.detectors)
        runs: list[DetectorRun] = []
        candidates: list[IssueCandidate] = []
        for detector in detectors:
            run = self._run_detector(detector, context, scan_run_id)
            runs.append(run)
            if run.status == "completed":
                candidates.extend(detector.detect(context))
        if config.custom_rules:
            runs.append(
                self._run_contract_rules(context, config.custom_rules, candidates, scan_run_id)
            )
        fused = self._fusion.fuse(candidates, scan_run_id, row_count=context.handle.count_rows())
        issues = [self._scoring.apply(issue) for issue in fused]
        return runs, issues

    def _run_contract_rules(
        self,
        context: DetectionContext,
        rules: list[Rule],
        candidates: list[IssueCandidate],
        scan_run_id: str,
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
            rows_scanned=context.handle.count_rows(),
            duration_ms=int((time.monotonic() - started) * 1000),
            issues_candidates=issues_candidates,
            error=error,
        )

    def run_scan(
        self,
        context: DetectionContext,
        config: ScanConfig | None = None,
    ) -> tuple[ScanRun, list[DetectorRun], list[Issue]]:
        """完整扫描入口：跑检测器 → 融合 → 评分 → 组装 ScanRun（18.2）。

        返回 (scan_run, detector_runs, issues)，便于调用方持久化与展示。
        """
        config = config or ScanConfig()
        scan_run_id = f"scan_{uuid.uuid4().hex[:12]}"
        started_at = datetime.now(UTC)
        runs, issues = self.run(context, config, scan_run_id)
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
        scan_run = ScanRun(
            id=scan_run_id,
            dataset_id=context.dataset_id,
            status=status,
            config=config,
            fingerprint=context.handle.fingerprint(),
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
        self, detector: Detector, context: DetectionContext, scan_run_id: str
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
                rows_scanned=context.handle.count_rows(),
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

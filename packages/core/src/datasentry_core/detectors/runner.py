"""扫描调度（Step 7 配套）：跑检测器 → DetectorRun 记录 → 融合 → 评分 → ScanRun 组装。"""

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
from datasentry_core.engine.fusion import EvidenceFusionEngine
from datasentry_core.models.detector import IssueCandidate
from datasentry_core.models.enums import Severity
from datasentry_core.models.issue import Issue
from datasentry_core.models.scan import DetectorRun, ReproducibilityInfo, ScanConfig, ScanRun
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
        fused = self._fusion.fuse(candidates, scan_run_id, row_count=context.handle.count_rows())
        issues = [self._scoring.apply(issue) for issue in fused]
        return runs, issues

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
        ran_dimensions = {
            detector.quality_dimension
            for detector, run in zip(
                filter_by_config(self._registry.list_active(), config.detectors),
                runs,
                strict=True,
            )
            if run.status == "completed"
        }
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

"""首批确定性检测器（Step 6/13/14）：22 种（11.3~11.10 的子集，MVP 全表单表）。

注册入口：register_default_detectors(registry)。
后续步骤补齐剩余检测器（correlated_missingness、conditional_missingness、
spelling_variant、日期时间族 11.8 等）。
"""

from __future__ import annotations

from datasentry_core.detectors.base import Detector, DetectorRegistry
from datasentry_core.detectors.cross_field import CrossFieldRuleDetector
from datasentry_core.detectors.initial.categorical import (
    CategoryExplosionDetector,
    InconsistentCaseDetector,
    RareCategoryDetector,
    SuspiciousPlaceholderDetector,
)
from datasentry_core.detectors.initial.formula import FormulaInjectionDetector
from datasentry_core.detectors.initial.missing import (
    ExcessiveNullRateDetector,
    SuspiciousMissingTokenDetector,
)
from datasentry_core.detectors.initial.numeric import (
    HistogramRarityDetector,
    IqrOutlierDetector,
    ModifiedZScoreDetector,
    PercentileOutlierDetector,
    TailProbabilityDetector,
)
from datasentry_core.detectors.initial.textual import (
    HiddenControlCharacterDetector,
    InvalidEmailDetector,
    InvalidIpDetector,
    InvalidPhoneDetector,
    InvalidUrlDetector,
    LeadingTrailingWhitespaceDetector,
    RepeatedWhitespaceDetector,
    UnusualLengthDetector,
)
from datasentry_core.detectors.initial.uniqueness import UniquenessViolationDetector


def build_initial_detectors() -> list[Detector]:
    """首批检测器实例列表（按执行顺序）。"""
    return [
        ExcessiveNullRateDetector(),
        SuspiciousMissingTokenDetector(),
        UniquenessViolationDetector(),
        SuspiciousPlaceholderDetector(),
        RareCategoryDetector(),
        CategoryExplosionDetector(),
        InconsistentCaseDetector(),
        LeadingTrailingWhitespaceDetector(),
        RepeatedWhitespaceDetector(),
        HiddenControlCharacterDetector(),
        UnusualLengthDetector(),
        InvalidEmailDetector(),
        InvalidPhoneDetector(),
        InvalidUrlDetector(),
        InvalidIpDetector(),
        IqrOutlierDetector(),
        PercentileOutlierDetector(),
        ModifiedZScoreDetector(),
        TailProbabilityDetector(),
        HistogramRarityDetector(),
        FormulaInjectionDetector(),
        CrossFieldRuleDetector(),
    ]


def register_default_detectors(registry: DetectorRegistry) -> list[Detector]:
    """向注册表注册首批检测器，返回已注册实例。"""
    detectors = build_initial_detectors()
    for detector in detectors:
        registry.register(detector)
    return detectors

"""权重唯一来源（ADR-003）：severity 与 criticality 权重集中于此，任何模块不得另立常量。

原定义位于 models/enums.py（Step 1），按 ADR-003 影响条款迁入本模块；
enums.py 仅保留枚举类本身，权重消费方一律从此导入。
"""

from __future__ import annotations

from datasentry_core.models.enums import BusinessCriticality, Severity

SEVERITY_WEIGHTS: dict[Severity, float] = {
    Severity.INFO: 0.1,
    Severity.LOW: 0.25,
    Severity.MEDIUM: 0.5,
    Severity.HIGH: 0.75,
    Severity.CRITICAL: 1.0,
}

CRITICALITY_WEIGHTS: dict[BusinessCriticality, float] = {
    BusinessCriticality.INFORMATIONAL: 0.6,
    BusinessCriticality.NORMAL: 1.0,
    BusinessCriticality.IMPORTANT: 1.3,
    BusinessCriticality.CRITICAL: 1.6,
}

#: criticality 归一化基准（ADR-002）：(w − 0.6) / 1.0 ∈ [0, 1]，×10 在引擎侧应用
CRITICALITY_BASE = 0.6
CRITICALITY_SPAN = 1.0


def criticality_norm(criticality: BusinessCriticality) -> float:
    """12.8 criticality 项归一化权重（ADR-002，∈ [0, 1]）。"""
    return round((CRITICALITY_WEIGHTS[criticality] - CRITICALITY_BASE) / CRITICALITY_SPAN, 6)

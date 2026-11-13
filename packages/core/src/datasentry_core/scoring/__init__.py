"""评分引擎（Step 8/11/12）：Priority Score、质量总分与质量门禁。"""

from datasentry_core.scoring.engine import (
    WEIGHTS,
    ScoreBreakdown,
    ScoreResult,
    ScoringEngine,
)
from datasentry_core.scoring.gate import GateResult, QualityGateEvaluator
from datasentry_core.scoring.quality import DIMENSION_WEIGHTS, SCORE_VERSION, QualityScoreEngine
from datasentry_core.scoring.replay import (
    DEFAULT_TOLERANCE,
    ReplayReport,
    ReplaySource,
    ScoreDrift,
    replay_runs,
    replay_score,
)
from datasentry_core.scoring.weights import (
    CRITICALITY_WEIGHTS,
    SEVERITY_WEIGHTS,
    criticality_norm,
)

__all__ = [
    "CRITICALITY_WEIGHTS",
    "DEFAULT_TOLERANCE",
    "DIMENSION_WEIGHTS",
    "SCORE_VERSION",
    "SEVERITY_WEIGHTS",
    "WEIGHTS",
    "GateResult",
    "QualityGateEvaluator",
    "QualityScoreEngine",
    "ReplayReport",
    "ReplaySource",
    "ScoreBreakdown",
    "ScoreDrift",
    "ScoreResult",
    "ScoringEngine",
    "criticality_norm",
    "replay_runs",
    "replay_score",
]

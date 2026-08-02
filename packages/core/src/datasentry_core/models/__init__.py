"""领域模型包：核心 Pydantic v2 模型（对应 18 章领域模型与四十三第 8 项）。

定义顺序约定：enums -> evidence -> fingerprint -> profile -> quality -> llm ->
rules -> contract -> repair -> issue -> scan -> validation -> drift -> audit -> feedback。
跨文件引用在 `__init__.py` 聚合导入后由 Pydantic 延迟解析。
"""

from datasentry_core.models.audit import AuditEvent
from datasentry_core.models.contract import (
    ColumnCheck,
    ColumnContract,
    Contract,
    DatasetContract,
    QualityGate,
)
from datasentry_core.models.drift import ColumnDrift, DriftReport, SchemaChange
from datasentry_core.models.enums import (
    BusinessCriticality,
    EvidenceType,
    IssueStatus,
    QualityDimension,
    RepairOperation,
    RepairProposalStatus,
    RepairRunStatus,
    RiskLevel,
    RuleType,
    Severity,
)
from datasentry_core.models.evidence import Evidence, EvidenceProvenance
from datasentry_core.models.feedback import FeedbackEntry
from datasentry_core.models.fingerprint import DatasetFingerprint
from datasentry_core.models.issue import Issue
from datasentry_core.models.llm import (
    AIExplanation,
    CauseHypothesis,
    LLMInvocation,
    LLMUsageSummary,
    RecommendedAction,
)
from datasentry_core.models.profile import (
    ColumnProfile,
    DatasetProfile,
    SamplingInfo,
    SemanticEvidence,
    SemanticProfile,
)
from datasentry_core.models.quality import QualityScore
from datasentry_core.models.repair import (
    RepairOperationRecord,
    RepairPreview,
    RepairProposal,
    RepairRun,
    RowBeforeAfter,
)
from datasentry_core.models.rules import Condition, Rule, RuleCandidate, RulePreflightReport
from datasentry_core.models.scan import (
    DetectorRun,
    MaskConfig,
    ReproducibilityInfo,
    SamplingConfig,
    ScanConfig,
    ScanRun,
)
from datasentry_core.models.validation import ValidationResult

__all__ = [
    "AIExplanation",
    "AuditEvent",
    "BusinessCriticality",
    "CauseHypothesis",
    "ColumnCheck",
    "ColumnContract",
    "ColumnDrift",
    "ColumnProfile",
    "Condition",
    "Contract",
    "DatasetContract",
    "DatasetFingerprint",
    "DatasetProfile",
    "DetectorRun",
    "DriftReport",
    "Evidence",
    "EvidenceProvenance",
    "EvidenceType",
    "FeedbackEntry",
    "Issue",
    "IssueStatus",
    "LLMInvocation",
    "LLMUsageSummary",
    "MaskConfig",
    "QualityDimension",
    "QualityGate",
    "QualityScore",
    "RecommendedAction",
    "RepairOperation",
    "RepairOperationRecord",
    "RepairPreview",
    "RepairProposal",
    "RepairProposalStatus",
    "RepairRun",
    "RepairRunStatus",
    "ReproducibilityInfo",
    "RiskLevel",
    "RowBeforeAfter",
    "Rule",
    "RuleCandidate",
    "RulePreflightReport",
    "RuleType",
    "SamplingConfig",
    "SamplingInfo",
    "ScanConfig",
    "ScanRun",
    "SchemaChange",
    "SemanticEvidence",
    "SemanticProfile",
    "Severity",
    "ValidationResult",
]

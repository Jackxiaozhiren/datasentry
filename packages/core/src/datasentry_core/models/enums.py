"""枚举定义：质量、严重度、状态、证据、规则、修复等全部枚举的单一来源（ADR-003）。"""

from enum import StrEnum


class Severity(StrEnum):
    """潜在影响级别，非置信度（12.2）。"""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskLevel(StrEnum):
    """误报风险 / 修复风险级别（12.5/15.2）。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class BusinessCriticality(StrEnum):
    """字段业务关键度（12.4）。权重见 scoring.weights（ADR-003 唯一来源）。"""

    INFORMATIONAL = "informational"
    NORMAL = "normal"
    IMPORTANT = "important"
    CRITICAL = "critical"


class IssueStatus(StrEnum):
    """Issue 生命周期状态（18.1）。"""

    OPEN = "open"
    CONFIRMED = "confirmed"
    FALSE_POSITIVE = "false_positive"
    ACCEPTED_EXCEPTION = "accepted_exception"
    REPAIR_PROPOSED = "repair_proposed"
    REPAIR_APPROVED = "repair_approved"
    REPAIRED = "repaired"
    RESOLVED = "resolved"


class QualityDimension(StrEnum):
    """数据质量维度（九章），评分按 ADR-001 归并。"""

    COMPLETENESS = "completeness"
    VALIDITY = "validity"
    UNIQUENESS = "uniqueness"
    CONSISTENCY = "consistency"
    ACCURACY_PROXY = "accuracy_proxy"
    TIMELINESS = "timeliness"
    INTEGRITY = "integrity"
    DISTRIBUTION_STABILITY = "distribution_stability"


class EvidenceType(StrEnum):
    """证据类型（12.1）。"""

    SCHEMA_FACT = "schema_fact"
    STATISTICAL_MEASURE = "statistical_measure"
    CONSTRAINT_VIOLATION = "constraint_violation"
    RULE_VIOLATION = "rule_violation"
    DISTRIBUTION_SHIFT = "distribution_shift"
    SEMANTIC_INFERENCE = "semantic_inference"
    PATTERN_MATCH = "pattern_match"
    DUPLICATE_MATCH = "duplicate_match"
    REFERENCE_LOOKUP = "reference_lookup"
    USER_REPORT = "user_report"


class RuleType(StrEnum):
    """规则类型（14.2）。"""

    COLUMN_COMPARISON = "column_comparison"
    CONDITIONAL_NOT_NULL = "conditional_not_null"
    CONDITIONAL_VALUE = "conditional_value"
    VALUE_RANGE = "value_range"
    ALLOWED_VALUES = "allowed_values"
    REGEX = "regex"
    UNIQUENESS = "uniqueness"
    NOT_NULL = "not_null"
    AGGREGATE = "aggregate"


class RepairOperation(StrEnum):
    """修复操作类型（15.1）。"""

    TRIM_WHITESPACE = "trim_whitespace"
    NORMALIZE_CASE = "normalize_case"
    NORMALIZE_UNICODE = "normalize_unicode"
    CAST_TYPE = "cast_type"
    PARSE_DATE = "parse_date"
    REPLACE_MISSING_TOKEN = "replace_missing_token"
    SET_NULL = "set_null"
    CLIP_VALUE = "clip_value"
    MAP_CATEGORY = "map_category"
    STANDARDIZE_UNIT = "standardize_unit"
    DEDUPLICATE = "deduplicate"
    IMPUTE_VALUE = "impute_value"
    REGEX_REPLACE = "regex_replace"
    DERIVE_COLUMN = "derive_column"
    SPLIT_COLUMN = "split_column"
    MERGE_COLUMNS = "merge_columns"
    CUSTOM_EXPRESSION = "custom_expression"


class RepairProposalStatus(StrEnum):
    """修复提案状态机。"""

    PROPOSED = "proposed"
    PREVIEWED = "previewed"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"


class RepairRunStatus(StrEnum):
    """修复运行状态（15.7）。"""

    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"

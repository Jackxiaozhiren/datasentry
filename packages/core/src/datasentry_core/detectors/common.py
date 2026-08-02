"""确定性检测器公共设施：基类、SQL 工具、证据/候选构造（Step 6）。"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

from datasentry_core.detectors.base import DetectionContext
from datasentry_core.models.detector import DetectorCapabilities, DetectorMeta, IssueCandidate
from datasentry_core.models.enums import EvidenceType, QualityDimension, Severity
from datasentry_core.models.evidence import Evidence


def quote_ident(column: str) -> str:
    """SQL 标识符引号转义。"""
    return '"' + column.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    """SQL 单引号字面量转义。"""
    return "'" + value.replace("'", "''") + "'"


def quote_re(pattern: str) -> str:
    """正则字面量（SQL 单引号转义）。"""
    return quote_literal(pattern)


def make_evidence(
    detector_id: str,
    detector_version: str,
    evidence_type: EvidenceType,
    description: str,
    data: dict[str, Any] | None = None,
    confidence: float = 1.0,
) -> Evidence:
    return Evidence(
        evidence_id=f"ev_{uuid.uuid4().hex[:12]}",
        evidence_type=evidence_type,
        detector_id=detector_id,
        detector_version=detector_version,
        description=description,
        data=data or {},
        confidence=confidence,
    )


def make_candidate(
    detector_id: str,
    detector_version: str,
    context: DetectionContext,
    issue_type: str,
    columns: list[str],
    affected_count: int,
    evidence: list[Evidence],
    raw_score: float,
    confidence: float,
    severity: Severity,
    fpr: float = 0.1,
    affected_rows: list[str] | None = None,
) -> IssueCandidate:
    return IssueCandidate(
        issue_type=issue_type,
        detector_id=detector_id,
        detector_version=detector_version,
        dataset_id=context.dataset_id,
        table_name=context.table_name,
        columns=columns,
        affected_rows=affected_rows,
        affected_count=affected_count,
        evidence=evidence,
        raw_score=raw_score,
        confidence=confidence,
        estimated_false_positive_risk=fpr,
        suggested_severity=severity.value,
    )


_STRING_TYPES = frozenset({"VARCHAR", "CHAR", "TEXT", "BPCHAR"})
# TIME 列不与 DATE/TIMESTAMP 互比（类型错误），MVP 不使用
_TEMPORAL_TYPES = frozenset({"DATE", "TIMESTAMP", "TIMESTAMPTZ"})
_NUMERIC_TYPES = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
        "FLOAT",
        "REAL",
        "DOUBLE",
        "DECIMAL",
    }
)


def string_columns(context: DetectionContext) -> list[str]:
    """字符串列名（按 schema 顺序）。"""
    return [
        c.name for c in context.handle.schema().columns if c.physical_type.upper() in _STRING_TYPES
    ]


def numeric_columns(context: DetectionContext) -> list[str]:
    """数值列名（按 schema 顺序）。"""
    return [
        c.name for c in context.handle.schema().columns if c.physical_type.upper() in _NUMERIC_TYPES
    ]


def datetime_columns(context: DetectionContext) -> list[str]:
    """日期时间列名（物理类型 DATE/TIMESTAMP/TIME，按 schema 顺序）。"""
    return [
        c.name
        for c in context.handle.schema().columns
        if c.physical_type.upper() in _TEMPORAL_TYPES
    ]


class DetectorBase:
    """检测器基类：类属性声明元数据，metadata() 统一构建。"""

    detector_id: ClassVar[str] = ""
    detector_version: ClassVar[str] = "1.0.0"
    display_name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    quality_dimension: ClassVar[QualityDimension] = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities()
    needs_llm: ClassVar[bool] = False
    requires_reference: ClassVar[bool] = False
    experimental: ClassVar[bool] = False
    default_thresholds: ClassVar[dict[str, float | int | str]] = {}

    def supports(self, context: DetectionContext) -> bool:
        return bool(context.columns)

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        raise NotImplementedError

    def metadata(self) -> DetectorMeta:
        return DetectorMeta(
            detector_id=self.detector_id,
            display_name=self.display_name,
            description=self.description,
            quality_dimension=self.quality_dimension,
            capabilities=self.capabilities,
            default_thresholds=dict(self.default_thresholds),
            needs_llm=self.needs_llm,
            requires_reference=self.requires_reference,
            experimental=self.experimental,
        )

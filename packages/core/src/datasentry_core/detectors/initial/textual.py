"""字符串与格式检测器（11.7）：空白/控制字符/长度/邮箱。"""

from __future__ import annotations

from typing import ClassVar

from datasentry_core.detectors.base import DetectionContext
from datasentry_core.detectors.initial.common import (
    DetectorBase,
    make_candidate,
    make_evidence,
    quote_ident,
    string_columns,
)
from datasentry_core.models.detector import DetectorCapabilities, IssueCandidate
from datasentry_core.models.enums import EvidenceType, QualityDimension, Severity

_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
# RE2 支持 \xNN 十六进制转义；直接嵌入控制字符会破坏 duckdb 字符串字面量
_CONTROL_CHAR_RE = r"[\x00-\x08\x0b\x0c\x0e-\x1f]"
_EMAIL_HINTS = ("email", "mail", "@")


class LeadingTrailingWhitespaceDetector(DetectorBase):
    """前后空白（11.7）。"""

    detector_id = "leading_or_trailing_whitespace"
    display_name = "Leading/Trailing Whitespace"
    description = "Reports values with leading or trailing whitespace."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in string_columns(context):
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data WHERE {q} <> trim({q})"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="leading_or_trailing_whitespace",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.PATTERN_MATCH,
                                description=f"{count} values differ from their trimmed form",
                                data={"count": count},
                            )
                        ],
                        raw_score=count,
                        confidence=0.98,
                        severity=Severity.LOW,
                    )
                )
        return candidates


class RepeatedWhitespaceDetector(DetectorBase):
    """重复空白（11.7）：连续 2+ 空白。"""

    detector_id = "repeated_whitespace"
    display_name = "Repeated Whitespace"
    description = "Reports values containing runs of 2+ whitespace characters."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in string_columns(context):
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data WHERE regexp_matches({q}, '\\s{{2,}}')"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="repeated_whitespace",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.PATTERN_MATCH,
                                description=f"{count} values with repeated whitespace",
                                data={"count": count},
                            )
                        ],
                        raw_score=count,
                        confidence=0.95,
                        severity=Severity.LOW,
                    )
                )
        return candidates


class HiddenControlCharacterDetector(DetectorBase):
    """隐藏控制字符（11.7）：\x00-\x08 等。"""

    detector_id = "hidden_control_character"
    display_name = "Hidden Control Character"
    description = "Reports values containing control characters."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in string_columns(context):
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                "SELECT count(*) AS n FROM data "
                f"WHERE regexp_matches({q}, {quote_re(_CONTROL_CHAR_RE)})"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="hidden_control_character",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.PATTERN_MATCH,
                                description=f"{count} values contain control characters",
                                data={"count": count},
                            )
                        ],
                        raw_score=count,
                        confidence=0.98,
                        severity=Severity.MEDIUM,
                    )
                )
        return candidates


class UnusualLengthDetector(DetectorBase):
    """异常长度（11.7）：超长必报（> max_length）；P 边界分位数留待 profile 完善。"""

    detector_id = "unusual_length"
    display_name = "Unusual Length"
    description = "Reports values whose length exceeds max_length."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)
    default_thresholds: ClassVar[dict[str, float | int | str]] = {"max_length": 1024}

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        max_length = 1024
        candidates: list[IssueCandidate] = []
        for col in string_columns(context):
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data WHERE length({q}) > {max_length}"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="unusual_length",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.PATTERN_MATCH,
                                description=f"{count} values longer than {max_length} chars",
                                data={"max_length": max_length, "count": count},
                            )
                        ],
                        raw_score=count,
                        confidence=0.9,
                        severity=Severity.LOW,
                    )
                )
        return candidates


class InvalidEmailDetector(DetectorBase):
    """无效邮箱（11.7）：仅对列名含 email 特征的列启用。"""

    detector_id = "invalid_email"
    display_name = "Invalid Email"
    description = "Reports values that do not match a basic email pattern."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def supports(self, context: DetectionContext) -> bool:
        return any(hint in col.lower() for col in context.columns for hint in _EMAIL_HINTS)

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in string_columns(context):
            if not any(hint in col.lower() for hint in _EMAIL_HINTS):
                continue
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data "
                f"WHERE {q} IS NOT NULL AND NOT regexp_matches(trim({q}), {quote_re(_EMAIL_RE)})"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="invalid_email",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.PATTERN_MATCH,
                                description=f"{count} values fail email pattern",
                                data={"count": count, "pattern": _EMAIL_RE},
                            )
                        ],
                        raw_score=count,
                        confidence=0.85,
                        severity=Severity.LOW,
                    )
                )
        return candidates


def quote_re(pattern: str) -> str:
    """正则字面量（SQL 单引号转义）。"""
    from datasentry_core.detectors.initial.common import quote_literal

    return quote_literal(pattern)

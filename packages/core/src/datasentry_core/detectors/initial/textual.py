"""字符串与格式检测器（11.7）：空白/控制字符/长度/邮箱/电话/URL/IP。"""

from __future__ import annotations

from typing import ClassVar

from datasentry_core.detectors.base import DetectionContext
from datasentry_core.detectors.common import (
    DetectorBase,
    make_candidate,
    make_evidence,
    quote_ident,
    quote_re,
    string_columns,
)
from datasentry_core.models.detector import DetectorCapabilities, IssueCandidate
from datasentry_core.models.enums import EvidenceType, QualityDimension, Severity
from datasentry_core.reporting.evidence_desc import ev

_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
# RE2 支持 \xNN 十六进制转义；直接嵌入控制字符会破坏 duckdb 字符串字面量
_CONTROL_CHAR_RE = r"[\x00-\x08\x0b\x0c\x0e-\x1f]"
_EMAIL_HINTS = ("email", "mail", "@")
_URL_RE = r"^(https?|ftp)://[^\s]+$"
_URL_HINTS = ("url", "website", "web", "link")
_IPV4_RE = (
    r"^(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
    r"(\.(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])){3}$"
)
_IP_HINTS = ("ip_address", "ip_addr", "ipv4")


def _hinted_columns(context: DetectionContext, hints: tuple[str, ...]) -> list[str]:
    return [col for col in string_columns(context) if any(h in col.lower() for h in hints)]


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
                                description=ev(
                                    "textual.trimmed",
                                    {"count": count},
                                    count=count,
                                ),
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
                                description=ev(
                                    "textual.whitespace",
                                    {"count": count},
                                    count=count,
                                ),
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
                                description=ev(
                                    "textual.control",
                                    {"count": count},
                                    count=count,
                                ),
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
                                description=ev(
                                    "textual.long",
                                    {"max_length": max_length, "count": count},
                                    count=count,
                                    max_length=max_length,
                                ),
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
                                description=ev(
                                    "textual.email",
                                    {"count": count, "pattern": _EMAIL_RE},
                                    count=count,
                                ),
                            )
                        ],
                        raw_score=count,
                        confidence=0.85,
                        severity=Severity.LOW,
                    )
                )
        return candidates


class InvalidPhoneDetector(DetectorBase):
    """无效电话（11.7）：去非数字后长度 ∉ [7,15]；仅列名含电话特征启用。"""

    detector_id = "invalid_phone"
    display_name = "Invalid Phone"
    description = "Reports values whose digit-only length is outside [7, 15]."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)
    default_thresholds: ClassVar[dict[str, float | int | str]] = {"min_digits": 7, "max_digits": 15}

    def supports(self, context: DetectionContext) -> bool:
        return any(h in col.lower() for col in context.columns for h in ("phone", "tel", "mobile"))

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in _hinted_columns(context, ("phone", "tel", "mobile")):
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data "
                f"WHERE {q} IS NOT NULL "
                f"AND (length(regexp_replace({q}, '[^0-9]', '', 'g')) < 7 "
                f"OR length(regexp_replace({q}, '[^0-9]', '', 'g')) > 15)"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="invalid_phone",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.PATTERN_MATCH,
                                description=ev(
                                    "textual.digit_count",
                                    {"count": count, "min_digits": 7, "max_digits": 15},
                                    count=count,
                                ),
                            )
                        ],
                        raw_score=count,
                        confidence=0.9,
                        severity=Severity.LOW,
                    )
                )
        return candidates


class InvalidUrlDetector(DetectorBase):
    """无效 URL（11.7）：非 scheme:// 开头；仅列名含 URL 特征启用。"""

    detector_id = "invalid_url"
    display_name = "Invalid URL"
    description = "Reports values that do not match a basic http(s)/ftp URL pattern."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def supports(self, context: DetectionContext) -> bool:
        return any(h in col.lower() for col in context.columns for h in _URL_HINTS)

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in _hinted_columns(context, _URL_HINTS):
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data "
                f"WHERE {q} IS NOT NULL AND NOT regexp_matches(trim({q}), {quote_re(_URL_RE)})"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="invalid_url",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.PATTERN_MATCH,
                                description=ev(
                                    "textual.url",
                                    {"count": count, "pattern": _URL_RE},
                                    count=count,
                                ),
                            )
                        ],
                        raw_score=count,
                        confidence=0.9,
                        severity=Severity.LOW,
                    )
                )
        return candidates


class InvalidIpDetector(DetectorBase):
    """无效 IP（11.7）：IPv4 严格校验；仅列名含 ip 特征且非 zip 类启用。"""

    detector_id = "invalid_ip"
    display_name = "Invalid IP"
    description = "Reports values that do not match a strict IPv4 pattern."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def supports(self, context: DetectionContext) -> bool:
        return any(
            any(h in col.lower() for h in _IP_HINTS) or col.lower() == "ip"
            for col in context.columns
        )

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in string_columns(context):
            low = col.lower()
            if not (any(h in low for h in _IP_HINTS) or low == "ip"):
                continue
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data "
                f"WHERE {q} IS NOT NULL AND NOT regexp_matches(trim({q}), {quote_re(_IPV4_RE)})"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="invalid_ip",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.PATTERN_MATCH,
                                description=ev(
                                    "textual.ipv4",
                                    {"count": count, "pattern": _IPV4_RE},
                                    count=count,
                                ),
                            )
                        ],
                        raw_score=count,
                        confidence=0.95,
                        severity=Severity.LOW,
                    )
                )
        return candidates

"""日期时间检测器（11.8，C-13 P0 化 6 个核心）：invalid/impossible/future/stale/mixed/duplicate。"""

from __future__ import annotations

import re
from collections import Counter
from typing import ClassVar

from datasentry_core.detectors.base import DetectionContext
from datasentry_core.detectors.common import (
    DetectorBase,
    datetime_columns,
    make_candidate,
    make_evidence,
    quote_ident,
    quote_re,
    string_columns,
)
from datasentry_core.models.detector import DetectorCapabilities, IssueCandidate
from datasentry_core.models.enums import EvidenceType, QualityDimension, Severity

# 字符串日期列判定（列名特征；VARCHAR 列才适用 invalid/impossible/mixed）
# 注意：时间戳列（created_at 等，物理 TIMESTAMP 或含时间值）不归此组，
# 由 future/stale/duplicate 经 datetime_columns() 处理
_DATE_HINTS = ("date", "dob", "birth")

# stale_date 豁免列（历史数据字段，规格 11.8：由语义类型豁免；MVP 按列名特征）
_STALE_EXEMPT_HINTS = ("birth", "dob", "founded", "hire", "established", "historical", "history")

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SLASH_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
_DOT_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")
_COMPACT_RE = re.compile(r"^\d{8}$")

_DATETIME_RE = r"^\d{4}-\d{2}-\d{2}$"


def _date_hinted_string_columns(context: DetectionContext) -> list[str]:
    return [col for col in string_columns(context) if any(h in col.lower() for h in _DATE_HINTS)]


class InvalidDateDetector(DetectorBase):
    """无效日期（11.8）：字符串日期列中不匹配 ISO 日期模式的值。"""

    detector_id = "invalid_date"
    display_name = "Invalid Date"
    description = "Reports non-null string values that do not match a date pattern."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def supports(self, context: DetectionContext) -> bool:
        return bool(_date_hinted_string_columns(context))

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in _date_hinted_string_columns(context):
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data "
                f"WHERE {q} IS NOT NULL AND NOT regexp_matches(trim({q}), {quote_re(_DATETIME_RE)})"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="invalid_date",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.PATTERN_MATCH,
                                description=f"{count} values fail ISO date pattern",
                                data={"count": count, "pattern": _DATETIME_RE},
                            )
                        ],
                        raw_score=count,
                        confidence=0.9,
                        severity=Severity.MEDIUM,
                    )
                )
        return candidates


class ImpossibleDateDetector(DetectorBase):
    """不可能日期（11.8）：格式合法但日历不存在（如 2024-02-30）。"""

    detector_id = "impossible_date"
    display_name = "Impossible Date"
    description = "Reports calendar-invalid dates (e.g. 2024-02-30)."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def supports(self, context: DetectionContext) -> bool:
        return bool(_date_hinted_string_columns(context))

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in _date_hinted_string_columns(context):
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data "
                f"WHERE {q} IS NOT NULL "
                f"AND regexp_matches(trim({q}), {quote_re(_DATETIME_RE)}) "
                f"AND try_strptime(trim({q}), '%Y-%m-%d') IS NULL"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="impossible_date",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.PATTERN_MATCH,
                                description=f"{count} calendar-invalid dates",
                                data={"count": count, "pattern": _DATETIME_RE},
                            )
                        ],
                        raw_score=count,
                        confidence=0.95,
                        severity=Severity.HIGH,
                    )
                )
        return candidates


class FutureDateDetector(DetectorBase):
    """未来日期（11.8）：date > now + 1 day（排除时区漂移）。"""

    detector_id = "future_date"
    display_name = "Future Date"
    description = "Reports dates more than 1 day in the future."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)
    default_thresholds: ClassVar[dict[str, float | int | str]] = {"max_future_days": 1}

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in datetime_columns(context):
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data "
                f"WHERE {q} IS NOT NULL AND {q} > current_date + INTERVAL 1 DAY"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="future_date",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.CONSTRAINT_VIOLATION,
                                description=f"{count} dates beyond today + 1 day",
                                data={"count": count, "max_future_days": 1},
                            )
                        ],
                        raw_score=count,
                        confidence=0.9,
                        severity=Severity.LOW,
                    )
                )
        return candidates


class StaleDateDetector(DetectorBase):
    """过期日期（11.8）：date < now - 365d；历史数据字段按列名豁免。"""

    detector_id = "stale_date"
    display_name = "Stale Date"
    description = "Reports dates older than 365 days (exempting historical fields)."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)
    default_thresholds: ClassVar[dict[str, float | int | str]] = {"max_age_days": 365}

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in datetime_columns(context):
            if any(h in col.lower() for h in _STALE_EXEMPT_HINTS):
                continue
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data "
                f"WHERE {q} IS NOT NULL AND {q} < current_date - INTERVAL 365 DAY"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="stale_date",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.CONSTRAINT_VIOLATION,
                                description=f"{count} dates older than 365 days",
                                data={"count": count, "max_age_days": 365},
                            )
                        ],
                        raw_score=count,
                        confidence=0.85,
                        severity=Severity.LOW,
                    )
                )
        return candidates


def _classify_date(value: str) -> str:
    v = value.strip()
    if _ISO_RE.match(v):
        return "iso"
    if _SLASH_RE.match(v):
        return "slash"
    if _DOT_RE.match(v):
        return "dot"
    if _COMPACT_RE.match(v):
        return "compact"
    return "other"


class MixedDateFormatDetector(DetectorBase):
    """混合日期格式（11.8）：同一列 >=2 种解析格式且占比均 > 0.02，报告各格式占比。"""

    detector_id = "mixed_date_format"
    display_name = "Mixed Date Format"
    description = "Reports columns with >=2 date formats each covering >2% of values."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)
    default_thresholds: ClassVar[dict[str, float | int | str]] = {"min_ratio": 0.02}

    def supports(self, context: DetectionContext) -> bool:
        return bool(_date_hinted_string_columns(context))

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in _date_hinted_string_columns(context):
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT {q} AS v FROM data WHERE {q} IS NOT NULL LIMIT 10000"
            ).table
            values = table.column("v").to_pylist()
            if len(set(values)) < 10:
                continue
            counter: Counter[str] = Counter(_classify_date(str(v)) for v in values)
            total = sum(counter.values())
            formats = {fmt: count / total for fmt, count in counter.items() if fmt != "other"}
            major = [fmt for fmt, ratio in formats.items() if ratio > 0.02]
            if len(major) < 2:
                continue
            secondary = sum(counter[f] for f in formats if f not in (major[0],))
            candidates.append(
                make_candidate(
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    context=context,
                    issue_type="mixed_date_format",
                    columns=[col],
                    affected_count=secondary,
                    evidence=[
                        make_evidence(
                            detector_id=self.detector_id,
                            detector_version=self.detector_version,
                            evidence_type=EvidenceType.STATISTICAL_MEASURE,
                            description=f"{len(major)} date formats each >2%",
                            data={"formats": {k: round(v, 4) for k, v in formats.items()}},
                        )
                    ],
                    raw_score=len(major),
                    confidence=0.85,
                    severity=Severity.LOW,
                )
            )
        return candidates


class DuplicateTimestampDetector(DetectorBase):
    """重复时间戳（11.8）：同一时间戳出现 > 2x；主键联合判断归 V1（无主键声明）。"""

    detector_id = "duplicate_timestamp"
    display_name = "Duplicate Timestamp"
    description = "Reports timestamps appearing more than twice."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)
    default_thresholds: ClassVar[dict[str, float | int | str]] = {"max_duplicates": 2}

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in datetime_columns(context):
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT {q} AS v, count(*) AS n FROM data "
                f"WHERE {q} IS NOT NULL GROUP BY {q} "
                f"HAVING count(*) > 2 LIMIT 20"
            ).table
            counts = table.column("n").to_pylist()
            if not counts:
                continue
            affected = sum(int(c) - 1 for c in counts)
            candidates.append(
                make_candidate(
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    context=context,
                    issue_type="duplicate_timestamp",
                    columns=[col],
                    affected_count=affected,
                    evidence=[
                        make_evidence(
                            detector_id=self.detector_id,
                            detector_version=self.detector_version,
                            evidence_type=EvidenceType.DUPLICATE_MATCH,
                            description=f"{len(counts)} timestamps appear more than twice",
                            data={"groups": len(counts), "affected": affected},
                        )
                    ],
                    raw_score=len(counts),
                    confidence=0.9,
                    severity=Severity.MEDIUM,
                )
            )
        return candidates

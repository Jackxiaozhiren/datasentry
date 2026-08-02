"""表示变体与编码检测器（11.7/11.9 余项，Step 17）：spelling/fullwidth/mojibake/numeric。

全部 SQL pushdown、列级统计证据（ADR-017 决策 3 延续）：
- spelling_variant：同一逻辑值的多种表示（去分隔符归一化后相同的不同原值）
- fullwidth_character：全角字母数字混入（U+FF10-19/U+FF21-3A/U+FF41-5A）
- mojibake_character：编码损坏（U+FFFD 替换符）
- invalid_numeric：数值语义列（列名 hint）中的非数值文本
"""

from __future__ import annotations

import re
from collections import defaultdict
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

# 全角字母数字（CJK 全角标点/中文本身不含这些码位，不误报）
# duckdb RE2 不支持 \u 转义，用 \x{HHHH} 形式（RE2 Go 语法）
_FULLWIDTH_RE = r"[\x{FF10}-\x{FF19}\x{FF21}-\x{FF3A}\x{FF41}-\x{FF5A}]"
# U+FFFD 替换符（�）：无效 UTF-8 / 解码损坏的标志
_MOJIBAKE_RE = r"\x{FFFD}"
# 数值文本：可选符号 + 数字 + 千分位/小数分隔符 + 空白
_NUMERIC_TEXT_RE = r"^[+-]?[\d,.\s]+$"
_NUMERIC_HINTS = (
    "price",
    "amount",
    "count",
    "quantity",
    "qty",
    "salary",
    "money",
    "fee",
    "total",
    "cost",
    "age",
    "num",
)
# 与日期/时间 hint 冲突防御：这些列名特征不判 invalid_numeric
_NUMERIC_SKIP_HINTS = ("date", "time", "timestamp")

# 拼写变体归一化：lower + 去常见分隔符（MVP：仅保留字母数字，ADR-018）
_VARIANT_STRIP_RE = re.compile(r"[^0-9a-z]+")
_VARIANT_DISTINCT_CAP = 500
_VARIANT_MIN_RATIO = 0.001
_VARIANT_MIN_PAIRS = 2


def _hinted_columns(context: DetectionContext, hints: tuple[str, ...]) -> list[str]:
    return [col for col in string_columns(context) if any(h in col.lower() for h in hints)]


class SpellingVariantDetector(DetectorBase):
    """拼写/表示变体（11.9）：去分隔符归一化后相同的不同原值（≥2 对且占比达标）。"""

    detector_id = "spelling_variant"
    display_name = "Spelling Variant"
    description = "Reports values that differ only in separators or casing."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def supports(self, context: DetectionContext) -> bool:
        return bool(string_columns(context))

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in string_columns(context):
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT DISTINCT {q} AS v FROM data "
                f"WHERE {q} IS NOT NULL AND {q} <> '' LIMIT {_VARIANT_DISTINCT_CAP}"
            ).table
            raw_values = [str(v) for v in table.column("v").to_pylist()]
            groups: dict[str, list[str]] = defaultdict(list)
            for value in raw_values:
                key = _VARIANT_STRIP_RE.sub("", value.lower())
                if key:
                    groups[key].append(value)
            variant_pairs = [vals for vals in groups.values() if len(vals) >= 2]
            if len(variant_pairs) < _VARIANT_MIN_PAIRS:
                continue
            # 占比过滤：每组变体合计占比 ≥ 阈值
            table_total = context.handle.sql_aggregate("SELECT count(*) AS n FROM data").table
            total = int(table_total.column("n").to_pylist()[0])
            if total <= 0:
                continue
            flagged: list[tuple[str, list[str]]] = []
            affected = 0
            for vals in variant_pairs:
                quoted = ", ".join(quote_re(v) for v in vals)
                count_table = context.handle.sql_aggregate(
                    f"SELECT count(*) AS n FROM data WHERE {q} IN ({quoted})"
                ).table
                count = int(count_table.column("n").to_pylist()[0])
                if count / total >= _VARIANT_MIN_RATIO:
                    flagged.append((col, vals))
                    affected += count
            if flagged:
                variants = {v: vals for _, vals in flagged for v in vals}
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="spelling_variant",
                        columns=[col],
                        affected_count=affected,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.PATTERN_MATCH,
                                description=(
                                    f"{affected} values ({affected / total:.4f}) share "
                                    f"normalized forms"
                                ),
                                data={
                                    "column": col,
                                    "variants": variants,
                                    "total": total,
                                },
                            )
                        ],
                        raw_score=affected / total,
                        confidence=0.8,
                        severity=Severity.LOW,
                        fpr=0.2,
                    )
                )
        return candidates


class FullwidthCharacterDetector(DetectorBase):
    """全角字符（11.7 余项）：全角字母数字与半角混用。"""

    detector_id = "fullwidth_character"
    display_name = "Fullwidth Character"
    description = "Reports values mixing fullwidth alphanumeric characters."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def supports(self, context: DetectionContext) -> bool:
        return bool(string_columns(context))

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in string_columns(context):
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data "
                f"WHERE {q} IS NOT NULL AND regexp_matches({q}, {quote_re(_FULLWIDTH_RE)})"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="fullwidth_character",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.PATTERN_MATCH,
                                description=f"{count} values contain fullwidth characters",
                                data={"count": count, "pattern": _FULLWIDTH_RE},
                            )
                        ],
                        raw_score=count,
                        confidence=0.9,
                        severity=Severity.LOW,
                        fpr=0.1,
                    )
                )
        return candidates


class MojibakeCharacterDetector(DetectorBase):
    """编码损坏（11.7 余项）：U+FFFD 替换符（�）。"""

    detector_id = "mojibake_character"
    display_name = "Mojibake Character"
    description = "Reports values with encoding corruption markers (U+FFFD)."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def supports(self, context: DetectionContext) -> bool:
        return bool(string_columns(context))

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in string_columns(context):
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data "
                f"WHERE {q} IS NOT NULL AND regexp_matches({q}, {quote_re(_MOJIBAKE_RE)})"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="mojibake_character",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.PATTERN_MATCH,
                                description=f"{count} values contain encoding corruption markers",
                                data={"count": count, "pattern": _MOJIBAKE_RE},
                            )
                        ],
                        raw_score=count,
                        confidence=0.95,
                        severity=Severity.LOW,
                        fpr=0.05,
                    )
                )
        return candidates


class InvalidNumericDetector(DetectorBase):
    """数值语义列中的非数值文本（11.7 余项）：列名暗示数值但含文本。"""

    detector_id = "invalid_numeric"
    display_name = "Invalid Numeric"
    description = "Reports non-numeric text in numeric-semantic columns."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)

    def supports(self, context: DetectionContext) -> bool:
        return bool(_hinted_columns(context, _NUMERIC_HINTS))

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in _hinted_columns(context, _NUMERIC_HINTS):
            if any(skip in col.lower() for skip in _NUMERIC_SKIP_HINTS):
                continue
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data "
                f"WHERE {q} IS NOT NULL "
                f"AND NOT regexp_matches(trim({q}), {quote_re(_NUMERIC_TEXT_RE)})"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count < 2:
                continue
            table_total = context.handle.sql_aggregate("SELECT count(*) AS n FROM data").table
            total = int(table_total.column("n").to_pylist()[0])
            if total <= 0 or count / total < 0.01:
                continue
            candidates.append(
                make_candidate(
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    context=context,
                    issue_type="invalid_numeric",
                    columns=[col],
                    affected_count=count,
                    evidence=[
                        make_evidence(
                            detector_id=self.detector_id,
                            detector_version=self.detector_version,
                            evidence_type=EvidenceType.PATTERN_MATCH,
                            description=(
                                f"{count} values ({count / total:.4f}) are not numeric in "
                                f"numeric-semantic column {col}"
                            ),
                            data={
                                "column": col,
                                "count": count,
                                "total": total,
                                "ratio": round(count / total, 6),
                            },
                        )
                    ],
                    raw_score=count / total,
                    confidence=0.85,
                    severity=Severity.MEDIUM,
                    fpr=0.15,
                )
            )
        return candidates

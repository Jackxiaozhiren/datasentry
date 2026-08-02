"""数值异常检测器（11.5）：IQR / Modified Z-score / Tail / Percentile / Histogram rarity。"""

from __future__ import annotations

from typing import ClassVar

from datasentry_core.detectors.base import DetectionContext
from datasentry_core.detectors.initial.common import (
    DetectorBase,
    make_candidate,
    make_evidence,
    numeric_columns,
    quote_ident,
)
from datasentry_core.models.detector import DetectorCapabilities, IssueCandidate
from datasentry_core.models.enums import EvidenceType, QualityDimension, Severity


class IqrOutlierDetector(DetectorBase):
    """IQR 异常（11.5）：下界 k=1.5 / 上界 k=3.0（偏态安全）。"""

    detector_id = "iqr_outlier"
    display_name = "IQR Outlier"
    description = "Flags values outside IQR fences (lower k=1.5, upper k=3.0)."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)
    default_thresholds: ClassVar[dict[str, float | int | str]] = {"lower_k": 1.5, "upper_k": 3.0}

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        lower_k, upper_k = 1.5, 3.0
        candidates: list[IssueCandidate] = []
        for col in numeric_columns(context):
            q = quote_ident(col)
            stat = context.handle.sql_aggregate(
                f"SELECT quantile_cont({q}, 0.25) AS q25, quantile_cont({q}, 0.75) AS q75 FROM data"
            ).table
            row = stat.to_pylist()[0]
            q25, q75 = row["q25"], row["q75"]
            if q25 is None or q75 is None:
                continue
            iqr = float(q75) - float(q25)
            if iqr <= 0:
                continue
            lower = float(q25) - lower_k * iqr
            upper = float(q75) + upper_k * iqr
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data "
                f"WHERE {q} IS NOT NULL AND ({q} < {lower} OR {q} > {upper})"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="iqr_outlier",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.STATISTICAL_MEASURE,
                                description=f"{count} values outside [{lower:.3g}, {upper:.3g}]",
                                data={
                                    "q25": q25,
                                    "q75": q75,
                                    "iqr": iqr,
                                    "lower": lower,
                                    "upper": upper,
                                },
                            )
                        ],
                        raw_score=count,
                        confidence=0.9,
                        severity=Severity.MEDIUM,
                    )
                )
        return candidates


class ModifiedZScoreDetector(DetectorBase):
    """Modified Z-score（11.5）：|0.6745·(x−median)/MAD| > 3.5。"""

    detector_id = "modified_zscore"
    display_name = "Modified Z-Score"
    description = "Flags values whose MAD-based z-score exceeds 3.5 (robust)."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)
    default_thresholds: ClassVar[dict[str, float | int | str]] = {"z_threshold": 3.5}

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        z_threshold = 3.5
        candidates: list[IssueCandidate] = []
        for col in numeric_columns(context):
            q = quote_ident(col)
            stat = context.handle.sql_aggregate(
                f"SELECT median({q}) AS m, mad({q}) AS mad FROM data"
            ).table
            row = stat.to_pylist()[0]
            median, mad = row["m"], row["mad"]
            if median is None or mad is None or mad == 0:
                continue
            scale = 0.6745 * float(mad) * z_threshold
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data "
                f"WHERE {q} IS NOT NULL AND abs({q} - {float(median)}) > {scale}"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="modified_zscore",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.STATISTICAL_MEASURE,
                                description=f"{count} values beyond {z_threshold} MAD-z",
                                data={"median": median, "mad": mad, "threshold": z_threshold},
                            )
                        ],
                        raw_score=count,
                        confidence=0.8,
                        severity=Severity.MEDIUM,
                    )
                )
        return candidates


class TailProbabilityDetector(DetectorBase):
    """尾部概率（11.5）：半开域负值（默认 min_value=0，如 age/amount）。"""

    detector_id = "tail_probability"
    display_name = "Tail Probability"
    description = "Flags values below a physical minimum (default 0)."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)
    default_thresholds: ClassVar[dict[str, float | int | str]] = {"min_value": 0.0}

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        min_value = 0.0
        candidates: list[IssueCandidate] = []
        for col in numeric_columns(context):
            q = quote_ident(col)
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data WHERE {q} IS NOT NULL AND {q} < {min_value}"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="tail_probability",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.CONSTRAINT_VIOLATION,
                                description=f"{count} values below min_value={min_value}",
                                data={"min_value": min_value, "count": count},
                            )
                        ],
                        raw_score=count,
                        confidence=0.95,
                        severity=Severity.MEDIUM,
                    )
                )
        return candidates


class PercentileOutlierDetector(DetectorBase):
    """分位数异常（11.5）：< P0.1 或 > P99.9（可配 P0.01/P99.99）。"""

    detector_id = "percentile_outlier"
    display_name = "Percentile Outlier"
    description = "Flags values below P0.1 or above P99.9 (robust to skew)."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)
    default_thresholds: ClassVar[dict[str, float | int | str]] = {
        "lower_p": 0.001,
        "upper_p": 0.999,
    }

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in numeric_columns(context):
            q = quote_ident(col)
            stat = context.handle.sql_aggregate(
                f"SELECT quantile_cont({q}, 0.001) AS p_low, quantile_cont({q}, 0.999) AS p_high "
                f"FROM data"
            ).table
            row = stat.to_pylist()[0]
            p_low, p_high = row["p_low"], row["p_high"]
            if p_low is None or p_high is None:
                continue
            table = context.handle.sql_aggregate(
                f"SELECT count(*) AS n FROM data "
                f"WHERE {q} IS NOT NULL AND ({q} < {p_low} OR {q} > {p_high})"
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count > 0:
                candidates.append(
                    make_candidate(
                        detector_id=self.detector_id,
                        detector_version=self.detector_version,
                        context=context,
                        issue_type="percentile_outlier",
                        columns=[col],
                        affected_count=count,
                        evidence=[
                            make_evidence(
                                detector_id=self.detector_id,
                                detector_version=self.detector_version,
                                evidence_type=EvidenceType.STATISTICAL_MEASURE,
                                description=f"{count} values outside [{p_low:.3g}, {p_high:.3g}]",
                                data={"p0_1": p_low, "p99_9": p_high, "count": count},
                            )
                        ],
                        raw_score=count,
                        confidence=0.9,
                        severity=Severity.MEDIUM,
                    )
                )
        return candidates


class HistogramRarityDetector(DetectorBase):
    """直方图稀有值（11.5）：20 等宽桶，桶频数 < 1e-5 × n（至少 1）。"""

    detector_id = "histogram_rarity"
    display_name = "Histogram Rarity"
    description = "Flags values in histogram bins with frequency below 1e-5 of rows."
    quality_dimension = QualityDimension.VALIDITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(supports_sql_pushdown=True)
    default_thresholds: ClassVar[dict[str, float | int | str]] = {"n_bins": 20, "min_ratio": 1e-5}

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        n_bins = 20
        min_ratio = 1e-5
        candidates: list[IssueCandidate] = []
        for col in numeric_columns(context):
            q = quote_ident(col)
            stat = context.handle.sql_aggregate(
                f"SELECT min({q}) AS lo, max({q}) AS hi, count({q}) AS n FROM data"
            ).table
            row = stat.to_pylist()[0]
            lo, hi, total = row["lo"], row["hi"], row["n"]
            if lo is None or hi is None or total is None or total <= 0:
                continue
            width = (float(hi) - float(lo)) / n_bins
            if width <= 0:
                continue
            threshold = max(1, int(total * min_ratio))
            table = context.handle.sql_aggregate(
                f"SELECT floor(({q} - {lo}) / {width}) AS bin, count(*) AS c "
                f"FROM data WHERE {q} IS NOT NULL "
                f"GROUP BY bin HAVING count(*) < {threshold} ORDER BY c"
            ).table
            bins = table.column("bin").to_pylist()
            counts = table.column("c").to_pylist()
            if not bins:
                continue
            affected = sum(int(c) for c in counts)
            candidates.append(
                make_candidate(
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    context=context,
                    issue_type="histogram_rarity",
                    columns=[col],
                    affected_count=affected,
                    evidence=[
                        make_evidence(
                            detector_id=self.detector_id,
                            detector_version=self.detector_version,
                            evidence_type=EvidenceType.STATISTICAL_MEASURE,
                            description=f"{affected} values in {len(bins)} rare bins",
                            data={
                                "n_bins": n_bins,
                                "bin_width": width,
                                "min_frequency": threshold,
                                "rare_bins": list(zip(bins, counts, strict=True)),
                            },
                        )
                    ],
                    raw_score=len(bins),
                    confidence=0.85,
                    severity=Severity.MEDIUM,
                )
            )
        return candidates

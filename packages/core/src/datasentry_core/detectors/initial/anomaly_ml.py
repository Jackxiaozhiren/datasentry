"""机器学习异常检测器（Step 42）：Isolation Forest / LOF，distribution_stability 维度。

与统计法（IQR/z-score）互补：模型法捕获任意形状分布中的偏离点。
每列独立建模（单变量）；超过 max_samples 时随机采样训练/打分
（模型提示性信号，severity LOW，供人工确认，不冒充确证异常）。
"""

from __future__ import annotations

import random
from typing import ClassVar

import numpy as np

from datasentry_core.detectors.base import DetectionContext
from datasentry_core.detectors.common import (
    DetectorBase,
    make_candidate,
    make_evidence,
)
from datasentry_core.models.detector import DetectorCapabilities, IssueCandidate
from datasentry_core.models.enums import EvidenceType, QualityDimension, Severity

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
        "FLOAT",
        "DOUBLE",
        "DECIMAL",
    }
)

_MAX_SAMPLES = 20_000
_MIN_ANOMALIES = 3
_MAX_EXAMPLES = 10


def _infer_columns(context: DetectionContext) -> list[str]:
    return [
        col.name
        for col in context.handle.schema().columns
        if col.physical_type.upper() in _NUMERIC_TYPES
    ]


class ModelOutlierDetector(DetectorBase):
    """单变量 IF/LOF 异常检测（distribution_stability）。

    config["model"]: "isolation_forest"（默认）| "local_outlier_factor"。
    anomaly_ratio：异常比例上限（默认 0.05），超过视为模型噪声不报。
    """

    detector_id = "model_outlier"
    display_name = "Model Outlier (IF/LOF)"
    description = (
        "Flags points deviating from the main distribution using "
        "Isolation Forest or LOF (single-column, sampled when large)."
    )
    quality_dimension = QualityDimension.DISTRIBUTION_STABILITY
    capabilities: ClassVar[DetectorCapabilities] = DetectorCapabilities(
        supports_sampling=True,
        requires_row_materialization=True,
    )
    default_thresholds: ClassVar[dict[str, float | int | str]] = {
        "model": "isolation_forest",
        "max_samples": _MAX_SAMPLES,
        "min_anomalies": _MIN_ANOMALIES,
        "anomaly_ratio": 0.05,
        "contamination": 0.02,
    }

    def __init__(self, model: str = "isolation_forest") -> None:
        self._model = model

    def supports(self, context: DetectionContext) -> bool:
        return bool(_infer_columns(context))

    def _detect_column(
        self,
        values: np.ndarray,
        model: str,
        max_samples: int,
        seed: int,
        contamination: float,
    ) -> tuple[int, float, list[float]]:
        """返回 (异常行数, 异常比例, 样例异常值)。"""
        if len(values) < 8:
            return 0, 0.0, []
        rng = random.Random(seed)
        if len(values) > max_samples:
            values = np.array(rng.sample(list(values), max_samples))
        from sklearn.ensemble import IsolationForest  # type: ignore[import-untyped]
        from sklearn.neighbors import LocalOutlierFactor  # type: ignore[import-untyped]

        x = values.reshape(-1, 1)
        if model == "local_outlier_factor":
            predictor = LocalOutlierFactor(
                n_neighbors=min(20, max(5, len(x) // 10)),
                contamination=contamination,
                novelty=False,
            )
            pred = predictor.fit_predict(x)
        else:
            predictor = IsolationForest(
                n_estimators=100,
                contamination=contamination,
                random_state=seed,
                n_jobs=1,
            )
            pred = predictor.fit_predict(x)
        anomaly_mask = pred == -1
        count = int(anomaly_mask.sum())
        if count == 0:
            return 0, 0.0, []
        examples = [float(v) for v in x[anomaly_mask][:_MAX_EXAMPLES, 0]]
        return count, count / len(x), examples

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        from datasentry_core.detectors.common import quote_ident

        candidates: list[IssueCandidate] = []
        model = context.config.detector_params.get("model") if context.config else None
        model = model or self._model
        max_samples = (
            int(context.config.detector_params.get("max_samples", _MAX_SAMPLES))
            if context.config
            else _MAX_SAMPLES
        )
        min_anomalies = (
            int(context.config.detector_params.get("min_anomalies", _MIN_ANOMALIES))
            if context.config
            else _MIN_ANOMALIES
        )
        max_ratio = (
            float(context.config.detector_params.get("anomaly_ratio", 0.05))
            if context.config
            else 0.05
        )
        contamination = (
            float(context.config.detector_params.get("contamination", 0.02))
            if context.config
            else 0.02
        )
        seed = context.config.seed if context.config else 42
        for column in _infer_columns(context):
            q = quote_ident(column)
            try:
                table = context.handle.sql_aggregate(
                    f"SELECT {q} AS v FROM data WHERE {q} IS NOT NULL"
                ).table
            except Exception:
                continue
            values = np.asarray(table.column("v").to_pylist(), dtype=float)
            values = values[np.isfinite(values)]
            count, ratio, examples = self._detect_column(
                values,
                str(model),
                max_samples,
                seed,
                contamination,
            )
            if count < min_anomalies or ratio > max_ratio:
                continue
            candidates.append(
                make_candidate(
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    context=context,
                    issue_type="model_outlier",
                    columns=[column],
                    affected_count=count,
                    evidence=[
                        make_evidence(
                            detector_id=self.detector_id,
                            detector_version=self.detector_version,
                            evidence_type=EvidenceType.STATISTICAL_MEASURE,
                            description=(
                                f"{model}: {count} anomalies out of "
                                f"{len(values)} values ({ratio:.1%})"
                            ),
                            data={
                                "model": model,
                                "anomaly_count": count,
                                "anomaly_ratio": round(ratio, 6),
                                "total_values": len(values),
                                "examples": [round(v, 4) for v in examples],
                            },
                        )
                    ],
                    raw_score=count,
                    confidence=0.7,
                    severity=Severity.LOW,
                    fpr=0.3,
                )
            )
        return candidates

"""示例插件检测器（Step 31，插件 API v1，ADR-031）。

放入 `<workspace>/plugins/` 目录后由 `datasentry` 自动加载（也可用
`load_plugin_detectors` 手动加载）。本插件检测数值列中的负值：

    # 任意 CSV/Parquet 数据
    datasentry scan --file data.csv --project <workspace>
    datasentry detectors list --project <workspace>

约定（插件 API v1 稳定性承诺，见 docs/00-设计裁决记录-ADR.md ADR-031）：
    * 实现 datasentry_core.detectors.base.Detector 协议
    * 无参构造（注册表直接实例化）
    * detector_id 必须全局唯一（与内置检测器冲突会抛 PluginLoadError）
    * 插件是本机可信代码，不做沙箱（与内置检测器同权）
"""

from __future__ import annotations

from typing import ClassVar

from datasentry_core.detectors.base import DetectionContext
from datasentry_core.detectors.common import make_candidate, make_evidence
from datasentry_core.models.detector import DetectorCapabilities, DetectorMeta, IssueCandidate
from datasentry_core.models.enums import EvidenceType, QualityDimension, Severity


class NegativeValueDetector:
    """数值语义列中的负值（示例插件：val 列名暗示数值时检查 min < 0）。"""

    detector_id: ClassVar[str] = "plugin_negative_value"
    detector_version: ClassVar[str] = "1.0.0"
    quality_dimension: ClassVar[QualityDimension] = QualityDimension.VALIDITY

    def supports(self, context: DetectionContext) -> bool:
        return any(col.lower().startswith(("price", "amount", "val")) for col in context.columns)

    def detect(self, context: DetectionContext) -> list[IssueCandidate]:
        candidates: list[IssueCandidate] = []
        for col in context.columns:
            if not col.lower().startswith(("price", "amount", "val")):
                continue
            table = context.handle.sql_aggregate(
                f'SELECT count(*) AS n FROM data WHERE "{col}" < 0'
            ).table
            count = int(table.column("n").to_pylist()[0])
            if count == 0:
                continue
            candidates.append(
                make_candidate(
                    detector_id=self.detector_id,
                    detector_version=self.detector_version,
                    context=context,
                    issue_type="negative_value",
                    columns=[col],
                    affected_count=count,
                    evidence=[
                        make_evidence(
                            detector_id=self.detector_id,
                            detector_version=self.detector_version,
                            evidence_type=EvidenceType.PATTERN_MATCH,
                            description=f"{count} negative values in column {col}",
                            data={"column": col, "count": count},
                        )
                    ],
                    raw_score=count / max(context.handle.count_rows(), 1),
                    confidence=0.9,
                    severity=Severity.HIGH,
                    fpr=0.05,
                )
            )
        return candidates

    def metadata(self) -> DetectorMeta:
        return DetectorMeta(
            detector_id=self.detector_id,
            display_name="Negative Value (plugin)",
            description="Reports negative values in numeric-semantic columns (example plugin).",
            quality_dimension=self.quality_dimension,
            capabilities=DetectorCapabilities(supports_sql_pushdown=True),
        )

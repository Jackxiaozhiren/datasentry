"""datasentry-sample-detector：DataSentry 示例检测器插件（V2-C / ADR-050）。

经 entry points 发现的插件包形态：`pyproject.toml` 声明
`[project.entry-points."datasentry.detectors"]`，`datasentry` 启动时自动
发现并注册（无需复制到 workspace/plugins/）。本示例检测数值列中的负值：

    # 安装示例插件（仓库根目录内）
    uv pip install -e examples/plugins/datasentry-sample-detector

    # 扫描：插件检测器自动参与（scan / detectors list / plugin list 可观测）
    datasentry scan data.csv
    datasentry plugin list

约定（插件 API v1，ADR-031/050）：
    * 实现 datasentry_core.detectors.base.Detector 协议
    * entry 值可为：Detector 实例 / Detector 子类（无参实例化）/ 返回 Detector 的工厂
    * detector_id 全局唯一（冲突会在 plugin list 的 errors 中可见，不影响扫描）
    * 插件是本机可信代码，不做沙箱（与内置检测器同权）
"""

from __future__ import annotations

from typing import ClassVar

from datasentry_core.detectors.base import DetectionContext
from datasentry_core.detectors.common import make_candidate, make_evidence
from datasentry_core.models.detector import DetectorCapabilities, DetectorMeta, IssueCandidate
from datasentry_core.models.enums import EvidenceType, QualityDimension, Severity


class NegativeValueDetector:
    """数值语义列中的负值（示例插件：price/amount/val 列名暗示数值时检查 min < 0）。"""

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
            display_name="Negative Value (sample plugin)",
            description="Reports negative values in numeric-semantic columns (example plugin).",
            quality_dimension=self.quality_dimension,
            capabilities=DetectorCapabilities(supports_sql_pushdown=True),
        )

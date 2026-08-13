"""检测器注册表（11.1）：注册/查询/能力过滤/启停。

Detector 协议与规格 11 章一致；`metadata()` 为可选扩展（UI 与文档生成用）。
"""

from __future__ import annotations

import builtins
import threading
from collections.abc import Sequence
from typing import ClassVar, Protocol, runtime_checkable

from datasentry_core.connectors.base import DataHandle
from datasentry_core.models.contract import TableReference
from datasentry_core.models.detector import DetectorMeta, IssueCandidate
from datasentry_core.models.enums import QualityDimension
from datasentry_core.models.profile import DatasetProfile
from datasentry_core.models.scan import ScanConfig


class DetectionContext:
    """一次检测的上下文：句柄 + 扫描配置 + 可选画像/采样。

    懒加载字段由调度层注入（Step 7），检测器不得自行打开数据源。
    """

    def __init__(
        self,
        dataset_id: str,
        table_name: str | None,
        columns: list[str],
        handle: DataHandle,
        config: ScanConfig | None = None,
        profile: DatasetProfile | None = None,
        sample_rows: int | None = None,
        references: list[TableReference] | None = None,
    ) -> None:
        self.dataset_id = dataset_id
        self.table_name = table_name
        self.columns = columns
        self.handle = handle
        self.config = config
        self.profile = profile
        self.sample_rows = sample_rows
        self.references: list[TableReference] = references or []

    def with_handle(self, handle: DataHandle) -> DetectionContext:
        """返回换句柄的新上下文（Step 71：抽样支撑检测器注入抽样句柄）。"""
        return DetectionContext(
            dataset_id=self.dataset_id,
            table_name=self.table_name,
            columns=self.columns,
            handle=handle,
            config=self.config,
            profile=self.profile,
            sample_rows=self.sample_rows,
            references=self.references,
        )


@runtime_checkable
class Detector(Protocol):
    """检测器协议（11 章）。"""

    detector_id: ClassVar[str]
    detector_version: ClassVar[str]
    quality_dimension: ClassVar[QualityDimension]

    def supports(self, context: DetectionContext) -> bool: ...

    def detect(self, context: DetectionContext) -> list[IssueCandidate]: ...

    def metadata(self) -> DetectorMeta: ...


class DetectorRegistry:
    """线程安全注册表：register/get/list/enable（11.1）。"""

    def __init__(self) -> None:
        self._detectors: dict[str, Detector] = {}
        self._enabled: set[str] = set()
        self._lock = threading.RLock()

    def register(self, detector: Detector) -> None:
        """注册检测器；重复 detector_id 抛 ValueError。"""
        with self._lock:
            if detector.detector_id in self._detectors:
                raise ValueError(f"detector already registered: {detector.detector_id}")
            self._detectors[detector.detector_id] = detector
            self._enabled.add(detector.detector_id)

    def get(self, detector_id: str) -> Detector:
        with self._lock:
            try:
                return self._detectors[detector_id]
            except KeyError as exc:
                raise KeyError(f"detector not registered: {detector_id}") from exc

    def list(self, capability: str | None = None) -> list[Detector]:
        """按注册顺序返回；capability 按 DetectorCapabilities 字段名过滤（True）。"""
        with self._lock:
            detectors = list(self._detectors.values())
        if capability is None:
            return detectors
        return [d for d in detectors if getattr(d.metadata().capabilities, capability, False)]

    def enable(self, detector_id: str, enabled: bool) -> None:
        with self._lock:
            if detector_id not in self._detectors:
                raise KeyError(f"detector not registered: {detector_id}")
            if enabled:
                self._enabled.add(detector_id)
            else:
                self._enabled.discard(detector_id)

    def is_enabled(self, detector_id: str) -> bool:
        with self._lock:
            return detector_id in self._enabled

    def list_active(self) -> builtins.list[Detector]:
        """启用的检测器（按注册顺序）；ScanConfig.detectors 过滤在调度层做。"""
        with self._lock:
            return [d for d in self._detectors.values() if d.detector_id in self._enabled]


def filter_by_config(
    detectors: Sequence[Detector], config_detectors: list[str] | None
) -> list[Detector]:
    """ScanConfig.detectors 白名单过滤（None = 全部启用）。"""
    if config_detectors is None:
        return list(detectors)
    selected = set(config_detectors)
    return [d for d in detectors if d.detector_id in selected]

"""检测器插件加载（Step 31 目录版 + Step 50 entry points 版，ADR-031 / ADR-050）。

- `load_plugin_detectors(registry, dirs)`：从目录加载 Python 模块（Step 31，
  ADR-031，fail-fast 语义保持不破坏）。
- `discover_entrypoint_detectors(registry)`：`importlib.metadata.entry_points`
  发现已安装插件包（Step 50，V2-C）：entry point group `datasentry.detectors`。
  与目录版不同，入口点版**优雅降级**：单个入口加载/实例化/注册失败只记录
  `PluginError`，不影响其他入口与内置检测器（缺依赖给明确报错不崩）。
- 入口值形态（`ep.load()` 返回）：Detector 实例 / Detector 子类（无参实例化）/
  返回 Detector 实例的工厂函数；其余形态记为错误。
- 加载语义（目录版沿用 Step 31）：只加载 `*.py`，跳过 `_`/`.` 前缀文件；
  模块级非 Detector 属性忽略；冲突抛 `PluginLoadError`。
- 安全边界：插件是**用户本机可信代码**（与内置检测器同权），不做沙箱——
  11.10/ADR-015 的安全表达式求值只约束规则表达式；仅加载已安装包
  （`importlib.metadata` 只枚举已安装发行版），不执行任意文件路径。

稳定性承诺（ADR-031 扩展，ADR-050）：`Detector` 协议、`DetectionContext`、
`DetectorRegistry`、`load_plugin_detectors` 签名保持 v1 稳定；新增
`discover_entrypoint_detectors` / `PluginLoadReport` / `PluginError` 作为
V2-C 插件生态 v1 接口。
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import traceback
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from datasentry_core.detectors.base import Detector, DetectorRegistry

#: 检测器插件的 entry point group（V2-C，ADR-050）。
DETECTOR_ENTRY_POINT_GROUP = "datasentry.detectors"


class PluginLoadError(Exception):
    """插件加载失败：import/实例化/注册冲突，message 含文件与原因。"""


@dataclass(frozen=True)
class PluginError:
    """单个入口点插件的加载失败记录（优雅降级：不中断其他插件）。"""

    name: str
    message: str


@dataclass
class PluginLoadReport:
    """entry points 发现结果：成功注册的 detector_id + 失败项列表。"""

    loaded: list[str] = field(default_factory=list)
    errors: list[PluginError] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.loaded or self.errors)


def _module_paths(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        p
        for p in directory.iterdir()
        if p.is_file() and p.suffix == ".py" and not p.name.startswith(("_", "."))
    )


def _load_module(path: Path, module_name: str) -> Any:
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise PluginLoadError(f"cannot create module spec for {path.name}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except PluginLoadError:
        raise
    except Exception as exc:
        raise PluginLoadError(
            f"failed to import plugin {path.name}: {exc}\n{traceback.format_exc(limit=3)}"
        ) from exc


def _iter_detector_classes(module: Any) -> Iterator[type[Detector]]:
    for obj in vars(module).values():
        if isinstance(obj, type) and isinstance(obj, Detector):
            yield cast(type[Detector], obj)


def load_plugin_detectors(
    registry: DetectorRegistry,
    plugin_dirs: Sequence[str | Path],
) -> list[str]:
    """从目录加载插件检测器并注册，返回新注册的 detector_id 列表。

    加载顺序：目录按给定顺序，文件按名称排序（确定性）。
    """
    loaded: list[str] = []
    for directory in plugin_dirs:
        path = Path(directory)
        for file_path in _module_paths(path):
            module_name = f"datasentry_plugin_{path.name}_{file_path.stem}"
            module = _load_module(file_path, module_name)
            for cls in _iter_detector_classes(module):
                try:
                    detector = cls()
                    registry.register(detector)
                except Exception as exc:
                    raise PluginLoadError(
                        f"failed to register detector from {file_path.name}: {exc}"
                    ) from exc
                loaded.append(detector.detector_id)
    return loaded


def _entry_points_for(group: str) -> Sequence[Any]:
    """importlib.metadata.entry_points 按组筛选（3.10+ 均支持 select）。"""
    return importlib.metadata.entry_points().select(group=group)


def _coerce_entry_value(value: Any, name: str) -> Detector:
    """entry 值 → Detector 实例：类无参实例化 / 实例直通 / 工厂调用。"""
    if isinstance(value, type):
        return cast(Detector, value())
    if isinstance(value, Detector):
        return value
    if callable(value):
        detector = cast(Detector, value())
        if not isinstance(detector, Detector):
            raise TypeError(f"factory returned {type(detector).__name__}, expected Detector")
        return detector
    raise TypeError(f"entry value is {type(value).__name__}, expected Detector/class/factory")


def discover_entrypoint_detectors(
    registry: DetectorRegistry,
    group: str = DETECTOR_ENTRY_POINT_GROUP,
) -> PluginLoadReport:
    """发现并注册已安装包的检测器（entry points，V2-C / ADR-050）。

    优雅降级：单个入口加载/实例化/注册失败记录到 `errors`（含入口名与原因），
    其余入口与内置检测器不受影响；不会中断扫描。
    """
    report = PluginLoadReport()
    for entry_point in _entry_points_for(group):
        name = entry_point.name
        try:
            detector = _coerce_entry_value(entry_point.load(), name)
            try:
                registry.register(detector)
            except ValueError as exc:
                report.errors.append(PluginError(name=name, message=str(exc)))
                continue
        except Exception as exc:
            report.errors.append(
                PluginError(
                    name=name,
                    message=f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}",
                )
            )
            continue
        report.loaded.append(detector.detector_id)
    return report


__all__ = [
    "DETECTOR_ENTRY_POINT_GROUP",
    "PluginError",
    "PluginLoadError",
    "PluginLoadReport",
    "discover_entrypoint_detectors",
    "load_plugin_detectors",
]

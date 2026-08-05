"""检测器插件加载（Step 31，插件 API v1，ADR-031）。

- `load_plugin_detectors(registry, dirs)`：从目录加载 Python 模块，
  发现并实例化实现 `Detector` 协议（base.py）的类，注册进注册表。
- 加载语义：
    * 目录不存在 → 跳过（返回空）
    * 只加载 `*.py`，跳过 `_`/`.` 前缀文件（私有/缓存）
    * 模块级非 Detector 属性忽略；一个模块可含多个检测器
    * import 失败 / 实例化失败 / 与已有 detector_id 冲突 →
      抛 `PluginLoadError`（CLI 可见，不静默吞掉）
- 安全边界：插件是**用户本机可信代码**（与内置检测器同权），
  不做沙箱——11.10/ADR-015 的安全表达式求值只约束规则表达式，
  不适用于插件模块；文档与 `detectors list` 输出需声明来源。

稳定性承诺（ADR-031）：`Detector` 协议（detector_id/
detector_version/quality_dimension/supports/detect/metadata）、
`DetectionContext` 字段与 `DetectorRegistry` 为插件 API v1 的
稳定接口；`load_plugin_detectors` 签名与错误类型不破坏性变更。
"""

from __future__ import annotations

import importlib.util
import traceback
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, cast

from datasentry_core.detectors.base import Detector, DetectorRegistry


class PluginLoadError(Exception):
    """插件加载失败：import/实例化/注册冲突，message 含文件与原因。"""


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

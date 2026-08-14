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
- Step 82（ADR-082）清单化：插件目录支持 `plugin.yaml` 清单
  （name/version/author/license/description）；清单目录
  `<dir>/<name>/*.py` 与旧平铺 `<dir>/*.py` 都加载（零迁移）；
  `read_plugin_manifests(dirs)` 供管理面（list/install）消费。
- 安全边界：插件是**用户本机可信代码**（与内置检测器同权），不做沙箱——
  11.10/ADR-015 的安全表达式求值只约束规则表达式；仅加载已安装包
  （`importlib.metadata` 只枚举已安装发行版），不执行任意文件路径。

稳定性承诺（ADR-031 扩展，ADR-050）：`Detector` 协议、`DetectionContext`、
`DetectorRegistry`、`load_plugin_detectors` 签名保持 v1 稳定；新增
`discover_entrypoint_detectors` / `PluginLoadReport` / `PluginError` 作为
V2-C 插件生态 v1 接口；Step 82 增 `PluginManifest` / `read_plugin_manifests`
作为 V12 插件治理接口。
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import re
import traceback
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

from datasentry_core.detectors.base import Detector, DetectorRegistry

#: 检测器插件的 entry point group（V2-C，ADR-050）。
DETECTOR_ENTRY_POINT_GROUP = "datasentry.detectors"

#: 插件清单文件名（Step 82，ADR-082）。
PLUGIN_MANIFEST_FILE = "plugin.yaml"

_MANIFEST_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class PluginManifestError(ValueError):
    """插件清单非法：缺字段/字段格式错误（含文件路径与原因）。"""


@dataclass(frozen=True)
class PluginManifest:
    """插件清单（Step 82，ADR-082）：name/version 必填，其余可选。"""

    name: str
    version: str
    author: str = "unknown"
    license: str = "unknown"
    description: str = ""

    @classmethod
    def from_file(cls, path: Path) -> PluginManifest:
        """读取并校验 plugin.yaml；非法抛 PluginManifestError（含路径）。"""
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise PluginManifestError(f"{path}: cannot read manifest: {exc}") from exc
        except yaml.YAMLError as exc:
            raise PluginManifestError(f"{path}: invalid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise PluginManifestError(f"{path}: manifest must be a mapping")
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise PluginManifestError(f"{path}: manifest requires a non-empty 'name'")
        if not _MANIFEST_NAME_RE.match(name):
            raise PluginManifestError(
                f"{path}: manifest 'name' {name!r} must match {_MANIFEST_NAME_RE.pattern}"
            )
        version = raw.get("version")
        if not isinstance(version, str) or not version:
            raise PluginManifestError(f"{path}: manifest requires a non-empty 'version'")
        return cls(
            name=name,
            version=version,
            author=str(raw.get("author") or "unknown"),
            license=str(raw.get("license") or "unknown"),
            description=str(raw.get("description") or ""),
        )


def _manifest_paths(directory: Path) -> list[Path]:
    """目录下所有 plugin.yaml（顶层平铺或子目录清单目录）。"""
    if not directory.is_dir():
        return []
    paths = [directory / PLUGIN_MANIFEST_FILE]
    paths += sorted(p / PLUGIN_MANIFEST_FILE for p in directory.iterdir() if p.is_dir())
    return [p for p in paths if p.is_file()]


def read_plugin_manifests(dirs: Sequence[str | Path]) -> dict[str, PluginManifest]:
    """扫描目录收集插件清单（name → manifest）；非法清单抛 PluginManifestError。

    平铺目录也支持单文件清单（旧布局可选增强，无清单不影响加载）。
    """
    manifests: dict[str, PluginManifest] = {}
    for directory in dirs:
        for manifest_path in _manifest_paths(Path(directory)):
            manifest = PluginManifest.from_file(manifest_path)
            if manifest.name in manifests:
                raise PluginManifestError(f"duplicate plugin name {manifest.name!r} in {directory}")
            manifests[manifest.name] = manifest
    return manifests


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


def _manifest_name_of(sub: Path) -> str:
    """子目录的插件名：plugin.yaml 的 name（非法/缺失回退目录名）。"""
    manifest = sub / PLUGIN_MANIFEST_FILE
    if manifest.is_file():
        try:
            raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                name = raw.get("name")
                if isinstance(name, str) and name:
                    return name
        except yaml.YAMLError:
            pass
    return sub.name


def plugin_units(plugins_root: Path) -> list[tuple[str, Path]]:
    """枚举插件单元（name, 路径）：清单目录 + 平铺 `*.py`。

    与加载语义一致（Step 82）：无 plugin.yaml 的子目录不属于插件。
    """
    if not plugins_root.is_dir():
        return []
    units: list[tuple[str, Path]] = []
    for sub in sorted(p for p in plugins_root.iterdir() if p.is_dir()):
        if (sub / PLUGIN_MANIFEST_FILE).is_file():
            units.append((_manifest_name_of(sub), sub))
    for file_path in _module_paths(plugins_root):
        units.append((file_path.stem, file_path))
    return units


def _load_units(registry: DetectorRegistry, units: Sequence[tuple[str, Path]]) -> list[str]:
    loaded: list[str] = []
    for name, root in units:
        if root.is_file():
            module_name = f"datasentry_plugin_{name}"
            loaded.extend(_register_module(registry, root, module_name))
            continue
        for file_path in _module_paths(root):
            module_name = f"datasentry_plugin_{name}_{file_path.stem}"
            loaded.extend(_register_module(registry, file_path, module_name))
    return loaded


def load_plugin_detectors(
    registry: DetectorRegistry,
    plugin_dirs: Sequence[str | Path],
) -> list[str]:
    """从目录加载插件检测器并注册，返回新注册的 detector_id 列表。

    Step 82（ADR-082）：顶层平铺 `*.py`（旧布局）与清单目录
    `<dir>/<name>/*.py` 均加载；无 `plugin.yaml` 的子目录忽略
    （避免误加载任意嵌套）。加载顺序：目录按给定顺序，文件按名称
    排序（确定性）。
    """
    loaded: list[str] = []
    for directory in plugin_dirs:
        root = Path(directory)
        if not root.is_dir():
            continue
        loaded.extend(_load_units(registry, plugin_units(root)))
    return loaded


def load_plugin_detectors_excluding(
    registry: DetectorRegistry,
    plugin_dirs: Sequence[str | Path],
    exclude: set[str],
) -> list[str]:
    """同 `load_plugin_detectors`，但按插件名跳过 exclude 中的单元。

    Step 83（ADR-083）：完整性校验失败（tampered）的插件跳过加载，
    其余插件与内置不受影响（校验失败仅限该插件）。
    """
    loaded: list[str] = []
    for directory in plugin_dirs:
        root = Path(directory)
        if not root.is_dir():
            continue
        units = [(name, unit) for name, unit in plugin_units(root) if name not in exclude]
        loaded.extend(_load_units(registry, units))
    return loaded


def _register_module(registry: DetectorRegistry, file_path: Path, module_name: str) -> list[str]:
    module = _load_module(file_path, module_name)
    registered: list[str] = []
    for cls in _iter_detector_classes(module):
        try:
            detector = cls()
            registry.register(detector)
        except Exception as exc:
            raise PluginLoadError(
                f"failed to register detector from {file_path.name}: {exc}"
            ) from exc
        registered.append(detector.detector_id)
    return registered


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
    "PLUGIN_MANIFEST_FILE",
    "PluginError",
    "PluginLoadError",
    "PluginLoadReport",
    "PluginManifest",
    "PluginManifestError",
    "discover_entrypoint_detectors",
    "load_plugin_detectors",
    "load_plugin_detectors_excluding",
    "plugin_units",
    "read_plugin_manifests",
]

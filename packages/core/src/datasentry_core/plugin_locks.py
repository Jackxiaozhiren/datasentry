"""插件完整性锁（Step 83，ADR-083）：安装时 SHA-256 锁定，加载前校验。

- 锁文件：`<workspace>/.datasentry/plugin_locks.json`
  （`{"version": 1, "locks": {"<name>": {version, files{relpath: sha256},
  installed_at}}}`）。
- 名称来源：清单目录 → manifest.name；旧平铺 `*.py` → file.stem
  （与 plugins.py 加载语义一致，两布局均获完整性）。
- 校验在 import 前完成（import 即执行，必须先验后载）；校验失败
  拒绝加载（目录版 fail-fast 语义延续，ADR-031），不影响内置与
  其他插件。
- 安全模型不变（ADR-031/050）：插件=本机可信代码，锁解决
  "篡改无感知"，不引入签名公钥体系；entry points 插件由包管理器
  负责完整性，不在此面。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LOCKS_VERSION = 1


@dataclass(frozen=True)
class PluginLock:
    """单个插件的锁：版本 + 全部文件的相对路径→SHA-256。"""

    version: str
    files: dict[str, str]
    installed_at: str


@dataclass
class PluginLocks:
    """锁文件的内存视图：name → PluginLock。"""

    locks: dict[str, PluginLock] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: Path) -> PluginLocks:
        """读锁文件；缺失/损坏返回空锁（不抛——缺失由调用方建锁）。"""
        if not path.is_file():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(raw, dict) or raw.get("version") != _LOCKS_VERSION:
            return cls()
        locks: dict[str, PluginLock] = {}
        for name, entry in raw.get("locks", {}).items():
            if not isinstance(entry, dict):
                continue
            files = entry.get("files")
            if not isinstance(files, dict) or not isinstance(name, str):
                continue
            locks[name] = PluginLock(
                version=str(entry.get("version", "")),
                files={str(k): str(v) for k, v in files.items()},
                installed_at=str(entry.get("installed_at", "")),
            )
        return cls(locks=locks)

    def to_file(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {"version": _LOCKS_VERSION, "locks": {}}
        for name, lock in sorted(self.locks.items()):
            payload["locks"][name] = {
                "version": lock.version,
                "files": dict(sorted(lock.files.items())),
                "installed_at": lock.installed_at,
            }
        path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def set_plugin(self, name: str, lock: PluginLock) -> None:
        self.locks[name] = lock

    def remove_plugin(self, name: str) -> None:
        self.locks.pop(name, None)


@dataclass(frozen=True)
class IntegrityEntry:
    """单个插件的校验结果。"""

    name: str
    status: str  # "ok" | "tampered" | "no_lock"
    detail: str = ""


@dataclass
class IntegrityReport:
    """校验报告：按插件名列出 ok/tampered/no_lock。"""

    entries: list[IntegrityEntry] = field(default_factory=list)

    def status(self, name: str) -> str:
        for entry in self.entries:
            if entry.name == name:
                return entry.status
        return "unknown"

    def tampered(self) -> list[IntegrityEntry]:
        return [e for e in self.entries if e.status == "tampered"]


def compute_sha256(path: Path) -> str:
    """文件 SHA-256（分块读取，流式）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rel_file_map(plugin_root: Path) -> dict[str, str]:
    """插件内容文件映射（相对路径→SHA-256）；排除编译缓存等衍生文件。"""
    if plugin_root.is_file():
        return {plugin_root.name: compute_sha256(plugin_root)}
    files: dict[str, str] = {}
    for p in sorted(plugin_root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(plugin_root)
        parts = rel.parts
        if "__pycache__" in parts or p.suffix in (".pyc", ".pyo") or p.name == ".DS_Store":
            continue
        files[str(rel)] = compute_sha256(p)
    return files


def build_lock(plugin_root: Path, version: str = "") -> PluginLock:
    """为插件单元建锁：全部文件相对路径→SHA-256。"""
    return PluginLock(
        version=version,
        files=_rel_file_map(plugin_root),
        installed_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def integrity_report(plugins_root: Path, locks: PluginLocks) -> IntegrityReport:
    """校验 plugins_root 下所有插件与锁一致；目录不存在返回空报告。

    单元枚举与加载语义一致（plugin_units：清单目录 + 平铺 *.py）。
    """
    from datasentry_core.plugins import plugin_units

    report = IntegrityReport()
    if not plugins_root.is_dir():
        return report
    for name, root in plugin_units(plugins_root):
        lock = locks.locks.get(name)
        if lock is None:
            report.entries.append(
                IntegrityEntry(
                    name=name, status="no_lock", detail="no lock entry (auto-lock on load)"
                )
            )
            continue
        current = _rel_file_map(root)
        if current == lock.files:
            report.entries.append(IntegrityEntry(name=name, status="ok"))
        else:
            changed = sorted(set(current) ^ set(lock.files))
            report.entries.append(
                IntegrityEntry(
                    name=name,
                    status="tampered",
                    detail=f"files differ from lock: {changed[:5]}",
                )
            )
    return report


__all__ = [
    "IntegrityEntry",
    "IntegrityReport",
    "PluginLock",
    "PluginLocks",
    "build_lock",
    "compute_sha256",
    "integrity_report",
]

"""存储布局（ADR-010 二元化）。

- 项目数据（数据集元数据、扫描、报告、契约、审计）→ 项目工作区 `.datasentry/`
- 全局配置/缓存（凭据、全局设置、LLM 缓存）→ 平台数据目录 `datasentry/`

本模块只做路径解析，不碰文件系统副作用；目录创建由调用方（store.open）负责。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def global_data_dir() -> Path:
    """全局数据目录（macOS/Linux/Windows 三平台约定）。"""
    override = os.environ.get("DATASENTRY_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        base = Path(os.environ.get("HOME", "~")) / "Library/Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", "~\\AppData\\Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser()
    return base / "datasentry"


def project_data_dir(workspace: Path) -> Path:
    """项目工作区数据目录：<workspace>/.datasentry（ADR-010）。"""
    return Path(workspace).expanduser() / ".datasentry"


def project_db_path(workspace: Path) -> Path:
    """项目元数据库：<workspace>/.datasentry/metadata.db。"""
    return project_data_dir(workspace) / "metadata.db"


def project_reports_dir(workspace: Path) -> Path:
    """项目报告目录：<workspace>/.datasentry/reports（26 章报告导出默认落点）。"""
    return project_data_dir(workspace) / "reports"


def project_repairs_dir(workspace: Path) -> Path:
    """项目修复副本目录：<workspace>/.datasentry/repairs（15 章修复产物，ADR-020）。"""
    return project_data_dir(workspace) / "repairs"

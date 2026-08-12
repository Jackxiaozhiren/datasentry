"""凭据文件管理（Step 59，ADR-059）：~/.config/datasentry/secrets.env。

- 行格式 `KEY=VALUE`（空行/`#` 注释忽略），键名必须匹配环境变量命名
  形态 `[A-Z][A-Z0-9_]*`（连接器 connection_ref 直接按键名引用）；
- 文件权限强制 600（读取与写入双向校验，过松给可操作错误）；
- 统一解析链（CLI 参数 > 进程环境变量 > secrets.env 自动加载）：
  `lookup_secret` 先查进程 env 再查文件，均无返回 None；
- 值不落库/日志/报告（连接器层仅内存态流转，沿用 Step 55/56 红线）。
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

_KEY_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*")
_FILE_NAME = "secrets.env"


class SecretsFileError(ValueError):
    """secrets.env 解析或权限错误；消息可操作（含文件路径与行号）。"""


def secrets_path() -> Path:
    """secrets.env 路径：DATASENTRY_CONFIG_HOME > XDG_CONFIG_HOME > ~/.config。"""
    base = os.environ.get("DATASENTRY_CONFIG_HOME") or os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "datasentry" / _FILE_NAME
    return Path.home() / ".config" / "datasentry" / _FILE_NAME


def _check_permissions(path: Path) -> None:
    """权限校验（读取侧）：组/他位可读即拒绝——凭据 600 是强制语义。"""
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise SecretsFileError(
            f"secrets file permissions too open: {path} is {oct(mode)}; "
            "run `chmod 600 <file>` (datasentry secrets set 会自动修正)"
        )


def _read_secrets(path: Path) -> dict[str, str]:
    """解析 secrets.env（不校验权限——写入路径自会重写为 600）。"""
    secrets: dict[str, str] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise SecretsFileError(f"malformed secrets line {path}:{lineno} (expected KEY=VALUE)")
        name, value = stripped.split("=", 1)
        if not _KEY_PATTERN.fullmatch(name):
            raise SecretsFileError(
                f"invalid secret key {path}:{lineno}: {name!r} (keys must match [A-Z][A-Z0-9_]*)"
            )
        secrets[name] = value
    return secrets


def load_secrets(path: Path | None = None) -> dict[str, str]:
    """解析 secrets.env 为 dict；文件缺失返回 {}，权限过松/格式错误抛错。"""
    secrets_file = path or secrets_path()
    if not secrets_file.is_file():
        return {}
    _check_permissions(secrets_file)
    return _read_secrets(secrets_file)


def lookup_secret(name: str, path: Path | None = None) -> str | None:
    """统一解析链：进程环境变量优先，回落 secrets.env；均无返回 None。"""
    value = os.environ.get(name)
    if value:
        return value
    return load_secrets(path).get(name)


def write_secrets(secrets: dict[str, str], path: Path | None = None) -> Path:
    """原子写回 secrets.env：父目录 700、文件 600（set/rm 共用）。"""
    secrets_file = path or secrets_path()
    secrets_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = secrets_file.with_name(f".{secrets_file.name}.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        os.fchmod(fh.fileno(), 0o600)
        for name in sorted(secrets):
            fh.write(f"{name}={secrets[name]}\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, secrets_file)
    os.chmod(secrets_file, 0o600)
    return secrets_file


def set_secret(name: str, value: str, path: Path | None = None) -> Path:
    """写入/更新单个凭据（CLI `secrets set` 入口）。

    读取侧绕过权限检查：本操作随即将文件整体重写为 600——过松权限
    在此被自动修正（而非拒绝写入）。
    """
    secrets_file = path or secrets_path()
    secrets = _read_secrets(secrets_file) if secrets_file.is_file() else {}
    secrets[name] = value
    return write_secrets(secrets, secrets_file)


def remove_secret(name: str, path: Path | None = None) -> bool:
    """删除单个凭据；键不存在返回 False（CLI `secrets rm` 入口）。"""
    secrets_file = path or secrets_path()
    if not secrets_file.is_file():
        return False
    secrets = _read_secrets(secrets_file)
    if name not in secrets:
        return False
    del secrets[name]
    write_secrets(secrets, secrets_file)
    return True

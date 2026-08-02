"""SQL 只读守卫（11.10/49.2：检测器/修复引擎只允许只读 SQL）。

应用层白名单，防误用而非防恶意（本地工具、连接不外泄）。
"""

from __future__ import annotations

from datasentry_core.connectors.errors import UnsafeSqlError

_READONLY_LEADING = frozenset({"select", "show", "describe", "explain", "with"})


def assert_read_only_sql(sql: str) -> None:
    """只读白名单：首 token 限定 + 拒绝多语句。"""
    stripped = sql.strip()
    if not stripped:
        raise UnsafeSqlError("empty sql")
    body = stripped.rstrip(";")
    if ";" in body:
        raise UnsafeSqlError("multi-statement sql is not allowed")
    leading = body.split(None, 1)[0].lower()
    if leading not in _READONLY_LEADING:
        raise UnsafeSqlError(f"sql leading keyword not allowed: {leading}")

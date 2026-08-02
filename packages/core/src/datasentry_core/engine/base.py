"""SQL 执行层协议（Step 3）。

Polars 引擎（ADR-005 归 V1）将实现同一协议，消费方无需感知后端差异。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

import pyarrow as pa

SqlParams = Mapping[str, object] | Sequence[object] | None


@runtime_checkable
class SqlExecutor(Protocol):
    """只读 SQL 执行器协议。"""

    def execute(
        self,
        sql: str,
        params: SqlParams = None,
    ) -> pa.Table:
        """执行只读 SQL，返回 Arrow 表。

        仅允许只读语句（SELECT/WITH/SHOW/DESCRIBE/EXPLAIN，单语句）。
        篡改语句抛 UnsafeSqlError。
        """
        ...

    def close(self) -> None:
        """释放底层连接。关闭后 execute 抛错误。"""
        ...

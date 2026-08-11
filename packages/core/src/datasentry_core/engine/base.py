"""SQL 执行层协议（Step 3）。

Polars 引擎（ADR-005 归 V1）将实现同一协议，消费方无需感知后端差异。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
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


@runtime_checkable
class SetupExecutor(Protocol):
    """连接器内部执行器协议：只读面 + 受信 setup 面（视图/ATTACH/register）。

    FileDataHandle 等连接器基类以此类型持有执行器，便于包装器
    （如 Step 55 的凭据净化包装）在类型安全的前提下替换实现。
    """

    def execute_setup(self, sql: str) -> None: ...

    def register(self, name: str, obj: object) -> None: ...

    def execute(
        self,
        sql: str,
        params: SqlParams = None,
    ) -> pa.Table: ...

    def execute_stream(
        self,
        sql: str,
        batch_size: int = 65536,
    ) -> Iterator[pa.RecordBatch]: ...

    def close(self) -> None: ...

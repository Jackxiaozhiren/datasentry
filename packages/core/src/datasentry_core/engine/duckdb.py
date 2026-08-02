"""DuckDB 只读执行器（ADR-005：MVP 唯一执行引擎）。

- 应用层只读守卫（sql_guard）为唯一安全控制点：DuckDB 的
  enable_external_access=false 会同时禁用 read_csv_auto 文件访问（视图惰性求值），
  故不做连接级沙箱；守卫白名单防误用、单语句限制防拼接
- 支持位置参数（?）与具名参数（$name）
- 统一返回 PyArrow 表
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import duckdb
import pyarrow as pa

from datasentry_core.connectors.errors import UnsafeSqlError
from datasentry_core.engine.base import SqlParams
from datasentry_core.engine.sql_guard import assert_read_only_sql


class DuckDBExecutor:
    """DuckDB 只读执行器。"""

    def __init__(self) -> None:
        self._conn = duckdb.connect(database=":memory:")
        self._closed = False

    def execute_setup(self, sql: str) -> None:
        """内部受信路径：连接器初始化视图等 DDL 使用。

        仅允许受信代码调用（连接器内部）；不得用于外部传入 SQL。
        """
        if self._closed:
            raise UnsafeSqlError("executor is closed")
        self._conn.execute(sql)

    def register(self, name: str, obj: object) -> None:
        """内部受信路径：注册 Arrow 表 / 文件等对象为关系。"""
        if self._closed:
            raise UnsafeSqlError("executor is closed")
        self._conn.register(name, obj)

    def execute(
        self,
        sql: str,
        params: SqlParams = None,
    ) -> pa.Table:
        if self._closed:
            raise UnsafeSqlError("executor is closed")
        assert_read_only_sql(sql)
        if params is None:
            return self._conn.execute(sql).to_arrow_table()
        if isinstance(params, Mapping):
            # duckdb 具名参数：$name
            return self._conn.execute(sql, params).to_arrow_table()
        assert isinstance(params, Sequence) and not isinstance(params, (str, bytes))
        return self._conn.execute(sql, list(params)).to_arrow_table()

    def close(self) -> None:
        if not self._closed:
            self._conn.close()
            self._closed = True

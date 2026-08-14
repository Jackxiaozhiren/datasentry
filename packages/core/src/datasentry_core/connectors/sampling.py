"""抽样数据句柄（Step 71/73，ADR-071/073）：检测器 SQL 顶层 FROM data 改写。

零连接器改动：与底层句柄共享 executor，首次数据访问时把抽样子集物化为
TEMP TABLE（reservoir + REPEATABLE(seed) 可复现，20.3），后续检测器
查询直接读内存表（ADR-073：避免每检测器重复 reservoir 重扫 1e6 行，
bench 抽样档 52s→~15s、峰值 449MB→<300MB）；无 executor 的连接器
（协议兜底）退回抽样子查询重写。
count_rows() 返回抽样行数（该句柄实际可观测的行数）。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from datasentry_core.connectors.base import (
    DataHandle,
    FingerprintMode,
    FrameBatch,
    LoadWarning,
    SamplingMethod,
    SchemaInfo,
)
from datasentry_core.connectors.spec import DataSourceType
from datasentry_core.models.fingerprint import DatasetFingerprint

#: 仓库内检测器/规则/画像 SQL 一律以顶层 FROM data 引用视图（50 处均为该形态）。
#: 重写后残留（子查询内部）不匹配 lookahead，见 _rewrite 守卫。
_FROM_DATA_RE = re.compile(r"\bFROM data\b")

#: 物化 TEMP TABLE 名（与 data 视图同 executor 连接内，无命名冲突）
_SAMPLED_TABLE = "sampled_data"


class SampledDataHandle:
    """包装句柄：把对 data 视图的查询改写为抽样数据（顶层 FROM data 单形态）。

    首次数据访问物化 TEMP TABLE（每次检测器查询复用，避免重复 reservoir
    重扫）；不支持 read_batches（抽样语义面向 SQL 下推）；read_sample
    委托底层（对抽样数据再抽样无意义，交由调用方决定）。
    """

    def __init__(
        self,
        handle: DataHandle,
        n: int,
        seed: int = 42,
        method: SamplingMethod = "reservoir",
    ) -> None:
        if n < 1:
            raise ValueError("n must be >= 1")
        self._inner = handle
        self._n = n
        self._seed = seed
        self._method = method
        self._executor = getattr(handle, "_executor", None)
        self._sampled_ready = False

    def _ensure_sampled_table(self) -> None:
        if self._sampled_ready or self._executor is None:
            return
        # 惰性视图：建表前确保 data 视图已注册（count_rows 触发各连接器
        # _ensure_view；CSV 1e6 行计数 ~0.2s，可接受）
        self._inner.count_rows()
        if self._method == "none":
            ddl = (
                f"CREATE OR REPLACE TEMP TABLE {_SAMPLED_TABLE} AS "
                f"SELECT * FROM data LIMIT {self._n}"
            )
        else:
            # SAMPLE 子句不支持预编译参数，n/seed 均为代码内整数（已校验），安全内联
            ddl = (
                f"CREATE OR REPLACE TEMP TABLE {_SAMPLED_TABLE} AS "
                f"SELECT * FROM data USING SAMPLE reservoir({self._n} ROWS) "
                f"REPEATABLE ({self._seed})"
            )
        self._executor.execute_setup(ddl)
        self._sampled_ready = True

    def _sampled_subquery(self) -> str:
        if self._method == "none":
            return f"(SELECT * FROM data LIMIT {self._n})"
        # SAMPLE 子句不支持预编译参数，n/seed 均为代码内整数（已校验），安全内联
        return (
            f"(SELECT * FROM data USING SAMPLE reservoir({self._n} ROWS) REPEATABLE ({self._seed}))"
        )

    def _rewrite(self, sql: str) -> str:
        if self._executor is not None:
            self._ensure_sampled_table()
            target = f"FROM {_SAMPLED_TABLE}"
        else:
            target = f"FROM {self._sampled_subquery()}"
        rewritten = _FROM_DATA_RE.sub(target, sql)
        # 守卫：重写后不允许残留裸 FROM data（防未覆盖 SQL 形态，如别名/join）
        if re.search(r"\bFROM data\b(?! USING SAMPLE)", rewritten):
            raise ValueError(f"cannot rewrite sampled SQL: {sql!r}")
        return rewritten

    @property
    def source_type(self) -> DataSourceType:
        return self._inner.source_type

    @property
    def source_path(self) -> Path | str | None:
        return self._inner.source_path

    @property
    def table_name(self) -> str | None:
        return self._inner.table_name

    def schema(self) -> SchemaInfo:
        return self._inner.schema()

    def read_batches(self, batch_size: int = 65536) -> Iterator[FrameBatch]:
        return self._inner.read_batches(batch_size)

    def read_sample(self, n: int, method: SamplingMethod = "random") -> FrameBatch:
        return self._inner.read_sample(n, method)

    def sql_aggregate(self, sql: str, params: dict[str, object] | None = None) -> FrameBatch:
        return self._inner.sql_aggregate(self._rewrite(sql), params)

    def count_rows(self) -> int:
        return min(self._n, self._inner.count_rows())

    def fingerprint(self, mode: FingerprintMode = "full") -> DatasetFingerprint:
        return self._inner.fingerprint(mode)

    def content_fingerprint(self) -> str:
        return self._inner.content_fingerprint()

    def stats_fingerprint(self) -> str:
        return self._inner.stats_fingerprint()

    def warnings(self) -> list[LoadWarning]:
        return self._inner.warnings()

    def close(self) -> None:
        self._inner.close()

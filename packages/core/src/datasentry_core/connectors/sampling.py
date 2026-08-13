"""抽样数据句柄（Step 71，ADR-071）：检测器 SQL 顶层 FROM data 重写为抽样子查询。

零连接器改动：与底层句柄共享 executor 与视图，仅重写 SQL（reservoir +
REPEATABLE(seed) 可复现，20.3）；非抽样支撑检测器继续使用原句柄。
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


class SampledDataHandle:
    """包装句柄：把对 data 视图的查询重写到抽样子查询（顶层 FROM data 单形态）。

    不支持 read_batches（抽样语义面向 SQL 下推）；read_sample 委托底层
    （对抽样数据再抽样无意义，交由调用方决定）。
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

    def _sampled_subquery(self) -> str:
        if self._method == "none":
            return f"(SELECT * FROM data LIMIT {self._n})"
        # SAMPLE 子句不支持预编译参数，n/seed 均为代码内整数（已校验），安全内联
        return (
            f"(SELECT * FROM data USING SAMPLE reservoir({self._n} ROWS) REPEATABLE ({self._seed}))"
        )

    def _rewrite(self, sql: str) -> str:
        rewritten = _FROM_DATA_RE.sub(f"FROM {self._sampled_subquery()}", sql)
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

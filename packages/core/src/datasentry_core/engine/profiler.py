"""Profiling engine（Step 4 / 18.2 画像）。

策略：经执行层（DuckDB pushdown）对全部列发起单次聚合查询，
避免 N 列 N 次扫描；1e6 行画像预算 < 60s（42.1 Phase 1 DoD）。

约定：
- 数值列（INTEGER/FLOAT/DOUBLE/DECIMAL…）计算 min/max/mean/std/q25/median/q75；
  字符串列仅统计 distinct/min/max；其余类型取 min/max
- 空字符串在 duckdb 视图路径已被折叠为 NULL（见 CSV 语义约定），null 统计包含空串
- examples 字段刻意留空：脱敏设施在 Step 15（LLM Provider 抽象）之后提供，
  在此之前避免把原始样本写入画像输出
- top_categories 每次对整列 GROUP BY（MVP 直白实现，基准后再优化采样）
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa

from datasentry_core.connectors.base import DataHandle
from datasentry_core.models.profile import ColumnProfile, DatasetProfile

_NUMERIC_TYPES = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "UHUGEINT",
        "FLOAT",
        "REAL",
        "DOUBLE",
        "DECIMAL",
    }
)


def _column_exprs(column: str, quoted: str, numeric: bool) -> list[str]:
    alias = _quote_ident(f"{column}__n")
    exprs = [
        f"count({quoted}) AS {alias}",
        f"count(DISTINCT {quoted}) AS {_quote_ident(f'{column}__distinct')}",
        f"min({quoted}) AS {_quote_ident(f'{column}__min')}",
        f"max({quoted}) AS {_quote_ident(f'{column}__max')}",
    ]
    if numeric:
        exprs += [
            f"avg({quoted}) AS {_quote_ident(f'{column}__mean')}",
            f"stddev({quoted}) AS {_quote_ident(f'{column}__std')}",
            f"quantile_cont({quoted}, 0.25) AS {_quote_ident(f'{column}__q25')}",
            f"quantile_cont({quoted}, 0.5) AS {_quote_ident(f'{column}__median')}",
            f"quantile_cont({quoted}, 0.75) AS {_quote_ident(f'{column}__q75')}",
        ]
    return exprs


class Profiler:
    """对已打开的数据句柄执行画像（只读）。"""

    def __init__(self, handle: DataHandle, dataset_id: str) -> None:
        self._handle = handle
        self._dataset_id = dataset_id

    def profile(self) -> DatasetProfile:
        schema = self._handle.schema()
        columns = [c.name for c in schema.columns]
        if not columns:
            raise ValueError("dataset has no columns")
        quoted = {c: _quote_ident(c) for c in columns}
        row_count = self._handle.count_rows()
        exprs: list[str] = ["count(*) AS __row_count"]
        numeric: dict[str, bool] = {}
        for c in schema.columns:
            is_num = c.physical_type.upper() in _NUMERIC_TYPES
            numeric[c.name] = is_num
            exprs.extend(_column_exprs(c.name, quoted[c.name], is_num))
        table = self._handle.sql_aggregate("SELECT " + ", ".join(exprs) + " FROM data").table
        row = _single_row(table)

        column_profiles: dict[str, ColumnProfile] = {}
        for c in schema.columns:
            name = c.name
            q = quoted[name]
            distinct = _as_int(row.get(f"{name}__distinct"))
            non_null = _as_int(row.get(f"{name}__n"))
            column_profiles[name] = ColumnProfile(
                dataset_id=self._dataset_id,
                column_name=name,
                physical_type=c.physical_type,
                null_ratio=_safe_ratio(row_count - non_null, row_count),
                unique_ratio=_safe_ratio(distinct, non_null),
                distinct_count=distinct,
                min=_as_scalar(row.get(f"{name}__min")),
                q25=_as_float(row.get(f"{name}__q25")),
                median=_as_float(row.get(f"{name}__median")),
                q75=_as_float(row.get(f"{name}__q75")),
                max=_as_scalar(row.get(f"{name}__max")),
                mean=_as_float(row.get(f"{name}__mean")),
                std=_as_float(row.get(f"{name}__std")),
                top_categories=self._top_categories(name, q, distinct),
            )
        return DatasetProfile(
            dataset_id=self._dataset_id,
            row_count=row_count,
            column_count=len(columns),
            column_profiles=column_profiles,
            profiler_version="0.1.0",
        )

    def _top_categories(
        self, column: str, quoted: str, distinct: int
    ) -> list[tuple[str, int]] | None:
        if distinct <= 1 or distinct > 1000:
            # 单值或高基数列不做类别统计（无信息量或代价过高）
            return None
        table = self._handle.sql_aggregate(
            f"SELECT {quoted} AS v, count(*) AS n FROM data "
            f"GROUP BY {quoted} ORDER BY n DESC LIMIT 10"
        ).table
        return [
            (str(v), int(n))
            for v, n in zip(table.column(0).to_pylist(), table.column(1).to_pylist(), strict=True)
        ]


def _quote_ident(column: str) -> str:
    return '"' + column.replace('"', '""') + '"'


def _single_row(table: pa.Table) -> dict[str, Any]:
    if table.num_rows != 1:
        raise AssertionError(f"profiling query returned {table.num_rows} rows")
    return {name: table[name][0].as_py() for name in table.schema.names}


def _as_scalar(value: Any) -> Any:
    return value


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)

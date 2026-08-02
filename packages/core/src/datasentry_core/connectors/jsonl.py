"""JSONL 连接器（Step 18）：duckdb read_json_auto(newline_delimited) 视图。

read_batches 用 SQL LIMIT/OFFSET 分页（duckdb 无 JSONL 流式读接口；
1e6 行预算内可接受，ADR-019）。
"""

from __future__ import annotations

from collections.abc import Iterator

from datasentry_core.connectors.base import FrameBatch
from datasentry_core.connectors.file_based import FileDataHandle
from datasentry_core.connectors.spec import DataSourceSpec, DataSourceType


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class JsonlDataHandle(FileDataHandle):
    """JSONL 数据句柄。"""

    def _ensure_view(self) -> None:
        if self._view_ready:
            return
        self._executor.execute_setup(
            "CREATE OR REPLACE VIEW data AS "
            "SELECT * FROM read_json_auto("
            f"{_sql_string_literal(str(self._path))}, "
            "format='newline_delimited')"
        )
        self._view_ready = True

    def read_batches(self, batch_size: int = 65536) -> Iterator[FrameBatch]:
        self._check_open()
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        self._ensure_view()
        offset = 0
        idx = 0
        while True:
            table = self._executor.execute(f"SELECT * FROM data LIMIT {batch_size} OFFSET {offset}")
            if table.num_rows == 0:
                return
            yield FrameBatch(
                source=self._spec,
                table=table,
                batch_index=idx,
                row_offset=offset,
            )
            offset += table.num_rows
            idx += 1


class JsonlConnector:
    """JSONL 连接器（7.1）。"""

    connector_id = "jsonl"
    display_name = "JSONL"

    def supports(self, source: DataSourceSpec) -> bool:
        return source.source_type == DataSourceType.JSONL and source.path is not None

    def open(self, source: DataSourceSpec) -> JsonlDataHandle:
        return JsonlDataHandle(source)

    def close(self) -> None:
        pass


__all__ = ["JsonlConnector", "JsonlDataHandle"]

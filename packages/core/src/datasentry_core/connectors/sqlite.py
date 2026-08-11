"""SQLite 数据源连接器（Step 54：V3 多数据源第一落点）。

对 `.db` / `.sqlite` / `.sqlite3` 文件经 DuckDB 的 sqlite 扩展
（sqlite_scan 表函数）暴露只读 data 视图，复用 FileDataHandle
共享实现（schema/read_sample/sql_aggregate/count_rows/fingerprint/
warnings）。表名必填（spec.table_name）；路径/表名一律字符串
字面量转义（防注入）。
"""

from __future__ import annotations

from collections.abc import Iterator

import pyarrow as pa

from datasentry_core.connectors.base import FrameBatch
from datasentry_core.connectors.errors import DataSourceNotFoundError
from datasentry_core.connectors.file_based import FileDataHandle
from datasentry_core.connectors.spec import DataSourceSpec, DataSourceType


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class SQLiteDataHandle(FileDataHandle):
    """SQLite 文件表数据句柄（只读，表名必填）。"""

    def __init__(self, spec: DataSourceSpec) -> None:
        super().__init__(spec)
        if spec.path is None or not spec.path.is_file():
            raise DataSourceNotFoundError(f"sqlite file not found: {spec.path}")
        if spec.table_name is None:
            raise DataSourceNotFoundError("sqlite connector requires table_name")
        self._table_name = spec.table_name

    @property
    def table_name(self) -> str | None:
        return self._table_name

    def _ensure_view(self) -> None:
        if self._view_ready:
            return
        assert self._path is not None
        self._executor.execute_setup("LOAD sqlite")
        self._executor.execute_setup(
            "CREATE OR REPLACE VIEW data AS "
            f"SELECT * FROM sqlite_scan({_sql_string_literal(str(self._path))}, "
            f"{_sql_string_literal(self._table_name)})"
        )
        self._view_ready = True

    def read_batches(self, batch_size: int = 65536) -> Iterator[FrameBatch]:
        self._check_open()
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        self._ensure_view()
        row_offset = 0
        for idx, batch in enumerate(
            self._executor.execute_stream("SELECT * FROM data", batch_size)
        ):
            table = pa.Table.from_batches([batch])
            yield FrameBatch(
                source=self._spec,
                table=table,
                batch_index=idx,
                row_offset=row_offset,
            )
            row_offset += table.num_rows


class SqliteConnector:
    """SQLite 文件连接器（Step 54，V3 多数据源第一落点）。"""

    connector_id = "sqlite"
    display_name = "SQLite"

    def supports(self, source: DataSourceSpec) -> bool:
        return source.source_type == DataSourceType.SQLITE and source.path is not None

    def open(self, source: DataSourceSpec) -> SQLiteDataHandle:
        if source.table_name is None:
            raise DataSourceNotFoundError("sqlite connector requires table_name")
        return SQLiteDataHandle(source)

    def close(self) -> None:
        pass

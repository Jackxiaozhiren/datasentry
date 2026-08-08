"""DuckDB 文件连接器（Step 38，V1：数据库型数据源落地）。

对 `.duckdb` 文件做 READ_ONLY ATTACH，表经只读视图暴露给
共享 FileDataHandle 实现（schema/read_sample/sql_aggregate/
count_rows/fingerprint/warnings）。表名必填（spec.table_name），
schema 名经 options["schema"] 传入；标识符一律双引号转义。
"""

from __future__ import annotations

from collections.abc import Iterator

import pyarrow as pa

from datasentry_core.connectors.base import FrameBatch
from datasentry_core.connectors.errors import DataSourceNotFoundError
from datasentry_core.connectors.file_based import FileDataHandle
from datasentry_core.connectors.spec import DataSourceSpec, DataSourceType

#: ATTACH 目标别名（连接器内部名，避开用户数据命名冲突概率极低）
_DB_ALIAS = "src_db"


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ident(value: str) -> str:
    """双引号标识符转义（防表名/schema 名注入）。"""
    return '"' + value.replace('"', '""') + '"'


class DuckDBDataHandle(FileDataHandle):
    """DuckDB 文件表数据句柄。"""

    def __init__(self, spec: DataSourceSpec) -> None:
        super().__init__(spec)
        if spec.path is None or not spec.path.is_file():
            raise DataSourceNotFoundError(f"duckdb file not found: {spec.path}")
        if spec.table_name is None:
            raise DataSourceNotFoundError("duckdb connector requires table_name")
        self._table_name = spec.table_name
        self._schema_name = spec.options.get("schema")

    def _ensure_view(self) -> None:
        if self._view_ready:
            return
        assert self._path is not None
        self._executor.execute_setup(
            f"ATTACH {_sql_string_literal(str(self._path))} AS {_DB_ALIAS} (READ_ONLY)"
        )
        table_ident = (
            f"{_ident(str(self._schema_name))}.{_ident(self._table_name)}"
            if self._schema_name
            else _ident(self._table_name)
        )
        self._executor.execute_setup(
            f"CREATE OR REPLACE VIEW data AS SELECT * FROM {_DB_ALIAS}.{table_ident}"
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


class DuckdbConnector:
    """DuckDB 文件连接器（7.1 V1 扩展）。"""

    connector_id = "duckdb"
    display_name = "DuckDB"

    def supports(self, source: DataSourceSpec) -> bool:
        return source.source_type == DataSourceType.DUCKDB and source.path is not None

    def open(self, source: DataSourceSpec) -> DuckDBDataHandle:
        if source.table_name is None:
            raise DataSourceNotFoundError(
                "duckdb connector requires table_name (e.g. scan --table <table>)"
            )
        return DuckDBDataHandle(source)

    def close(self) -> None:
        pass


__all__ = ["DuckDBDataHandle", "DuckdbConnector"]

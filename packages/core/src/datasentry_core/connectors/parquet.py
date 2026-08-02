"""Parquet 连接器（Step 18）：duckdb read_parquet 视图 + pyarrow 流式批读。"""

from __future__ import annotations

from collections.abc import Iterator

import pyarrow as pa
import pyarrow.parquet as pq

from datasentry_core.connectors.base import FrameBatch
from datasentry_core.connectors.file_based import FileDataHandle
from datasentry_core.connectors.spec import DataSourceSpec, DataSourceType


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class ParquetDataHandle(FileDataHandle):
    """Parquet 数据句柄。"""

    def _ensure_view(self) -> None:
        if self._view_ready:
            return
        self._executor.execute_setup(
            "CREATE OR REPLACE VIEW data AS "
            f"SELECT * FROM read_parquet({_sql_string_literal(str(self._path))})"
        )
        self._view_ready = True

    def read_batches(self, batch_size: int = 65536) -> Iterator[FrameBatch]:
        self._check_open()
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        row_offset = 0
        with pq.ParquetFile(self._path) as reader:
            for idx, batch in enumerate(reader.iter_batches(batch_size=batch_size)):
                table = pa.Table.from_batches([batch])
                yield FrameBatch(
                    source=self._spec,
                    table=table,
                    batch_index=idx,
                    row_offset=row_offset,
                )
                row_offset += table.num_rows


class ParquetConnector:
    """Parquet 连接器（7.1）。"""

    connector_id = "parquet"
    display_name = "Parquet"

    def supports(self, source: DataSourceSpec) -> bool:
        return source.source_type == DataSourceType.PARQUET and source.path is not None

    def open(self, source: DataSourceSpec) -> ParquetDataHandle:
        return ParquetDataHandle(source)

    def close(self) -> None:
        pass


__all__ = ["ParquetConnector", "ParquetDataHandle"]

"""连接器协议与共享类型（49.1）。

统一返回 PyArrow 帧（FrameBatch），与 DuckDB 零拷贝互操作（ADR-005）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal, Protocol

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field

from datasentry_core.connectors.spec import DataSourceSpec, DataSourceType
from datasentry_core.models.enums import Severity
from datasentry_core.models.fingerprint import DatasetFingerprint

SamplingMethod = Literal[
    "random", "stratified", "reservoir", "time_based", "rare_oversampling", "none"
]
FingerprintMode = Literal["full", "sampled", "metadata_only"]


class FrameBatch(BaseModel):
    """一批数据（49.1）：PyArrow Table + 来源与位置元信息。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: DataSourceSpec
    table: pa.Table
    batch_index: int = 0
    row_offset: int = 0

    @property
    def row_count(self) -> int:
        """本批行数。"""
        rows = self.table.num_rows
        assert isinstance(rows, int)
        return rows

    @property
    def column_names(self) -> list[str]:
        """本批列名。"""
        return list(self.table.column_names)


class ColumnInfo(BaseModel):
    """单列信息（49.1 describe 输出）。"""

    name: str
    physical_type: str
    nullable: bool = True


class SchemaInfo(BaseModel):
    """数据源 Schema 描述。"""

    columns: list[ColumnInfo] = Field(default_factory=list)

    @property
    def column_names(self) -> list[str]:
        """列名列表。"""
        return [c.name for c in self.columns]


class LoadWarning(BaseModel):
    """加载期警告（如 CSV 公式注入标记），value_preview 必须截断脱敏。"""

    row: int = Field(ge=0)
    column: str
    message: str
    severity: Severity = Severity.MEDIUM
    value_preview: str = ""


class DataHandle(Protocol):
    """打开后的数据句柄（49.1）。连接器不持有分析逻辑。"""

    @property
    def source_type(self) -> DataSourceType: ...
    @property
    def source_path(self) -> Path | None: ...

    def schema(self) -> SchemaInfo: ...
    def read_batches(self, batch_size: int = 65536) -> Iterator[FrameBatch]: ...
    def read_sample(self, n: int, method: SamplingMethod = "random") -> FrameBatch: ...
    def sql_aggregate(self, sql: str, params: dict[str, Any] | None = None) -> FrameBatch: ...
    def count_rows(self) -> int: ...
    def fingerprint(self, mode: FingerprintMode = "full") -> DatasetFingerprint: ...
    def warnings(self) -> list[LoadWarning]: ...
    def close(self) -> None: ...


class DataConnector(Protocol):
    """数据源连接器（49.1）。"""

    connector_id: str
    display_name: str

    def supports(self, source: DataSourceSpec) -> bool: ...
    def open(self, source: DataSourceSpec) -> DataHandle: ...
    def close(self) -> None: ...

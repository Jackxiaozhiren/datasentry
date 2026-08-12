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
    def source_path(self) -> Path | str | None: ...

    @property
    def table_name(self) -> str | None:
        """DuckDB 等需要表名的数据源返回表名，其他返回 None（Step 40）。"""
        return None

    def schema(self) -> SchemaInfo: ...
    def read_batches(self, batch_size: int = 65536) -> Iterator[FrameBatch]: ...
    def read_sample(self, n: int, method: SamplingMethod = "random") -> FrameBatch: ...
    def sql_aggregate(self, sql: str, params: dict[str, Any] | None = None) -> FrameBatch: ...
    def count_rows(self) -> int: ...
    def fingerprint(self, mode: FingerprintMode = "full") -> DatasetFingerprint: ...
    def content_fingerprint(self) -> str:
        """内容指纹（Step 55）：文件源=文件 SHA-256；无文件字节源（如 PostgreSQL）
        由子类实现内容哈希——调度器变更感知（Step 53/55）的跳过判定基准。"""
        ...

    def stats_fingerprint(self) -> str:
        """统计层指纹（Step 58，ADR-058）：schema_hash + row_count 组合，零内容读取。

        调度两层快速失效第一层：统计层一致才计算内容指纹；统计层变化
        立即判定变更。远程源（PG/MySQL/云文件）由 FileDataHandle 默认
        实现（DESCRIBE 目录查询 + count），文件源不参与（沿用 Step 53
        单层文件 SHA-256）。协议默认体：本地文件句柄不实现（调度器不会
        对本地文件调用），实现者不覆盖即视为不适用。"""
        raise NotImplementedError("stats_fingerprint is not supported by this handle")

    def warnings(self) -> list[LoadWarning]: ...
    def close(self) -> None: ...


class DataConnector(Protocol):
    """数据源连接器（49.1）。"""

    connector_id: str
    display_name: str

    def supports(self, source: DataSourceSpec) -> bool: ...
    def open(self, source: DataSourceSpec) -> DataHandle: ...
    def close(self) -> None: ...

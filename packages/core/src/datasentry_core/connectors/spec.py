"""数据源规格（49.1/四十三第 10 项：凭据不入库，只存引用）。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from datasentry_core.models.evidence import utcnow


class DataSourceType(StrEnum):
    """数据源类型（7.1 MVP 六个 + V1 扩展）。"""

    CSV = "csv"
    PARQUET = "parquet"
    JSONL = "jsonl"
    XLSX = "xlsx"
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    DUCKDB = "duckdb"


#: 文件扩展名 → 数据源类型（Step 18/57：scan_file 与调度器按后缀推断，
#: 云存储文件源仅限 CSV/Parquet/JSONL，见 RemoteFileConnector）
EXT_TO_SOURCE_TYPE: dict[str, DataSourceType] = {
    ".csv": DataSourceType.CSV,
    ".tsv": DataSourceType.CSV,
    ".parquet": DataSourceType.PARQUET,
    ".pq": DataSourceType.PARQUET,
    ".jsonl": DataSourceType.JSONL,
    ".ndjson": DataSourceType.JSONL,
    ".xlsx": DataSourceType.XLSX,
    ".duckdb": DataSourceType.DUCKDB,
    ".db": DataSourceType.SQLITE,
    ".sqlite": DataSourceType.SQLITE,
    ".sqlite3": DataSourceType.SQLITE,
}


class DataSourceSpec(BaseModel):
    """数据源规格：描述"从哪读、怎么读"，不含凭据原文。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_type: DataSourceType
    path: Path | str | None = None
    table_name: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    connection_ref: str | None = None
    created_at: datetime = Field(default_factory=utcnow)

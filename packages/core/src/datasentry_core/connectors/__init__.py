"""连接器包：数据源读取抽象（49.1/四十一 Step 2）。"""

from datasentry_core.connectors.base import (
    ColumnInfo,
    DataConnector,
    DataHandle,
    FingerprintMode,
    FrameBatch,
    LoadWarning,
    SamplingMethod,
    SchemaInfo,
)
from datasentry_core.connectors.csv import CsvConnector
from datasentry_core.connectors.duckdb import DuckdbConnector
from datasentry_core.connectors.errors import (
    ConnectorError,
    DataSourceNotFoundError,
    UnsafeSqlError,
    UnsupportedFormatError,
)
from datasentry_core.connectors.jsonl import JsonlConnector
from datasentry_core.connectors.mysql import MySQLConnector, MySQLDataHandle
from datasentry_core.connectors.parquet import ParquetConnector
from datasentry_core.connectors.postgres import PostgresConnector, PostgresDataHandle
from datasentry_core.connectors.registry import ConnectorRegistry, default_registry
from datasentry_core.connectors.spec import DataSourceSpec, DataSourceType
from datasentry_core.connectors.sqlite import SqliteConnector
from datasentry_core.connectors.xlsx import XlsxConnector

__all__ = [
    "ColumnInfo",
    "ConnectorError",
    "ConnectorRegistry",
    "CsvConnector",
    "DataConnector",
    "DataHandle",
    "DataSourceNotFoundError",
    "DataSourceSpec",
    "DataSourceType",
    "DuckdbConnector",
    "FingerprintMode",
    "FrameBatch",
    "JsonlConnector",
    "LoadWarning",
    "MySQLConnector",
    "MySQLDataHandle",
    "ParquetConnector",
    "PostgresConnector",
    "PostgresDataHandle",
    "SamplingMethod",
    "SchemaInfo",
    "SqliteConnector",
    "UnsafeSqlError",
    "UnsupportedFormatError",
    "XlsxConnector",
    "default_registry",
]

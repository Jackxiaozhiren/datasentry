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
from datasentry_core.connectors.errors import (
    ConnectorError,
    DataSourceNotFoundError,
    UnsafeSqlError,
    UnsupportedFormatError,
)
from datasentry_core.connectors.registry import ConnectorRegistry, default_registry
from datasentry_core.connectors.spec import DataSourceSpec, DataSourceType

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
    "FingerprintMode",
    "FrameBatch",
    "LoadWarning",
    "SamplingMethod",
    "SchemaInfo",
    "UnsafeSqlError",
    "UnsupportedFormatError",
    "default_registry",
]

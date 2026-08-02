"""连接器注册表（49.1）：按 source_type 分发。"""

from __future__ import annotations

from datasentry_core.connectors.base import DataConnector, DataHandle
from datasentry_core.connectors.csv import CsvConnector
from datasentry_core.connectors.errors import UnsupportedFormatError
from datasentry_core.connectors.jsonl import JsonlConnector
from datasentry_core.connectors.parquet import ParquetConnector
from datasentry_core.connectors.spec import DataSourceSpec
from datasentry_core.connectors.xlsx import XlsxConnector


class ConnectorRegistry:
    """连接器注册表：register/get/list/get_for/open。"""

    def __init__(self) -> None:
        self._connectors: dict[str, DataConnector] = {}

    def register(self, connector: DataConnector) -> None:
        """注册连接器，重复注册抛 ValueError。"""
        if connector.connector_id in self._connectors:
            raise ValueError(f"connector already registered: {connector.connector_id}")
        self._connectors[connector.connector_id] = connector

    def get(self, connector_id: str) -> DataConnector | None:
        """按 id 获取连接器。"""
        return self._connectors.get(connector_id)

    def list(self) -> list[DataConnector]:
        """全部已注册连接器。"""
        return list(self._connectors.values())

    def get_for(self, source: DataSourceSpec) -> DataConnector:
        """找到支持该数据源的连接器，找不到抛 UnsupportedFormatError。"""
        for connector in self._connectors.values():
            if connector.supports(source):
                return connector
        raise UnsupportedFormatError(f"no connector supports source type={source.source_type}")

    def open(self, source: DataSourceSpec) -> DataHandle:
        """打开数据源，返回 DataHandle。"""
        return self.get_for(source).open(source)


def default_registry() -> ConnectorRegistry:
    """默认注册表：MVP 文件型连接器集合（数据库型归 V1，ADR-019）。"""
    registry = ConnectorRegistry()
    registry.register(CsvConnector())
    registry.register(ParquetConnector())
    registry.register(JsonlConnector())
    registry.register(XlsxConnector())
    return registry

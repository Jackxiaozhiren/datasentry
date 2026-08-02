"""连接器错误类型。SDK 层的 DataSentryError 体系在 SDK 包建立时映射（47.2）。"""

from __future__ import annotations


class ConnectorError(Exception):
    """连接器基类错误。"""


class UnsupportedFormatError(ConnectorError):
    """数据源格式不被任何已注册连接器支持。"""


class DataSourceNotFoundError(ConnectorError):
    """数据源路径/资源不存在或不可读。"""


class UnsafeSqlError(ConnectorError):
    """聚合查询未通过只读白名单校验（11.10/49.2）。"""

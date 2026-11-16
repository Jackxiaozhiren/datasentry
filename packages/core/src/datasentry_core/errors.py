"""`DataSentryError` 家族（阶段 3 第 1 项切片 a，阶段 3 提案 §1）。

四类 kind 各带三面映射（CLI 退出码 / REST 状态码 / MCP JSON-RPC code），
切片 b 起把修复引擎的四个拒绝搬到对应子类，切片 c 把三个面改为从这里读。
基类继承 `ValueError`：修复引擎的拒绝全 raise `ValueError`，现有 `except ValueError`
调用方在切片 b 搬运之前不能破。
"""

from __future__ import annotations

from enum import Enum


class ErrorKind(Enum):
    """四类错误，每类自带三面映射。

    数值来源（不是发明）：CLI 退出码照 `cli.py:39-43` 的既有常量
    （`EXIT_CONFIG=2` / `EXIT_ERROR=3` / `EXIT_SOURCE_UNAVAILABLE=4`），
    REST 照 `api.py:240-256` 的 `_error()` 现有分支（400/404/500），
    MCP 照 `mcp_server.py` 的 JSON-RPC 约定（-32602 caller / -32603 server）。
    `policy_refusal` 的 REST 422 与现有 `_error()` 的 ValueError→422 路径**一致**——
    切片 b 搬运四个拒绝时 REST 行为零变化（最安全的切片 b），切片 c 才决定
    要不要把哪一面改成更细的状态码。
    """

    USER_ERROR = (2, 400, -32602)
    DATA_SOURCE_UNREACHABLE = (4, 404, -32602)
    POLICY_REFUSAL = (3, 422, -32602)
    INTERNAL_FAULT = (3, 500, -32603)

    def __init__(self, exit_code: int, http_status: int, mcp_code: int) -> None:
        self.exit_code = exit_code
        self.http_status = http_status
        self.mcp_code = mcp_code


class DataSentryError(ValueError):
    """审计四面的共享根：kind 决定三面映射，消息原样到人。"""

    kind: ErrorKind = ErrorKind.INTERNAL_FAULT

    def __init__(self, message: str, *, kind: ErrorKind | None = None) -> None:
        super().__init__(message)
        if kind is not None:
            self.kind = kind

    @property
    def exit_code(self) -> int:
        return self.kind.exit_code

    @property
    def http_status(self) -> int:
        return self.kind.http_status

    @property
    def mcp_code(self) -> int:
        return self.kind.mcp_code


class UserError(DataSentryError):
    """调用方给错了参数或配置——不是产品的错。"""

    kind: ErrorKind = ErrorKind.USER_ERROR


class DataSourceUnreachableError(DataSentryError):
    """数据源不存在、不可读或格式不支持。"""

    kind: ErrorKind = ErrorKind.DATA_SOURCE_UNREACHABLE


class PolicyRefusalError(DataSentryError):
    """产品按策略拒绝了一个请求（路径越界、写源、上限封顶）。"""

    kind: ErrorKind = ErrorKind.POLICY_REFUSAL


class InternalFaultError(DataSentryError):
    """产品自身的错误——调用方无过错，500 是对的。"""

    kind: ErrorKind = ErrorKind.INTERNAL_FAULT

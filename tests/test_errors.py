"""`DataSentryError` 家族的契约测试（阶段 3 第 1 项切片 a）。

设计来自 阶段 3 提案 §1：四类 kind 各带三面映射（CLI 退出码 / REST 状态码 /
MCP JSON-RPC code），从同一个异常出发——用户错不能变成 500，内部错不能变成 exit-0。
基类继承 `ValueError`（修复引擎的四个拒绝全 raise `ValueError`，现有 `except ValueError`
调用方必须不破），切片 b 搬运调用点时才换成各自的 kind。
"""

from __future__ import annotations

import pytest

from datasentry_core.errors import (
    DataSentryError,
    DataSourceUnreachableError,
    ErrorKind,
    InternalFaultError,
    PolicyRefusalError,
    UserError,
)


def test_kind_carries_the_three_surface_mappings() -> None:
    """每类 kind 的三面映射与各面现有约定对齐。"""
    assert ErrorKind.USER_ERROR.exit_code == 2  # EXIT_CONFIG
    assert ErrorKind.USER_ERROR.http_status == 400
    assert ErrorKind.USER_ERROR.mcp_code == -32602
    assert ErrorKind.DATA_SOURCE_UNREACHABLE.exit_code == 4  # EXIT_SOURCE_UNAVAILABLE
    assert ErrorKind.DATA_SOURCE_UNREACHABLE.http_status == 404
    assert ErrorKind.DATA_SOURCE_UNREACHABLE.mcp_code == -32602
    assert ErrorKind.POLICY_REFUSAL.exit_code == 3  # EXIT_ERROR
    assert ErrorKind.POLICY_REFUSAL.http_status == 422  # matches existing ValueError->422
    assert ErrorKind.POLICY_REFUSAL.mcp_code == -32602
    assert ErrorKind.INTERNAL_FAULT.exit_code == 3  # EXIT_ERROR
    assert ErrorKind.INTERNAL_FAULT.http_status == 500
    assert ErrorKind.INTERNAL_FAULT.mcp_code == -32603


def test_the_four_kinds_cover_the_proposal_without_overlap() -> None:
    """四类 kind 恰好是提案点名的四类，且三面映射两两不同（不然一个面无法区分两类）。"""
    kinds = list(ErrorKind)
    assert len(kinds) == 4
    assert {k.name for k in kinds} == {
        "USER_ERROR",
        "DATA_SOURCE_UNREACHABLE",
        "POLICY_REFUSAL",
        "INTERNAL_FAULT",
    }
    triples = [(k.exit_code, k.http_status, k.mcp_code) for k in kinds]
    assert len(set(triples)) == 4, f"two kinds share a full triple: {triples}"


def test_base_is_a_valueerror_so_existing_except_valueerror_keeps_working() -> None:
    """提案切片 a 的硬约束：基类继承 `ValueError`，现有调用方不破。"""
    assert issubclass(DataSentryError, ValueError)
    with pytest.raises(ValueError):
        raise UserError("bad input")


def test_subclasses_are_distinguishable_by_class_and_kind() -> None:
    """四个具名子类各自绑定一个 kind，except 按类名即可分流。"""
    assert DataSourceUnreachableError("x").kind is ErrorKind.DATA_SOURCE_UNREACHABLE
    assert PolicyRefusalError("x").kind is ErrorKind.POLICY_REFUSAL
    assert InternalFaultError("x").kind is ErrorKind.INTERNAL_FAULT
    assert UserError("x").kind is ErrorKind.USER_ERROR
    for cls in (DataSourceUnreachableError, PolicyRefusalError, InternalFaultError, UserError):
        assert issubclass(cls, DataSentryError)


def test_the_base_carries_its_kind_and_the_mappings_travel_with_it() -> None:
    """base 可直接抛（带 kind 参数），三面映射从实例可读——四个面不必 import 枚举。"""
    exc = DataSentryError("too many rows", kind=ErrorKind.POLICY_REFUSAL)
    assert exc.kind is ErrorKind.POLICY_REFUSAL
    assert exc.exit_code == 3
    assert exc.http_status == 422
    assert exc.mcp_code == -32602

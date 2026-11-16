"""`api._error()` 对 `DataSentryError` 的四类 kind 各读各的 `http_status`（阶段 3 第 1 项切片 c）。

切片 b 用显式分支把 `PolicyRefusalError→422` 从"碰巧走对"变成有意走对；切片 c 把这条显式分支
推广到整个家族——四类 kind 各读各的映射，与 `errors.ErrorKind` 的枚举一致，而不是逐类写分支。
"""

from __future__ import annotations

from datasentry.api import _error
from datasentry_core.errors import (
    DataSentryError,
    DataSourceUnreachableError,
    ErrorKind,
    InternalFaultError,
    PolicyRefusalError,
    UserError,
)


def test_each_kind_reads_its_own_http_status() -> None:
    """四类 kind 的 REST 状态码从枚举映射读取——与 `ErrorKind.http_status` 一致。"""
    for cls, kind in [
        (UserError, ErrorKind.USER_ERROR),
        (DataSourceUnreachableError, ErrorKind.DATA_SOURCE_UNREACHABLE),
        (PolicyRefusalError, ErrorKind.POLICY_REFUSAL),
        (InternalFaultError, ErrorKind.INTERNAL_FAULT),
    ]:
        exc = cls("test")
        assert _error(exc) == kind.http_status, (
            f"{cls.__name__}: _error() gave {_error(exc)}, kind says {kind.http_status}"
        )


def test_the_base_with_an_explicit_kind_reads_that_kind() -> None:
    """base 可带 kind 参数直接抛，`_error()` 读出的是那个 kind 的映射。"""
    exc = DataSentryError("too many rows", kind=ErrorKind.POLICY_REFUSAL)
    assert _error(exc) == 422
    exc2 = DataSentryError("disk full", kind=ErrorKind.INTERNAL_FAULT)
    assert _error(exc2) == 500


def test_a_refusal_cannot_become_a_500_and_a_fault_cannot_become_a_4xx() -> None:
    """提案 §1 的原话，钉成判据。"""
    assert _error(PolicyRefusalError("refusal")) != 500
    assert _error(InternalFaultError("fault")) >= 500

"""错误分类体系的三面映射判据（阶段 3 第 1 项切片 b/c）。

`PolicyRefusalError` 是 `ValueError` 子类，所以现有 `except ValueError` 调用方不破。
REST 的 `_error()` 从 kind 枚举读 `http_status`：策略拒绝得到 422（与旧的 ValueError
fallthrough 同码，但从"碰巧走对"变成"有意走对"），内部故障得到 500。MCP 与 CLI 各读
`mcp_code` / `exit_code`，四类不再挤在同一个 catch-all 里。

本批只搬运 main 上已经存在的那个拒绝点（`_require_copy_writer`）。另外三个拒绝点和
`_require_json_writable` 属于 JSON cell-carry 等后续切片，尚未进入 main，因此不在这里
断言——测试不能引用不存在的函数。
"""

from __future__ import annotations

import pathlib

import pytest

from datasentry.api import _error
from datasentry_core.errors import PolicyRefusalError
from datasentry_core.repair.engine import RepairEngine


def test_require_copy_writer_raises_policy_refusal() -> None:
    """`_require_copy_writer` 是四拒绝之一：类型必须从 ValueError 变成 PolicyRefusalError。"""
    from datasentry_core.connectors.spec import DataSourceType

    with pytest.raises(PolicyRefusalError, match="does not support"):
        RepairEngine._require_copy_writer(DataSourceType.SQLITE)
    # 且它仍是 ValueError——现有调用方的 except ValueError 不破
    with pytest.raises(ValueError, match="does not support"):
        RepairEngine._require_copy_writer(DataSourceType.SQLITE)


def test_api_error_maps_policy_refusal_to_422() -> None:
    """`_error()` 显式认得 PolicyRefusalError → 422（与 ValueError 路径同码，但路径是有意的）。"""
    assert _error(PolicyRefusalError("refusal")) == 422
    # 且不是 500——提案点名的形状："a refusal cannot become a 500"
    assert _error(PolicyRefusalError("refusal")) != 500


def _mcp_dispatch_error(exc: Exception, tmp_path: pathlib.Path) -> dict[str, object]:
    """让一个真实已注册的工具抛出 `exc`，走完整的 dispatch 错误分支。

    不用 `repair_apply`：那个工具名在 main 上不存在，未知工具本来就是 -32602，
    测试会在映射分支删掉后仍然通过（假绿）。批量修复处理器又把逐条异常吞进
    `errors` 字典，到不了 dispatch。这里替换 `detectors_list` 的 handler——schema
    为空、无副作用——专门钉住 dispatch 这一层。
    """
    from unittest.mock import patch as mock_patch

    from datasentry.mcp_server import McpServer

    def _raise() -> None:
        raise exc

    server = McpServer(project=tmp_path / "ws")
    try:
        with mock_patch.dict(server._handlers, {"detectors_list": _raise}):
            response = server._handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "detectors_list", "arguments": {}},
                }
            )
        assert response is not None
        assert "error" in response, f"expected a JSON-RPC error, got {response}"
        error = response["error"]
        assert isinstance(error, dict)
        return error
    finally:
        server.close()


def test_mcp_dispatch_maps_policy_refusal_to_32602(tmp_path: pathlib.Path) -> None:
    """策略拒绝是 caller 侧的事，不能落进 -32603 "Server error"。"""
    from datasentry_core.errors import InternalFaultError

    refusal = _mcp_dispatch_error(PolicyRefusalError("no copy writer for sqlite"), tmp_path)
    assert refusal["code"] == -32602, f"expected -32602, got {refusal}"
    # 同一条 dispatch 上，内部故障必须仍是 -32603：新增分支只按 kind 分流
    fault = _mcp_dispatch_error(InternalFaultError("disk full"), tmp_path)
    assert fault["code"] == -32603, f"expected -32603, got {fault}"


def test_cli_preview_policy_refusal_exits_3(tmp_path: pathlib.Path) -> None:
    """CLI 面：`repair preview` 的策略拒绝读 `exc.exit_code`（3），不再回 EXIT_CONFIG(2)。

    之前的 preview handler 把所有 ValueError 统一返回 EXIT_CONFIG(2)——策略拒绝和
    参数错误拿到同一个退出码，调用方分不清。切片 c 让 DataSentryError 的
    exit_code 从 kind 枚举来：PolicyRefusalError 的 exit_code 是 3。
    """
    from unittest.mock import patch as mock_patch

    from datasentry.cli import EXIT_CONFIG, EXIT_ERROR, main

    ws = tmp_path / "ws"
    ws.mkdir()
    csv = tmp_path / "d.csv"
    csv.write_text("id\n1\n", encoding="utf-8")

    with (
        mock_patch(
            "datasentry.client.DataSentry.repair_preview",
            side_effect=PolicyRefusalError("no copy writer for sqlite"),
        ),
        mock_patch(
            "sys.argv",
            [
                "datasentry",
                "--project",
                str(ws),
                "repair",
                "preview",
                "--file",
                str(csv),
                "some-issue",
            ],
        ),
    ):
        rc = main()
    assert rc == EXIT_ERROR, f"expected EXIT_ERROR({EXIT_ERROR}) for a policy refusal, got {rc}"
    assert rc != EXIT_CONFIG, "a policy refusal is not a config error"

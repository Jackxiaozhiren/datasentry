"""四端语义一致的两条钉死判据（`D1-02` 的 CLI 半 + `D7-15`）。

`D1-02` 的形态是"同一个参数在四端各写一遍，其中一端少几个取值"。MCP 那半在行 r1 修好了，
CLI 的取值也被并行工作补齐，但**没有任何东西保证下一次有人加第 7 个方法时三端一起改**——
所以这里钉的是"相等"，不是"包含六个"。

`D7-15` 的形态是 argparse 的父子解析器共享一个命名空间：子命令自己声明的 `--project`
带 `default=None`，会在解析时把全局 `--project` 的值覆写成 None，于是
`datasentry --project X mcp` 静默落在当前目录。
"""

from __future__ import annotations

import argparse
import typing

from datasentry.cli import build_parser
from datasentry.mcp_server import SAMPLING_METHODS
from datasentry_core.models.scan import SamplingConfig

# core 的 `Literal` 是唯一事实源。取 annotation 而不是 default：default 是字符串 "reservoir"，
# 对它 get_args 得到空元组——而空元组能让任何 `== ()` 断言悄悄成立。
CORE_METHODS: tuple[str, ...] = typing.get_args(SamplingConfig.model_fields["method"].annotation)


def _subparser(name: str) -> argparse.ArgumentParser:
    """argparse has no public accessor for a sub-parser, so the private hop lives here, once."""
    action = build_parser()._subparsers._group_actions[0]
    parser = action.choices[name]
    assert isinstance(parser, argparse.ArgumentParser), name
    return parser


def _cli_sampling_methods() -> tuple[str, ...]:
    for action in _subparser("scan")._actions:
        if "--sampling-method" in action.option_strings:
            return tuple(str(choice) for choice in action.choices)
    raise AssertionError("scan has no --sampling-method flag; the surface this test pins moved")


class TestSamplingMethodParity:
    """core 的 `Literal` 是唯一事实源，CLI 与 MCP 必须与它逐项相等。"""

    def test_core_is_the_source_of_truth(self) -> None:
        """Guards the empty-tuple trap described above: a wrong extraction yields () and passes."""
        assert CORE_METHODS == (
            "random",
            "stratified",
            "reservoir",
            "time_based",
            "rare_oversampling",
            "none",
        )

    def test_cli_choices_equal_core(self) -> None:
        assert _cli_sampling_methods() == CORE_METHODS

    def test_mcp_advertises_the_same_set_as_core(self) -> None:
        assert SAMPLING_METHODS == CORE_METHODS

    def test_the_three_surfaces_do_not_drift_apart(self) -> None:
        """一条式子看住三端：任何一端单边增删都会在这里红。"""
        assert _cli_sampling_methods() == CORE_METHODS == SAMPLING_METHODS


class TestMcpProjectFlagPosition:
    """`--project` 在全局位与子命令位都必须生效（D7-15）。"""

    def test_global_position_project_survives_the_mcp_subparser(self, tmp_path: object) -> None:
        parser = build_parser()  # registers the `mcp` subcommand itself
        args = parser.parse_args(["--project", str(tmp_path), "mcp"])
        assert args.project == str(tmp_path), (
            "the sub-parser's default clobbered the global --project: the server would run on cwd"
        )

    def test_subcommand_position_still_wins(self, tmp_path: object) -> None:
        parser = build_parser()  # registers the `mcp` subcommand itself
        args = parser.parse_args(["mcp", "--project", str(tmp_path)])
        assert args.project == str(tmp_path)

    def test_no_project_anywhere_stays_unset_for_the_cwd_default(self) -> None:
        """两个位置都没给时仍是 None，`McpServer(project=None)` 的当前目录默认不能被改坏。"""
        parser = build_parser()  # registers the `mcp` subcommand itself
        args = parser.parse_args(["mcp"])
        assert args.project is None

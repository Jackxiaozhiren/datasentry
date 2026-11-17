"""修复引擎的四个策略拒绝点按 kind 映射（阶段 3 第 1 项切片 b 收尾）。

#65 只把 main 上当时已存在的 `_require_copy_writer` 搬成 `PolicyRefusalError`；
`_copy_cells` 的四个"搬不动就拒绝"此前仍是裸 `ValueError`，于是：
- CLI `repair preview` 把它们算成 EXIT_CONFIG(2)（配置错），而它们是执行期的策略拒绝；
- REST 恰好 fallthrough 到 422，MCP 恰好落进 -32603——都是"碰巧"，不随 kind 走。

本批把这四个拒绝点也归入 `PolicyRefusalError`（仍是 `ValueError` 子类，现有捕获方不破），
三面从此按 kind 读同一张映射表。判据钉在真会触发的四个场景上，逐个移除改动都会转红。

`_require_json_writable` 与 `ABSENT` cell-carry 哨兵在 main 上仍不存在，属后续切片，
这里不引用——测试不指向不存在的函数（#65 的假绿教训）。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch as mock_patch

import pytest

from datasentry_core.errors import PolicyRefusalError
from datasentry_core.models.enums import RepairOperation
from datasentry_core.models.repair import RepairProposal
from datasentry_core.repair.engine import RepairEngine


def _jsonl_source(tmp_path: Path) -> Path:
    src = tmp_path / "orders.jsonl"
    src.write_text(
        "\n".join(
            [
                json.dumps({"id": 1, "name": "  Alice "}),
                json.dumps({"id": 2, "name": "Bob"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return src


def _copy_cells_context(source: Path):
    """直接构造 context + proposal，绕开扫描，专打 `_copy_cells` 的拒绝分支。"""
    import pyarrow as pa

    from datasentry_core.connectors import DataSourceSpec, DataSourceType, default_registry
    from datasentry_core.detectors import DetectionContext

    handle = default_registry().open(DataSourceSpec(source_type=DataSourceType.CSV, path=source))
    context = DetectionContext(
        dataset_id="g", table_name=None, columns=["name", "qty"], handle=handle
    )
    proposal = RepairProposal(
        proposal_id="prop_g",
        issue_id="iss_g",
        operation=RepairOperation.TRIM_WHITESPACE,
        target_columns=["name"],
        estimated_rows_changed=1,
    )
    table = pa.Table.from_pydict({"name": ["Alice"], "qty": ["1e5"]})
    return handle, context, proposal, table


def test_jsonl_refusal_is_a_policy_refusal(tmp_path: Path) -> None:
    """JSONL 逐格搬运未实现 → 拒绝，且是可映射的 PolicyRefusalError。"""
    import pyarrow as pa

    from datasentry_core.connectors import DataSourceSpec, DataSourceType, default_registry
    from datasentry_core.detectors import DetectionContext

    src = _jsonl_source(tmp_path)
    # JSONL 分支只看 source_type，第一个判断就拒，无需对齐的 table。
    handle = default_registry().open(DataSourceSpec(source_type=DataSourceType.JSONL, path=src))
    try:
        ctx = DetectionContext(dataset_id="g", table_name=None, columns=["name"], handle=handle)
        proposal = RepairProposal(
            proposal_id="p",
            issue_id="i",
            operation=RepairOperation.TRIM_WHITESPACE,
            target_columns=["name"],
            estimated_rows_changed=1,
        )
        table = pa.Table.from_pydict({"name": ["Alice", "Bob"]})
        with pytest.raises(PolicyRefusalError, match="JSONL"):
            RepairEngine._copy_cells(ctx, table, proposal, src)
        # 基类仍是 ValueError：现有 except ValueError 调用方不破。
        with pytest.raises(ValueError, match="JSONL"):
            RepairEngine._copy_cells(ctx, table, proposal, src)
    finally:
        handle.close()


def test_unreadable_source_refusal_is_a_policy_refusal(tmp_path: Path) -> None:
    """逐格读不出的源（参差 CSV）→ 拒绝，类型带 kind。"""
    import pyarrow as pa

    from datasentry_core.connectors import DataSourceSpec, DataSourceType, default_registry
    from datasentry_core.detectors import DetectionContext

    src = tmp_path / "ragged.csv"
    src.write_text("id,name\n1,Alice,extra\n2,  Bob ,x\n", encoding="utf-8")
    handle = default_registry().open(DataSourceSpec(source_type=DataSourceType.CSV, path=src))
    try:
        ctx = DetectionContext(dataset_id="g", table_name=None, columns=["name"], handle=handle)
        proposal = RepairProposal(
            proposal_id="p",
            issue_id="i",
            operation=RepairOperation.TRIM_WHITESPACE,
            target_columns=["name"],
            estimated_rows_changed=1,
        )
        table = pa.Table.from_pydict({"name": ["Alice", "Bob"]})
        with pytest.raises(PolicyRefusalError, match="cell-by-cell"):
            RepairEngine._copy_cells(ctx, table, proposal, src)
    finally:
        handle.close()


def test_row_mismatch_refusal_is_a_policy_refusal(tmp_path: Path) -> None:
    """投影行数与源文件不一致 → 拒绝，类型带 kind。"""
    src = tmp_path / "g.csv"
    src.write_text("name,qty\n  Alice ,1e5\nBob,2\n", encoding="utf-8")
    handle, ctx, proposal, short = _copy_cells_context(src)
    try:
        with pytest.raises(PolicyRefusalError, match="row"):
            RepairEngine._copy_cells(ctx, short, proposal, src)
    finally:
        handle.close()


def test_column_name_mismatch_refusal_is_a_policy_refusal(tmp_path: Path) -> None:
    """连接器投影的列名与文件表头不一致（重名表头塌缩）→ 拒绝，类型带 kind。"""
    import pyarrow as pa

    src = tmp_path / "dup.csv"
    src.write_text("a,a,a_1\n1,  x ,3\n4,5,  y \n", encoding="utf-8")
    # projection 列名 3 个、源文件表头 3 个但拼写不同 → 命中列名不一致分支
    projection = pa.Table.from_pydict({"a_1": ["1", "4"], "a": ["x", "5"], "a_2": ["3", "y"]})
    handle, ctx, proposal, _ = _copy_cells_context(src)
    try:
        # 消息里的 "connector reads" 只出现在列名不一致分支，钉住它而非行数分支。
        with pytest.raises(PolicyRefusalError, match="connector reads"):
            RepairEngine._copy_cells(ctx, projection, proposal, src)
    finally:
        handle.close()


def test_cli_preview_maps_carry_refusal_to_execution_error(tmp_path: Path) -> None:
    """判据：CLI preview 的策略拒绝退 3（执行错），不再退 2（配置错）。

    负向对照：把 engine 的四处拒绝改回裸 ValueError，本例立刻回到 EXIT_CONFIG(2) 转红。
    """
    from datasentry.cli import EXIT_CONFIG, EXIT_ERROR, main
    from datasentry.client import DataSentry

    ws = tmp_path / "ws"
    ws.mkdir()
    src = _jsonl_source(ws)
    client = DataSentry(project=ws)
    try:
        run, _ds, _issues = client.scan_file(src)
        issue_id = next(
            i.id
            for i in client.list_issues(scan_run_id=run.id)
            if "leading_or_trailing_whitespace" in ",".join(i.detector_ids)
        )
    finally:
        client.close()
    with mock_patch(
        "sys.argv",
        ["datasentry", "--project", str(ws), "repair", "preview", "--file", str(src), issue_id],
    ):
        rc = main()
    assert rc == EXIT_ERROR, f"a policy refusal must exit {EXIT_ERROR}, got {rc}"
    assert rc != EXIT_CONFIG, "a policy refusal is not a config error"


def test_rest_maps_carry_refusal_to_422() -> None:
    """REST 面：PolicyRefusalError 读 kind 得到 422，与旧 ValueError fallthrough 同码但有意。"""
    from datasentry.api import _error

    exc = PolicyRefusalError("refuse to carry")
    assert _error(exc) == 422
    assert _error(exc) != 500

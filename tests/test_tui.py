"""交互式终端界面（TUI，Step 118/ADR-118）无头测试。

用 Textual Pilot 在无 TTY 环境下驱动真实界面：启动、导航、
扫描、问题选中、修复操作、退出确认。
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Button, DataTable, TabbedContent, TabPane

from datasentry import DataSentry
from datasentry.tui import DataSentryApp

DEMO_CSV = Path(__file__).resolve().parents[1] / "demo-data" / "orders.csv"


def _app(tmp_path: Path) -> DataSentryApp:
    client = DataSentry(project=tmp_path / "ws")
    return DataSentryApp(client)


async def test_app_starts_with_four_tabs(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test():
        assert len(list(app.query(TabPane))) == 4
        assert app.query_one("#dash-table", DataTable).row_count == 1  # 空态提示行


async def test_quit_requires_confirmation(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("q")
        await pilot.pause()
        assert len(app.screen.query("#quit-dialog")) == 1  # 对话框已出现
        assert app.focused.id == "quit-cancel"  # 默认焦点=取消（防误触）
        await pilot.click("#quit-cancel")
        await pilot.pause()
        assert len(app.screen.query("#quit-dialog")) == 0  # 取消后关闭
        assert app.is_running


async def test_scan_flow_pops_issues(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("2")
        input_w = app.query_one("#scan-input")
        input_w.value = str(DEMO_CSV)
        await pilot.click("#scan-button")

        # 等待后台扫描线程完成（最多 15s）
        for _ in range(300):
            await pilot.pause(0.05)
            if app._issues:
                break

        assert app._issues, "scan should have produced issues"
        await pilot.pause(0.2)  # 等 on_scan_result 处理完（跳转/刷新）
        assert app.query_one(TabbedContent).active == "tab-issues"
        table = app.query_one("#issue-table", DataTable)
        assert table.row_count == len(app._issues)


async def test_select_issue_shows_evidence_detail(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("2")
        input_w = app.query_one("#scan-input")
        input_w.value = str(DEMO_CSV)
        await pilot.click("#scan-button")
        for _ in range(300):
            await pilot.pause(0.05)
            if app._issues:
                break
        assert app._issues

        table = app.query_one("#issue-table", DataTable)
        table.move_cursor(row=0)
        await pilot.press("enter")
        detail = app.query_one("#issue-detail").content
        assert app._issues[0].id in str(detail)
        assert "affected=" in str(detail)


async def test_repair_propose_from_selected_issue(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("2")
        input_w = app.query_one("#scan-input")
        input_w.value = str(DEMO_CSV)
        await pilot.click("#scan-button")
        for _ in range(300):
            await pilot.pause(0.05)
            if app._issues:
                break
        assert app._issues

        table = app.query_one("#issue-table", DataTable)
        table.move_cursor(row=0)
        await pilot.press("enter")
        await pilot.press("4")
        await pilot.click("#r-propose")
        out = str(app.query_one("#repair-out").content)
        assert "propose" in out and "failed" not in out


async def test_rollback_requires_confirmation(tmp_path: Path) -> None:
    """rollback 移出主操作区，且必须二次确认才执行（防误触）。"""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("2")
        input_w = app.query_one("#scan-input")
        input_w.value = str(DEMO_CSV)
        await pilot.click("#scan-button")
        for _ in range(300):
            await pilot.pause(0.05)
            if app._issues:
                break
        assert app._issues

        # rollback 按钮独立于主操作按钮行
        rollback_btn = app.query_one("#r-rollback")
        actions = app.query_one("#repair-actions")
        assert rollback_btn not in actions.query(Button)

        # 选中一个 issue（rollback 需要 current_issue）
        table = app.query_one("#issue-table", DataTable)
        table.move_cursor(row=0)
        await pilot.press("enter")

        # 点 rollback：先弹确认框，不执行
        table = app.query_one("#issue-table", DataTable)
        table.move_cursor(row=0)
        await pilot.press("enter")
        await pilot.press("4")
        for _ in range(20):
            await pilot.pause(0.05)
        clicked = await pilot.click("#r-rollback")
        for _ in range(20):
            await pilot.pause(0.05)
            if app.screen.query("#quit-dialog"):
                break
        assert clicked, "rollback click should hit"
        assert len(app.screen.query("#quit-dialog")) == 1  # 复用对话框样式 id
        assert app.focused.id == "rrb-cancel"  # 默认焦点=取消（防误触）
        assert "回滚最近一次修复" in str(app.screen.query_one("#quit-title").content)
        out_before = str(app.query_one("#repair-out").content)
        await pilot.click("#rrb-cancel")
        await pilot.pause()
        assert len(app.screen.query("#quit-dialog")) == 0
        assert str(app.query_one("#repair-out").content) == out_before  # 取消后无动作

        # 再次点击并确认：执行回滚
        await pilot.click("#r-rollback")
        await pilot.pause()
        await pilot.click("#rrb-confirm")
        await pilot.pause()
        out = str(app.query_one("#repair-out").content)
        assert "rollback" in out and "failed" not in out

"""交互式终端界面（TUI，Step 118/ADR-118）无头测试。

用 Textual Pilot 在无 TTY 环境下驱动真实界面：启动、导航、
扫描、问题选中、修复操作、退出确认。
"""

from __future__ import annotations

from pathlib import Path

from textual.widgets import DataTable, TabbedContent, TabPane

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

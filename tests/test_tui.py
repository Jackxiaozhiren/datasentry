"""交互式终端界面（TUI，Step 118/ADR-118）无头测试。

用 Textual Pilot 在无 TTY 环境下驱动真实界面：启动、导航、
扫描、问题选中、修复操作、退出确认。
"""

from __future__ import annotations

from pathlib import Path

from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, TabbedContent, TabPane

from datasentry import DataSentry
from datasentry.tui import DataSentryApp, ScanProgress

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


async def _scan_ready(app: DataSentryApp, pilot) -> None:
    for _ in range(300):
        await pilot.pause(0.05)
        if app._issues:
            break


async def test_keyboard_navigation_and_help(tmp_path: Path) -> None:
    """j/k 导航、? 帮助、ctrl+p 命令面板、ctrl+tab 切视图。"""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("2")
        input_w = app.query_one("#scan-input")
        input_w.value = str(DEMO_CSV)
        await pilot.click("#scan-button")
        await _scan_ready(app, pilot)
        assert app._issues

        table = app.query_one("#issue-table", DataTable)
        table.focus()
        await pilot.pause()
        await pilot.press("j")
        await pilot.press("j")
        assert table.cursor_row == 2
        await pilot.press("k")
        assert table.cursor_row == 1

        await pilot.press("?")
        await pilot.pause()
        assert len(app.screen.query("#quit-dialog")) == 1  # 帮助弹窗（同款样式）
        assert "DataSentry 快捷键" in str(app.screen.query_one("#quit-title").content)
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen.query("#quit-dialog")) == 0

        await pilot.press("ctrl+p")
        await pilot.pause()
        assert isinstance(app.screen, ModalScreen)  # CommandPalette 是系统 ModalScreen
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("ctrl+tab")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "tab-repair"
        await pilot.press("ctrl+tab")
        await pilot.pause()
        assert app.query_one(TabbedContent).active == "tab-dashboard"


async def test_issue_filter_and_sort(tmp_path: Path) -> None:
    """/ 过滤（关键字 + severity:）与 s 排序切换。"""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("2")
        input_w = app.query_one("#scan-input")
        input_w.value = str(DEMO_CSV)
        await pilot.click("#scan-button")
        await _scan_ready(app, pilot)
        assert app._issues

        await pilot.press("/")
        await pilot.pause()
        assert app.focused.id == "issue-filter"

        filt = app.query_one("#issue-filter")
        filt.value = "severity:high"
        await pilot.pause()
        table = app.query_one("#issue-table", DataTable)
        assert table.row_count > 0
        assert table.row_count < len(app._issues)

        filt.value = "nonexistent_token_xyz"
        await pilot.pause()
        assert table.row_count == 1  # 空态提示行
        assert "没有匹配" in str(table.get_row_at(0)[0])

        filt.value = ""
        await pilot.pause()
        assert table.row_count == len(app._issues)

        app._sort_key = "affected"
        app.action_cycle_sort()
        assert app._sort_key == "confidence"
        app.action_cycle_sort()
        assert app._sort_key == "priority"


async def test_scan_progress_and_csv_preview(tmp_path: Path) -> None:
    """扫描页：输入路径自动预览 CSV + 检测器实时进度消息。"""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("2")
        input_w = app.query_one("#scan-input")
        input_w.value = str(DEMO_CSV)
        await pilot.pause(1.0)  # 等 debounce 预览
        preview = str(app.query_one("#scan-preview").content)
        assert "预览" in preview and "列" in preview
        assert "int" in preview or "str" in preview or "float" in preview

        progress: list[ScanProgress] = []
        orig = DataSentryApp.on_scan_progress
        DataSentryApp.on_scan_progress = lambda self, m: progress.append(m)  # type: ignore[method-assign]
        try:
            await pilot.click("#scan-button")
            await _scan_ready(app, pilot)
        finally:
            DataSentryApp.on_scan_progress = orig  # type: ignore[method-assign]
        assert progress, "scan should emit progress messages"
        assert progress[0].total == progress[-1].total


async def test_scan_auto_selects_first_issue(tmp_path: Path) -> None:
    """扫描完成后自动选中首个 issue（按 4 即可直接修复，无需先手动选中）。"""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("2")
        input_w = app.query_one("#scan-input")
        input_w.value = str(DEMO_CSV)
        await pilot.click("#scan-button")
        await _scan_ready(app, pilot)
        assert app._issues
        await pilot.pause(0.3)
        assert app.current_issue is not None
        assert app.current_issue.id == app._issues[0].id
        assert app.query_one("#issue-table", DataTable).cursor_row == 0


async def test_filter_keeps_cursor_selection(tmp_path: Path) -> None:
    """过滤/排序重建列表后光标与选中保持。"""
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("2")
        input_w = app.query_one("#scan-input")
        input_w.value = str(DEMO_CSV)
        await pilot.click("#scan-button")
        await _scan_ready(app, pilot)
        assert app._issues

        table = app.query_one("#issue-table", DataTable)
        table.focus()
        await pilot.press("j")
        await pilot.press("j")
        assert table.cursor_row == 2

        filt = app.query_one("#issue-filter")
        filt.value = "severity:medium"
        await pilot.pause()
        assert table.cursor_row == 2
        assert app.current_issue is not None

        filt.value = ""
        await pilot.pause()
        assert table.cursor_row == 2


async def test_status_bar_shows_context(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with app.run_test() as pilot:
        bar = app.query_one("#status-bar")
        assert "workspace" in str(bar.content)
        await pilot.press("3")
        await pilot.pause()
        assert "视图: 问题" in str(bar.content)
        await pilot.press("4")
        await pilot.pause()
        assert "视图: 修复" in str(bar.content)

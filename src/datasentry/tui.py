"""DataSentry 交互式终端界面（Step 118，V24，ADR-118）。

Textual 构建的 TUI，四个视图（TabbedContent）：

- 工作台：workspace 概览——最近扫描、评分、问题数、质量趋势（sparkline）
- 扫描：选数据文件（路径输入 + 目录树浏览）→ 实时检测器进度 → 自动跳转问题视图
- 问题：问题列表（过滤/排序 + 严重级着色）+ 证据链详情（说明/占比/置信度/检测器）
- 修复：修复工作台——propose → preview → apply → rollback 引导式操作

键盘流：1-4 切视图、j/k 上下、/ 过滤、s 排序、? 帮助、ctrl+p 命令面板、q 退出。
入口：`datasentry`（无子命令）或 `datasentry ui`。全部操作走
`DataSentry` 客户端（与 CLI/Web UI 同一套语义与安全约束：
AI 建议、人工审批、可回滚）。
"""

# ruff: noqa: RUF001, RUF012
# RUF001: UI 文案使用中文标点，属有意为之
# RUF012: BINDINGS 类属性类型与基类（list invariant）冲突，保持无注解

from __future__ import annotations

import asyncio
import csv
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    DirectoryTree,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    Static,
    TabbedContent,
    TabPane,
)

from datasentry import DataSentry
from datasentry_core.models.issue import Issue
from datasentry_core.models.scan import ScanRun

_SEVERITY_COLORS: dict[str, str] = {
    "high": "#ff6b6b",
    "medium": "#ffd93d",
    "low": "#6bcb77",
    "info": "#4d96ff",
}

_SPARKLINE_BARS = "▁▂▃▄▅▆▇█"

_TAB_NAMES = ["tab-dashboard", "tab-scan", "tab-issues", "tab-repair"]

_SORT_KEYS = ["priority", "affected", "confidence"]


def _sparkline(scores: list[float]) -> str:
    """最近分数画 8 档 sparkline（无数据返回 '-',）。"""
    if not scores:
        return "-"
    lo, hi = min(scores), max(scores)
    span = hi - lo
    out = []
    for s in scores:
        idx = 0 if span == 0 else int((s - lo) / span * (len(_SPARKLINE_BARS) - 1))
        out.append(_SPARKLINE_BARS[min(idx, len(_SPARKLINE_BARS) - 1)])
    return "".join(out)


class ScanError(Message):
    """扫描失败事件（worker → UI 线程）。"""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ScanProgress(Message):
    """扫描检测器进度事件（worker 线程 → UI 线程）。"""

    def __init__(self, done: int, total: int, name: str) -> None:
        super().__init__()
        self.done = done
        self.total = total
        self.name = name


class ScanResult(Message):
    """扫描完成事件（worker → UI 线程）。"""

    def __init__(self, run: ScanRun, issues: list[Issue]) -> None:
        super().__init__()
        self.run = run
        self.issues = issues


class HelpScreen(ModalScreen[None]):
    """快捷键帮助（? 键弹出）。"""

    BINDINGS = [("escape", "close", "关闭")]

    def compose(self) -> ComposeResult:
        rows = "\n".join(
            f"{k:<14}{v}"
            for k, v in [
                ("1 / 2 / 3 / 4", "切换视图：工作台 / 扫描 / 问题 / 修复"),
                ("j / k", "问题或扫描列表上/下移动"),
                ("Enter", "选中问题行，查看证据链"),
                ("/", "问题列表过滤（severity:/column:/type:/detector:）"),
                ("s", "切换排序：优先级 / 影响行数 / 置信度"),
                ("ctrl+p", "命令面板（扫描 / 切视图 / 帮助 / 退出）"),
                ("r", "刷新视图"),
                ("q", "退出（二次确认）"),
            ]
        )
        yield Vertical(
            Label("DataSentry 快捷键", id="quit-title"),
            Label(rows, id="help-body"),
            id="quit-dialog",
        )

    def action_close(self) -> None:
        self.dismiss(None)


class DataSentryCommands(Provider):
    """命令面板提供者：扫描 / 切视图 / 刷新 / 帮助 / 退出。"""

    async def discover(self) -> Hits:
        for name, help_text, action in self._commands():
            yield DiscoveryHit(name, self._run(action), help=help_text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for name, help_text, action in self._commands():
            if (match := matcher.match(name)) > 0:
                yield Hit(match, matcher.highlight(name), self._run(action), help=help_text)

    @staticmethod
    def _commands() -> list[tuple[str, str, str]]:
        return [
            ("扫描数据文件", "跳转到扫描页并聚焦路径输入", "tab_scan"),
            ("切换到工作台", "查看最近扫描与质量趋势", "tab_dashboard"),
            ("切换到问题", "查看全部质量问题", "tab_issues"),
            ("切换到修复", "修复工作台", "tab_repair"),
            ("刷新视图", "重新拉取扫描与问题", "refresh_view"),
            ("打开帮助", "快捷键说明", "help"),
            ("退出", "退出 DataSentry", "request_quit"),
        ]

    def _run(self, action: str) -> Callable[[], object]:
        return lambda: self.app.run_action(action)


class QuitScreen(ModalScreen[bool]):
    """退出确认（防止误触 q 丢会话）。"""

    BINDINGS = [("escape", "cancel", "取消")]
    AUTO_FOCUS = "#quit-cancel"

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("退出 DataSentry？", id="quit-title"),
            Label("未完成的操作不会丢失——扫描与修复都落盘在 workspace。", id="quit-hint"),
            Horizontal(
                Button("退出", id="quit-confirm", variant="error"),
                Button("取消", id="quit-cancel"),
            ),
            id="quit-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "quit-confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class RollbackConfirmScreen(ModalScreen[bool]):
    """回滚二次确认（rollback 会撤销最近一次已应用的修复，需用户明确同意）。"""

    BINDINGS = [("escape", "cancel", "取消")]
    AUTO_FOCUS = "#rrb-cancel"

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("回滚最近一次修复？", id="quit-title"),
            Label(
                "回滚将撤销最近一次 apply 的修复，把数据恢复为修复前的状态"
                "（基于回滚工件）。该操作不可逆，确认后原修复会失效。",
                id="quit-hint",
            ),
            Horizontal(
                Button("确认回滚", id="rrb-confirm", variant="error"),
                Button("取消", id="rrb-cancel"),
            ),
            id="quit-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "rrb-confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


def _fmt_score(score: object) -> str:
    if score is None:
        return "-"
    if hasattr(score, "overall"):
        return str(score.overall)
    return str(score)


def _fmt_issues_count(counts: object) -> str:
    if not counts:
        return ""
    items = counts.items() if isinstance(counts, dict) else []
    return " ".join(f"{k}:{v}" for k, v in items)


def _fmt_time(dt: object) -> str:
    if dt is None:
        return "-"
    text = str(dt)
    return text[11:19] if len(text) >= 19 else text


def _guess_type(values: list[str]) -> str:
    """从样本值推断列类型（int/float/bool/date/str）。"""
    if not values:
        return "?"
    if all(_is_int(v) for v in values):
        return "int"
    if all(_is_float(v) for v in values):
        return "float"
    if all(v.lower() in ("true", "false") for v in values):
        return "bool"
    if all(_is_date(v) for v in values):
        return "date"
    return "str"


def _is_int(v: str) -> bool:
    try:
        int(v)
        return True
    except ValueError:
        return False


def _is_float(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


def _is_date(v: str) -> bool:
    if len(v) < 8:
        return False
    try:
        from datetime import datetime

        datetime.strptime(v[:19], "%Y-%m-%d %H:%M:%S")
        return True
    except ValueError:
        pass
    try:
        from datetime import datetime

        datetime.strptime(v, "%Y-%m-%d")
        return True
    except ValueError:
        return False


class DataSentryApp(App[None]):
    """DataSentry 终端界面主应用。"""

    TITLE = "DataSentry"
    SUB_TITLE = "evidence-driven data quality"
    COMMANDS = {DataSentryCommands}
    BINDINGS = [
        Binding("q", "request_quit", "退出"),
        Binding("r", "refresh_view", "刷新"),
        Binding("1", "tab_dashboard", "工作台"),
        Binding("2", "tab_scan", "扫描"),
        Binding("3", "tab_issues", "问题"),
        Binding("4", "tab_repair", "修复"),
        Binding("j", "next_row", "下移"),
        Binding("k", "prev_row", "上移"),
        Binding("?", "help", "帮助"),
        Binding("ctrl+tab", "next_tab", "下一个视图"),
        Binding("/", "focus_issues_filter", "过滤"),
        Binding("s", "cycle_sort", "排序"),
    ]
    CSS = """
    Screen { background: #101418; }
    TabbedContent { height: 1fr; }
    #dash-table { height: 1fr; }
    #issue-table { height: 1fr; }
    #issue-detail { height: auto; max-height: 12; border: round #2a3540; padding: 0 1; }
    #issue-filter { width: 1fr; margin: 0 1; }
    #scan-row { height: auto; padding: 0 1; }
    #scan-input { width: 1fr; }
    #scan-button { width: auto; }
    #scan-status { height: auto; padding: 0 1; }
    #scan-preview { height: auto; max-height: 8; padding: 0 1; color: $text-muted; }
    #scan-tree { height: 1fr; }
    #repair-file-row { height: auto; padding: 0 1; }
    #repair-file { width: 1fr; }
    #repair-actions { height: auto; padding: 0 1; }
    #repair-out { height: 1fr; border: round #2a3540; padding: 0 1; }
    #quit-dialog {
        width: 60; height: auto; align: center middle;
        border: round $accent; background: $surface; padding: 1 2;
    }
    #quit-title { text-style: bold; }
    #quit-hint { color: $text-muted; }
    #help-body { color: $text; }
    #repair-actions { height: auto; padding: 0 1; }
    #rollback-zone { height: 7; padding: 0 1; margin: 0 1; border: round #5a2323; }
    #rollback-hint { color: $text-muted; }
    #status-bar { height: 1; color: $text-muted; padding: 0 1; }
    .sev-high { color: #ff6b6b; text-style: bold; }
    .sev-medium { color: #ffd93d; }
    .sev-low { color: #6bcb77; }
    .sev-info { color: #4d96ff; }
    """

    current_issue: reactive[Issue | None] = reactive(None)

    def __init__(self, client: DataSentry) -> None:
        super().__init__()
        self._client = client
        self._issues: list[Issue] = []
        self._scans: list[ScanRun] = []
        self._last_scan_path: str = ""
        self._filter_text: str = ""
        self._sort_key: str = "priority"
        self._preview_task: asyncio.Task[None] | None = None

    # ---- 生命周期 ----------------------------------------------------------

    def on_mount(self) -> None:
        self.refresh_view()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="tab-dashboard"):
            with TabPane("工作台", id="tab-dashboard"):
                yield Static(f"workspace: {self._client.workspace}", id="ws-path")
                yield DataTable(id="dash-table", cursor_type="row")
            with TabPane("扫描", id="tab-scan"):
                with Horizontal(id="scan-row"):
                    yield Input(
                        placeholder="数据文件路径（CSV/Parquet/JSONL/XLSX…）", id="scan-input"
                    )
                    yield Button("开始扫描", id="scan-button", variant="primary")
                yield ProgressBar(total=None, show_eta=False, id="scan-progress")
                yield Static("", id="scan-status")
                yield Static("", id="scan-preview")
                yield DirectoryTree(str(self._client.workspace), id="scan-tree")
            with TabPane("问题", id="tab-issues"):
                yield Input(
                    placeholder=(
                        "过滤：关键字 或 severity:high / column:order_id / "
                        "type:missing / detector:…（s 排序）"
                    ),
                    id="issue-filter",
                )
                yield Static("", id="issue-detail")
                yield DataTable(id="issue-table", cursor_type="row")
            with TabPane("修复", id="tab-repair"):
                with Horizontal(id="repair-file-row"):
                    yield Input(
                        placeholder="修复源数据文件（propose/apply 需要）", id="repair-file"
                    )
                with Horizontal(id="repair-actions"):
                    yield Button("propose", id="r-propose")
                    yield Button("preview", id="r-preview")
                    yield Button("apply", id="r-apply")
                with Vertical(id="rollback-zone"):
                    yield Label(
                        "危险区：撤销最近一次已应用的修复（需二次确认）", id="rollback-hint"
                    )
                    yield Button("回滚修复", id="r-rollback", variant="error")
                yield Static(
                    "在「问题」视图选中一个 issue，再在这里操作（AI 建议、人工审批、可回滚）",
                    id="repair-out",
                )
        yield Static("", id="status-bar")
        yield Footer()

    # ---- 动作 --------------------------------------------------------------

    def action_request_quit(self) -> None:
        self.push_screen(QuitScreen(), callback=self._on_quit)

    def _on_quit(self, confirmed: bool | None) -> None:
        if confirmed:
            self.exit()

    def action_tab_dashboard(self) -> None:
        self._set_tab("tab-dashboard")

    def action_tab_scan(self) -> None:
        self._set_tab("tab-scan")
        self.query_one("#scan-input", Input).focus()

    def action_tab_issues(self) -> None:
        self._set_tab("tab-issues")
        self.query_one("#issue-table", DataTable).focus()

    def action_tab_repair(self) -> None:
        self._set_tab("tab-repair")
        self.query_one("#repair-file", Input).focus()

    def _set_tab(self, name: str) -> None:
        self.query_one(TabbedContent).active = name
        self._render_status_bar()

    def action_refresh_view(self) -> None:
        self.refresh_view()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_next_tab(self) -> None:
        cur = self.query_one(TabbedContent).active
        if cur not in _TAB_NAMES:
            cur = _TAB_NAMES[0]
        nxt = _TAB_NAMES[(_TAB_NAMES.index(cur) + 1) % len(_TAB_NAMES)]
        self._set_tab(nxt)
        self._focus_tab_widget(nxt)

    def _focus_tab_widget(self, name: str) -> None:
        if name == "tab-dashboard":
            self.query_one("#dash-table", DataTable).focus()
        elif name == "tab-scan":
            self.query_one("#scan-input", Input).focus()
        elif name == "tab-issues":
            self.query_one("#issue-filter", Input).focus()
        elif name == "tab-repair":
            self.query_one("#repair-file", Input).focus()

    def action_next_row(self) -> None:
        self._move_cursor(+1)

    def action_prev_row(self) -> None:
        self._move_cursor(-1)

    def _move_cursor(self, delta: int) -> None:
        table = self.focused if isinstance(self.focused, DataTable) else None
        if table is None:
            return
        row = table.cursor_row + delta
        if 0 <= row < table.row_count:
            table.move_cursor(row=row)
            if table.id == "issue-table":
                self._select_issue_row(row)

    def action_focus_issues_filter(self) -> None:
        if self.query_one(TabbedContent).active != "tab-issues":
            self._set_tab("tab-issues")
        self.query_one("#issue-filter", Input).focus()

    def action_cycle_sort(self) -> None:
        self._sort_key = _SORT_KEYS[(_SORT_KEYS.index(self._sort_key) + 1) % len(_SORT_KEYS)]
        self._render_issues()
        self._render_status_bar()

    # ---- 数据加载 ----------------------------------------------------------

    def refresh_view(self) -> None:
        """重新拉取扫描与问题列表，刷新工作台/问题视图。"""
        try:
            self._scans = self._client.list_scan_runs()
            self._issues = self._client.list_issues()
        except Exception as exc:
            self._scans = []
            self._issues = []
            self._flash(f"加载失败: {exc}")
        self._render_dashboard()
        self._render_issues()
        self._render_status_bar()

    def _render_status_bar(self) -> None:
        names = {
            "tab-dashboard": "工作台",
            "tab-scan": "扫描",
            "tab-issues": "问题",
            "tab-repair": "修复",
        }
        view = names.get(self.query_one(TabbedContent).active, "?")
        text = (
            f"workspace: {self._client.workspace}  |  视图: {view}"
            f"  |  问题 {len(self._issues)}  |  最近扫描 {len(self._scans)}"
        )
        with suppress(Exception):
            self.query_one("#status-bar", Static).update(text)

    def _flash(self, text: str) -> None:
        with suppress(Exception):
            self.query_one("#scan-status", Static).update(text)

    def _render_dashboard(self) -> None:
        table = self.query_one("#dash-table", DataTable)
        if not table.columns:
            table.add_column("scan_run_id", key="id", width=18)
            table.add_column("dataset", key="ds", width=14)
            table.add_column("status", key="st", width=10)
            table.add_column("score", key="sc", width=8)
            table.add_column("issues", key="iss", width=14)
            table.add_column("trend", key="tr", width=10)
            table.add_column("time", key="tm", width=10)
        table.clear()
        if not self._scans:
            table.add_row("— 还没有扫描，去「扫描」标签页开始 —", "", "", "", "", "", "")
            return
        for i, run in enumerate(self._scans[:12]):
            scores = [self._score_of(s) for s in self._scans[max(0, i - 7) : i + 1]]
            table.add_row(
                run.id,
                run.dataset_id or "-",
                run.status,
                _fmt_score(run.quality_score),
                _fmt_issues_count(run.issues_count),
                _sparkline(scores),
                _fmt_time(run.started_at),
                key=run.id,
            )

    @staticmethod
    def _score_of(run: ScanRun) -> float:
        s = run.quality_score
        v: object = getattr(s, "overall", None) if s is not None else None
        if isinstance(v, (int, float)):
            return float(v)
        if v is not None:
            try:
                return float(str(v))
            except (TypeError, ValueError):
                pass
        return 0.0

    def _render_issues(self) -> None:
        table = self.query_one("#issue-table", DataTable)
        if not table.columns:
            table.add_column("severity", key="sev", width=8)
            table.add_column("type", key="typ", width=24)
            table.add_column("column", key="col", width=16)
            table.add_column("affected", key="aff", width=9)
            table.add_column("conf", key="conf", width=6)
            table.add_column("priority", key="pri", width=8)
        table.clear()
        issues = self._filter_issues()
        if not issues:
            if self._filter_text:
                table.add_row("— 没有匹配的 issue，改一下过滤条件 —", "", "", "", "", "")
            else:
                table.add_row("— 没有问题，去扫描点什么 —", "", "", "", "", "")
            self._render_issue_detail()
            return
        sort = _SORT_KEYS.index(self._sort_key)
        issues = sorted(
            issues,
            key=lambda it: (
                it.priority_score,
                it.affected_count,
                it.confidence,
                it.issue_type,
            )[sort],
            reverse=True,
        )
        for issue in issues:
            table.add_row(
                issue.severity,
                issue.issue_type,
                ", ".join(issue.columns or ["?"]),
                str(issue.affected_count),
                f"{issue.confidence:.2f}",
                f"{issue.priority_score:.0f}",
                key=issue.id,
            )
        self._render_issue_detail()

    def _filter_issues(self) -> list[Issue]:
        text = self._filter_text.strip()
        if not text:
            return self._issues
        tokens = text.split()
        result = self._issues
        for tok in tokens:
            if ":" in tok:
                key, _, val = tok.partition(":")
                val = val.lower()
                if key in ("severity", "sev"):
                    result = [i for i in result if i.severity.lower() == val]
                elif key in ("column", "col"):
                    result = [i for i in result if val in ",".join(i.columns or []).lower()]
                elif key in ("type", "issue_type"):
                    result = [i for i in result if val in i.issue_type.lower()]
                elif key in ("detector", "detectors"):
                    result = [i for i in result if val in ",".join(i.detector_ids or []).lower()]
                else:
                    result = [i for i in result if val in i.issue_type.lower()]
            else:
                t = tok.lower()
                result = [
                    i
                    for i in result
                    if t in i.issue_type.lower()
                    or t in ",".join(i.columns or []).lower()
                    or t in (i.id or "").lower()
                ]
        return result

    def _render_issue_detail(self) -> None:
        static = self.query_one("#issue-detail", Static)
        issue = self.current_issue
        if issue is None:
            static.update("↑ 选中一行查看证据链")
            return
        lines = [
            f"[b]{issue.id}[/b]  [{self._sev_class(issue.severity)}]{issue.severity}[/] "
            f"{issue.issue_type}  →  {', '.join(issue.columns or [])}",
            f"affected={issue.affected_count} ({issue.affected_ratio:.1%})  "
            f"confidence={issue.confidence:.2f}  priority={issue.priority_score:.1f}  "
            f"detectors={', '.join(issue.detector_ids or [])}",
        ]
        for ev in issue.evidence[:3]:
            lines.append(f"  evidence: {ev.description}")
        static.update("\n".join(lines))

    def _sev_class(self, severity: str) -> str:
        sev = (severity or "info").lower()
        if sev in _SEVERITY_COLORS:
            return f"sev-{sev}"
        return "sev-info"

    # ---- 事件 --------------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "issue-table":
            self._select_issue_row(event.data_table.cursor_row)

    def _select_issue_row(self, row_index: int) -> None:
        issues = self._filter_issues()
        if not issues or row_index >= len(issues):
            return
        self.current_issue = issues[row_index]
        self._render_issue_detail()

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.query_one("#scan-input", Input).value = str(event.path)
        self._schedule_preview()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "scan-input":
            self._start_scan()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "issue-filter":
            self._filter_text = event.value
            self._render_issues()
        elif event.input.id == "scan-input":
            self._schedule_preview()

    def _schedule_preview(self) -> None:
        if self._preview_task is not None:
            self._preview_task.cancel()
        self._preview_task = asyncio.create_task(self._preview_delayed())

    async def _preview_delayed(self) -> None:
        await asyncio.sleep(0.6)
        raw = self.query_one("#scan-input", Input).value.strip().strip('"')
        if not raw:
            return
        path = Path(raw).expanduser()
        if not path.is_file() or path.suffix.lower() != ".csv":
            self.query_one("#scan-preview", Static).update("")
            return
        try:
            preview = await asyncio.to_thread(self._preview_file, path)
        except Exception:
            self.query_one("#scan-preview", Static).update("")
            return
        self.query_one("#scan-preview", Static).update(preview)

    @staticmethod
    def _preview_file(path: Path) -> str:
        """读 CSV 前 5 行并推断列类型（数据预览：先看后扫）。"""
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            raw_rows = [next(reader, None) for _ in range(5)]
        if header is None:
            return "（空文件）"
        rows: list[list[str]] = [r for r in raw_rows if r is not None]
        lines = [f"预览 {path.name}: {len(rows)} 行 × {len(header)} 列"]
        for idx, col in enumerate(header):
            sample = [r[idx] for r in rows if idx < len(r) and r[idx] != ""]
            kind = _guess_type(sample)
            values = " | ".join(r[idx] for r in rows if idx < len(r)) or "-"
            lines.append(f"  {col} ({kind}): {values[:40]}")
        return "\n".join(lines)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "scan-button":
            self._start_scan()
        elif bid == "r-propose":
            self._repair_op("propose")
        elif bid == "r-preview":
            self._repair_op("preview")
        elif bid == "r-apply":
            self._repair_op("apply")
        elif bid == "r-rollback":
            if self.current_issue is None:
                self.query_one("#repair-out", Static).update("先在「问题」视图选中一个 issue")
                return
            self.push_screen(RollbackConfirmScreen(), callback=self._on_rollback)

    # ---- 扫描 --------------------------------------------------------------

    def _start_scan(self) -> None:
        raw = self.query_one("#scan-input", Input).value.strip().strip('"')
        if not raw:
            self._flash("先输入要扫描的数据文件路径")
            return
        path = Path(raw).expanduser()
        if not path.exists():
            self._flash(f"文件不存在: {path}")
            return
        self._last_scan_path = str(path)
        self.query_one("#scan-progress", ProgressBar).styles.display = "block"
        self._flash(f"扫描中: {path} …")
        self.run_worker(self._scan_worker(path), exclusive=True)

    async def _scan_worker(self, path: Path) -> None:
        def on_progress(done: int, total: int, name: str) -> None:
            self.call_from_thread(self.post_message, ScanProgress(done, total, name))

        try:
            run, _, issues = await asyncio.to_thread(
                self._client.scan_file, path, on_progress=on_progress
            )
        except Exception as exc:
            self.post_message(ScanError(str(exc)))
            return
        self.post_message(ScanResult(run, issues))

    def on_scan_progress(self, message: ScanProgress) -> None:
        self._flash(f"检测器 {message.done + 1}/{message.total}: {message.name} …")

    def on_scan_result(self, message: ScanResult) -> None:
        self.notify(
            f"{message.run.dataset_id} — {len(message.issues)} issues, "
            f"score {_fmt_score(message.run.quality_score)}",
            title="扫描完成",
            timeout=6,
        )
        self.query_one("#scan-progress", ProgressBar).styles.display = "none"
        self.refresh_view()
        self._set_tab("tab-issues")
        self.query_one("#issue-table", DataTable).focus()

    def on_scan_error(self, message: ScanError) -> None:
        self._flash(f"扫描失败: {message.text}")
        self.query_one("#scan-progress", ProgressBar).styles.display = "none"

    # ---- 修复工作台 ---------------------------------------------------------

    def _repair_source(self) -> str | None:
        raw = self.query_one("#repair-file", Input).value.strip().strip('"')
        if raw:
            return raw
        if self._last_scan_path:
            self.query_one("#repair-file", Input).value = self._last_scan_path
            return self._last_scan_path
        return None

    def _on_rollback(self, confirmed: bool | None) -> None:
        if confirmed:
            self._repair_op("rollback")

    def _repair_op(self, op: str) -> None:
        issue = self.current_issue
        out = self.query_one("#repair-out", Static)
        if issue is None:
            out.update("先在「问题」视图选中一个 issue")
            return
        src = self._repair_source()
        if src is None:
            out.update("先填修复源数据文件路径（或先扫描一个文件）")
            return
        try:
            if op == "propose":
                proposal = self._client.repair_propose(issue.id, src)
                if proposal is None:
                    out.update("propose: 该 issue 无可用修复提案")
                else:
                    out.update(
                        f"propose ok: {proposal.proposal_id}\n"
                        f"operation={proposal.operation.value}  "
                        f"estimated_rows_changed={proposal.estimated_rows_changed}\n"
                        f"rationale: {proposal.rationale}"
                    )
            elif op == "preview":
                result = self._client.repair_preview(issue.id, src)
                if result is None:
                    out.update("preview: 无提案可预览")
                else:
                    _, preview = result
                    out.update(
                        f"preview ok: rows_changed={preview.rows_changed}  "
                        f"rule_failures {preview.rule_failures_before} → "
                        f"{preview.rule_failures_after}\n"
                        f"null_delta={preview.null_delta}  unique_delta={preview.unique_delta}"
                    )
            elif op == "apply":
                run = self._client.repair_apply(issue.id, src)
                out.update(
                    f"applied: {run.id}\n"
                    f"fingerprint {(run.fingerprint_before or '')[:12]}… → "
                    f"{(run.fingerprint_after or '')[:12]}…\n"
                    f"rollback artifact: {run.rollback_artifact}"
                )
            elif op == "rollback":
                runs = self._client.list_repair_runs()
                if not runs:
                    out.update("rollback: 没有可回滚的修复")
                else:
                    done = self._client.repair_rollback(runs[0].id)
                    out.update(
                        f"rollback ok: {done.id}\n"
                        f"fingerprint {(done.fingerprint_before or '')[:12]}… → "
                        f"{(done.fingerprint_after or '')[:12]}…"
                    )
        except Exception as exc:
            out.update(f"{op} failed: {exc}")


def run_tui(project: str | None = None) -> int:
    """启动交互式终端界面（cli 入口：`datasentry` / `datasentry ui`）。"""
    client = DataSentry(project)
    DataSentryApp(client).run()
    return 0

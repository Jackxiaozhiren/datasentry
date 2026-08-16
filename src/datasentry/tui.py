"""DataSentry 交互式终端界面（Step 118，V24，ADR-118）。

Textual 构建的 TUI，四个视图（TabbedContent）：

- 工作台：workspace 概览——最近扫描、评分、问题数（一目了然）
- 扫描：选数据文件（路径输入 + 目录树浏览）→ 扫描 → 自动跳转问题视图
- 问题：问题列表（严重级着色）+ 证据链详情（说明/占比/置信度/检测器）
- 修复：修复工作台——propose → preview → apply → rollback 引导式操作

入口：`datasentry`（无子命令）或 `datasentry ui`。全部操作走
`DataSentry` 客户端（与 CLI/Web UI 同一套语义与安全约束：
AI 建议、人工审批、可回滚）。
"""

# ruff: noqa: RUF001, RUF012
# RUF001: UI 文案使用中文标点，属有意为之
# RUF012: BINDINGS 类属性类型与基类（list invariant）冲突，保持无注解

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
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


class ScanError(Message):
    """扫描失败事件（worker → UI 线程）。"""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ScanResult(Message):
    """扫描完成事件（worker → UI 线程）。"""

    def __init__(self, run: ScanRun, issues: list[Issue]) -> None:
        super().__init__()
        self.run = run
        self.issues = issues


class QuitScreen(ModalScreen[bool]):
    """退出确认（防止误触 q 丢会话）。"""

    BINDINGS = [("escape", "cancel", "取消")]

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


class DataSentryApp(App[None]):
    """DataSentry 终端界面主应用。"""

    TITLE = "DataSentry"
    SUB_TITLE = "evidence-driven data quality"
    BINDINGS = [
        Binding("q", "request_quit", "退出"),
        Binding("r", "refresh_view", "刷新"),
        Binding("1", "tab_dashboard", "工作台"),
        Binding("2", "tab_scan", "扫描"),
        Binding("3", "tab_issues", "问题"),
        Binding("4", "tab_repair", "修复"),
    ]
    CSS = """
    Screen { background: #101418; }
    #dash-table { height: 1fr; }
    #issue-table { height: 1fr; }
    #issue-detail { height: auto; max-height: 12; border: round #2a3540; padding: 0 1; }
    #scan-row { height: auto; padding: 0 1; }
    #scan-input { width: 1fr; }
    #scan-button { width: auto; }
    #scan-status { height: auto; padding: 0 1; }
    #scan-tree { height: 1fr; }
    #repair-file-row { height: auto; padding: 0 1; }
    #repair-file { width: 1fr; }
    #repair-actions { height: auto; padding: 0 1; }
    #repair-out { height: 1fr; border: round #2a3540; padding: 0 1; }
    #quit-dialog {
        width: 60; height: 9; align: center middle;
        border: round $accent; background: $surface; padding: 1 2;
    }
    #quit-title { text-style: bold; }
    #quit-hint { color: $text-muted; }
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
                yield DirectoryTree(str(self._client.workspace), id="scan-tree")
            with TabPane("问题", id="tab-issues"):
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
                    yield Button("rollback", id="r-rollback")
                yield Static(
                    "在「问题」视图选中一个 issue，再在这里操作（AI 建议、人工审批、可回滚）",
                    id="repair-out",
                )
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

    def action_refresh_view(self) -> None:
        self.refresh_view()

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
            table.add_column("time", key="tm", width=10)
        table.clear()
        if not self._scans:
            table.add_row("— 还没有扫描，去「扫描」标签页开始 —", "", "", "", "", "")
            return
        for run in self._scans[:12]:
            table.add_row(
                run.id,
                run.dataset_id or "-",
                run.status,
                _fmt_score(run.quality_score),
                _fmt_issues_count(run.issues_count),
                _fmt_time(run.started_at),
                key=run.id,
            )

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
        if not self._issues:
            table.add_row("— 没有问题，去扫描点什么 —", "", "", "", "", "")
            self._render_issue_detail()
            return
        for issue in self._issues:
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
        if event.data_table.id != "issue-table":
            return
        for issue in self._issues:
            if issue.id == event.row_key.value:
                self.current_issue = issue
                self._render_issue_detail()
                return

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.query_one("#scan-input", Input).value = str(event.path)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "scan-input":
            self._start_scan()

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
            self._repair_op("rollback")

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
        try:
            run, _, issues = await asyncio.to_thread(self._client.scan_file, path)
        except Exception as exc:
            self.post_message(ScanError(str(exc)))
            return
        self.post_message(ScanResult(run, issues))

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

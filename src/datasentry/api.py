"""DataSentry REST API（Step 23，MVP REST 面，22/23 章 HTTP 映射）。

无状态不适用：本 API 是「单工作区门面」——`create_app(project=...)` 绑定一个
`DataSentry` 实例，所有端点复用同一条导入→扫描→落库→修复闭环（与 CLI/SDK
同源）。MVP 只提供同步端点（FastAPI async 并发已覆盖多数用法），异步 Job
队列归 V1（ADR-023）。

端点一览：
    GET    /health                         存活探针
    GET    /                               端点清单
    POST   /scans                          扫描文件 → 201 ScanResponse
    GET    /scans                          ScanRun id 列表
    GET    /scans/{run_id}                ScanRun 详情
    GET    /scans/{run_id}/issues         Issue 列表
    GET    /scans/{run_id}/report         26 章规范 JSON 报告
    GET    /scans/{run_id}/score          27 章质量总分
    GET    /issues                        跨扫描 Issue（severity 过滤）
    POST   /scans/{run_id}/repairs/propose   修复提案
    POST   /scans/{run_id}/repairs/preview   提案+预览
    POST   /scans/{run_id}/repairs/apply      应用修复
    POST   /repairs/{run_id}/rollback         回滚
    GET    /repairs                          修复运行列表

错误映射：FileNotFoundError/KeyError→404、ValueError→400、其余→500，
body 统一 {"ok": false, "detail": "..."}。
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import secrets
import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import urlparse

from fastapi import FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from datasentry import __version__, ui
from datasentry import client as sdk
from datasentry.pii_vault import PIIVault, VaultKeyMissingError, format_mapping_summary
from datasentry.redact import safe_detail
from datasentry.scheduler.core import LocalScanExecutor, Scheduler, SchedulerWorker
from datasentry.scheduler.models import (
    JobCommand,
    JobCreate,
    JobUpdate,
    ScheduledJob,
    iso,
    utcnow,
    webhook_target_refusal,
)
from datasentry_core.models.issue import Issue
from datasentry_core.models.repair import RepairPreview, RepairProposal, RepairRun
from datasentry_core.models.scan import DetectorRun, SamplingConfig, ScanConfig, ScanRun
from datasentry_core.reporting.i18n import t as _t

logger = logging.getLogger(__name__)

# 扫描实时进度（V25）：path → 进度快照；线程池中 on_progress 回调写入，
# Web UI 通过 GET /scans/progress 轮询读取。
_SCAN_PROGRESS: dict[str, dict[str, object]] = {}
_PROGRESS_LOCK = threading.Lock()


def _expand_scan_paths(raw: str, *, workspace: str | Path) -> list[str]:
    """Web 批量扫描路径解析：逗号/分号/换行分隔 + glob 展开 + 存在校验 + 去重。

    D5-04：每个候选先过 workspace 收束（`scan_paths`）；越界直接抛
    `ScanPathRejected` 中断整批（fail-closed），不再“接受外部路径再回显内容”。
    缺失文件不进入结果，但记录在 _last_missing_paths 供错误页展示。
    """
    from datasentry.scan_paths import resolve_allowed_scan_path

    global _last_missing_paths
    import glob as _glob

    seen: set[str] = set()
    paths: list[str] = []
    missing: list[str] = []
    from datasentry.scan_paths import allowed_roots

    root = allowed_roots(workspace)[0]
    for part in re.split(r"[,\n;]+", raw):
        part = part.strip().strip("\"'")
        if not part:
            continue
        # Glob from the workspace, not the process CWD. `Path.cwd()`-relative expansion is the same
        # base mismatch as the plain relative case (review A-1): `*.csv` matched files sitting next
        # to the workspace, and after that was closed a legitimate `cust*.csv` inside the workspace
        # matched nothing at all. Absolute patterns keep their own base.
        typed = str(Path(part).expanduser())
        pattern = typed if Path(typed).is_absolute() else str(root / typed)
        expanded = _glob.glob(pattern)
        candidates = [str(p) for p in expanded] if expanded else [part]
        for c in candidates:
            # 越界即整批拒绝（不在 missing 里静默记一笔了事），并且**用锚定后的写法**继续：
            # 只校验却把原始相对串交给下游，等于"按工作区判、按进程 CWD 开"，正是
            # `resolve_allowed_scan_path` 内部修掉的那个错配在这里被重新引入（独立复核 A-1
            # 实测 `POST /ui/scans path=payroll.csv` 扫到工作区同级文件）。缺失项仍回显用户
            # 自己写的形式，锚定后的绝对路径属服务端布局，不进面向用户的页面。
            anchored = resolve_allowed_scan_path(c, workspace=workspace)
            if anchored in seen:
                continue
            seen.add(anchored)
            if Path(anchored).exists():
                paths.append(anchored)
            else:
                missing.append(c)
    _last_missing_paths = missing
    return paths


_last_missing_paths: list[str] = []
_last_batch: dict[str, object] | None = None


def _publish_progress(path: str, done: int, total: int, name: str, scanning: bool) -> None:
    with _PROGRESS_LOCK:
        _SCAN_PROGRESS[path] = {
            "scanning": scanning,
            "done": done,
            "total": total,
            "detector": name,
            "path": path,
            "_ts": time.monotonic(),
        }


def _progress_for(path: str) -> dict[str, object]:
    with _PROGRESS_LOCK:
        return dict(_SCAN_PROGRESS.get(path, {}))


def _latest_progress() -> dict[str, object]:
    with _PROGRESS_LOCK:
        if not _SCAN_PROGRESS:
            return {}
        latest_path = max(
            _SCAN_PROGRESS, key=lambda k: cast(float, _SCAN_PROGRESS[k].get("_ts", 0.0))
        )
        latest = _SCAN_PROGRESS[latest_path]
        return {k: v for k, v in latest.items() if k != "_ts"}


def _on_progress_for(path: str) -> Any:
    return lambda done, total, name: _publish_progress(path, done, total, name, True)


class ScanRequest(BaseModel):
    """POST /scans 请求体：源文件路径 + 扫描配置。

    D5-09 路径语义（以本次实现为准，旧“workspace 相对”表述作废）：
    绝对路径须落在 workspace（或 `DATASENTRY_ALLOWED_ROOTS`）内；
    相对路径按服务进程 CWD 解析且不许逃出；远端 DSN/URI 透传。
    """

    path: str
    dataset_id: str | None = None
    table_name: str | None = None
    detectors: list[str] | None = None
    seed: int = 42
    tags: dict[str, str] = Field(default_factory=dict)
    sampling: SamplingConfig | None = None


class ScanResponse(BaseModel):
    run: ScanRun
    detector_runs: list[DetectorRun]
    issues: list[Issue]


class ProposeRequest(BaseModel):
    source_path: str
    issue_id: str


class PreviewRequest(BaseModel):
    source_path: str
    issue_id: str


class ApplyRequest(BaseModel):
    source_path: str
    issue_id: str


class HealthResponse(BaseModel):
    ok: bool
    service: str
    version: str
    # D5-06：只暴露 workspace 目录名，不再回显服务端绝对路径。
    workspace: str


class ErrorBody(BaseModel):
    ok: bool = False
    detail: str


class PiiRestoreRequest(BaseModel):
    """POST /pii/sessions/{session_id}/restore 请求体：待还原的占位符文本。"""

    text: str


class PiiRotateRequest(BaseModel):
    """POST /pii/rotate-key 可选请求体：指定新密钥材料（缺省自动生成）。"""

    new_key: str | None = None


class PiiPurgeRequest(BaseModel):
    """POST /pii/sessions/purge 请求体：删除早于 N 天的会话（<1 → 422）。"""

    older_than_days: int = Field(ge=1)


def _config_from(req: ScanRequest) -> ScanConfig:
    return ScanConfig(
        detectors=req.detectors,
        seed=req.seed,
        scan_tags=req.tags,
        sampling=req.sampling or SamplingConfig(),
    )


def _error(exc: Exception) -> int:
    if isinstance(exc, (FileNotFoundError, KeyError)):
        return 404
    from datasentry_core.connectors.errors import (
        ConnectorError,
        DataSourceNotFoundError,
        UnsafeSqlError,
        UnsupportedFormatError,
    )

    if isinstance(exc, DataSourceNotFoundError):
        return 404
    if isinstance(exc, (UnsupportedFormatError, UnsafeSqlError, ConnectorError)):
        return 400
    if isinstance(exc, ValueError):
        return 422
    return 500


def _handle(exc: Exception) -> HTTPException:
    """D5-06：4xx 保留可读 detail；5xx 只给固定文案 + request 级日志。

    旧行为 detail=str(exc) 把服务端绝对路径与驱动异常原文回显给调用方，
    帮攻击者测绘文件系统布局。KeyError 仍按 404 映射（调用处显式处理）。
    """
    status = _error(exc)
    if status >= 500:
        logger.exception("unhandled API error (%s)", type(exc).__name__)
        return HTTPException(status_code=status, detail="internal error (see server logs)")
    return HTTPException(status_code=status, detail=safe_detail(exc))


def _require_api_token(token: str | None) -> None:
    """D5-02：`DATASENTRY_API_TOKEN` 设置时，数据面端点需 `X-Datasentry-Token`。

    未设置 = 本地单机默认（与 CLI 同信任边界），行为零变化；设置后无头/
    错头一律 401。覆盖范围：`/pii/*` 全量 + repairs 写端点（apply/rollback）。

    行 r12 之前这条是唯一的闸门，并把「UI 表单写端点与 MCP stdio」当作同机
    交互面豁免；`D5-11` 实测该豁免使 21 条会改状态的路由在管理员设了 token
    之后仍可裸调（`POST /scans` 回 201）。豁免已收回：`enforce_write_guard`
    中间件覆盖全部写请求，本函数保留为数据面端点的第二层（同一凭据同一语义）。
    """
    expected = os.environ.get("DATASENTRY_API_TOKEN")
    if not expected:
        return
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="invalid or missing API token")


# One fail-closed gate for every state-changing request (D5-10, D5-11). The per-handler
# `_require_api_token` calls were the only mechanism, which is how 21 mutating routes stayed
# reachable with no credential: a new route simply forgot to call it. A middleware cannot be
# forgotten by the next route added.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
UI_WRITE_PREFIX = "/ui/"
WORKER_RPC_PREFIX = "/rpc/"


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


INSECURE_BIND_OPT_IN = "DATASENTRY_ALLOW_INSECURE_BIND"


class InsecureBindRefused(RuntimeError):
    """D5-01/D5-02: a non-loopback bind with nobody allowed to answer for it."""


def resolve_bind(host: str, *, token: str | None, opted_in: bool) -> str | None:
    """Decide whether this bind address may start, returning the warning to log or None.

    Raises instead of warning: `D5-01` measured that a warning is read as permission, and the
    default face behind it is a writable REST/UI surface. Loopback always starts; a non-loopback
    bind needs either an API token (writes then require the header) or an explicit opt-in.
    """
    if _is_loopback(host):
        return None
    if token:
        return f"binding {host!r} on a non-loopback interface; writes require X-Datasentry-Token"
    if opted_in:
        return (
            f"binding {host!r} with NO API token because {INSECURE_BIND_OPT_IN} is set: "
            "every route that is not write-gated is reachable by anyone who can route to this port"
        )
    raise InsecureBindRefused(
        f"refusing to bind {host!r}: it is not a loopback address and no API token is "
        f"configured. Set DATASENTRY_API_TOKEN (writes then need the X-Datasentry-Token "
        f"header), bind 127.0.0.1, or set {INSECURE_BIND_OPT_IN}=1 to accept the exposure "
        "deliberately."
    )


def _audit_restore(*, session_id: str, via: str, ok: bool, reason: str = "") -> None:
    """One line per plaintext-restore request (D5-02). Never the text and never the value:
    invariant 5 keeps secrets and PII out of logs, so only the opaque id, the surface, the
    outcome and a fixed reason code are recorded."""
    logger.info(
        "pii-restore session=%s via=%s ok=%s%s",
        session_id,
        via,
        ok,
        f" reason={reason}" if reason else "",
    )


def _same_origin(request: Request) -> bool:
    """Refuse cross-site form posts. A browser sets Origin itself and a page cannot forge it."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True
    host = urlparse(origin).hostname
    request_host = urlparse(f"//{request.headers.get('host', '')}").hostname
    return host is not None and host == request_host


def _write_allowed(request: Request) -> tuple[bool, str]:
    expected = os.environ.get("DATASENTRY_API_TOKEN")
    peer = request.client.host if request.client else ""
    if not expected:
        if not _same_origin(request):
            return False, "cross-origin write refused"
        return True, ""
    supplied = request.headers.get("x-datasentry-token")
    if supplied and secrets.compare_digest(supplied, expected):
        return True, ""
    if (
        request.url.path.startswith(UI_WRITE_PREFIX)
        and _is_loopback(peer)
        and _same_origin(request)
    ):
        return True, ""
    return False, "invalid or missing API token"


def _get_issue(client: sdk.DataSentry, issue_id: str) -> Issue | None:
    return client.get_issue(issue_id)


# ---------------------------------------------------------------------------
# 调度器（Step 51，V2-D 云侧调度）
# ---------------------------------------------------------------------------


def parse_workers(raw: str) -> list[tuple[str, str]]:
    """解析 DATASENTRY_WORKERS（"url:token;url:token"）为 (url, token) 列表。

    非法条目（空 url/缺 token/畸形分隔）跳过不炸启动（V15，
    ADR-094）。
    """
    workers: list[tuple[str, str]] = []
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        url, _, token = entry.rpartition(":")
        if not url.strip() or not token.strip():
            logger.warning("skipping malformed worker entry: %r", entry)
            continue
        workers.append((url.strip(), token.strip()))
    return workers


def _parse_max_workers(raw: str | None) -> int:
    """解析 DATASENTRY_MAX_WORKERS：非法/<=1 → 1（同步语义），告警。"""
    if not raw:
        return 1
    try:
        value = int(raw)
    except ValueError:
        logger.warning("DATASENTRY_MAX_WORKERS=%r invalid, using 1", raw)
        return 1
    return value if value > 1 else 1


def _build_scheduler(client: sdk.DataSentry) -> Scheduler:
    """绑定工作区元数据库的调度器（SQLite 持久化任务队列，ADR-051）。

    执行器选择（V15，ADR-094）：配置了 DATASENTRY_WORKERS 则用
    WorkerPoolExecutor（url:token 分号分隔，多节点容错路由），
    否则回退 LocalScanExecutor（零迁移）。
    并行度（V16，ADR-096/097）：DATASENTRY_MAX_WORKERS>1 时
    tick/trigger 异步派发线程池并行执行；默认 1 保持同步（零迁移）。
    """
    from datasentry.scheduler.store import SchedulerStore
    from datasentry_core.storage.paths import project_db_path

    db_path = project_db_path(client.workspace)
    store = SchedulerStore(db_path)
    raw_workers = os.environ.get("DATASENTRY_WORKERS", "")
    if raw_workers:
        from datasentry.scheduler.pool import RemoteWorker, WorkerPoolExecutor

        workers = [
            RemoteWorker(id=f"w{i}", url=url, token=token)
            for i, (url, token) in enumerate(parse_workers(raw_workers))
        ]
        if workers:
            logger.info("scheduler using worker pool: %d worker(s)", len(workers))
            return Scheduler(
                store=store,
                executor=WorkerPoolExecutor(workers),
                max_workers=_parse_max_workers(os.environ.get("DATASENTRY_MAX_WORKERS")),
            )
        logger.warning("DATASENTRY_WORKERS set but no valid entries; using local executor")
    return Scheduler(
        store=store,
        executor=LocalScanExecutor(),
        max_workers=_parse_max_workers(os.environ.get("DATASENTRY_MAX_WORKERS")),
    )


def _job_command_from(req: JobCreate, workspace: str) -> JobCommand:
    """请求 → JobCommand：路径相对 workspace 解析为绝对路径。"""
    path = Path(req.path).expanduser()
    if not path.is_absolute():
        path = Path(workspace) / path
    return JobCommand(
        project=req.project or str(Path(workspace).resolve()),
        path=str(path),
        dataset_id=req.dataset_id,
        table_name=req.table_name,
        export_report=req.export_report,
        config=req.config,
    )


def _ui_allowed_source(client: sdk.DataSentry, source_path: str) -> tuple[str, HTMLResponse | None]:
    """Same confinement as `_require_allowed_source`, but answering in the UI's own currency.

    `/ui/*` renders HTML; a raised `HTTPException` would hand the browser bare JSON (UI-06), so the
    refusal comes back as a response for the caller to return.
    """
    from datasentry.scan_paths import ScanPathRejected, resolve_allowed_scan_path

    try:
        return resolve_allowed_scan_path(source_path, workspace=client.workspace), None
    except ScanPathRejected as exc:
        refusal = HTMLResponse(
            ui.render_error(_t("en", "ui.scan_failed"), safe_detail(exc)), status_code=422
        )
        return source_path, refusal


def _require_allowed_source(client: sdk.DataSentry, source_path: str) -> str:
    """D5-04/D5-09: a repair reads *and writes beside* the file it is given, so `source_path` is
    path input just like a scan target, and goes through the same workspace confinement."""
    from datasentry.scan_paths import ScanPathRejected, resolve_allowed_scan_path

    try:
        return resolve_allowed_scan_path(source_path, workspace=client.workspace)
    except ScanPathRejected as exc:
        raise HTTPException(status_code=422, detail=safe_detail(exc)) from exc


def _registered_sources(client: sdk.DataSentry) -> list[str]:
    """Source paths this workspace has already scanned, most recent first (UI-04).

    Feeds the repair faces' picker so the user names a dataset the server knows instead of typing
    into its filesystem namespace. Convenience, not the boundary: a crafted POST still has to pass
    `resolve_allowed_scan_path` (row r5).
    """
    paths: list[str] = []
    for run in client.list_scan_runs():
        path = run.source_path
        if path and path not in paths:
            paths.append(path)
    return paths


def _no_issue_selected(run_id: str) -> HTMLResponse:
    """The batch faces' refusal when a submit carries no issue at all (UI-07).

    It used to read "no issues selected", which contradicts the ticks still visible on the page the
    user came from; it now names the action to take and links back to that scan.
    """
    return HTMLResponse(
        ui.render_error(
            _t("en", "ui.batch_repair_title"),
            _t("en", "ui.select_at_least_one"),
            back_href=f"/ui/scans/{run_id}",
            back_label=_t("en", "ui.back_to_scan"),
        ),
        status_code=400,
    )


def _pii_vault(client: sdk.DataSentry) -> PIIVault:
    """绑定工作区元数据库的 PII vault（V17，Step 99，ADR-099）。

    key 未配置（env/文件均无）时抛 503——与 /rpc/execute 的
    disabled 语义一致（CLI 侧等价 EXIT_CONFIG）。删除端点不经过
    本函数：删除密文行无需密钥（与 CLI llm restore --delete 一致）。
    """
    vault = client.pii_vault()
    if not vault.key_configured:
        raise HTTPException(
            status_code=503,
            detail="pii vault disabled: no encryption key configured — "
            "set DATASENTRY_ENCRYPTION_KEY or run 'datasentry llm rotate-key'",
        )
    return vault


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------


def create_app(project: str | Path | None = None, *, worker_token: str | None = None) -> FastAPI:
    """创建绑定给定工作区的应用（默认当前目录或 DATASENTRY_PROJECT）。

    `worker_token`：启用 POST /rpc/execute 远端执行端点（V14，
    ADR-091）的共享密钥——未配置时端点默认禁用（503），避免
    无鉴权执行入口。环境变量 `DATASENTRY_WORKER_TOKEN` 为后备。
    """
    if project is None:
        project = os.environ.get("DATASENTRY_PROJECT")
    if worker_token is None:
        worker_token = os.environ.get("DATASENTRY_WORKER_TOKEN")
    client = sdk.DataSentry(project=project, enforce_scan_containment=True)
    scheduler = _build_scheduler(client)
    worker = SchedulerWorker(scheduler)
    # V22（Step 115，ADR-115）：in-flight run 取消标记 registry（线程安全）。
    # run_token = 调度端 run_id（worker 无 job 概念）；rpc_execute 登记，
    # /rpc/cancel 打标，扫描完成后回执 cancelled:true（结果由调度端丢弃，
    # worker 无法强杀扫描线程——尽力而为语义）。
    inflight_lock = threading.Lock()
    inflight_cancelled: dict[str, bool] = {}

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        scheduler.recover()
        worker.start()
        try:
            yield
        finally:
            worker.stop()

    app = FastAPI(title="DataSentry API", version=__version__, lifespan=_lifespan)

    @app.middleware("http")
    async def enforce_write_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """D5-10/D5-11: mutating requests are authorised here or not at all.

        `/rpc/*` is exempt because it authenticates with the worker token in its own handler; that
        exemption is a named prefix, and `AUDIT/tools/route_guard_census.py` fails if it grows.
        """
        if request.method in MUTATING_METHODS and not request.url.path.startswith(
            WORKER_RPC_PREFIX
        ):
            allowed, detail = _write_allowed(request)
            if not allowed:
                return JSONResponse({"ok": False, "detail": detail}, status_code=401)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def render_validation_error(request: Request, exc: RequestValidationError) -> Response:
        """UI forms answer in HTML; the REST face keeps FastAPI's JSON contract (UI-06).

        A form field submitted empty arrives as *missing* to Starlette's parser, so a browser
        pressing a button on an unfillable form used to get `{"detail":[{"type":"missing",…}]}` --
        a page with no navigation to escape it. `source_path` on an empty workspace reaches this
        path without anyone hand-crafting a request (row r6 made it reachable).
        """
        if request.url.path.startswith(UI_WRITE_PREFIX):
            fields = ", ".join(
                ".".join(str(part) for part in err.get("loc", ()) if part != "body")
                for err in exc.errors()
            )
            return HTMLResponse(
                ui.render_error(
                    _t("en", "ui.scan_failed"),
                    f"missing form field: {fields or 'unknown'}",
                ),
                status_code=422,
            )
        return JSONResponse(
            {"detail": jsonable_encoder(exc.errors())},
            status_code=422,
        )

    app.state.client = client
    app.state.scheduler = scheduler

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        return HealthResponse(
            ok=True,
            service="datasentry",
            version=__version__,
            workspace=client.workspace.name,
        )

    @app.get("/", tags=["meta"])
    def root() -> dict[str, object]:
        return {
            "service": "datasentry",
            "version": __version__,
            "endpoints": list(_ENDPOINTS),
        }

    @app.get("/scans", tags=["scans"])
    def list_scan_ids() -> list[str]:
        return [scan.id for scan in client.list_scan_runs()]

    @app.post("/scans", response_model=ScanResponse, tags=["scans"], status_code=201)
    def create_scan(req: ScanRequest) -> ScanResponse:
        from datasentry.scan_paths import resolve_allowed_scan_path

        try:
            scan_path = resolve_allowed_scan_path(req.path, workspace=client.workspace)
            scan, runs, issues = client.scan_file(
                scan_path,
                dataset_id=req.dataset_id,
                table_name=req.table_name,
                config=_config_from(req),
                on_progress=_on_progress_for(req.path),
            )
        except Exception as exc:
            _publish_progress(req.path, 0, 0, "", False)
            raise _handle(exc) from exc
        _publish_progress(req.path, len(runs), len(runs), "", False)
        return ScanResponse(run=scan, detector_runs=runs, issues=issues)

    @app.get("/scans/progress", tags=["scans"])
    def scan_progress(path: str = Query(min_length=1)) -> dict[str, object]:
        """V25：Web UI 轮询扫描进度（path → {scanning, done, total, detector}）。"""
        progress = _progress_for(path)
        if not progress:
            raise HTTPException(status_code=404, detail=f"no scan progress for path: {path}")
        return progress

    @app.get("/scans/progress/latest", tags=["scans"])
    def scan_progress_latest() -> dict[str, object]:
        """V25：最近更新的扫描进度（批量场景前端按文件轮询）。"""
        latest = _latest_progress()
        if not latest:
            raise HTTPException(status_code=404, detail="no scan progress yet")
        return latest

    @app.get("/scans/{run_id}", response_model=ScanRun, tags=["scans"])
    def get_scan(run_id: str) -> ScanRun:
        try:
            scan = client.get_scan(run_id)
        except Exception as exc:
            raise _handle(exc) from exc
        if scan is None:
            raise HTTPException(status_code=404, detail=f"scan run not found: {run_id}")
        return scan

    @app.get("/scans/{run_id}/issues", response_model=list[Issue], tags=["scans"])
    def get_scan_issues(run_id: str) -> list[Issue]:
        return client.list_issues(scan_run_id=run_id)

    @app.get("/scans/{run_id}/report", tags=["scans"])
    def get_report(run_id: str) -> dict[str, object]:
        try:
            return client.export_report(run_id)
        except Exception as exc:
            raise _handle(exc) from exc

    @app.get("/scans/{run_id}/score", tags=["scans"])
    def get_score(run_id: str) -> dict[str, object]:
        try:
            score = client.quality_score(run_id)
        except Exception as exc:
            raise _handle(exc) from exc
        if score is None:
            raise HTTPException(status_code=404, detail="quality score unavailable")
        return score.model_dump()

    @app.get("/scans/{run_id}/report.html", response_class=HTMLResponse, tags=["ui"])
    def ui_report_html(
        run_id: str,
        request: Request,
        lang: str = Query(default="en"),
    ) -> HTMLResponse:
        """交互式 HTML 报告（Step 49，V2-B）：server 模式注入工作台联动与趋势数据。"""
        from datasentry.trends import build_comparison, build_trends
        from datasentry_core.reporting.html import render_html

        try:
            report = client.export_report(run_id)
        except Exception as exc:
            raise _handle(exc) from exc
        trends = [t.to_report_dict() for t in build_trends(client.list_scan_runs())]
        base = str(request.base_url).rstrip("/")
        profiles = client.load_profile(run_id)
        dataset_id = str(cast("dict[str, Any]", report)["scan"]["dataset_id"])
        comparison = build_comparison(client.list_scan_runs(), dataset_id, run_id)
        return HTMLResponse(
            render_html(
                report,
                trends=trends or None,
                server_base_url=base,
                profiles=profiles,
                comparison=comparison,
                lang=lang,
            )
        )

    @app.get("/trends", tags=["trends"])
    def list_trends(dataset_id: str | None = Query(default=None)) -> dict[str, object]:
        """跨扫描趋势 JSON 数据面（Step 65 同源，ADR-066）：build_trends 摘要。"""
        from datasentry.trends import build_trends

        trends = build_trends(client.list_scan_runs())
        if dataset_id is not None:
            trends = [t for t in trends if t.dataset_id == dataset_id]
        data = [
            {
                **t.to_report_dict(),
                "delta": t.delta,
                "direction": t.direction,
                "latest_score": t.latest_score,
                "latest_issues": t.latest_issues,
            }
            for t in trends
        ]
        return {"trends": data, "count": len(data)}

    @app.get("/scans/{run_id}/profiles", tags=["scans"])
    def get_scan_profiles(run_id: str) -> dict[str, object]:
        """画像 sidecar JSON（Step 61 数据面，ADR-066）：缺失 404。"""
        profiles = client.load_profile(run_id)
        if profiles is None:
            raise HTTPException(status_code=404, detail="column profiles unavailable")
        return profiles

    @app.get("/issues", response_model=list[Issue], tags=["issues"])
    def list_all_issues(
        severity_at_least: str | None = Query(default=None),
    ) -> list[Issue]:
        return client.list_issues(severity_at_least=severity_at_least)

    # ---- 修复端点（15 章 / ADR-020；source_path 为待修复源文件） -------

    @app.post(
        "/scans/{run_id}/repairs/propose",
        response_model=RepairProposal | None,
        tags=["repairs"],
    )
    def repair_propose(run_id: str, req: ProposeRequest) -> RepairProposal | None:
        source_path = _require_allowed_source(client, req.source_path)
        try:
            return client.repair_propose(req.issue_id, source_path)
        except Exception as exc:
            raise _handle(exc) from exc

    @app.post(
        "/scans/{run_id}/repairs/preview",
        tags=["repairs"],
    )
    def repair_preview(run_id: str, req: PreviewRequest) -> dict[str, object] | None:
        source_path = _require_allowed_source(client, req.source_path)
        try:
            result = client.repair_preview(req.issue_id, source_path)
        except Exception as exc:
            raise _handle(exc) from exc
        if result is None:
            return None
        proposal, preview = result
        return {
            "proposal": proposal.model_dump(),
            "preview": preview.model_dump(),
        }

    @app.post(
        "/scans/{run_id}/repairs/apply",
        response_model=RepairRun,
        tags=["repairs"],
    )
    def repair_apply(
        run_id: str,
        req: ApplyRequest,
        token: Annotated[str | None, Header(alias="X-Datasentry-Token")] = None,
    ) -> RepairRun:
        _require_api_token(token)
        source_path = _require_allowed_source(client, req.source_path)
        try:
            return client.repair_apply(req.issue_id, source_path)
        except Exception as exc:
            raise _handle(exc) from exc

    @app.post(
        "/repairs/{run_id}/rollback",
        response_model=RepairRun,
        tags=["repairs"],
    )
    def repair_rollback(
        run_id: str,
        token: Annotated[str | None, Header(alias="X-Datasentry-Token")] = None,
    ) -> RepairRun:
        _require_api_token(token)
        try:
            return client.repair_rollback(run_id)
        except Exception as exc:
            raise _handle(exc) from exc

    @app.get("/repairs", response_model=list[RepairRun], tags=["repairs"])
    def list_repair_runs() -> list[RepairRun]:
        return client.list_repair_runs()

    @app.post("/repairs/{repair_run_id}/verify", tags=["repairs"])
    def repair_run_verify(repair_run_id: str) -> dict[str, object]:
        """V41：验证闭环 REST 端点——重扫修复副本，返回对比报告 JSON。"""
        try:
            scan, report = client.repair_verify(repair_run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=safe_detail(exc)) from exc
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=safe_detail(exc)) from exc
        return {"verify_scan_run_id": scan.id, **report}

    @app.get("/repairs/{repair_run_id}/diff", tags=["repairs"])
    def repair_run_diff(repair_run_id: str) -> dict[str, object]:
        """V46：修复工件 diff REST 端点——仅返回变更行（JSON 友好）。"""
        try:
            run, columns, before_rows, after_rows, changed = client.repair_diff(repair_run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=safe_detail(exc)) from exc
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=safe_detail(exc)) from exc
        rows = []
        for i in changed:
            b = before_rows[i] if i < len(before_rows) else []
            a = after_rows[i] if i < len(after_rows) else []
            rows.append(
                {
                    "line": i + 2,
                    "before": {c: b[j] for j, c in enumerate(columns) if j < len(b)},
                    "after": {c: a[j] for j, c in enumerate(columns) if j < len(a)},
                }
            )
        return {
            "run_id": run.id,
            "status": run.status.value,
            "columns": columns,
            "changed_rows": rows,
        }

    # ---- Web UI（Step 24：服务端渲染核心页） ----------------------------

    @app.get("/ui", response_class=HTMLResponse, tags=["ui"])
    @app.get("/ui/", response_class=HTMLResponse, tags=["ui"])
    def ui_home(lang: str = Query(default="en")) -> HTMLResponse:
        return HTMLResponse(ui.render_home(client.list_scan_runs(), lang=lang))

    @app.post("/ui/scans", response_class=HTMLResponse, tags=["ui"])
    def ui_create_scan(path: str = Form()) -> Response:
        from datasentry.scan_paths import ScanPathRejected

        global _last_batch
        _last_batch = None
        try:
            paths = _expand_scan_paths(path, workspace=client.workspace)
        except ScanPathRejected as exc:
            return HTMLResponse(
                ui.render_error(_t("en", "ui.scan_failed"), safe_detail(exc)), status_code=422
            )
        if not paths:
            detail = (
                f"not found: {_last_missing_paths[0]}"
                if _last_missing_paths
                else "no parseable path"
            )
            return HTMLResponse(
                ui.render_error(_t("en", "ui.scan_failed"), detail), status_code=404
            )
        run = None
        scanned: list[ScanRun] = []
        failed: list[dict[str, str]] = [
            {"path": p, "error": "file not found"} for p in _last_missing_paths
        ]
        for p in paths:
            try:
                scan, _runs, _issues = client.scan_file(p, on_progress=_on_progress_for(p))
            except Exception as exc:
                failed.append({"path": p, "error": safe_detail(exc)})
                _publish_progress(p, 0, 0, "", False)
                continue
            run = scan
            scanned.append(scan)
            _publish_progress(p, len(_runs), len(_runs), "", False)
        if run is None:
            detail = "; ".join(f"{e['path']}: {e['error']}" for e in failed) or "no scan produced"
            return HTMLResponse(
                ui.render_error(_t("en", "ui.scan_failed"), detail), status_code=404
            )
        if len(paths) == 1 and not failed:
            return RedirectResponse(url=f"/ui/scans/{run.id}", status_code=303)
        scores = [s.quality_score.overall for s in scanned if s.quality_score]
        _last_batch = {
            "run_ids": [s.id for s in scanned],
            "files_scanned": len(scanned),
            "files_failed": len(failed),
            "errors": failed,
            "total_issues": sum(v for s in scanned for v in s.issues_count.values()),
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        }
        return RedirectResponse(url="/ui/scans", status_code=303)

    @app.get("/ui/scans", response_class=HTMLResponse, tags=["ui"])
    def ui_scans_list(lang: str = Query(default="en")) -> HTMLResponse:
        global _last_batch
        batch, _last_batch = _last_batch, None
        return HTMLResponse(
            ui.render_home(
                client.list_scan_runs(),
                batch=batch,
                lang=lang,
                title=_t(lang, "ui.nav_scans"),
            )
        )

    @app.post(
        "/ui/scans/{run_id}/repairs/batch-propose",
        response_class=HTMLResponse,
        tags=["ui"],
    )
    def ui_batch_repair_propose(
        run_id: str,
        issue_ids: Annotated[list[str] | None, Form()] = None,
        source_path: str = Form(),
    ) -> HTMLResponse:
        """V30：批量修复提案（只 propose，不 apply；写路径仍走单条工作台）。"""
        source_path, refusal = _ui_allowed_source(client, source_path)
        if refusal is not None:
            return refusal
        issue_ids = issue_ids or []
        if not issue_ids:
            return _no_issue_selected(run_id)
        proposals: dict[str, object] = {}
        errors: dict[str, str] = {}
        for issue_id in issue_ids:
            try:
                prop = client.repair_propose(issue_id, source_path)
                if prop is not None:
                    proposals[issue_id] = prop
            except Exception as exc:
                errors[issue_id] = safe_detail(exc)
        issues = client.list_issues(scan_run_id=run_id)
        by_id = {i.id: i for i in issues}
        selected = [by_id[i] for i in issue_ids if i in by_id]
        return HTMLResponse(
            ui.render_batch_repair(
                run_id,
                selected,
                cast(dict[str, Any], proposals),
                errors,
                source_path=source_path,
            )
        )

    @app.post(
        "/ui/scans/{run_id}/repairs/batch-apply",
        response_class=HTMLResponse,
        tags=["ui"],
    )
    def ui_batch_repair_apply(
        run_id: str,
        issue_ids: Annotated[list[str] | None, Form()] = None,
        source_path: str = Form(),
    ) -> HTMLResponse:
        """V31：批量应用修复（写数据；结果页含每行回滚入口）。"""
        source_path, refusal = _ui_allowed_source(client, source_path)
        if refusal is not None:
            return refusal
        issue_ids = issue_ids or []
        if not issue_ids:
            return _no_issue_selected(run_id)
        runs: dict[str, object] = {}
        skipped: dict[str, str] = {}
        errors: dict[str, str] = {}
        for issue_id in issue_ids:
            try:
                run = client.repair_apply(issue_id, source_path)
                runs[issue_id] = run
            except ValueError as exc:
                if "no repair proposal" in str(exc):
                    skipped[issue_id] = safe_detail(exc)
                else:
                    errors[issue_id] = safe_detail(exc)
            except Exception as exc:
                errors[issue_id] = safe_detail(exc)
        issues = client.list_issues(scan_run_id=run_id)
        by_id = {i.id: i for i in issues}
        selected = [by_id[i] for i in issue_ids if i in by_id]
        return HTMLResponse(
            ui.render_batch_apply(
                run_id,
                selected,
                cast(dict[str, Any], runs),
                errors,
                skipped=skipped,
                source_path=source_path,
            )
        )

    @app.post(
        "/ui/scans/{run_id}/repairs/batch-rollback",
        response_class=HTMLResponse,
        tags=["ui"],
    )
    def ui_batch_repair_rollback(
        run_id: str,
        repair_run_ids: Annotated[list[str] | None, Form()] = None,
    ) -> HTMLResponse:
        """V34：批量回滚（写数据；结果页含状态）。"""
        repair_run_ids = repair_run_ids or []
        if not repair_run_ids:
            return HTMLResponse(
                ui.render_error(_t("en", "ui.scan_failed"), "no repair runs selected"),
                status_code=400,
            )
        runs: list[RepairRun] = []
        errors: dict[str, str] = {}
        for repair_run_id in repair_run_ids:
            try:
                runs.append(client.repair_rollback(repair_run_id))
            except Exception as exc:
                errors[repair_run_id] = safe_detail(exc)
        return HTMLResponse(ui.render_batch_rollback(runs, errors))

    @app.get("/ui/repairs", response_class=HTMLResponse, tags=["ui"])
    def ui_repairs(lang: str = "en") -> HTMLResponse:
        """V32：修复历史页——所有修复 run + 回滚入口。"""
        return HTMLResponse(ui.render_repairs(client.list_repair_runs(), lang=lang))

    @app.get("/ui/repairs/{repair_run_id}/artifact", response_class=HTMLResponse, tags=["ui"])
    def ui_repairs_artifact(repair_run_id: str, lang: str = "en") -> Response:
        """V43：修复工件页——before 快照 vs 修复副本逐行 diff。"""
        try:
            run, columns, before_rows, after_rows, changed = client.repair_diff(repair_run_id)
        except KeyError:
            return HTMLResponse(
                ui.render_error(_t("en", "ui.scan_failed"), "repair run not found"),
                status_code=404,
            )
        except (ValueError, FileNotFoundError) as exc:
            return HTMLResponse(
                ui.render_error(_t("en", "ui.scan_failed"), safe_detail(exc)), status_code=400
            )
        return HTMLResponse(
            ui.render_repair_artifact(run, columns, before_rows, after_rows, changed, lang=lang)
        )

    @app.post("/ui/repairs/{repair_run_id}/verify", response_class=HTMLResponse, tags=["ui"])
    def ui_repairs_verify(repair_run_id: str) -> Response:
        """V41：验证闭环——重扫修复副本，303 到原扫描 vs 修复后扫描的对比页。"""
        try:
            scan, report = client.repair_verify(repair_run_id)
        except KeyError:
            return HTMLResponse(
                ui.render_error(_t("en", "ui.scan_failed"), "repair run not found"),
                status_code=404,
            )
        except (ValueError, FileNotFoundError) as exc:
            return HTMLResponse(
                ui.render_error(_t("en", "ui.scan_failed"), safe_detail(exc)), status_code=400
            )
        return RedirectResponse(
            url=f"/ui/compare?runs={report['source_scan_run_id']}&runs={scan.id}",
            status_code=303,
        )

    @app.post("/ui/repairs/{repair_run_id}/rollback", response_class=HTMLResponse, tags=["ui"])
    def ui_repairs_rollback(repair_run_id: str) -> Response:
        try:
            client.repair_rollback(repair_run_id)
        except KeyError:
            return HTMLResponse(
                ui.render_error(_t("en", "ui.rollback_failed"), "repair run not found"),
                status_code=404,
            )
        except Exception:
            logger.exception("ui rollback failed for %s", repair_run_id)
            return HTMLResponse(
                ui.render_error(
                    _t("en", "ui.rollback_failed"), "rollback failed (see server logs)"
                ),
                status_code=500,
            )
        return RedirectResponse(url="/ui/repairs", status_code=303)

    @app.get("/ui/compare", response_class=HTMLResponse, tags=["ui"])
    def ui_compare(
        runs: Annotated[list[str], Query(min_length=2, max_length=2)],
        lang: Annotated[str, Query()] = "en",
    ) -> HTMLResponse:
        """V28：两 run 对比页（勾选 → /ui/compare?runs=a&runs=b）。"""
        try:
            report = client.drift_compare(runs[0], runs[1])
        except KeyError as exc:
            return HTMLResponse(
                ui.render_error(_t("en", "ui.scan_failed"), safe_detail(exc)), status_code=404
            )
        runs_map = {r.id: r for r in client.list_scan_runs()}
        reference = runs_map.get(runs[0])
        current = runs_map.get(runs[1])
        if reference is None or current is None:
            return HTMLResponse(
                ui.render_error(_t("en", "ui.scan_failed"), "scan run not found"),
                status_code=404,
            )
        return HTMLResponse(
            ui.render_compare(
                reference,
                current,
                report,
                client.list_issues(scan_run_id=runs[0]),
                client.list_issues(scan_run_id=runs[1]),
                repairs=client.list_repair_runs(),
                lang=lang,
            )
        )

    @app.get("/ui/trends", response_class=HTMLResponse, tags=["ui"])
    def ui_trends(lang: str = Query(default="en")) -> HTMLResponse:
        from datasentry.trends import build_trends

        return HTMLResponse(ui.render_trends(build_trends(client.list_scan_runs()), lang=lang))

    @app.get("/ui/pii", response_class=HTMLResponse, tags=["ui"])
    def ui_pii(lang: str = Query(default="en")) -> HTMLResponse:
        """PII 加密会话管理页（V17，Step 101，ADR-101）：列表 + 还原表单。"""

        vault = client.pii_vault()
        return HTMLResponse(
            ui.render_pii(
                client.list_pii_mappings(),
                key_source=vault.key_source,
                key_configured=vault.key_configured,
                lang=lang,
            )
        )

    @app.post("/ui/pii", response_class=HTMLResponse, tags=["ui"])
    def ui_pii_restore(
        session_id: str = Form(), text: str = Form(), lang: str = Query(default="en")
    ) -> HTMLResponse:
        """还原表单提交：同页展示还原结果（仅内存响应体，不落盘）。"""
        from datasentry.pii_vault import VaultKeyMissingError

        vault = client.pii_vault()
        restored: str | None = None
        error: str | None = None
        if not vault.key_configured:
            error = _t(lang, "ui.pii_key_missing")
            _audit_restore(session_id=session_id, via="ui", ok=False, reason="key-missing")
        else:
            try:
                restored = vault.restore_text(text, session_id)
                _audit_restore(session_id=session_id, via="ui", ok=True)
            except KeyError as exc:
                error = safe_detail(exc)
                _audit_restore(session_id=session_id, via="ui", ok=False, reason="not-found")
            except VaultKeyMissingError as exc:
                error = safe_detail(exc)
                _audit_restore(session_id=session_id, via="ui", ok=False, reason="key-missing")
        return HTMLResponse(
            ui.render_pii(
                client.list_pii_mappings(),
                key_source=vault.key_source,
                key_configured=vault.key_configured,
                restored=restored,
                error=error,
                lang=lang,
            )
        )

    @app.post("/ui/pii/rotate", response_class=HTMLResponse, tags=["ui"])
    def ui_pii_rotate(lang: str = Query(default="en")) -> HTMLResponse:
        """轮换密钥按钮：重加密全部映射 + 写入本地 key 文件（V18，ADR-102）。"""
        from datasentry.pii_vault import VaultKeyMissingError

        vault = client.pii_vault()
        key_ok: str | None = None
        key_result: dict[str, Any] | None = None
        error: str | None = None
        if not vault.key_configured:
            error = _t(lang, "ui.pii_key_missing")
        else:
            try:
                result = vault.rotate_key()
            except VaultKeyMissingError as exc:
                error = safe_detail(exc)
            else:
                key_ok = _t(lang, "ui.pii_rotate_ok")
                key_result = {"rotated": result["rotated"], "key_file": result["key_file"]}
        return HTMLResponse(
            ui.render_pii(
                client.list_pii_mappings(),
                key_source=vault.key_source,
                key_configured=vault.key_configured,
                error=error,
                key_ok=key_ok,
                key_result=key_result,
                lang=lang,
            )
        )

    @app.post("/ui/pii/key", response_class=HTMLResponse, tags=["ui"])
    def ui_pii_set_key(
        new_key: str = Form(default=""), lang: str = Query(default="en")
    ) -> HTMLResponse:
        """设置密钥表单：以指定材料轮换（与 CLI rotate-key --new-key 对齐，V18）。"""
        from datasentry.pii_vault import VaultKeyMissingError

        vault = client.pii_vault()
        key_ok: str | None = None
        key_result: dict[str, Any] | None = None
        error: str | None = None
        if not vault.key_configured:
            error = _t(lang, "ui.pii_key_missing")
        else:
            try:
                result = vault.rotate_key(new_key=new_key or None)
            except VaultKeyMissingError as exc:
                error = safe_detail(exc)
            else:
                key_ok = _t(lang, "ui.pii_set_key_ok")
                key_result = {"rotated": result["rotated"], "key_file": result["key_file"]}
        return HTMLResponse(
            ui.render_pii(
                client.list_pii_mappings(),
                key_source=vault.key_source,
                key_configured=vault.key_configured,
                error=error,
                key_ok=key_ok,
                key_result=key_result,
                lang=lang,
            )
        )

    @app.post("/ui/pii/purge", response_class=HTMLResponse, tags=["ui"])
    def ui_pii_purge(
        older_than_days: int = Form(default=30), lang: str = Query(default="en")
    ) -> HTMLResponse:
        """清理表单：删除早于 N 天的会话（无需密钥，V18，Step 103，ADR-103）。"""

        vault = client.pii_vault()
        error: str | None = None
        purge_ok: str | None = None
        purged: int | None = None
        if older_than_days < 1:
            error = "older_than_days must be >= 1"
        else:
            purge_ok = _t(lang, "ui.pii_purged_result")
            purged = vault.purge_sessions(older_than_days)
        return HTMLResponse(
            ui.render_pii(
                client.list_pii_mappings(),
                key_source=vault.key_source,
                key_configured=vault.key_configured,
                error=error,
                purge_ok=purge_ok,
                purged=purged,
                lang=lang,
            )
        )

    @app.get("/ui/scans/{run_id}", response_class=HTMLResponse, tags=["ui"])
    def ui_scan_detail(
        run_id: str,
        severity: str | None = Query(default=None),
        lang: str = Query(default="en"),
    ) -> HTMLResponse:
        scan = client.get_scan(run_id)
        if scan is None:
            return HTMLResponse(
                ui.render_error(_t("en", "ui.scan_not_found"), f"scan run: {run_id}", lang=lang),
                status_code=404,
            )
        issues = client.list_issues(scan_run_id=run_id, severity_at_least=severity)
        return HTMLResponse(
            ui.render_scan_detail(
                scan,
                issues,
                severity_filter=severity,
                lang=lang,
                known_sources=_registered_sources(client),
            )
        )

    @app.get(
        "/ui/scans/{run_id}/issues/{issue_id}",
        response_class=HTMLResponse,
        tags=["ui"],
    )
    def ui_workbench(run_id: str, issue_id: str) -> HTMLResponse:
        issue = _get_issue(client, issue_id)
        if issue is None:
            return HTMLResponse(
                ui.render_error(_t("en", "ui.issue_not_found"), issue_id), status_code=404
            )
        scan = client.get_scan(run_id)
        return HTMLResponse(
            ui.render_workbench(
                issue,
                run_id=run_id,
                source_path=scan.source_path if scan else None,
                known_sources=_registered_sources(client),
            )
        )

    @app.post(
        "/ui/scans/{run_id}/issues/{issue_id}",
        response_class=HTMLResponse,
        tags=["ui"],
    )
    def ui_workbench_action(
        run_id: str,
        issue_id: str,
        source_path: str = Form(),
        action: str = Form(),
    ) -> HTMLResponse:
        source_path, refusal = _ui_allowed_source(client, source_path)
        if refusal is not None:
            return refusal
        issue = _get_issue(client, issue_id)
        if issue is None:
            return HTMLResponse(
                ui.render_error(_t("en", "ui.issue_not_found"), issue_id), status_code=404
            )
        error: str | None = None
        proposal: RepairProposal | None = None
        preview: RepairPreview | None = None
        run: RepairRun | None = None
        diff: tuple[list[str], list[list[object]], list[list[object]], list[int]] | None = None
        try:
            if action == "propose":
                proposal = client.repair_propose(issue_id, source_path)
                if proposal is not None:
                    pair = client.repair_preview(issue_id, source_path)
                    if pair is not None:
                        preview = pair[1]
                else:
                    error = "no repair proposal available for this issue"
            elif action == "apply":
                run = client.repair_apply(issue_id, source_path)
                _, columns, before_rows, after_rows, changed = client.repair_diff(run.id)
                diff = (columns, before_rows, after_rows, changed)
            else:
                error = f"unknown action: {action}"
        except Exception as exc:
            # Not `str(exc)`: `repair_diff` names the artefact paths it could not read, and this is
            # a page served to anyone who can reach the port (D2-02, D5-06).
            error = safe_detail(exc)
        return HTMLResponse(
            ui.render_workbench(
                issue,
                run_id=run_id,
                source_path=source_path,
                proposal=proposal,
                preview=preview,
                run=run,
                diff=diff,
                error=error,
                known_sources=_registered_sources(client),
            )
        )

    @app.post(
        "/ui/scans/{run_id}/repairs/{repair_run_id}/rollback",
        response_class=HTMLResponse,
        tags=["ui"],
    )
    def ui_rollback(run_id: str, repair_run_id: str) -> Response:
        try:
            client.repair_rollback(repair_run_id)
        except KeyError:
            return HTMLResponse(
                ui.render_error(_t("en", "ui.rollback_failed"), "repair run not found"),
                status_code=404,
            )
        except Exception:
            logger.exception("ui rollback failed for %s", repair_run_id)
            return HTMLResponse(
                ui.render_error(
                    _t("en", "ui.rollback_failed"), "rollback failed (see server logs)"
                ),
                status_code=500,
            )
        return RedirectResponse(url=f"/ui/scans/{run_id}", status_code=303)

    # ---- 计划任务（Step 51，V2-D 云侧调度） --------------------------------

    @app.post("/jobs", tags=["jobs"], status_code=201)
    def create_job(req: JobCreate) -> dict[str, Any]:
        from datasentry.scheduler.core import InvalidCronError, next_run, validate_cron

        try:
            validate_cron(req.cron)
        except InvalidCronError as exc:
            raise HTTPException(status_code=422, detail=safe_detail(exc)) from exc
        from datasentry.scan_paths import ScanPathRejected, allowed_roots, resolve_allowed_scan_path

        # D5-13: a job is a scheduled scan the caller will not be watching, so both of its
        # filesystem handles are checked here -- the path to scan, and the project directory the
        # executor would otherwise happily create a workspace inside of.
        try:
            resolve_allowed_scan_path(req.path, workspace=client.workspace)
        except ScanPathRejected as exc:
            raise HTTPException(status_code=422, detail=safe_detail(exc)) from exc
        if req.project:
            project_root = Path(req.project).expanduser().resolve()
            if not any(
                project_root == root or project_root.is_relative_to(root)
                for root in allowed_roots(client.workspace)
            ):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "job project must be the server workspace or a "
                        "DATASENTRY_ALLOWED_ROOTS entry"
                    ),
                )
        now = utcnow()
        job = ScheduledJob(
            job_id=f"job_{uuid.uuid4().hex[:12]}",
            name=req.name,
            project=req.project or str(client.workspace),
            command=_job_command_from(req, str(client.workspace)),
            cron=req.cron,
            retry_attempts=req.retry_attempts,
            webhook_url=req.webhook_url,
            gate_quality_min=req.gate_quality_min,
            export_report=req.export_report,
            next_run_at=next_run(req.cron, now),
            created_at=now,
            updated_at=now,
        )
        scheduler.store.create_job(job)
        return job.view()

    @app.get("/jobs", tags=["jobs"])
    def list_jobs() -> list[dict[str, Any]]:
        return [job.view() for job in scheduler.store.list_jobs()]

    @app.get("/jobs/{job_id}", tags=["jobs"])
    def get_job(job_id: str) -> dict[str, Any]:
        job = scheduler.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        return {
            "job": job.view(),
            "runs": [run.view() for run in scheduler.store.list_runs(job_id)],
        }

    @app.get("/jobs/{job_id}/runs", tags=["jobs"])
    def list_job_runs(job_id: str, limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
        if scheduler.store.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        runs = [run.view() for run in scheduler.store.list_runs(job_id, limit=limit)]
        return {"job_id": job_id, "count": len(runs), "runs": runs}

    @app.post("/jobs/{job_id}/test-webhook", tags=["jobs"])
    def test_job_webhook(job_id: str) -> dict[str, Any]:
        """发送样例通知负载到任务 webhook（V13，ADR-087 协作链路验证）。

        D5-03：响应不再回显远端 status_code（盲打回显 oracle），仅返回
        notified 布尔值；elapsed_ms 保留用于排障。
        """
        from datasentry.scheduler.models import JobResult

        job = scheduler.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        if not job.webhook_url:
            raise HTTPException(status_code=422, detail=f"job has no webhook_url: {job_id}")
        payload: dict[str, object] = {
            "event": "job.test",
            "job_id": job_id,
            "name": job.name,
            "timestamp": iso(utcnow()),
            "payload": JobResult().model_dump(),
        }
        refusal = webhook_target_refusal(job.webhook_url)
        if refusal:
            # Before the try/except below: a 422 for the caller's target must not be swallowed
            # by the delivery-failure handler and re-labelled 502.
            raise HTTPException(status_code=422, detail=refusal)
        try:
            import time

            import httpx

            started = time.monotonic()
            with httpx.Client(timeout=5.0) as client:
                response = client.post(job.webhook_url, json=payload)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return {
                "job_id": job_id,
                "elapsed_ms": elapsed_ms,
                "notified": response.status_code < 400,
            }
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"webhook delivery failed: {safe_detail(exc)}"
            ) from exc

    @app.post("/jobs/{job_id}/trigger", tags=["jobs"], status_code=202)
    def trigger_job(job_id: str) -> dict[str, Any]:
        job = scheduler.store.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        run_id = scheduler.trigger(job_id)
        if run_id is None:
            raise HTTPException(status_code=409, detail=f"job already running: {job_id}")
        return {"run_id": run_id}

    @app.patch("/jobs/{job_id}", tags=["jobs"])
    def update_job(job_id: str, req: JobUpdate) -> dict[str, Any]:
        from datasentry.scheduler.core import InvalidCronError, next_run, validate_cron

        if scheduler.store.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        changes: dict[str, object] = {}
        if req.enabled is not None:
            changes["enabled"] = req.enabled
        if req.cron is not None:
            try:
                validate_cron(req.cron)
            except InvalidCronError as exc:
                raise HTTPException(status_code=422, detail=safe_detail(exc)) from exc
            changes["cron"] = req.cron
            changes["next_run_at"] = next_run(req.cron, utcnow())
        if req.retry_attempts is not None:
            changes["retry_attempts"] = req.retry_attempts
        if req.webhook_url is not None:
            changes["webhook_url"] = req.webhook_url
        if req.gate_quality_min is not None:
            changes["gate_quality_min"] = req.gate_quality_min
        if req.enabled:
            changes["status"] = "idle"
        scheduler.store.update_job(job_id, **changes)
        job = scheduler.store.get_job(job_id)
        assert job is not None
        return job.view()

    @app.delete("/jobs/{job_id}", tags=["jobs"], status_code=204)
    def delete_job(job_id: str) -> Response:
        if not scheduler.store.delete_job(job_id):
            raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
        return Response(status_code=204)

    @app.post("/rpc/execute", tags=["rpc"])
    def rpc_execute(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """远端执行端点（V14，ADR-091）：接收 JobCommand，本地执行扫描并回传 JobResult。

        安全：仅配置了 worker_token 时启用（503 未启用）；token 以
        `X-Datasentry-Token` 头常量时间比对（401 拒绝）。
        """
        import secrets

        if worker_token is None:
            raise HTTPException(
                status_code=503, detail="worker endpoint disabled: set DATASENTRY_WORKER_TOKEN"
            )
        supplied = request.headers.get("X-Datasentry-Token")
        if not supplied or not secrets.compare_digest(supplied, worker_token):
            raise HTTPException(status_code=401, detail="invalid worker token")
        try:
            command = JobCommand.model_validate(body)
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid job command: {safe_detail(exc)}"
            ) from exc
        run_token = command.run_token or ""
        with inflight_lock:
            inflight_cancelled[run_token] = False
        try:
            # V21（Step 113，ADR-113）：worker 用自身 project 执行——扫描历史
            # 落 worker 库（物理隔离语义），命令中的调度端 project 仅作契约
            # 字段保留；command.path 需在 worker 侧可达（单机绝对路径/远端
            # 同步均由部署者保证）。
            worker_command = command.model_copy(update={"project": str(project)})
            result = LocalScanExecutor().execute(worker_command)
        except Exception as exc:
            with inflight_lock:
                inflight_cancelled.pop(run_token, None)
            raise HTTPException(
                status_code=500, detail=f"scan failed: {type(exc).__name__}"
            ) from exc
        with inflight_lock:
            cancelled = inflight_cancelled.pop(run_token, False)
        if cancelled:
            # V22（ADR-115）：已被调度端取消——结果作废回执（扫描已落 worker
            # 库无法回收，由调度端丢弃结果；文档化边界）。
            result = result.model_copy(update={"cancelled": True})
        return result.model_dump()

    @app.post("/rpc/cancel", tags=["rpc"])
    def rpc_cancel(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """远端取消标记端点（V22，Step 115，ADR-115）。

        安全语义与 /rpc/execute 一致（503 未启用 / 401 错 token）。
        已知 run_token 打 cancelled 标记（200）；未知 token 404 无
        操作（调度端已标 cancelled，回执仅信息面）。
        """
        import secrets

        if worker_token is None:
            raise HTTPException(
                status_code=503, detail="worker endpoint disabled: set DATASENTRY_WORKER_TOKEN"
            )
        supplied = request.headers.get("X-Datasentry-Token")
        if not supplied or not secrets.compare_digest(supplied, worker_token):
            raise HTTPException(status_code=401, detail="invalid worker token")
        run_token = str(body.get("run_token", ""))
        if not run_token:
            raise HTTPException(status_code=422, detail="run_token required")
        with inflight_lock:
            if run_token not in inflight_cancelled:
                raise HTTPException(status_code=404, detail="unknown run token")
            inflight_cancelled[run_token] = True
        return {"cancelled": True}

    @app.get("/rpc/health", tags=["rpc"])
    def rpc_health() -> dict[str, Any]:
        """远端 worker 健康端点（V20，Step 109，ADR-109）。

        公开（信息面）：仅返回服务标识/版本/worker 启用标志，不涉数据、
        不需要 token——与数据面 `/rpc/execute`（token 鉴权）分离，供
        `RemoteScanExecutor.health()` / preflight 探测用。
        """
        return {
            "service": "datasentry-worker",
            "version": __version__,
            "worker": worker_token is not None,
        }

    @app.get("/rpc/reports/{scan_run_id}", tags=["rpc"])
    def rpc_report(scan_run_id: str, request: Request) -> Response:
        """远端报告下载端点（V20，Step 110，ADR-110）。

        数据面：与 `/rpc/execute` 同级 token 鉴权（401 拒绝、未配置
        worker_token 时 503）；按 `reports/{scan_run_id}.html` 约定
        （与 LocalScanExecutor 报告导出一致）返回 HTML，不存在 404。
        """
        import secrets

        if worker_token is None:
            raise HTTPException(
                status_code=503, detail="worker endpoint disabled: set DATASENTRY_WORKER_TOKEN"
            )
        supplied = request.headers.get("X-Datasentry-Token")
        if not supplied or not secrets.compare_digest(supplied, worker_token):
            raise HTTPException(status_code=401, detail="invalid worker token")
        report_path = client.reports_dir / f"{scan_run_id}.html"
        if not report_path.is_file():
            raise HTTPException(status_code=404, detail=f"report not found: {scan_run_id}")
        return Response(content=report_path.read_text(encoding="utf-8"), media_type="text/html")

    # ---- PII 加密 vault 管理面（V17，Step 99，ADR-099） -------------------

    @app.get("/pii/sessions", tags=["pii"])
    def pii_list_sessions(
        token: Annotated[str | None, Header(alias="X-Datasentry-Token")] = None,
    ) -> dict[str, Any]:
        """加密会话列表（不含密文；含 key_source 提示，与 CLI llm restore 对齐）。"""
        _require_api_token(token)
        vault = _pii_vault(client)
        return {
            "sessions": [
                {
                    "session_id": s["session_id"],
                    "key_version": s["key_version"],
                    "created_at": s["created_at"].isoformat(),
                }
                for s in client.list_pii_mappings()
            ],
            "key_source": vault.key_source,
        }

    @app.get("/pii/sessions/{session_id}", tags=["pii"])
    def pii_session_summary(
        session_id: str,
        token: Annotated[str | None, Header(alias="X-Datasentry-Token")] = None,
    ) -> dict[str, Any]:
        """会话映射摘要（kind → count + 掩码→原文预览）；缺 key 503、不存在 404。"""
        _require_api_token(token)
        vault = _pii_vault(client)
        try:
            mapping = vault.load_mapping(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=safe_detail(exc)) from exc
        except VaultKeyMissingError as exc:
            raise HTTPException(status_code=503, detail=safe_detail(exc)) from exc
        return {
            "session_id": session_id,
            "key_source": vault.key_source,
            "mapping": format_mapping_summary(mapping),
        }

    @app.post("/pii/sessions/{session_id}/restore", tags=["pii"])
    def pii_restore(
        session_id: str,
        req: PiiRestoreRequest,
        token: Annotated[str | None, Header(alias="X-Datasentry-Token")] = None,
    ) -> dict[str, Any]:
        """还原文本明文（显式授权语义：调用即授权查看明文，与 CLI restore 同源）。"""
        _require_api_token(token)
        try:
            vault = _pii_vault(client)
        except HTTPException:
            # 503 for an unconfigured key is raised while binding the vault, before the restore
            # is even attempted — the audit record has to cover that refusal too.
            _audit_restore(session_id=session_id, via="rest", ok=False, reason="key-missing")
            raise
        try:
            restored = vault.restore_text(req.text, session_id)
        except KeyError as exc:
            _audit_restore(session_id=session_id, via="rest", ok=False, reason="not-found")
            raise HTTPException(status_code=404, detail=safe_detail(exc)) from exc
        except VaultKeyMissingError as exc:
            _audit_restore(session_id=session_id, via="rest", ok=False, reason="key-missing")
            raise HTTPException(status_code=503, detail=safe_detail(exc)) from exc
        _audit_restore(session_id=session_id, via="rest", ok=True)
        return {
            "session_id": session_id,
            "key_source": vault.key_source,
            "restored": restored,
        }

    @app.delete("/pii/sessions/{session_id}", tags=["pii"], status_code=204)
    def pii_delete_session(
        session_id: str,
        token: Annotated[str | None, Header(alias="X-Datasentry-Token")] = None,
    ) -> Response:
        """删除加密会话（密文行，无需密钥）；不存在 404。"""
        _require_api_token(token)
        if not client.delete_pii_mapping(session_id):
            raise HTTPException(
                status_code=404, detail=f"pii mapping session not found: {session_id}"
            )
        return Response(status_code=204)

    @app.post("/pii/sessions/purge", tags=["pii"])
    def pii_purge_sessions(
        req: PiiPurgeRequest,
        token: Annotated[str | None, Header(alias="X-Datasentry-Token")] = None,
    ) -> dict[str, Any]:
        """删除创建时间早于 N 天的加密会话（V18，Step 103，ADR-103）。

        无需密钥（与 DELETE 同语义：只删密文行不解密）。
        """
        _require_api_token(token)
        vault = client.pii_vault()
        return {"purged": vault.purge_sessions(req.older_than_days)}

    @app.post("/pii/rotate-key", tags=["pii"])
    def pii_rotate_key(
        req: PiiRotateRequest | None = None,
        token: Annotated[str | None, Header(alias="X-Datasentry-Token")] = None,
    ) -> dict[str, Any]:
        """轮换加密密钥：全部映射以新密钥重加密 + 写入本地 key 文件。

        可选请求体 {"new_key": "..."} 指定新密钥材料（与 CLI
        rotate-key --new-key 对齐），缺省自动生成（v0.19.0 行为
        不变，向后兼容）。返回 key_version（轮换后恒 "file"——
        密钥已落盘，与落库行的 key_version 一致）；不返回新密钥
        材料本身（远程面不泄露）。
        """
        _require_api_token(token)
        vault = _pii_vault(client)
        try:
            result = vault.rotate_key(new_key=req.new_key if req else None)
        except VaultKeyMissingError as exc:
            raise HTTPException(status_code=503, detail=safe_detail(exc)) from exc
        return {
            "key_version": "file",
            "rotated": result["rotated"],
            "key_file": result["key_file"],
        }

    return app


_ENDPOINTS = frozenset(
    {
        "GET /health",
        "GET /scans",
        "POST /scans",
        "GET /scans/{run_id}",
        "GET /scans/{run_id}/issues",
        "GET /scans/{run_id}/report",
        "GET /scans/{run_id}/report.html",
        "GET /scans/{run_id}/score",
        "GET /issues",
        "POST /scans/{run_id}/repairs/propose",
        "POST /scans/{run_id}/repairs/preview",
        "POST /scans/{run_id}/repairs/apply",
        "POST /repairs/{run_id}/rollback",
        "GET /repairs",
        "POST /jobs",
        "POST /rpc/execute",
        "GET /jobs",
        "GET /jobs/{job_id}",
        "POST /jobs/{job_id}/trigger",
        "PATCH /jobs/{job_id}",
        "DELETE /jobs/{job_id}",
        "GET /rpc/health",
        "GET /rpc/reports/{scan_run_id}",
        "POST /rpc/cancel",
        "GET /pii/sessions",
        "GET /pii/sessions/{session_id}",
        "POST /pii/sessions/{session_id}/restore",
        "DELETE /pii/sessions/{session_id}",
        "POST /pii/sessions/purge",
        "POST /pii/rotate-key",
    }
)


def main(argv: list[str] | None = None) -> None:
    """启动 API 服务（容器/开发入口，默认回环 127.0.0.1:8000）。

    D5-01：旧默认 0.0.0.0 把无鉴权 REST 面暴露到全网卡。现默认仅回环；
    容器/局域网场景显式 --host 0.0.0.0 或 DATASENTRY_HOST 环境变量覆盖。
    非回环绑定不再只是 warning（行 r2+r3）：要么配置 `DATASENTRY_API_TOKEN`
    （写端点随即要求 `X-Datasentry-Token`，见 `ADR-120`），要么显式
    `DATASENTRY_ALLOW_INSECURE_BIND=1` 认下这个暴露面，否则拒绝启动。
    回环判定用 `ipaddress`（整个 127/8、`::1`、`::ffff:127.0.0.1`），
    不再是三个字符串的集合。
    """
    import argparse
    import os

    import uvicorn

    parser = argparse.ArgumentParser(description="DataSentry REST API + Web UI")
    parser.add_argument(
        "--host",
        default=os.environ.get("DATASENTRY_HOST", "127.0.0.1"),
        help="bind host (default 127.0.0.1; containers use 0.0.0.0 via "
        "DATASENTRY_HOST; default changed by D5-01)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("DATASENTRY_PORT", "8000")),
        help="bind port (default 8000)",
    )
    args = parser.parse_args(argv)
    host = args.host
    opted_in = os.environ.get(INSECURE_BIND_OPT_IN, "") == "1"
    try:
        warning = resolve_bind(
            host, token=os.environ.get("DATASENTRY_API_TOKEN"), opted_in=opted_in
        )
    except InsecureBindRefused as exc:
        raise SystemExit(str(exc)) from exc
    if warning:
        logger.warning(warning)
    uvicorn.run(create_app(), host=host, port=args.port)

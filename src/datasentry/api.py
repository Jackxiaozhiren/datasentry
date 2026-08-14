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

import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from datasentry import __version__, ui
from datasentry import client as sdk
from datasentry.pii_vault import PIIVault, VaultKeyMissingError, format_mapping_summary
from datasentry.scheduler.core import LocalScanExecutor, Scheduler, SchedulerWorker
from datasentry.scheduler.models import (
    JobCommand,
    JobCreate,
    JobUpdate,
    ScheduledJob,
    iso,
    utcnow,
)
from datasentry_core.models.issue import Issue
from datasentry_core.models.repair import RepairPreview, RepairProposal, RepairRun
from datasentry_core.models.scan import DetectorRun, SamplingConfig, ScanConfig, ScanRun
from datasentry_core.reporting.i18n import t as _t

logger = logging.getLogger(__name__)


class ScanRequest(BaseModel):
    """POST /scans 请求体：源文件路径（workspace 相对或绝对）+ 扫描配置。"""

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
    workspace: str


class ErrorBody(BaseModel):
    ok: bool = False
    detail: str


class PiiRestoreRequest(BaseModel):
    """POST /pii/sessions/{session_id}/restore 请求体：待还原的占位符文本。"""

    text: str


def _config_from(req: ScanRequest) -> ScanConfig:
    return ScanConfig(
        detectors=req.detectors,
        seed=req.seed,
        scan_tags=req.tags,
        sampling=req.sampling or SamplingConfig(method="none"),
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
    return HTTPException(status_code=_error(exc), detail=str(exc))


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


def _pii_vault(client: sdk.DataSentry) -> PIIVault:
    """绑定工作区元数据库的 PII vault（V17，Step 99，ADR-099）。

    key 未配置（env/文件均无）时抛 503——与 /rpc/execute 的
    disabled 语义一致（CLI 侧等价 EXIT_CONFIG）。删除端点不经过
    本函数：删除密文行无需密钥（与 CLI llm restore --delete 一致）。
    """
    vault = PIIVault(client._store)
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
    client = sdk.DataSentry(project=project)
    scheduler = _build_scheduler(client)
    worker = SchedulerWorker(scheduler)

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        scheduler.recover()
        worker.start()
        try:
            yield
        finally:
            worker.stop()

    app = FastAPI(title="DataSentry API", version=__version__, lifespan=_lifespan)
    app.state.client = client
    app.state.scheduler = scheduler

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        return HealthResponse(
            ok=True,
            service="datasentry",
            version=__version__,
            workspace=str(client.workspace),
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
        try:
            scan, runs, issues = client.scan_file(
                req.path,
                dataset_id=req.dataset_id,
                table_name=req.table_name,
                config=_config_from(req),
            )
        except Exception as exc:
            raise _handle(exc) from exc
        return ScanResponse(run=scan, detector_runs=runs, issues=issues)

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
        try:
            return client.repair_propose(req.issue_id, req.source_path)
        except Exception as exc:
            raise _handle(exc) from exc

    @app.post(
        "/scans/{run_id}/repairs/preview",
        tags=["repairs"],
    )
    def repair_preview(run_id: str, req: PreviewRequest) -> dict[str, object] | None:
        try:
            result = client.repair_preview(req.issue_id, req.source_path)
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
    def repair_apply(run_id: str, req: ApplyRequest) -> RepairRun:
        try:
            return client.repair_apply(req.issue_id, req.source_path)
        except Exception as exc:
            raise _handle(exc) from exc

    @app.post(
        "/repairs/{run_id}/rollback",
        response_model=RepairRun,
        tags=["repairs"],
    )
    def repair_rollback(run_id: str) -> RepairRun:
        try:
            return client.repair_rollback(run_id)
        except Exception as exc:
            raise _handle(exc) from exc

    @app.get("/repairs", response_model=list[RepairRun], tags=["repairs"])
    def list_repair_runs() -> list[RepairRun]:
        return client.list_repair_runs()

    # ---- Web UI（Step 24：服务端渲染核心页） ----------------------------

    @app.get("/ui", response_class=HTMLResponse, tags=["ui"])
    @app.get("/ui/", response_class=HTMLResponse, tags=["ui"])
    def ui_home(lang: str = Query(default="en")) -> HTMLResponse:
        return HTMLResponse(ui.render_home(client.list_scan_runs(), lang=lang))

    @app.post("/ui/scans", response_class=HTMLResponse, tags=["ui"])
    def ui_create_scan(path: str = Form()) -> Response:
        try:
            scan, _runs, _issues = client.scan_file(path)
        except Exception as exc:
            return HTMLResponse(
                ui.render_error(_t("en", "ui.scan_failed"), str(exc)), status_code=404
            )
        return RedirectResponse(url=f"/ui/scans/{scan.id}", status_code=303)

    @app.get("/ui/scans", response_class=HTMLResponse, tags=["ui"])
    def ui_scans_list(lang: str = Query(default="en")) -> HTMLResponse:
        return HTMLResponse(ui.render_home(client.list_scan_runs(), lang=lang))

    @app.get("/ui/trends", response_class=HTMLResponse, tags=["ui"])
    def ui_trends(lang: str = Query(default="en")) -> HTMLResponse:
        from datasentry.trends import build_trends

        return HTMLResponse(ui.render_trends(build_trends(client.list_scan_runs()), lang=lang))

    @app.get("/ui/pii", response_class=HTMLResponse, tags=["ui"])
    def ui_pii(lang: str = Query(default="en")) -> HTMLResponse:
        """PII 加密会话管理页（V17，Step 101，ADR-101）：列表 + 还原表单。"""
        from datasentry.pii_vault import PIIVault

        vault = PIIVault(client._store)
        return HTMLResponse(
            ui.render_pii(
                client._store.list_pii_mappings(),
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
        from datasentry.pii_vault import PIIVault, VaultKeyMissingError

        vault = PIIVault(client._store)
        restored: str | None = None
        error: str | None = None
        if not vault.key_configured:
            error = _t(lang, "ui.pii_key_missing")
        else:
            try:
                restored = vault.restore_text(text, session_id)
            except KeyError as exc:
                error = str(exc)
            except VaultKeyMissingError as exc:
                error = str(exc)
        return HTMLResponse(
            ui.render_pii(
                client._store.list_pii_mappings(),
                key_source=vault.key_source,
                key_configured=vault.key_configured,
                restored=restored,
                error=error,
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
            ui.render_scan_detail(scan, issues, severity_filter=severity, lang=lang)
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
        return HTMLResponse(ui.render_workbench(issue, run_id=run_id))

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
        issue = _get_issue(client, issue_id)
        if issue is None:
            return HTMLResponse(
                ui.render_error(_t("en", "ui.issue_not_found"), issue_id), status_code=404
            )
        error: str | None = None
        proposal: RepairProposal | None = None
        preview: RepairPreview | None = None
        run: RepairRun | None = None
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
            else:
                error = f"unknown action: {action}"
        except Exception as exc:
            error = str(exc)
        return HTMLResponse(
            ui.render_workbench(
                issue,
                run_id=run_id,
                source_path=source_path,
                proposal=proposal,
                preview=preview,
                run=run,
                error=error,
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
        except Exception as exc:
            return HTMLResponse(
                ui.render_error(_t("en", "ui.rollback_failed"), str(exc)), status_code=404
            )
        return RedirectResponse(url=f"/ui/scans/{run_id}", status_code=303)

    # ---- 计划任务（Step 51，V2-D 云侧调度） --------------------------------

    @app.post("/jobs", tags=["jobs"], status_code=201)
    def create_job(req: JobCreate) -> dict[str, Any]:
        from datasentry.scheduler.core import InvalidCronError, next_run, validate_cron

        try:
            validate_cron(req.cron)
        except InvalidCronError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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
        """发送样例通知负载到任务 webhook（V13，ADR-087 协作链路验证）。"""
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
        try:
            import time

            import httpx

            started = time.monotonic()
            with httpx.Client(timeout=5.0) as client:
                response = client.post(job.webhook_url, json=payload)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if response.status_code >= 400:
                return {
                    "job_id": job_id,
                    "url": job.webhook_url,
                    "status_code": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "notified": False,
                }
            return {
                "job_id": job_id,
                "url": job.webhook_url,
                "status_code": response.status_code,
                "elapsed_ms": elapsed_ms,
                "notified": True,
            }
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"webhook delivery failed: {exc}") from exc

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
                raise HTTPException(status_code=422, detail=str(exc)) from exc
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
            raise HTTPException(status_code=422, detail=f"invalid job command: {exc}") from exc
        try:
            result = LocalScanExecutor().execute(command)
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"scan failed: {type(exc).__name__}"
            ) from exc
        return result.model_dump()

    # ---- PII 加密 vault 管理面（V17，Step 99，ADR-099） -------------------

    @app.get("/pii/sessions", tags=["pii"])
    def pii_list_sessions() -> dict[str, Any]:
        """加密会话列表（不含密文；含 key_source 提示，与 CLI llm restore 对齐）。"""
        vault = _pii_vault(client)
        return {
            "sessions": [
                {
                    "session_id": s["session_id"],
                    "key_version": s["key_version"],
                    "created_at": s["created_at"].isoformat(),
                }
                for s in client._store.list_pii_mappings()
            ],
            "key_source": vault.key_source,
        }

    @app.get("/pii/sessions/{session_id}", tags=["pii"])
    def pii_session_summary(session_id: str) -> dict[str, Any]:
        """会话映射摘要（kind → count + 掩码→原文预览）；缺 key 503、不存在 404。"""
        vault = _pii_vault(client)
        try:
            mapping = vault.load_mapping(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except VaultKeyMissingError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "session_id": session_id,
            "key_source": vault.key_source,
            "mapping": format_mapping_summary(mapping),
        }

    @app.post("/pii/sessions/{session_id}/restore", tags=["pii"])
    def pii_restore(session_id: str, req: PiiRestoreRequest) -> dict[str, Any]:
        """还原文本明文（显式授权语义：调用即授权查看明文，与 CLI restore 同源）。"""
        vault = _pii_vault(client)
        try:
            restored = vault.restore_text(req.text, session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except VaultKeyMissingError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "session_id": session_id,
            "key_source": vault.key_source,
            "restored": restored,
        }

    @app.delete("/pii/sessions/{session_id}", tags=["pii"], status_code=204)
    def pii_delete_session(session_id: str) -> Response:
        """删除加密会话（密文行，无需密钥）；不存在 404。"""
        if not client._store.delete_pii_mapping(session_id):
            raise HTTPException(
                status_code=404, detail=f"pii mapping session not found: {session_id}"
            )
        return Response(status_code=204)

    @app.post("/pii/rotate-key", tags=["pii"])
    def pii_rotate_key() -> dict[str, Any]:
        """轮换加密密钥：全部映射以新密钥重加密 + 写入本地 key 文件。

        返回 key_version（轮换后恒 "file"——密钥已落盘，与落库行的
        key_version 一致）；不返回新密钥材料本身（远程面不泄露）。
        """
        vault = _pii_vault(client)
        try:
            result = vault.rotate_key()
        except VaultKeyMissingError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
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
        "GET /pii/sessions",
        "GET /pii/sessions/{session_id}",
        "POST /pii/sessions/{session_id}/restore",
        "DELETE /pii/sessions/{session_id}",
        "POST /pii/rotate-key",
    }
)


def main() -> None:
    """启动 API 服务（容器/开发入口，默认 0.0.0.0:8000）。"""
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=8000)

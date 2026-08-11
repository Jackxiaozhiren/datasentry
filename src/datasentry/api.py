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

import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from datasentry import __version__, ui
from datasentry import client as sdk
from datasentry.scheduler.core import LocalScanExecutor, Scheduler, SchedulerWorker
from datasentry.scheduler.models import (
    JobCommand,
    JobCreate,
    JobUpdate,
    ScheduledJob,
    utcnow,
)
from datasentry_core.models.issue import Issue
from datasentry_core.models.repair import RepairPreview, RepairProposal, RepairRun
from datasentry_core.models.scan import DetectorRun, ScanConfig, ScanRun


class ScanRequest(BaseModel):
    """POST /scans 请求体：源文件路径（workspace 相对或绝对）+ 扫描配置。"""

    path: str
    dataset_id: str | None = None
    table_name: str | None = None
    detectors: list[str] | None = None
    seed: int = 42
    tags: dict[str, str] = Field(default_factory=dict)


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


def _config_from(req: ScanRequest) -> ScanConfig:
    return ScanConfig(
        detectors=req.detectors,
        seed=req.seed,
        scan_tags=req.tags,
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


def _build_scheduler(client: sdk.DataSentry) -> Scheduler:
    """绑定工作区元数据库的调度器（SQLite 持久化任务队列，ADR-051）。"""
    from datasentry.scheduler.store import SchedulerStore
    from datasentry_core.storage.paths import project_db_path

    db_path = project_db_path(client.workspace)
    return Scheduler(store=SchedulerStore(db_path), executor=LocalScanExecutor())


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
    )


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------


def create_app(project: str | Path | None = None) -> FastAPI:
    """创建绑定给定工作区的应用（默认当前目录或 DATASENTRY_PROJECT）。"""
    if project is None:
        project = os.environ.get("DATASENTRY_PROJECT")
    app = FastAPI(title="DataSentry API", version=__version__)
    client = sdk.DataSentry(project=project)
    app.state.client = client

    scheduler = _build_scheduler(client)
    worker = SchedulerWorker(scheduler)

    @app.on_event("startup")
    def _startup() -> None:
        scheduler.recover()
        worker.start()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        worker.stop()

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
    def ui_report_html(run_id: str, request: Request) -> HTMLResponse:
        """交互式 HTML 报告（Step 49，V2-B）：server 模式注入工作台联动与趋势数据。"""
        from datasentry.trends import build_trends
        from datasentry_core.reporting.html import render_html

        try:
            report = client.export_report(run_id)
        except Exception as exc:
            raise _handle(exc) from exc
        trends = [t.to_report_dict() for t in build_trends(client.list_scan_runs())]
        base = str(request.base_url).rstrip("/")
        return HTMLResponse(render_html(report, trends=trends or None, server_base_url=base))

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
    def ui_home() -> HTMLResponse:
        return HTMLResponse(ui.render_home(client.list_scan_runs()))

    @app.post("/ui/scans", response_class=HTMLResponse, tags=["ui"])
    def ui_create_scan(path: str = Form()) -> Response:
        try:
            scan, _runs, _issues = client.scan_file(path)
        except Exception as exc:
            return HTMLResponse(ui.render_error("Scan failed", str(exc)), status_code=404)
        return RedirectResponse(url=f"/ui/scans/{scan.id}", status_code=303)

    @app.get("/ui/scans", response_class=HTMLResponse, tags=["ui"])
    def ui_scans_list() -> HTMLResponse:
        return HTMLResponse(ui.render_home(client.list_scan_runs()))

    @app.get("/ui/trends", response_class=HTMLResponse, tags=["ui"])
    def ui_trends() -> HTMLResponse:
        from datasentry.trends import build_trends

        return HTMLResponse(ui.render_trends(build_trends(client.list_scan_runs())))

    @app.get("/ui/scans/{run_id}", response_class=HTMLResponse, tags=["ui"])
    def ui_scan_detail(run_id: str, severity: str | None = Query(default=None)) -> HTMLResponse:
        scan = client.get_scan(run_id)
        if scan is None:
            return HTMLResponse(
                ui.render_error("Scan not found", f"scan run: {run_id}"), status_code=404
            )
        issues = client.list_issues(scan_run_id=run_id, severity_at_least=severity)
        return HTMLResponse(ui.render_scan_detail(scan, issues, severity_filter=severity))

    @app.get(
        "/ui/scans/{run_id}/issues/{issue_id}",
        response_class=HTMLResponse,
        tags=["ui"],
    )
    def ui_workbench(run_id: str, issue_id: str) -> HTMLResponse:
        issue = _get_issue(client, issue_id)
        if issue is None:
            return HTMLResponse(ui.render_error("Issue not found", issue_id), status_code=404)
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
            return HTMLResponse(ui.render_error("Issue not found", issue_id), status_code=404)
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
            return HTMLResponse(ui.render_error("Rollback failed", str(exc)), status_code=404)
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
        "GET /jobs",
        "GET /jobs/{job_id}",
        "POST /jobs/{job_id}/trigger",
        "PATCH /jobs/{job_id}",
        "DELETE /jobs/{job_id}",
    }
)


def main() -> None:
    """启动 API 服务（容器/开发入口，默认 0.0.0.0:8000）。"""
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=8000)

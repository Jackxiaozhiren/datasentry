"""MCP（Model Context Protocol）stdio 服务器（Step 43）。

零依赖自实现 JSON-RPC 2.0 over stdio（newline-delimited JSON），
遵循 MCP 2024-11-05 规范的核心子集：initialize / notifications/
initialized / tools/list / tools/call / ping。工具复用 DataSentry
SDK（与 CLI/REST 同源）。

工具清单（MCP 面，供 LLM 代理调用）：
    scan_file            扫描数据文件 → 摘要
    list_issues          查询 Issue（severity/scan 过滤）
    quality_score        最近质量总分（六维）
    drift_compare        两历史扫描漂移比较
    drift_latest         数据集最近两次扫描漂移
    detectors_list       检测器注册表
    contract_validate    契约文件校验
    jobs_list            列出调度任务（Step 51/52）
    job_create           注册调度任务（cron + 可选质量门禁）
    job_trigger          立即触发一次调度任务

用法：`datasentry mcp [--project DIR]`；由 MCP 客户端（如 Claude
Code）以 stdio 方式启动。
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from datasentry import __version__
from datasentry.client import DataSentry

_PROTOCOL_VERSION = "2024-11-05"


def _json_safe[T](value: T) -> T:
    if isinstance(value, (datetime, date)):
        return cast(T, value.isoformat())
    if isinstance(value, Path):
        return cast(T, str(value))
    if isinstance(value, dict):
        return cast(T, {str(k): _json_safe(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return cast(T, [_json_safe(v) for v in value])
    if isinstance(value, set):
        return cast(T, [_json_safe(v) for v in sorted(value, key=str)])
    return value


class McpServer:
    """MCP stdio 服务器：单工作区门面（与 REST create_app 同构）。"""

    def __init__(self, project: str | Path | None = None) -> None:
        self._client = DataSentry(project=project)
        self._tools: dict[str, dict[str, Any]] = {}
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._register_tools()

    # ---- 工具注册 ------------------------------------------------------

    def _tool(
        self,
        name: str,
        description: str,
        properties: dict[str, dict[str, Any]],
        required: list[str],
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._tools[name] = {
                "name": name,
                "description": description,
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            }
            self._handlers[name] = fn
            return fn

        return decorator

    def _register_tools(self) -> None:
        client = self._client

        @self._tool(
            "scan_file",
            "Scan a data file (CSV/Parquet/JSONL/XLSX/DuckDB), a PostgreSQL table "
            "(postgresql://DSN, Step 55), a MySQL table (mysql://DSN, Step 56) or "
            "a cloud storage file (s3:// gs:// az:// CSV/Parquet/JSONL, Step 57) "
            "and persist the quality report. Returns scan id, status, row count, "
            "quality score and issue counts.",
            {
                "path": {
                    "type": "string",
                    "description": "Path to the data file, a postgresql:// / mysql:// DSN "
                    "(table_name required), or an s3:// gs:// az:// cloud file URI",
                },
                "dataset_id": {"type": "string"},
                "table_name": {"type": "string"},
                "seed": {"type": "integer"},
            },
            ["path"],
        )
        def scan_file(
            path: str,
            dataset_id: str | None = None,
            table_name: str | None = None,
            seed: int = 42,
        ) -> dict[str, Any]:
            from datasentry_core.models.scan import ScanConfig

            scan, _, issues = client.scan_file(
                path,
                dataset_id=dataset_id,
                table_name=table_name,
                config=ScanConfig(seed=seed),
            )
            return _json_safe(
                {
                    "scan_run_id": scan.id,
                    "dataset_id": scan.dataset_id,
                    "status": scan.status,
                    "row_count": scan.fingerprint.row_count,
                    "quality_score": (scan.quality_score.overall if scan.quality_score else None),
                    "total_issues": len(issues),
                    "issues_by_severity": {k.value: v for k, v in scan.issues_count.items()},
                }
            )

        @self._tool(
            "list_issues",
            "List quality issues across scans, optionally filtered by "
            "minimum severity (info/low/medium/high/critical) or a scan id.",
            {
                "severity_at_least": {"type": "string"},
                "scan_run_id": {"type": "string"},
            },
            [],
        )
        def list_issues(
            severity_at_least: str | None = None,
            scan_run_id: str | None = None,
        ) -> list[dict[str, Any]]:
            issues = client.list_issues(
                severity_at_least=severity_at_least, scan_run_id=scan_run_id
            )
            return _json_safe([issue.model_dump() for issue in issues])

        @self._tool(
            "quality_score",
            "Six-dimension quality score of a scan run (use list_issues or "
            "scan_file to obtain scan_run_id). Dimensions below 60 indicate risk.",
            {
                "scan_run_id": {"type": "string"},
            },
            ["scan_run_id"],
        )
        def quality_score(scan_run_id: str) -> dict[str, Any]:
            score = client.quality_score(scan_run_id)
            data: dict[str, Any] | None = score.model_dump() if score else None
            return cast(dict[str, Any], _json_safe(data))

        @self._tool(
            "drift_compare",
            "Compare two historical scans of a dataset: schema, row-count, "
            "score and issue-distribution drift.",
            {
                "reference_run_id": {"type": "string"},
                "current_run_id": {"type": "string"},
            },
            ["reference_run_id", "current_run_id"],
        )
        def drift_compare(reference_run_id: str, current_run_id: str) -> dict[str, Any]:
            return _json_safe(client.drift_compare(reference_run_id, current_run_id).model_dump())

        @self._tool(
            "drift_latest",
            "Drift between the two most recent scans of a dataset. Fails "
            "if fewer than two completed scans exist.",
            {
                "dataset_id": {"type": "string"},
            },
            ["dataset_id"],
        )
        def drift_latest(dataset_id: str) -> dict[str, Any]:
            return _json_safe(client.drift_latest(dataset_id).model_dump())

        @self._tool(
            "detectors_list",
            "List all registered quality detectors with id, dimension, "
            "capabilities and default thresholds.",
            {},
            [],
        )
        def detectors_list() -> list[dict[str, Any]]:
            from datasentry_core.detectors import DetectorRegistry
            from datasentry_core.detectors.initial import register_default_detectors

            registry = DetectorRegistry()
            register_default_detectors(registry)
            return _json_safe([d.metadata().model_dump() for d in registry.list()])

        @self._tool(
            "contract_validate",
            "Validate a data contract YAML file and return the structured "
            "contract or validation errors.",
            {"path": {"type": "string"}},
            ["path"],
        )
        def contract_validate(path: str) -> dict[str, Any]:
            import yaml

            from datasentry_core.models.contract import Contract

            try:
                raw = yaml.safe_load(Path(path).expanduser().read_text(encoding="utf-8"))
                contract = Contract.model_validate(raw)
            except Exception as exc:
                return {"valid": False, "error": str(exc)}
            return _json_safe({"valid": True, "contract": contract.model_dump()})

        @self._tool(
            "jobs_list",
            "List scheduled scan jobs (Step 51/52): cron, enabled, status, "
            "next run time and last result. Optionally filter by status "
            "(idle/running/dead).",
            {"status": {"type": "string"}},
            [],
        )
        def jobs_list(status: str | None = None) -> list[dict[str, Any]]:
            from datasentry.scheduler.store import SchedulerStore
            from datasentry_core.storage.paths import project_db_path

            jobs = SchedulerStore(project_db_path(self._client.workspace)).list_jobs()
            return _json_safe(
                [job.view() for job in jobs if status is None or job.status.value == status]
            )

        @self._tool(
            "job_create",
            "Register a scheduled scan job (Step 51): cron expression, data "
            "file path, optional quality gate threshold (gate_quality_min, "
            "0-100) and optional webhook URL. Returns the created job view.",
            {
                "name": {"type": "string"},
                "path": {"type": "string"},
                "cron": {"type": "string", "description": "5-field cron, e.g. '0 9 * * *'"},
                "dataset_id": {"type": "string"},
                "table_name": {"type": "string"},
                "retry_attempts": {"type": "integer"},
                "webhook_url": {"type": "string"},
                "gate_quality_min": {"type": "number"},
            },
            ["name", "path", "cron"],
        )
        def job_create(
            name: str,
            path: str,
            cron: str,
            dataset_id: str | None = None,
            table_name: str | None = None,
            retry_attempts: int = 0,
            webhook_url: str | None = None,
            gate_quality_min: float | None = None,
        ) -> dict[str, Any]:
            from datasentry.scheduler.core import InvalidCronError, next_run, validate_cron
            from datasentry.scheduler.models import JobCommand, ScheduledJob, utcnow
            from datasentry.scheduler.store import SchedulerStore
            from datasentry_core.storage.paths import project_db_path

            try:
                validate_cron(cron)
            except InvalidCronError as exc:
                return {"ok": False, "error": str(exc)}
            path = str(Path(path).expanduser())
            now = utcnow()
            job = ScheduledJob(
                job_id=f"job_{uuid.uuid4().hex[:12]}",
                name=name,
                project=str(self._client.workspace),
                command=JobCommand(
                    project=str(self._client.workspace),
                    path=path,
                    dataset_id=dataset_id,
                    table_name=table_name,
                ),
                cron=cron,
                retry_attempts=retry_attempts,
                webhook_url=webhook_url,
                gate_quality_min=gate_quality_min,
                next_run_at=next_run(cron, now),
                created_at=now,
                updated_at=now,
            )
            SchedulerStore(project_db_path(self._client.workspace)).create_job(job)
            return {"ok": True, "job": job.view()}

        @self._tool(
            "job_trigger",
            "Immediately run a scheduled job once (Step 51). Returns the run "
            "id and outcome; mutual exclusion: 409-style error if already running.",
            {"job_id": {"type": "string"}},
            ["job_id"],
        )
        def job_trigger(job_id: str) -> dict[str, Any]:
            from datasentry.scheduler.core import LocalScanExecutor, Scheduler
            from datasentry.scheduler.store import SchedulerStore
            from datasentry_core.storage.paths import project_db_path

            store = SchedulerStore(project_db_path(self._client.workspace))
            job = store.get_job(job_id)
            if job is None:
                return {"ok": False, "error": f"job not found: {job_id}"}
            scheduler = Scheduler(store=store, executor=LocalScanExecutor())
            run_id = scheduler.trigger(job_id)
            if run_id is None:
                return {"ok": False, "error": f"job already running: {job_id}"}
            run = store.get_run(run_id)
            assert run is not None
            return {"ok": True, "run": run.view(), "summary": run.summary}

    # ---- JSON-RPC 分发 --------------------------------------------------

    def _rpc_error(self, code: int, message: str, data: Any = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "error": error}

    def _handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "datasentry", "version": __version__},
                },
            }
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": message_id, "result": {}}
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {"tools": list(self._tools.values())},
            }
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            handler = self._handlers.get(cast(str, name))
            if handler is None:
                return self._rpc_error(-32602, f"unknown tool: {name}")
            try:
                result = handler(**arguments)
            except Exception as exc:
                return self._rpc_error(-32603, str(exc))
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                    "isError": False,
                },
            }
        if message_id is not None:
            return self._rpc_error(-32601, f"method not found: {method}")
        return None

    def serve_stdio(self) -> None:
        """阻塞读 stdin 逐行 JSON-RPC；stdout 每行一个响应。"""
        for line in sys.stdin:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                message = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            response = self._handle_message(message)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()

    def close(self) -> None:
        self._client.close()


def build_mcp_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "mcp",
        help="MCP stdio server (Step 43): JSON-RPC tools for LLM agents",
    )
    parser.add_argument("--project", default=None, help="workspace directory")
    return parser


def run_mcp(args: argparse.Namespace) -> int:
    server = McpServer(project=args.project)
    try:
        server.serve_stdio()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
    return 0

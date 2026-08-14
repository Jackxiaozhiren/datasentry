"""Step 51/53（V2-D 云侧调度 + 变更感知）核心：cron 语义 + 调度器 + 执行器 + worker
（ADR-051 / ADR-053）。

- `Scheduler.tick(now)`：抢占到期任务并执行；`max_workers>1` 时
  执行异步派发到线程池（并行执行，tick 不阻塞，V16/ADR-096）。
- 重试语义：失败且 attempt <= retry_attempts → 60s 后重试；超过 → 死信（dead）。
- 重启恢复：`recover_interrupted` 将 running 任务置回 idle，run 标记 interrupted。
- 并发互斥：SQLite BEGIN IMMEDIATE 条件更新，同一任务同一时刻仅一个执行者。
- webhook：执行结束（成功/失败/跳过）尽力通知（失败仅记录，不影响调度）；URL 为空即关闭。
- 变更感知（Step 53）：文件 SHA-256 与上次成功执行一致 → 记 skipped run（不建
  scan_run、不重判门禁、webhook 带 skipped:true）。
- 扩展点：`ScanExecutor` Protocol——未来可换云函数/SSH 远端执行。
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from croniter import CroniterBadCronError, croniter  # type: ignore[import-untyped]

from datasentry.scheduler.models import (
    GateResult,
    JobCommand,
    JobResult,
    JobStatus,
    ScheduledJob,
    iso,
    utcnow,
)
from datasentry.scheduler.store import RETRY_BACKOFF, SchedulerStore

logger = logging.getLogger(__name__)


class InvalidCronError(ValueError):
    """cron 表达式非法（5 字段校验失败）。"""


def evaluate_gate(job: ScheduledJob, result: JobResult) -> GateResult | None:
    """质量门禁判定（Step 52）：未配置返回 None（不启用）；否则按阈值判定。

    门禁是业务判定而非执行失败——任务照常 completed，仅结果/通知标记。
    """
    if job.gate_quality_min is None:
        return None
    passed = result.quality_score >= job.gate_quality_min
    return GateResult(
        configured=True,
        quality_min=job.gate_quality_min,
        quality_score=result.quality_score,
        passed=passed,
    )


def validate_cron(expr: str) -> str:
    """校验 cron 表达式，非法抛 InvalidCronError；返回规范化表达式。"""
    try:
        croniter(expr)
    except (CroniterBadCronError, ValueError, IndexError) as exc:
        raise InvalidCronError(f"invalid cron expression {expr!r}: {exc}") from exc
    return expr


def next_run(expr: str, after: datetime) -> datetime:
    """cron 表达式在 after 之后的下一次执行时间（无时区，UTC 语义）。"""
    return cast(datetime, croniter(expr, after).get_next(datetime))


def file_sha256(path: str) -> str:
    """数据文件内容 SHA-256（流式，大文件友好）；文件缺失抛异常（触发正常失败路径）。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_remote_db_path(path: str) -> bool:
    return path.startswith(("postgresql://", "postgres://", "mysql://"))


def _is_cloud_uri(path: str) -> bool:
    return path.startswith(("s3://", "gs://", "az://"))


def _db_source_type(path: str) -> str:
    """远程库 URL → DataSourceType（Step 55/56：PG 与 MySQL 源类型不同）。"""
    if path.startswith("mysql://"):
        return "mysql"
    return "postgresql"


def _is_remote_source(path: str) -> bool:
    """远程源判定（Step 58）：远程库（PG/MySQL）或云文件 URI 走两层指纹。"""
    return _is_remote_db_path(path) or _is_cloud_uri(path)


def _composite_hash(stats: str, content: str | None) -> str:
    """复合指纹（Step 58，ADR-058）：{"stats": ..., "content": ...} 定序 JSON。

    内容层未计算（统计层已判定变更）时 content=None——跳过判定永不成立，
    但统计层已足够证明变更，零内容读取。
    """
    return json.dumps({"stats": stats, "content": content}, separators=(",", ":"))


def _parse_composite_hash(raw: str | None) -> tuple[str, str | None] | None:
    """解析复合指纹；非复合（None/遗留单段 hash，Step 55/56/57 时代）返回 None。"""
    if raw is None or not raw.startswith("{"):
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    stats = obj.get("stats")
    if not isinstance(stats, str):
        return None
    content = obj.get("content")
    return stats, content if isinstance(content, str) else None


def _source_fingerprint(command: JobCommand, previous: str | None) -> str | None:
    """源指纹（Step 55/56/57/58）：文件源=文件 SHA-256（Step 53 原语义）；
    远程源（PG/MySQL/云文件）=两层复合指纹（Step 58，ADR-058）：
      第一层统计（schema_hash+row_count，零内容读取）：与上次复合指纹的
      统计层不一致 → 立即判定变更（返回 content=None，不读内容）；
      一致 → 第二层内容指纹（PG/MySQL 全表哈希 / 云文件 size+last_modified
      元数据组合）判定跳过。
    previous：最近一次成功执行的复合指纹；遗留单段 hash 视为「统计层未知」
      → 保守走内容层比对（等价旧语义），成功后落库复合指纹完成迁移。

    源不可达（文件缺失/库断连/缺表名/对象删除或不可读）返回 None——与
    Step 53「文件缺失不跳过」语义一致：交给执行器触发正常失败路径，
    绝不误跳过。
    """
    if isinstance(command.path, str) and _is_remote_source(command.path):
        handle = _open_remote_handle(command)
        if handle is None:
            return None
        try:
            try:
                stats = handle.stats_fingerprint()
            except Exception:
                return None
            prev = _parse_composite_hash(previous)
            if prev is not None and prev[0] == stats:
                try:
                    content = handle.content_fingerprint()
                except Exception:
                    return None
                return _composite_hash(stats, content)
            return _composite_hash(stats, None)
        finally:
            handle.close()
    return file_sha256(command.path)


def _open_remote_handle(command: JobCommand) -> Any | None:
    """远程源（PG/MySQL/云文件）指纹句柄；不可开（断连/缺表名/缺后缀）返回 None。"""
    from datasentry_core.connectors import DataSourceSpec, DataSourceType, default_registry
    from datasentry_core.connectors.spec import EXT_TO_SOURCE_TYPE

    path = command.path
    assert isinstance(path, str)
    try:
        if _is_cloud_uri(path):
            suffix = path.rsplit(".", 1)[-1].lower()
            source_type = EXT_TO_SOURCE_TYPE.get(f".{suffix}")
            if source_type not in (
                DataSourceType.CSV,
                DataSourceType.PARQUET,
                DataSourceType.JSONL,
            ):
                return None
            return default_registry().open(
                DataSourceSpec(
                    source_type=source_type,
                    path=path,
                    options={"dataset_id": path.rsplit("/", 2)[1]},
                )
            )
        return default_registry().open(
            DataSourceSpec(
                source_type=DataSourceType(_db_source_type(path)),
                table_name=command.table_name,
                options={"dsn": path},
            )
        )
    except Exception:
        return None


@runtime_checkable
class ScanExecutor(Protocol):
    """扫描执行抽象：当前为本地执行，未来可替换为云函数/远端。"""

    def execute(self, command: JobCommand) -> JobResult:
        """执行一次扫描，成功抛异常表示失败（触发重试/死信）。"""
        ...


class LocalScanExecutor:
    """本地执行器：新建 DataSentry(project=job.project) 执行 scan_file。

    export_report（ADR-070）：扫描成功后导出 HTML 报告到 reports 目录，
    失败仅记录日志不影响调度（与 webhook 尽力而为一致）。
    """

    def __init__(self, client_factory: Callable[[str], Any] | None = None) -> None:
        self._client_factory = client_factory

    def execute(self, command: JobCommand) -> JobResult:
        if self._client_factory is not None:
            client = self._client_factory(command.project)
        else:
            from datasentry import DataSentry

            client = DataSentry(project=command.project)
        try:
            scan_run, _runs, issues = client.scan_file(
                command.path,
                dataset_id=command.dataset_id,
                table_name=command.table_name,
                config=command.config,
            )
            report_path, report_size = self._export_report(client, scan_run.id, command)
        finally:
            client.close()
        by_severity: dict[str, int] = {}
        for issue in issues:
            by_severity[issue.severity.value] = by_severity.get(issue.severity.value, 0) + 1
        score = scan_run.quality_score.overall if scan_run.quality_score else 0.0
        # Step 55/56：文件源沿用 file_sha256（full 档）；PG/MySQL 源无文件字节，
        # full 档指纹的 content_sample_hash 即内容指纹——两侧同一字段（TEXT）落库
        # Step 58：远程源落库复合指纹（统计层+内容层）——扫描成功即携带最新统计
        # 与内容，下一次 tick 两层比对；统计层变化时跳过判定零内容读取
        fingerprint = scan_run.fingerprint
        source_hash = fingerprint.file_sha256 or fingerprint.content_sample_hash
        if (
            source_hash is not None
            and isinstance(command.path, str)
            and _is_remote_source(command.path)
        ):
            stats = hashlib.sha256(
                f"{fingerprint.schema_hash}|{fingerprint.row_count}".encode()
            ).hexdigest()
            source_hash = _composite_hash(stats, fingerprint.content_sample_hash)
        return JobResult(
            scan_run_id=scan_run.id,
            total_issues=len(issues),
            quality_score=score,
            issues_by_severity=by_severity,
            file_hash=source_hash,
            report_path=report_path,
            report_size=report_size,
        )

    def _export_report(
        self, client: Any, scan_run_id: str, command: JobCommand
    ) -> tuple[str | None, int | None]:
        """扫描后导出 HTML 报告（仅 export_report 任务）；失败仅记录日志。"""
        if not command.export_report:
            return None, None
        try:
            from datasentry_core.reporting.html import render_html

            report = client.export_report(scan_run_id)
            content = render_html(report)
            out = client.reports_dir / f"{scan_run_id}.html"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")
            rel = os.path.relpath(str(out), command.project)
            return rel, len(content.encode("utf-8"))
        except Exception as exc:
            logger.warning("report export failed for %s: %s", scan_run_id, exc)
            return None, None


class WebhookNotifier:
    """结果通知：HTTP POST JSON；失败仅记录日志（尽力而为，不阻塞调度）。"""

    def __init__(self, client_factory: Callable[[], Any] | None = None) -> None:
        self._client_factory = client_factory

    def notify(self, url: str, payload: dict[str, object]) -> None:
        try:
            if self._client_factory is not None:
                client = self._client_factory()
            else:
                import httpx

                client = httpx.Client(timeout=5.0)
            try:
                response = client.post(url, json=payload)
                if response.status_code >= 400:
                    logger.warning("webhook %s -> HTTP %s", url, response.status_code)
            finally:
                client.close()
        except Exception as exc:
            logger.warning("webhook %s failed: %s", url, exc)


class Scheduler:
    """任务调度核心（可注入 store/executor/notifier，纯同步可测）。"""

    def __init__(
        self,
        store: SchedulerStore,
        executor: ScanExecutor,
        notifier: WebhookNotifier | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        max_workers: int = 1,
    ) -> None:
        self.store = store
        self._executor = executor
        self._notifier = notifier or WebhookNotifier()
        self._clock = clock or utcnow
        self._max_workers = max(1, max_workers)
        self._pool: concurrent.futures.ThreadPoolExecutor | None = (
            concurrent.futures.ThreadPoolExecutor(max_workers=self._max_workers)
            if self._max_workers > 1
            else None
        )

    def tick(self) -> list[str]:
        """一轮调度：抢占到期任务并派发；返回本轮 job_id 列表（测试/观测用）。

        max_workers=1 时同步执行（与 V15 及更早一致）；>1 时提交
        线程池立即返回（并行执行，V16/ADR-096）。
        """
        now = self._clock()
        claimed = self.store.claim_due_jobs(now)
        for job_id, run_id, _attempt in claimed:
            self._submit(job_id, run_id)
        return [job_id for job_id, _run_id, _attempt in claimed]

    def trigger(self, job_id: str) -> str | None:
        """手动触发执行；任务已在执行中返回 None（互斥）。

        max_workers=1 时同步完成（返回时 run 已终态）；>1 时提交
        线程池立即返回 run_id（异步推进，V16/ADR-096）。
        """
        run_id = self.store.claim_job(job_id, self._clock())
        if run_id is not None:
            self._submit(job_id, run_id)
        return run_id

    def shutdown(self, wait: bool = True) -> None:
        """优雅关闭：停止接收新任务；wait=True 等待 in-flight 完成。

        max_workers=1（无池）时 no-op（V16/ADR-096）。
        """
        if self._pool is not None:
            self._pool.shutdown(wait=wait)
            self._pool = None

    def _submit(self, job_id: str, run_id: str) -> None:
        """派发执行：同步（无池）或提交线程池（异步）。"""
        if self._pool is None:
            self._run_job(job_id, run_id)
            return
        future = self._pool.submit(self._run_job, job_id, run_id)
        future.add_done_callback(self._consume)

    @staticmethod
    def _consume(future: concurrent.futures.Future[None]) -> None:
        """消费 future 异常：_run_job 内部全路径兜底，此处仅保险。"""
        exc = future.exception()
        if exc is not None:
            logger.error("scheduled job crashed: %s", exc)

    def recover(self) -> None:
        """服务启动时恢复：running → idle，run 标记 interrupted。"""
        self.store.recover_interrupted()

    def _run_job(self, job_id: str, run_id: str) -> None:
        job = self.store.get_job(job_id)
        if job is None:
            return
        previous = self.store.last_successful_hash(job.job_id)
        try:
            current_hash = _source_fingerprint(job.command, previous)
        except OSError:
            current_hash = None
        if current_hash is not None and previous == current_hash:
            self._finish_skipped(job, run_id, current_hash)
            return
        try:
            result = self._executor.execute(job.command)
        except Exception as exc:
            self._finish_failure(job, run_id, exc)
            return
        self._finish_success(job, run_id, result)

    def _finish_skipped(self, job: ScheduledJob, run_id: str, file_hash: str) -> None:
        """变更感知跳过（Step 53）：内容未变则不重扫——不建 scan_run、不重判门禁，
        仅记 completed+skipped run 并通知 webhook（携带 skipped:true 与 hash）。"""
        now = self._clock()
        result = JobResult(
            file_hash=file_hash,
            skipped=True,
        )
        summary = result.model_dump_json()
        self.store.finish_run(
            run_id,
            success=True,
            scan_run_id=None,
            summary=summary,
            file_hash=file_hash,
            skipped=True,
            next_run_at=next_run(job.cron, now),
            job_status=JobStatus.IDLE,
        )
        self._notify(job, run_id, result.model_dump())

    def _finish_success(self, job: ScheduledJob, run_id: str, result: JobResult) -> None:
        now = self._clock()
        result.gate = evaluate_gate(job, result)
        summary = result.model_dump_json()
        self.store.finish_run(
            run_id,
            success=True,
            scan_run_id=result.scan_run_id,
            summary=summary,
            file_hash=result.file_hash,
            next_run_at=next_run(job.cron, now),
            job_status=JobStatus.IDLE,
        )
        self._notify(job, run_id, result.model_dump())

    def _finish_failure(self, job: ScheduledJob, run_id: str, exc: Exception) -> None:
        now = self._clock()
        error = f"{type(exc).__name__}: {exc}"
        run = self.store.get_run(run_id)
        attempts_used = run.attempt if run is not None else 1
        if attempts_used <= job.retry_attempts:
            self.store.finish_run(
                run_id,
                success=False,
                error=error,
                next_run_at=now + RETRY_BACKOFF,
                job_status=JobStatus.IDLE,
            )
        else:
            self.store.finish_run(
                run_id,
                success=False,
                error=error,
                next_run_at=now + RETRY_BACKOFF,
                job_status=JobStatus.DEAD,
            )
        self._notify(
            job,
            run_id,
            {
                "scan_run_id": None,
                "total_issues": 0,
                "quality_score": None,
                "issues_by_severity": {},
                "error": error,
            },
        )

    def _notify(self, job: ScheduledJob, run_id: str, payload: dict[str, object]) -> None:
        if not job.webhook_url:
            return
        body: dict[str, object] = {
            "job_id": job.job_id,
            "run_id": run_id,
            "name": job.name,
            "status": "completed" if payload.get("error") is None else "failed",
            "at": iso(self._clock()),
            **payload,
        }
        if body.get("report_path") is None:
            body.pop("report_path", None)
        if body.get("report_size") is None:
            body.pop("report_size", None)
        self._notifier.notify(job.webhook_url, body)
        self.store.save_webhook_at(run_id, self._clock())


class SchedulerWorker:
    """后台调度线程：循环 tick；可优雅停止（服务 shutdown）。"""

    def __init__(
        self,
        scheduler: Scheduler,
        *,
        interval: float = 1.0,
        name: str = "datasentry-scheduler",
    ) -> None:
        self._scheduler = scheduler
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._thread.join(timeout)
        self._scheduler.shutdown(wait=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._scheduler.tick()
            except Exception:
                logger.exception("scheduler tick failed")
            self._stop.wait(self._interval)

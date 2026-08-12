"""Step 51/53（V2-D 云侧调度 + 变更感知）核心：cron 语义 + 调度器 + 执行器 + worker
（ADR-051 / ADR-053）。

- `Scheduler.tick(now)`：纯同步、可测；原子抢占到期任务 → 执行 → 落结果。
- 重试语义：失败且 attempt <= retry_attempts → 60s 后重试；超过 → 死信（dead）。
- 重启恢复：`recover_interrupted` 将 running 任务置回 idle，run 标记 interrupted。
- 并发互斥：SQLite BEGIN IMMEDIATE 条件更新，同一任务同一时刻仅一个执行者。
- webhook：执行结束（成功/失败/跳过）尽力通知（失败仅记录，不影响调度）；URL 为空即关闭。
- 变更感知（Step 53）：文件 SHA-256 与上次成功执行一致 → 记 skipped run（不建
  scan_run、不重判门禁、webhook 带 skipped:true）。
- 扩展点：`ScanExecutor` Protocol——未来可换云函数/SSH 远端执行。
"""

from __future__ import annotations

import hashlib
import logging
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


def _db_source_type(path: str) -> str:
    """远程库 URL → DataSourceType（Step 55/56：PG 与 MySQL 源类型不同）。"""
    if path.startswith("mysql://"):
        return "mysql"
    return "postgresql"


def _source_fingerprint(command: JobCommand) -> str | None:
    """源内容指纹（Step 55/56）：文件源=文件 SHA-256（Step 53 原语义）；
    PostgreSQL/MySQL 源=表内容指纹（无文件字节，经连接器 handle 计算）。

    源不可达（文件缺失/库断连/缺表名）返回 None——与 Step 53「文件缺失
    不跳过」语义一致：交给执行器触发正常失败路径，绝不误跳过。
    """
    if isinstance(command.path, str) and _is_remote_db_path(command.path):
        from datasentry_core.connectors import DataSourceSpec, DataSourceType, default_registry

        try:
            handle = default_registry().open(
                DataSourceSpec(
                    source_type=DataSourceType(_db_source_type(command.path)),
                    table_name=command.table_name,
                    options={"dsn": command.path},
                )
            )
        except Exception:
            return None
        try:
            return handle.content_fingerprint()
        except Exception:
            return None
        finally:
            handle.close()
    return file_sha256(command.path)


@runtime_checkable
class ScanExecutor(Protocol):
    """扫描执行抽象：当前为本地执行，未来可替换为云函数/远端。"""

    def execute(self, command: JobCommand) -> JobResult:
        """执行一次扫描，成功抛异常表示失败（触发重试/死信）。"""
        ...


class LocalScanExecutor:
    """本地执行器：新建 DataSentry(project=job.project) 执行 scan_file。"""

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
            )
        finally:
            client.close()
        by_severity: dict[str, int] = {}
        for issue in issues:
            by_severity[issue.severity.value] = by_severity.get(issue.severity.value, 0) + 1
        score = scan_run.quality_score.overall if scan_run.quality_score else 0.0
        # Step 55/56：文件源沿用 file_sha256（full 档）；PG/MySQL 源无文件字节，
        # full 档指纹的 content_sample_hash 即内容指纹——两侧同一字段（TEXT）落库
        fingerprint = scan_run.fingerprint
        source_hash = fingerprint.file_sha256 or fingerprint.content_sample_hash
        return JobResult(
            scan_run_id=scan_run.id,
            total_issues=len(issues),
            quality_score=score,
            issues_by_severity=by_severity,
            file_hash=source_hash,
        )


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
    ) -> None:
        self.store = store
        self._executor = executor
        self._notifier = notifier or WebhookNotifier()
        self._clock = clock or utcnow

    def tick(self) -> list[str]:
        """一轮调度：抢占到期任务并执行；返回本轮 job_id 列表（测试/观测用）。"""
        now = self._clock()
        claimed = self.store.claim_due_jobs(now)
        for job_id, run_id, _attempt in claimed:
            self._run_job(job_id, run_id)
        return [job_id for job_id, _run_id, _attempt in claimed]

    def trigger(self, job_id: str) -> str | None:
        """手动触发立即执行；任务已在执行中返回 None（互斥）。"""
        run_id = self.store.claim_job(job_id, self._clock())
        if run_id is not None:
            self._run_job(job_id, run_id)
        return run_id

    def recover(self) -> None:
        """服务启动时恢复：running → idle，run 标记 interrupted。"""
        self.store.recover_interrupted()

    def _run_job(self, job_id: str, run_id: str) -> None:
        job = self.store.get_job(job_id)
        if job is None:
            return
        try:
            current_hash = _source_fingerprint(job.command)
        except OSError:
            current_hash = None
        if current_hash is not None and self.store.last_successful_hash(job.job_id) == current_hash:
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
        self._notifier.notify(
            job.webhook_url,
            {
                "job_id": job.job_id,
                "run_id": run_id,
                "name": job.name,
                "status": "completed" if payload.get("error") is None else "failed",
                "at": iso(self._clock()),
                **payload,
            },
        )
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

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._scheduler.tick()
            except Exception:
                logger.exception("scheduler tick failed")
            self._stop.wait(self._interval)

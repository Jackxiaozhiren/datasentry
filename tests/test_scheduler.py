"""Step 51（V2-D 云侧调度）测试：cron 语义 / 调度状态机 / 持久化 / webhook。

覆盖验收标准：非法 cron 拒绝、到期执行、未到期跳过、并发互斥、
失败重试与死信、重启恢复、手动触发、webhook 通知。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from datasentry.scheduler.core import (
    InvalidCronError,
    Scheduler,
    WebhookNotifier,
    next_run,
    validate_cron,
)
from datasentry.scheduler.models import (
    JobCommand,
    JobResult,
    JobStatus,
    RunStatus,
    ScheduledJob,
)
from datasentry.scheduler.store import SchedulerStore

T0 = datetime(2026, 8, 10, 10, 0, 0)


def _command(path: str = "/tmp/x.csv", project: str = "/tmp/proj") -> JobCommand:
    return JobCommand(project=project, path=path)


def _job(
    *,
    job_id: str = "job_1",
    cron: str = "*/5 * * * *",
    next_at: datetime = T0,
    retry_attempts: int = 0,
    webhook_url: str | None = None,
    enabled: bool = True,
    status: JobStatus = JobStatus.IDLE,
) -> ScheduledJob:
    return ScheduledJob(
        job_id=job_id,
        name=f"job {job_id}",
        project="/tmp/proj",
        command=_command(),
        cron=cron,
        enabled=enabled,
        retry_attempts=retry_attempts,
        webhook_url=webhook_url,
        status=status,
        next_run_at=next_at,
        created_at=T0,
        updated_at=T0,
    )


class _FakeExecutor:
    """可编程执行器：按 job_id 返回结果或抛异常，记录调用。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_jobs: set[str] = set()

    def execute(self, command: JobCommand) -> JobResult:
        self.calls.append(command.path)
        if command.project in self.fail_jobs:
            raise RuntimeError("boom")
        return JobResult(
            scan_run_id=f"scan_{len(self.calls)}",
            total_issues=3,
            quality_score=88.0,
            issues_by_severity={"low": 3},
        )


class _BlockingExecutor(_FakeExecutor):
    """执行进入后阻塞，模拟长任务（互斥窗口）。"""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def execute(self, command: JobCommand) -> JobResult:
        self.calls.append(command.path)
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("blocked too long")
        if command.project in self.fail_jobs:
            raise RuntimeError("boom")
        return JobResult(
            scan_run_id=f"scan_{len(self.calls)}",
            total_issues=3,
            quality_score=88.0,
            issues_by_severity={"low": 3},
        )


class _Clock:
    """可推进假时钟（调度测试不依赖真实时间）。"""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: Any) -> None:
        self.now += timedelta(**kw)


@pytest.fixture
def store(tmp_path: Path) -> SchedulerStore:
    return SchedulerStore(tmp_path / "sched.db")


@pytest.fixture
def scheduler(
    store: SchedulerStore,
) -> tuple[Scheduler, _FakeExecutor, SchedulerStore, _Clock]:
    executor = _FakeExecutor()
    clock = _Clock(T0)
    return Scheduler(store=store, executor=executor, clock=clock), executor, store, clock


# ---- cron 语义 -----------------------------------------------------------


class TestCronSemantics:
    def test_valid_cron_accepted(self) -> None:
        assert validate_cron("*/5 * * * *") == "*/5 * * * *"
        assert validate_cron("0 9 * * 1-5")

    def test_invalid_cron_rejected(self) -> None:
        for expr in ["61 * * * *", "* * *", "bad cron", "a b c d e", ""]:
            with pytest.raises(InvalidCronError):
                validate_cron(expr)

    def test_next_run_computes(self) -> None:
        assert next_run("*/5 * * * *", T0) == T0 + timedelta(minutes=5)
        assert next_run("0 9 * * *", T0) == datetime(2026, 8, 11, 9, 0)

    def test_next_run_skips_past(self) -> None:
        nxt = next_run("*/5 * * * *", T0 + timedelta(seconds=1))
        assert nxt > T0


# ---- 调度状态机 -----------------------------------------------------------


class TestSchedulerTick:
    def test_due_job_executes_and_advances(
        self, scheduler: tuple[Scheduler, _FakeExecutor, SchedulerStore, _Clock]
    ) -> None:
        sched, executor, store, _clock = scheduler
        store.create_job(_job(next_at=T0))
        sched.tick()
        assert executor.calls == ["/tmp/x.csv"]
        job = store.get_job("job_1")
        assert job is not None
        assert job.status == JobStatus.IDLE
        assert job.next_run_at == T0 + timedelta(minutes=5)
        run = store.list_runs("job_1")[0]
        assert run.status == RunStatus.COMPLETED
        assert run.summary is not None
        assert json.loads(run.summary)["total_issues"] == 3

    def test_not_due_job_skipped(
        self, scheduler: tuple[Scheduler, _FakeExecutor, SchedulerStore, _Clock]
    ) -> None:
        sched, executor, store, _clock = scheduler
        store.create_job(_job(next_at=T0 + timedelta(minutes=5)))
        sched.tick()
        assert executor.calls == []
        assert store.get_job("job_1").status == JobStatus.IDLE  # type: ignore[union-attr]

    def test_disabled_job_skipped(
        self, scheduler: tuple[Scheduler, _FakeExecutor, SchedulerStore, _Clock]
    ) -> None:
        sched, executor, store, _clock = scheduler
        store.create_job(_job(next_at=T0, enabled=False))
        sched.tick()
        assert executor.calls == []

    def test_running_job_not_reclaimed(
        self, scheduler: tuple[Scheduler, _FakeExecutor, SchedulerStore, _Clock]
    ) -> None:
        """并发互斥：running 状态任务在下一轮 tick 不会被重复抢占。"""
        sched, executor, store, _clock = scheduler
        store.create_job(_job(next_at=T0))
        sched.tick()
        sched.tick()
        assert len(executor.calls) == 1

    def test_failure_retries_then_dead_letter(
        self, scheduler: tuple[Scheduler, _FakeExecutor, SchedulerStore, _Clock]
    ) -> None:
        sched, executor, store, clock = scheduler
        executor.fail_jobs.add("/tmp/proj")
        store.create_job(_job(job_id="job_retry", retry_attempts=2))
        sched.tick()
        job = store.get_job("job_retry")
        assert job is not None
        assert job.status == JobStatus.IDLE
        assert job.next_run_at == T0 + timedelta(seconds=60)
        assert store.list_runs("job_retry")[0].status == RunStatus.FAILED

        clock.advance(seconds=60)
        sched.tick()
        assert store.get_job("job_retry").status == JobStatus.IDLE  # type: ignore[union-attr]

        clock.advance(seconds=60)
        sched.tick()
        job = store.get_job("job_retry")
        assert job is not None
        assert job.status == JobStatus.DEAD
        assert "boom" in (job.last_result or "")

    def test_success_clears_failure_history(
        self, scheduler: tuple[Scheduler, _FakeExecutor, SchedulerStore, _Clock]
    ) -> None:
        sched, executor, store, clock = scheduler
        executor.fail_jobs.add("/tmp/proj")
        store.create_job(_job(job_id="job_a", retry_attempts=1))
        sched.tick()
        executor.fail_jobs.clear()
        clock.advance(seconds=60)
        sched.tick()
        assert store.get_job("job_a").status == JobStatus.IDLE  # type: ignore[union-attr]
        assert "scan_" in (store.get_job("job_a").last_result or "")  # type: ignore[union-attr]

    def test_manual_trigger_executes_and_mutual_excludes(self, store: SchedulerStore) -> None:
        """互斥窗口：执行中 trigger 拒绝（返回 None），完成后可再次触发。"""
        executor = _BlockingExecutor()
        sched = Scheduler(store=store, executor=executor)
        store.create_job(_job())
        thread = threading.Thread(target=sched.trigger, args=("job_1",))
        thread.start()
        assert executor.entered.wait(timeout=5)
        assert sched.trigger("job_1") is None  # 执行中 → 互斥拒绝
        executor.release.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert executor.calls == ["/tmp/x.csv"]
        run_id = sched.trigger("job_1")  # 完成后可再次触发
        assert run_id is not None
        assert len(executor.calls) == 2

    def test_trigger_unknown_job(
        self, scheduler: tuple[Scheduler, _FakeExecutor, SchedulerStore, _Clock]
    ) -> None:
        sched, _executor, _store, _clock = scheduler
        assert sched.trigger("nope") is None


# ---- 持久化与恢复 -----------------------------------------------------------


class TestPersistence:
    def test_restart_recovers_interrupted(self, tmp_path: Path) -> None:
        """重启恢复：running 任务置回 idle，run 标记 interrupted，可重新调度。"""
        store = SchedulerStore(tmp_path / "sched.db")
        store.create_job(_job(next_at=T0))
        # 模拟崩溃现场：另一个"进程"抢占任务后进程退出，未落执行结果
        run_id = store.claim_job("job_1", T0)
        assert run_id is not None
        store2 = SchedulerStore(tmp_path / "sched.db")
        store2.recover_interrupted()
        job = store2.get_job("job_1")
        assert job is not None
        assert job.status == JobStatus.IDLE
        run = store2.list_runs("job_1")[0]
        assert run.status == RunStatus.FAILED
        assert "interrupted" in (run.error or "")
        # 恢复后重新调度执行
        sched = Scheduler(store=store2, executor=_FakeExecutor())
        sched.tick()
        assert store2.list_runs("job_1")[0].status == RunStatus.COMPLETED

    def test_jobs_survive_store_reopen(self, tmp_path: Path) -> None:
        db = tmp_path / "sched.db"
        store1 = SchedulerStore(db)
        store1.create_job(_job(job_id="job_p", webhook_url="https://hook/x"))
        store2 = SchedulerStore(db)
        job = store2.get_job("job_p")
        assert job is not None
        assert job.command.path == "/tmp/x.csv"
        assert job.webhook_url == "https://hook/x"

    def test_update_and_delete(self, store: SchedulerStore) -> None:
        store.create_job(_job(job_id="job_u"))
        assert store.update_job("job_u", enabled=False, webhook_url=None)  # None = 置空
        job = store.get_job("job_u")
        assert job is not None
        assert job.enabled is False
        assert job.webhook_url is None
        assert store.update_job("job_u", cron="0 0 * * *")
        assert store.get_job("job_u").cron == "0 0 * * *"  # type: ignore[union-attr]
        assert store.delete_job("job_u") is True
        assert store.delete_job("job_u") is False
        assert store.get_job("job_u") is None


# ---- webhook -----------------------------------------------------------


class TestWebhook:
    def test_notify_sent_on_success(self, tmp_path: Path) -> None:
        payloads: list[dict[str, Any]] = []

        def transport(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        client = httpx.Client(transport=httpx.MockTransport(transport))
        original_post = client.post

        def spy_post(url: str, json: Any | None = None, **kwargs: Any) -> Any:
            payloads.append(json or {})
            return original_post(url, json=json, **kwargs)

        client.post = spy_post  # type: ignore[method-assign]
        store = SchedulerStore(tmp_path / "sched.db")
        store.create_job(_job(job_id="job_w", webhook_url="https://hook/x"))
        sched = Scheduler(
            store=store, executor=_FakeExecutor(), notifier=WebhookNotifier(lambda: client)
        )
        sched.tick()
        assert len(payloads) == 1
        body = payloads[0]
        assert body["job_id"] == "job_w"
        assert body["status"] == "completed"
        assert body["scan_run_id"] == "scan_1"
        assert body["total_issues"] == 3
        assert store.list_runs("job_w")[0].webhook_at is not None

    def test_notify_sent_on_failure(self, tmp_path: Path) -> None:
        payloads: list[dict[str, Any]] = []

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        original_post = client.post

        def spy_post(url: str, json: Any | None = None, **kwargs: Any) -> Any:
            payloads.append(json or {})
            return original_post(url, json=json, **kwargs)

        client.post = spy_post  # type: ignore[method-assign]
        executor = _FakeExecutor()
        executor.fail_jobs.add("/tmp/proj")
        store = SchedulerStore(tmp_path / "sched.db")
        store.create_job(_job(job_id="job_f", webhook_url="https://hook/x"))
        sched = Scheduler(store=store, executor=executor, notifier=WebhookNotifier(lambda: client))
        sched.tick()
        assert len(payloads) == 1
        assert payloads[0]["status"] == "failed"
        assert "boom" in payloads[0]["error"]

    def test_no_webhook_url_no_notify(self, tmp_path: Path) -> None:
        seen: list[str] = []

        def handler(_request: httpx.Request) -> httpx.Response:
            seen.append(str(_request.url))
            return httpx.Response(200)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        store = SchedulerStore(tmp_path / "sched.db")
        store.create_job(_job(job_id="job_n", webhook_url=None))
        sched = Scheduler(
            store=store, executor=_FakeExecutor(), notifier=WebhookNotifier(lambda: client)
        )
        sched.tick()
        assert seen == []

    def test_webhook_failure_does_not_break_schedule(self, tmp_path: Path) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        store = SchedulerStore(tmp_path / "sched.db")
        store.create_job(_job(job_id="job_ok", webhook_url="https://hook/x"))
        sched = Scheduler(
            store=store, executor=_FakeExecutor(), notifier=WebhookNotifier(lambda: client)
        )
        sched.tick()
        job = store.get_job("job_ok")
        assert job is not None
        assert job.status == JobStatus.IDLE
        assert store.list_runs("job_ok")[0].status == RunStatus.COMPLETED

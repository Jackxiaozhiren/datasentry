"""Step 96（ADR-096）：Scheduler 线程池异步执行测试。

- max_workers=1 同步语义与既有完全一致（回归）
- max_workers>1：tick/trigger 异步派发、并行执行、互斥保持、
  优雅关闭、异常消化
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from datasentry.scheduler.core import Scheduler
from datasentry.scheduler.models import JobCommand, JobResult, ScheduledJob, utcnow
from datasentry.scheduler.store import SchedulerStore
from datasentry_core.storage.paths import project_db_path


class _SlowExecutor:
    """可观测慢执行器：并发记录 + 可选抛错。"""

    def __init__(self, delay: float = 0.3, fail: bool = False) -> None:
        self.delay = delay
        self.fail = fail
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.executed: list[str] = []

    def execute(self, command: JobCommand) -> JobResult:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.fail:
                raise RuntimeError("executor boom")
            time.sleep(self.delay)
            self.executed.append(command.path)
            return JobResult(scan_run_id="s1", total_issues=1)
        finally:
            with self.lock:
                self.active -= 1


def _job(store: SchedulerStore, name: str, *, due_delta: float = -5.0) -> str:
    now = utcnow()
    store.create_job(
        ScheduledJob(
            job_id=f"job_{name}",
            name=name,
            project="p",
            command=JobCommand(project="p", path=f"{name}.csv"),
            cron="0 0 1 1 *",
            next_run_at=now + timedelta(seconds=due_delta),
            created_at=now,
            updated_at=now,
        )
    )
    return f"job_{name}"


def _status(store: SchedulerStore, run_id: str) -> str:
    run = store.get_run(run_id)
    assert run is not None
    return run.status.value


def _wait_terminal(store: SchedulerStore, run_id: str, timeout: float = 10.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = _status(store, run_id)
        if status in {"completed", "failed"}:
            return status
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not reach terminal state")


def _scheduler(db: Path, executor: Any, max_workers: int = 1) -> Scheduler:
    return Scheduler(store=SchedulerStore(db), executor=executor, max_workers=max_workers)


class TestSyncDefault:
    def test_tick_completes_synchronously(self, tmp_path: Path) -> None:
        store = SchedulerStore(project_db_path(tmp_path))
        slow = _SlowExecutor(delay=0.1)
        sched = _scheduler(project_db_path(tmp_path), slow)
        job_id = _job(store, "a")
        sched.tick()
        run_id = store.get_run(store.list_runs(job_id)[0].run_id)
        assert run_id is not None
        assert _status(store, run_id.run_id) == "completed"

    def test_trigger_completes_synchronously(self, tmp_path: Path) -> None:
        store = SchedulerStore(project_db_path(tmp_path))
        sched = _scheduler(project_db_path(tmp_path), _SlowExecutor(delay=0.05))
        job_id = _job(store, "a")
        run_id = sched.trigger(job_id)
        assert run_id is not None
        assert _status(store, run_id) == "completed"


class TestParallel:
    def test_tick_runs_jobs_in_parallel(self, tmp_path: Path) -> None:
        store = SchedulerStore(project_db_path(tmp_path))
        slow = _SlowExecutor(delay=0.3)
        sched = _scheduler(project_db_path(tmp_path), slow, max_workers=3)
        job_a = _job(store, "a")
        job_b = _job(store, "b")
        start = time.monotonic()
        sched.tick()
        assert time.monotonic() - start < 0.15
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and slow.max_active < 2:
            time.sleep(0.02)
        assert slow.max_active >= 2
        for job_id in (job_a, job_b):
            run = store.list_runs(job_id)[0]
            assert _wait_terminal(store, run.run_id) == "completed"

    def test_trigger_is_async(self, tmp_path: Path) -> None:
        store = SchedulerStore(project_db_path(tmp_path))
        slow = _SlowExecutor(delay=0.3)
        sched = _scheduler(project_db_path(tmp_path), slow, max_workers=2)
        job_id = _job(store, "a")
        run_id = sched.trigger(job_id)
        assert run_id is not None
        assert _status(store, run_id) == "running"
        assert _wait_terminal(store, run_id) == "completed"

    def test_mutex_holds_while_running(self, tmp_path: Path) -> None:
        store = SchedulerStore(project_db_path(tmp_path))
        slow = _SlowExecutor(delay=0.3)
        sched = _scheduler(project_db_path(tmp_path), slow, max_workers=2)
        job_id = _job(store, "a")
        run_id = sched.trigger(job_id)
        assert run_id is not None
        assert sched.trigger(job_id) is None
        _wait_terminal(store, run_id)

    def test_shutdown_waits_for_inflight(self, tmp_path: Path) -> None:
        store = SchedulerStore(project_db_path(tmp_path))
        slow = _SlowExecutor(delay=0.3)
        sched = _scheduler(project_db_path(tmp_path), slow, max_workers=2)
        job_id = _job(store, "a")
        run_id = sched.trigger(job_id)
        assert run_id is not None
        sched.shutdown(wait=True)
        assert _status(store, run_id) == "completed"

    def test_executor_error_finishes_failed(self, tmp_path: Path) -> None:
        store = SchedulerStore(project_db_path(tmp_path))
        sched = _scheduler(project_db_path(tmp_path), _SlowExecutor(fail=True), max_workers=2)
        job_id = _job(store, "a")
        run_id = sched.trigger(job_id)
        assert run_id is not None
        assert _wait_terminal(store, run_id) == "failed"
        sched.shutdown(wait=True)

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
    LocalScanExecutor,
    Scheduler,
    WebhookNotifier,
    file_sha256,
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
    gate_quality_min: float | None = None,
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
        gate_quality_min=gate_quality_min,
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


class TestQualityGate:
    def test_no_gate_returns_none(
        self, scheduler: tuple[Scheduler, _FakeExecutor, SchedulerStore, _Clock]
    ) -> None:
        sched, _executor, store, _clock = scheduler
        store.create_job(_job(job_id="job_ng"))
        sched.tick()
        run = store.list_runs("job_ng")[0]
        result = json.loads(run.summary or "{}")
        assert result["gate"] is None

    def test_gate_passed_when_score_above_threshold(
        self, scheduler: tuple[Scheduler, _FakeExecutor, SchedulerStore, _Clock]
    ) -> None:
        sched, _executor, store, _clock = scheduler
        store.create_job(_job(job_id="job_pass", gate_quality_min=80.0))
        sched.tick()
        result = json.loads(store.list_runs("job_pass")[0].summary or "{}")
        gate = result["gate"]
        assert gate["configured"] is True
        assert gate["quality_min"] == 80.0
        assert gate["quality_score"] == 88.0
        assert gate["passed"] is True

    def test_gate_failed_when_score_below_threshold(
        self, scheduler: tuple[Scheduler, _FakeExecutor, SchedulerStore, _Clock]
    ) -> None:
        sched, _executor, store, _clock = scheduler
        store.create_job(_job(job_id="job_fail", gate_quality_min=95.0))
        sched.tick()
        result = json.loads(store.list_runs("job_fail")[0].summary or "{}")
        assert result["gate"]["passed"] is False
        # 门禁失败 ≠ 执行失败：任务照常 idle，不触发重试/死信
        job = store.get_job("job_fail")
        assert job is not None
        assert job.status == JobStatus.IDLE

    def test_gate_exact_boundary_passes(
        self, scheduler: tuple[Scheduler, _FakeExecutor, SchedulerStore, _Clock]
    ) -> None:
        sched, _executor, store, _clock = scheduler
        store.create_job(_job(job_id="job_edge", gate_quality_min=88.0))
        sched.tick()
        result = json.loads(store.list_runs("job_edge")[0].summary or "{}")
        assert result["gate"]["passed"] is True

    def test_gate_shown_in_webhook_payload(self, tmp_path: Path) -> None:
        payloads: list[dict[str, Any]] = []
        import httpx

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        original_post = client.post

        def spy_post(url: str, json: Any | None = None, **kwargs: Any) -> Any:
            payloads.append(json or {})
            return original_post(url, json=json, **kwargs)

        client.post = spy_post  # type: ignore[method-assign]
        store = SchedulerStore(tmp_path / "sched.db")
        store.create_job(_job(job_id="job_wh", webhook_url="https://hook/x", gate_quality_min=85.0))
        sched = Scheduler(
            store=store, executor=_FakeExecutor(), notifier=WebhookNotifier(lambda: client)
        )
        sched.tick()
        assert payloads[0]["gate"]["passed"] is True
        assert payloads[0]["gate"]["quality_min"] == 85.0

    def test_trigger_result_includes_gate(self, store: SchedulerStore) -> None:
        store.create_job(_job(job_id="job_tr", gate_quality_min=90.0))
        sched = Scheduler(store=store, executor=_FakeExecutor())
        run_id = sched.trigger("job_tr")
        assert run_id is not None
        result = json.loads(store.get_run(run_id).summary or "{}")  # type: ignore[union-attr]
        assert result["gate"]["passed"] is False


# ---- 变更感知增量调度（Step 53，ADR-053） ------------------------------


class _HashExecutor(_FakeExecutor):
    """执行器：正常执行并回填文件 hash（真实路径 sha256）。"""

    def execute(self, command: JobCommand) -> JobResult:
        result = super().execute(command)
        result.file_hash = file_sha256(command.path)
        return result


def _file_job(job_id: str, path: Path, **kw: Any) -> ScheduledJob:
    job = _job(job_id=job_id, **kw)
    job.command = JobCommand(project=job.command.project, path=str(path))
    return job


class TestChangeAware:
    def test_unchanged_file_skips_and_change_resumes(
        self, store: SchedulerStore, tmp_path: Path
    ) -> None:
        data = tmp_path / "data.csv"
        data.write_text("a,b\n1,2\n", encoding="utf-8")
        executor = _HashExecutor()
        clock = _Clock(T0)
        scheduler = Scheduler(store=store, executor=executor, clock=clock)
        store.create_job(
            _file_job("job_sk", data, next_at=T0 - timedelta(minutes=1), cron="* * * * *")
        )

        scheduler.tick()
        assert executor.calls == [str(data)]

        clock.advance(minutes=1)
        scheduler.tick()
        assert executor.calls == [str(data)]
        runs = store.list_runs("job_sk")
        assert runs[0].skipped is True
        assert runs[0].status == RunStatus.COMPLETED
        assert runs[0].scan_run_id is None
        assert runs[0].file_hash is not None
        assert json.loads(runs[0].summary or "{}")["skipped"] is True

        data.write_text("a,b\n1,3\n", encoding="utf-8")
        clock.advance(minutes=1)
        scheduler.tick()
        assert executor.calls == [str(data), str(data)]
        runs = store.list_runs("job_sk")
        assert runs[0].skipped is False
        assert runs[0].scan_run_id is not None
        assert runs[0].file_hash != runs[1].file_hash

    def test_skipped_run_does_not_rejudge_gate(self, store: SchedulerStore, tmp_path: Path) -> None:
        data = tmp_path / "data.csv"
        data.write_text("a,b\n1,2\n", encoding="utf-8")
        executor = _HashExecutor()
        clock = _Clock(T0)
        scheduler = Scheduler(store=store, executor=executor, clock=clock)
        store.create_job(
            _file_job(
                "job_g",
                data,
                next_at=T0 - timedelta(minutes=1),
                cron="* * * * *",
                gate_quality_min=95.0,
            )
        )

        scheduler.tick()
        full = store.list_runs("job_g")[0]
        assert json.loads(full.summary or "{}")["gate"]["passed"] is False

        clock.advance(minutes=1)
        scheduler.tick()
        skipped = store.list_runs("job_g")[0]
        assert skipped.skipped is True
        assert json.loads(skipped.summary or "{}")["gate"] is None

    def test_skipped_webhook_payload(self, store: SchedulerStore, tmp_path: Path) -> None:
        data = tmp_path / "data.csv"
        data.write_text("a,b\n1,2\n", encoding="utf-8")
        payloads: list[dict[str, Any]] = []

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        original_post = client.post

        def spy_post(url: str, json: Any | None = None, **kwargs: Any) -> Any:
            payloads.append(json or {})
            return original_post(url, json=json, **kwargs)

        client.post = spy_post  # type: ignore[method-assign]
        executor = _HashExecutor()
        clock = _Clock(T0)
        scheduler = Scheduler(
            store=store,
            executor=executor,
            notifier=WebhookNotifier(lambda: client),
            clock=clock,
        )
        store.create_job(
            _file_job(
                "job_h",
                data,
                next_at=T0 - timedelta(minutes=1),
                cron="* * * * *",
                webhook_url="https://hook/x",
            )
        )

        scheduler.tick()
        clock.advance(minutes=1)
        scheduler.tick()
        assert len(payloads) == 2
        body = payloads[1]
        assert body["skipped"] is True
        assert body["scan_run_id"] is None
        assert body["file_hash"] is not None

    def test_missing_file_falls_back_to_full_run(
        self, store: SchedulerStore, tmp_path: Path
    ) -> None:
        missing = tmp_path / "nope.csv"
        executor = _FakeExecutor()
        scheduler = Scheduler(store=store, executor=executor, clock=_Clock(T0))
        store.create_job(_file_job("job_m", missing, next_at=T0 - timedelta(minutes=1)))
        scheduler.tick()
        assert executor.calls == [str(missing)]
        run = store.list_runs("job_m")[0]
        assert run.skipped is False
        assert run.scan_run_id is not None

    def test_last_successful_hash_query(self, store: SchedulerStore, tmp_path: Path) -> None:
        assert store.last_successful_hash("job_x") is None
        data = tmp_path / "data.csv"
        data.write_text("a,b\n1,2\n", encoding="utf-8")
        executor = _HashExecutor()
        clock = _Clock(T0)
        scheduler = Scheduler(store=store, executor=executor, clock=clock)
        store.create_job(_file_job("job_x", data, next_at=T0 - timedelta(minutes=1)))
        scheduler.tick()
        first = store.last_successful_hash("job_x")
        assert first is not None
        clock.advance(minutes=1)
        scheduler.tick()
        assert store.last_successful_hash("job_x") == first


# ---- Step 55：PG 任务变更感知（无文件字节 → 内容指纹） -----------------------


class _FakePgHandle:
    """假远程句柄：stats/content 双指纹来自共享状态（Step 58 两层语义）。

    stats_fingerprint 与 LocalScanExecutor 落库口径一致：
    sha256(f"{schema_hash}|{row_count}")。
    """

    def __init__(self, holder: dict[str, str]) -> None:
        self._holder = holder
        self.closed = False

    def content_fingerprint(self) -> str:
        self._holder["content_calls"] = self._holder.get("content_calls", 0) + 1
        return self._holder["fp"]

    def stats_fingerprint(self) -> str:
        self._holder["stats_calls"] = self._holder.get("stats_calls", 0) + 1
        import hashlib

        return hashlib.sha256(self._holder["stats"].encode()).hexdigest()

    def close(self) -> None:
        self.closed = True


class _FakePgRegistry:
    """替换 default_registry 工厂：PG spec → 假 handle，指纹来自共享状态。"""

    def __init__(self, holder: dict[str, str]) -> None:
        self._holder = holder
        self.opened_specs: list[Any] = []
        self.last_handle: _FakePgHandle | None = None

    def open(self, spec: Any) -> _FakePgHandle:
        self.opened_specs.append(spec)
        handle = _FakePgHandle(self._holder)
        self.last_handle = handle
        return handle


class _PgScanClient:
    """模拟 PG 扫描客户端：scan_run.fingerprint.content_sample_hash 即内容指纹。"""

    def __init__(self, holder: dict[str, str]) -> None:
        self._holder = holder
        self.calls: list[tuple[str, str | None]] = []

    def scan_file(
        self,
        path: str,
        *,
        dataset_id: str | None = None,
        table_name: str | None = None,
        config: Any = None,
        references: Any = None,
    ) -> Any:
        from datasentry_core.models.fingerprint import DatasetFingerprint
        from datasentry_core.models.quality import QualityScore
        from datasentry_core.models.scan import ReproducibilityInfo, ScanConfig, ScanRun

        self.calls.append((path, table_name))
        # Step 58：fingerprint 的 schema/row_count 由 holder["stats"] 推导
        # （"<schema>|<count>" 原串），保证 LocalScanExecutor 落库的复合
        # 统计层与 handle.stats_fingerprint()（sha256 同串）一致
        schema_part, count_part = self._holder["stats"].split("|")
        fingerprint = DatasetFingerprint(
            dataset_id=dataset_id or "pg",
            fingerprint_type="full",
            file_sha256=None,
            schema_hash=schema_part,
            row_count=int(count_part),
            column_count=1,
            column_signature=[("a", "INTEGER")],
            content_sample_hash=self._holder["fp"],
        )
        scan = ScanRun(
            id=f"scan_{len(self.calls)}",
            dataset_id=fingerprint.dataset_id,
            status="completed",
            config=ScanConfig(),
            fingerprint=fingerprint,
            quality_score=QualityScore(overall=80.0),
            reproducibility=ReproducibilityInfo(datasentry_version="test", seed=42),
        )
        return scan, [], []

    def close(self) -> None:
        pass


def _pg_job(job_id: str, **kw: Any) -> ScheduledJob:
    job = _job(job_id=job_id, **kw)
    job.command = JobCommand(
        project=job.command.project,
        path="postgresql://user:secret@localhost:55432/testdb",
        table_name="orders",
    )
    return job


def _cloud_job(job_id: str, **kw: Any) -> ScheduledJob:
    job = _job(job_id=job_id, **kw)
    job.command = JobCommand(
        project=job.command.project,
        path="s3://test-bucket/orders.csv",
    )
    return job


def _pg_stats(raw: str) -> str:
    """Step 58 统计层落库口径：sha256(f"{schema_hash}|{row_count}")。"""
    import hashlib

    return hashlib.sha256(raw.encode()).hexdigest()


class TestPgChangeAware:
    def test_pg_unchanged_skips_and_change_resumes(
        self, store: SchedulerStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datasentry.scheduler.core import LocalScanExecutor
        from datasentry_core.connectors import DataSourceType

        holder = {"fp": "pg-hash-1", "stats": "schema-hash|2"}
        registry = _FakePgRegistry(holder)
        monkeypatch.setattr("datasentry_core.connectors.default_registry", lambda: registry)
        client = _PgScanClient(holder)
        clock = _Clock(T0)
        scheduler = Scheduler(
            store=store,
            executor=LocalScanExecutor(client_factory=lambda _project: client),
            clock=clock,
        )
        store.create_job(_pg_job("job_pg", next_at=T0 - timedelta(minutes=1), cron="* * * * *"))

        scheduler.tick()
        assert client.calls == [("postgresql://user:secret@localhost:55432/testdb", "orders")]
        spec = registry.opened_specs[0]
        assert spec.source_type == DataSourceType.POSTGRESQL
        assert spec.options["dsn"] == "postgresql://user:secret@localhost:55432/testdb"

        clock.advance(minutes=1)
        scheduler.tick()
        assert client.calls == [("postgresql://user:secret@localhost:55432/testdb", "orders")]
        runs = store.list_runs("job_pg")
        assert runs[0].skipped is True
        stored = json.loads(runs[0].file_hash)
        assert stored["stats"] == _pg_stats("schema-hash|2")
        assert stored["content"] == "pg-hash-1"
        assert json.loads(runs[0].summary or "{}")["skipped"] is True

        holder["fp"] = "pg-hash-2"
        clock.advance(minutes=1)
        scheduler.tick()
        assert len(client.calls) == 2
        runs = store.list_runs("job_pg")
        assert runs[0].skipped is False
        assert runs[0].file_hash != runs[1].file_hash

    def test_pg_stats_change_zero_content_read(
        self, store: SchedulerStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Step 58 统计层：行数/结构变 → 立即判定变更，内容层零读取。"""
        from datasentry.scheduler.core import LocalScanExecutor

        holder = {"fp": "pg-hash-1", "stats": "schema-hash|2"}
        registry = _FakePgRegistry(holder)
        monkeypatch.setattr("datasentry_core.connectors.default_registry", lambda: registry)
        client = _PgScanClient(holder)
        clock = _Clock(T0)
        scheduler = Scheduler(
            store=store,
            executor=LocalScanExecutor(client_factory=lambda _project: client),
            clock=clock,
        )
        store.create_job(_pg_job("job_pg4", next_at=T0 - timedelta(minutes=1), cron="* * * * *"))

        scheduler.tick()

        clock.advance(minutes=1)
        scheduler.tick()
        assert store.list_runs("job_pg4")[0].skipped is True
        content_calls_before_stats_change = holder.get("content_calls", 0)

        holder["stats"] = "schema-hash|3"  # 追加行（行数变，内容未变）
        clock.advance(minutes=1)
        scheduler.tick()
        assert (
            holder.get("content_calls", 0) == content_calls_before_stats_change
        )  # 统计层判定变更 → 内容层零调用
        assert len(client.calls) == 2  # 判定变更 → 重新扫描
        stored = json.loads(store.list_runs("job_pg4")[0].file_hash)
        assert stored["stats"] == _pg_stats("schema-hash|3")

        clock.advance(minutes=1)
        scheduler.tick()
        assert len(client.calls) == 2  # 新统计层已落库（含扫描后内容层）→ 跳过
        assert store.list_runs("job_pg4")[0].skipped is True

    def test_pg_unreachable_falls_back_to_full_run(
        self, store: SchedulerStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _BrokenRegistry:
            def open(self, spec: Any) -> Any:
                raise RuntimeError("postgres unreachable")

        monkeypatch.setattr("datasentry_core.connectors.default_registry", _BrokenRegistry)
        executor = _FakeExecutor()
        clock = _Clock(T0)
        scheduler = Scheduler(store=store, executor=executor, clock=clock)
        store.create_job(_pg_job("job_pg2", next_at=T0 - timedelta(minutes=1), cron="* * * * *"))
        scheduler.tick()
        assert executor.calls == ["postgresql://user:secret@localhost:55432/testdb"]
        run = store.list_runs("job_pg2")[0]
        assert run.skipped is False
        assert run.scan_run_id is not None

    def test_pg_fingerprint_handle_closed(
        self, store: SchedulerStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        holder = {"fp": "x", "stats": "schema-hash|2"}
        registry = _FakePgRegistry(holder)
        monkeypatch.setattr("datasentry_core.connectors.default_registry", lambda: registry)
        executor = _FakeExecutor()
        scheduler = Scheduler(store=store, executor=executor, clock=_Clock(T0))
        store.create_job(_pg_job("job_pg3", next_at=T0 - timedelta(minutes=1)))
        scheduler.tick()
        assert len(registry.opened_specs) == 1
        assert registry.last_handle is not None
        assert registry.last_handle.closed is True  # 指纹句柄用完即关
        runs = store.list_runs("job_pg3")
        assert runs[0].skipped is False

    def test_pg_legacy_hash_migrates_to_composite(
        self, store: SchedulerStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Step 58 迁移：Step 55/56/57 时代落库的单段 hash 视为统计层未知 →
        保守走内容层比对 → 重扫成功后落库复合指纹（下次可两层跳过）。"""
        import sqlite3

        from datasentry.scheduler.core import LocalScanExecutor

        holder = {"fp": "pg-hash-1", "stats": "schema-hash|2"}
        registry = _FakePgRegistry(holder)
        monkeypatch.setattr("datasentry_core.connectors.default_registry", lambda: registry)
        client = _PgScanClient(holder)
        clock = _Clock(T0)
        scheduler = Scheduler(
            store=store,
            executor=LocalScanExecutor(client_factory=lambda _project: client),
            clock=clock,
        )
        store.create_job(_pg_job("job_pg5", next_at=T0 - timedelta(minutes=1), cron="* * * * *"))

        scheduler.tick()
        assert len(client.calls) == 1

        # 模拟遗留库：把落库的复合指纹篡改为单段 hash
        with sqlite3.connect(str(store._db_path)) as conn:
            conn.execute(
                "UPDATE job_runs SET file_hash = 'legacy-single-hash' WHERE job_id = 'job_pg5'"
            )

        clock.advance(minutes=1)
        scheduler.tick()
        assert len(client.calls) == 2  # 内容层保守比对 → 判定「未知」→ 重扫
        stored = json.loads(store.list_runs("job_pg5")[0].file_hash)
        assert stored["content"] == "pg-hash-1"

        clock.advance(minutes=1)
        scheduler.tick()
        assert len(client.calls) == 2  # 复合指纹已落库 → 两层跳过
        assert store.list_runs("job_pg5")[0].skipped is True


class _FakeCloudRegistry:
    """替换 default_registry 工厂：云 URI spec → 假 handle，指纹来自共享状态。"""

    def __init__(self, holder: dict[str, str]) -> None:
        self._holder = holder
        self.opened_specs: list[Any] = []
        self.last_handle: _FakePgHandle | None = None

    def open(self, spec: Any) -> _FakePgHandle:
        self.opened_specs.append(spec)
        handle = _FakePgHandle(self._holder)
        self.last_handle = handle
        return handle


class TestCloudChangeAware:
    """Step 57：s3:// gs:// az:// 云文件源变更感知（content_fingerprint 快速失效层）。"""

    def test_cloud_unchanged_skips_and_change_resumes(
        self, store: SchedulerStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datasentry.scheduler.core import LocalScanExecutor
        from datasentry_core.connectors import DataSourceType

        holder = {"fp": "cloud-hash-1", "stats": "schema-hash|2"}
        registry = _FakeCloudRegistry(holder)
        monkeypatch.setattr("datasentry_core.connectors.default_registry", lambda: registry)
        client = _PgScanClient(holder)
        clock = _Clock(T0)
        scheduler = Scheduler(
            store=store,
            executor=LocalScanExecutor(client_factory=lambda _project: client),
            clock=clock,
        )
        store.create_job(_cloud_job("job_s3", next_at=T0 - timedelta(minutes=1), cron="* * * * *"))

        scheduler.tick()
        assert client.calls == [("s3://test-bucket/orders.csv", None)]
        spec = registry.opened_specs[0]
        assert spec.source_type == DataSourceType.CSV
        assert spec.path == "s3://test-bucket/orders.csv"

        clock.advance(minutes=1)
        scheduler.tick()
        assert client.calls == [("s3://test-bucket/orders.csv", None)]
        runs = store.list_runs("job_s3")
        assert runs[0].skipped is True
        stored = json.loads(runs[0].file_hash)
        assert stored["stats"] == _pg_stats("schema-hash|2")
        assert stored["content"] == "cloud-hash-1"
        assert json.loads(runs[0].summary or "{}")["skipped"] is True

        holder["fp"] = "cloud-hash-2"
        clock.advance(minutes=1)
        scheduler.tick()
        assert len(client.calls) == 2
        runs = store.list_runs("job_s3")
        assert runs[0].skipped is False
        assert json.loads(runs[0].file_hash)["content"] == "cloud-hash-2"
        assert runs[0].file_hash != runs[1].file_hash

    def test_cloud_unreachable_falls_back_to_full_run(
        self, store: SchedulerStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _BrokenRegistry:
            def open(self, spec: Any) -> Any:
                raise RuntimeError("s3 unreachable")

        monkeypatch.setattr("datasentry_core.connectors.default_registry", _BrokenRegistry)
        executor = _FakeExecutor()
        clock = _Clock(T0)
        scheduler = Scheduler(store=store, executor=executor, clock=clock)
        store.create_job(_cloud_job("job_s3b", next_at=T0 - timedelta(minutes=1)))
        scheduler.tick()
        assert executor.calls == ["s3://test-bucket/orders.csv"]
        run = store.list_runs("job_s3b")[0]
        assert run.skipped is False

    def test_cloud_unsupported_suffix_falls_back(self, store: SchedulerStore) -> None:
        """缺后缀/未知后缀：指纹返回 None，正常执行（不跳过也不误判）。"""
        executor = _FakeExecutor()
        scheduler = Scheduler(store=store, executor=executor, clock=_Clock(T0))
        job = _cloud_job("job_s3c", next_at=T0 - timedelta(minutes=1))
        job.command = JobCommand(
            project=job.command.project,
            path="s3://test-bucket/orders",
        )
        store.create_job(job)
        scheduler.tick()
        assert executor.calls == ["s3://test-bucket/orders"]
        assert store.list_runs("job_s3c")[0].skipped is False

    def test_cloud_fingerprint_handle_closed(
        self, store: SchedulerStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        holder = {"fp": "x"}
        registry = _FakeCloudRegistry(holder)
        monkeypatch.setattr("datasentry_core.connectors.default_registry", lambda: registry)
        executor = _FakeExecutor()
        scheduler = Scheduler(store=store, executor=executor, clock=_Clock(T0))
        store.create_job(_cloud_job("job_s3d", next_at=T0 - timedelta(minutes=1)))
        scheduler.tick()
        assert len(registry.opened_specs) == 1
        assert registry.last_handle is not None
        assert registry.last_handle.closed is True  # 指纹句柄用完即关
        runs = store.list_runs("job_s3d")
        assert runs[0].skipped is False


class TestReportPush:
    """Step 70（ADR-070）：export_report 任务扫描后导出 HTML 报告并随 webhook 推送。"""

    def _spy_webhook(self) -> tuple[httpx.Client, list[dict[str, Any]]]:
        payloads: list[dict[str, Any]] = []

        def transport(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        client = httpx.Client(transport=httpx.MockTransport(transport))
        original_post = client.post

        def spy_post(url: str, json: Any | None = None, **kwargs: Any) -> Any:
            payloads.append(json or {})
            return original_post(url, json=json, **kwargs)

        client.post = spy_post  # type: ignore[method-assign]
        return client, payloads

    def _client_factory(self) -> Any:
        from datasentry import DataSentry

        return lambda project: DataSentry(project=project)

    def test_export_report_writes_html_and_webhook(self, tmp_path: Path) -> None:
        data = tmp_path / "data.csv"
        data.write_text("a,b\n1,2\n", encoding="utf-8")
        client, payloads = self._spy_webhook()
        store = SchedulerStore(tmp_path / "sched.db")
        job = _job(
            job_id="job_rpt",
            webhook_url="https://hook/x",
            next_at=T0 - timedelta(minutes=1),
        )
        job.project = str(tmp_path)
        job.command = JobCommand(project=str(tmp_path), path=str(data), export_report=True)
        store.create_job(job)
        sched = Scheduler(
            store=store,
            executor=LocalScanExecutor(client_factory=self._client_factory()),
            notifier=WebhookNotifier(lambda: client),
            clock=_Clock(T0),
        )
        sched.tick()
        run = store.list_runs("job_rpt")[0]
        assert run.status == RunStatus.COMPLETED
        assert run.scan_run_id is not None
        report = tmp_path / ".datasentry" / "reports" / f"{run.scan_run_id}.html"
        assert report.is_file()
        assert report.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
        body = payloads[0]
        assert body["report_path"] == f".datasentry/reports/{run.scan_run_id}.html"
        assert body["report_size"] == report.stat().st_size

    def test_export_failure_keeps_run_completed(self, tmp_path: Path) -> None:
        data = tmp_path / "data.csv"
        data.write_text("a,b\n1,2\n", encoding="utf-8")
        client, payloads = self._spy_webhook()

        class _BrokenExporter:
            def __init__(self, project: str) -> None:
                from datasentry import DataSentry

                self._inner = DataSentry(project=project)

            def scan_file(self, *a: Any, **kw: Any) -> Any:
                return self._inner.scan_file(*a, **kw)

            def close(self) -> None:
                self._inner.close()

            def export_report(self, _run_id: str) -> Any:
                raise RuntimeError("export boom")

        store = SchedulerStore(tmp_path / "sched.db")
        job = _job(
            job_id="job_rptf",
            webhook_url="https://hook/x",
            next_at=T0 - timedelta(minutes=1),
        )
        job.project = str(tmp_path)
        job.command = JobCommand(project=str(tmp_path), path=str(data), export_report=True)
        store.create_job(job)
        sched = Scheduler(
            store=store,
            executor=LocalScanExecutor(client_factory=lambda _p: _BrokenExporter(_p)),
            notifier=WebhookNotifier(lambda: client),
            clock=_Clock(T0),
        )
        sched.tick()
        run = store.list_runs("job_rptf")[0]
        assert run.status == RunStatus.COMPLETED  # 导出失败不影响 run 状态
        assert not list((tmp_path / ".datasentry" / "reports").glob("*.html"))
        body = payloads[0]
        assert "report_path" not in body
        assert "report_size" not in body

    def test_no_report_keys_without_export_flag(self, tmp_path: Path) -> None:
        client, payloads = self._spy_webhook()
        store = SchedulerStore(tmp_path / "sched.db")
        store.create_job(_job(job_id="job_plain", webhook_url="https://hook/x"))
        sched = Scheduler(
            store=store, executor=_FakeExecutor(), notifier=WebhookNotifier(lambda: client)
        )
        sched.tick()
        assert "report_path" not in payloads[0]
        assert "report_size" not in payloads[0]

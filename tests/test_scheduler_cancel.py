"""V22（Step 114/116，ADR-114/116）：调度端 cancel 语义闭环测试。

本地慢执行器（SleepExecutor）+ 线程触发（trigger 同步阻塞，cancel
从主线程进）证明：cancel 后执行器结果最终到达也被丢弃（run 保持
cancelled）。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from datasentry.cli import main
from datasentry.scheduler.core import Scheduler
from datasentry.scheduler.models import JobCommand, JobResult, RunStatus
from datasentry.scheduler.store import SchedulerStore
from datasentry_core.storage.paths import project_db_path


class SleepExecutor:
    """慢执行器：sleep 后返回成功结果（模拟长时间扫描）。"""

    def __init__(self, delay: float = 1.0) -> None:
        self.delay = delay

    def execute(self, command: JobCommand) -> JobResult:
        time.sleep(self.delay)
        return JobResult(scan_run_id="scan_sleep", total_issues=0, quality_score=100.0)


def _make_job(tmp_path: Path) -> tuple[Path, str]:
    csv = tmp_path / "orders.csv"
    csv.write_text("id,amount\n1,10\n2,20\n")
    args = [
        "--project",
        str(tmp_path),
        "job",
        "create",
        "cancel job",
        str(csv),
        "--cron",
        "0 0 1 1 *",
    ]
    assert main(args) == 0
    store = SchedulerStore(project_db_path(tmp_path))
    return tmp_path, store.list_jobs()[0].job_id


def _trigger_async(tmp_path: Path, job_id: str) -> threading.Thread:
    """线程内触发（Scheduler+SleepExecutor：trigger 同步阻塞 1s，主线程
    留出 cancel 时机）。CLI job trigger 用真扫描（毫秒级）无法做稳定
    竞态——这里走真 store + 慢执行器，CLI `job cancel` 仍从真路径进。
    """

    def _run() -> None:
        store = SchedulerStore(project_db_path(tmp_path))
        Scheduler(store=store, executor=SleepExecutor(delay=1.0)).trigger(job_id)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    time.sleep(0.2)  # 保证 claim 完成、run 进入 running
    return thread


def _running_run(tmp_path: Path, job_id: str) -> str:
    store = SchedulerStore(project_db_path(tmp_path))
    run = store.get_run(store.list_runs(job_id)[0].run_id)
    assert run is not None and run.status == RunStatus.RUNNING
    return run.run_id


class TestCancelLocalV22:
    """Step 114（ADR-114）：本地 cancel 语义。"""

    def test_cancel_running_run(self, tmp_path: Path) -> None:
        _ws, job_id = _make_job(tmp_path)
        thread = _trigger_async(tmp_path, job_id)
        run_id = _running_run(tmp_path, job_id)
        code = main(["--project", str(tmp_path), "job", "cancel", job_id])
        assert code == 0
        store = SchedulerStore(project_db_path(tmp_path))
        run = store.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.CANCELLED
        assert run.error == "cancelled by user"
        job = store.get_job(job_id)
        assert job is not None and job.status.value == "idle"
        thread.join(timeout=5)

    def test_cancel_not_running_config_error(self, tmp_path: Path) -> None:
        _ws, job_id = _make_job(tmp_path)
        code = main(["--project", str(tmp_path), "job", "cancel", job_id])
        assert code == 2
        assert code != 0

    def test_result_discarded_after_cancel(self, tmp_path: Path) -> None:
        """执行器结果最终到达时被丢弃：run 保持 cancelled，不复活 completed。"""
        _ws, job_id = _make_job(tmp_path)
        thread = _trigger_async(tmp_path, job_id)
        run_id = _running_run(tmp_path, job_id)
        assert main(["--project", str(tmp_path), "job", "cancel", job_id]) == 0
        thread.join(timeout=10)  # SleepExecutor 跑完后 finish_run 必须丢弃
        store = SchedulerStore(project_db_path(tmp_path))
        run = store.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.CANCELLED
        assert run.scan_run_id is None  # 结果未落

    def test_cancel_after_completion_noop(self, tmp_path: Path) -> None:
        _ws, job_id = _make_job(tmp_path)
        store = SchedulerStore(project_db_path(tmp_path))
        Scheduler(store=store, executor=SleepExecutor(delay=0.01)).trigger(job_id)
        code = main(["--project", str(tmp_path), "job", "cancel", job_id])
        assert code == 2

    def test_cancel_json_envelope(self, tmp_path: Path) -> None:
        _ws, job_id = _make_job(tmp_path)
        thread = _trigger_async(tmp_path, job_id)
        run_id = _running_run(tmp_path, job_id)
        assert (
            main(
                [
                    "--project",
                    str(tmp_path),
                    "--format",
                    "json",
                    "job",
                    "cancel",
                    job_id,
                ]
            )
            == 0
        )
        store = SchedulerStore(project_db_path(tmp_path))
        assert store.get_run(run_id) is not None
        assert store.get_run(run_id).status == RunStatus.CANCELLED
        thread.join(timeout=5)

    def test_recover_interrupted_untouched(self, tmp_path: Path) -> None:
        """重启恢复只处理 running——cancelled run 不受影响。"""
        _ws, job_id = _make_job(tmp_path)
        thread = _trigger_async(tmp_path, job_id)
        run_id = _running_run(tmp_path, job_id)
        assert main(["--project", str(tmp_path), "job", "cancel", job_id]) == 0
        store = SchedulerStore(project_db_path(tmp_path))
        store.recover_interrupted()
        run = store.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.CANCELLED
        thread.join(timeout=5)

    def test_scheduler_api_cancel(self, tmp_path: Path) -> None:
        """Scheduler.cancel 直接 API：返回 run_id；未运行返回 None。"""
        _ws, job_id = _make_job(tmp_path)
        store = SchedulerStore(project_db_path(tmp_path))
        scheduler = Scheduler(store=store, executor=SleepExecutor(delay=0.5))
        thread = threading.Thread(target=scheduler.trigger, args=(job_id,), daemon=True)
        thread.start()
        time.sleep(0.2)
        run_id = _running_run(tmp_path, job_id)
        assert scheduler.cancel(job_id) == run_id
        assert scheduler.cancel(job_id) is None
        thread.join(timeout=5)

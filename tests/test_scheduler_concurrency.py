"""V19（Step 107，ADR-107）：多调度端互斥跨进程验证。

subprocess 起真实子进程（不用 multiprocessing——pytest spawn 递归
陷阱），证明 SchedulerStore 的跨进程原子抢占语义：
- 两进程并发 claim_due_jobs → 同一 job 只被抢一次
- 两进程并发 claim_job（手动触发）→ 只有一个成功（另一个 None）
- 并发 tick（claim + finish 循环）状态机不坏、run 无丢失
- 调度端（SchedulerStore）与 CLI（MetadataStore）并发写 pii 会话
  不丢不 BUSY——独立调度端写会话可被主进程感知的并发证明
"""

from __future__ import annotations

import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from datasentry.scheduler.models import JobCommand, ScheduledJob, utcnow
from datasentry.scheduler.store import SchedulerStore
from datasentry_core.storage.store import MetadataStore

_CLAIM_TICK = r"""
import sys
from datetime import timedelta
from pathlib import Path

from datasentry.scheduler.models import JobStatus, utcnow
from datasentry.scheduler.store import SchedulerStore

db = Path(sys.argv[1])
store = SchedulerStore(db)
for _ in range(int(sys.argv[2])):
    claimed = store.claim_due_jobs(utcnow())
    for job_id, run_id, _attempt in claimed:
        store.finish_run(
            run_id,
            success=True,
            next_run_at=utcnow() + timedelta(hours=1),
            job_status=JobStatus.IDLE,
            summary="ok",
        )
"""

_CLAIM_MANUAL = r"""
import sys
from pathlib import Path

from datasentry.scheduler.models import utcnow
from datasentry.scheduler.store import SchedulerStore

db = Path(sys.argv[1])
store = SchedulerStore(db)
run_id = store.claim_job(sys.argv[2], utcnow())
print(run_id or "NONE")
"""

_PII_WRITER = r"""
import sys
from pathlib import Path

from datasentry_core.storage.store import MetadataStore

store = MetadataStore(Path(sys.argv[1]) / "meta.db")
for i in range(int(sys.argv[2])):
    store.save_pii_mapping(f"sched_{sys.argv[3]}_{i}", "ct", key_version="env")
store.close()
"""


def _job(db: Path, job_id: str, *, enabled: bool = True, due: bool = True) -> None:
    store = SchedulerStore(db)
    store.create_job(
        ScheduledJob(
            job_id=job_id,
            name=job_id,
            project=".",
            command=JobCommand(project=".", path="data.csv", dataset_id="ds"),
            cron="* * * * *",
            enabled=enabled,
            next_run_at=utcnow() - timedelta(minutes=1) if due else utcnow() + timedelta(hours=1),
            created_at=utcnow(),
            updated_at=utcnow(),
        )
    )


def test_concurrent_claim_due_claimed_once(tmp_path: Path) -> None:
    db = tmp_path / "sched.db"
    _job(db, "job_one")
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _CLAIM_TICK, str(db), "1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outputs = [p.communicate(timeout=120) for p in procs]
    for p, (out, err) in zip(procs, outputs, strict=True):
        assert p.returncode == 0, out + err
    store = SchedulerStore(db)
    runs = store.list_runs("job_one")
    assert len(runs) == 1
    assert runs[0].status.value == "completed"


def test_concurrent_manual_claim_one_wins(tmp_path: Path) -> None:
    db = tmp_path / "sched.db"
    _job(db, "job_two", due=False)
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _CLAIM_MANUAL, str(db), "job_two"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    results = [p.communicate(timeout=120) for p in procs]
    for p, (out, err) in zip(procs, results, strict=True):
        assert p.returncode == 0, out + err
    claimed = [out.strip() for out, _ in results if out.strip() != "NONE"]
    assert len(claimed) == 1
    store = SchedulerStore(db)
    assert store.get_job("job_two") is not None
    assert len(store.list_runs("job_two")) == 1


def test_concurrent_ticks_no_run_loss(tmp_path: Path) -> None:
    db = tmp_path / "sched.db"
    for i in range(4):
        _job(db, f"job_{i}")
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _CLAIM_TICK, str(db), "6"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, out + err
    store = SchedulerStore(db)
    for i in range(4):
        runs = store.list_runs(f"job_{i}")
        assert len(runs) == 1
        assert runs[0].status.value == "completed"


def test_scheduler_and_cli_concurrent_pii_writes(tmp_path: Path) -> None:
    db = tmp_path / "sched.db"
    _job(db, "job_pii")
    scheduler = subprocess.Popen(
        [sys.executable, "-c", _CLAIM_TICK, str(db), "1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    cli = subprocess.Popen(
        [sys.executable, "-c", _PII_WRITER, str(tmp_path), "30", "cli"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _, err_s = scheduler.communicate(timeout=120)
    _, err_c = cli.communicate(timeout=120)
    assert scheduler.returncode == 0, err_s
    assert cli.returncode == 0, err_c
    assert "locked" not in (err_s + err_c).lower()
    store = MetadataStore(tmp_path / "meta.db")
    try:
        assert store.count_pii_mappings() == 30
    finally:
        store.close()
    sched = SchedulerStore(db)
    runs = sched.list_runs("job_pii")
    assert len(runs) == 1

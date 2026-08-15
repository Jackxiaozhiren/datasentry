"""Step 51（V2-D）调度任务存储：SQLite 持久化 + 原子抢占（ADR-051）。

并发语义：worker 线程与 API 触发线程共享同一库，所有写操作在
`BEGIN IMMEDIATE` 事务内完成（SQLite 单写者锁 + 忙等待），
`claim_due_jobs` / `claim_job` 以「条件更新」原子抢占任务，
保证同一任务同一时刻只有一个执行者。
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from datasentry.scheduler.models import (
    JobCommand,
    JobRun,
    JobStatus,
    RunStatus,
    ScheduledJob,
    from_iso,
    iso,
    utcnow,
)

RETRY_BACKOFF = timedelta(seconds=60)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _ensure_schema(db_path: Path) -> None:
    """建调度表（复用 core schema 迁移，保证与元数据库 DDL 同源）。"""
    from datasentry_core.storage import schema as core_schema

    with closing(sqlite3.connect(db_path)) as conn:
        core_schema.migrate(conn)


def _row_to_job(row: sqlite3.Row) -> ScheduledJob:
    return ScheduledJob(
        job_id=row["job_id"],
        name=row["name"],
        project=row["project"],
        command=JobCommand.from_storage(row["command"]),
        cron=row["cron"],
        enabled=bool(row["enabled"]),
        retry_attempts=row["retry_attempts"],
        webhook_url=row["webhook_url"],
        gate_quality_min=row["gate_quality_min"],
        export_report=bool(row["export_report"]),
        status=JobStatus(row["status"]),
        next_run_at=from_iso(row["next_run_at"]),
        last_run_at=from_iso(row["last_run_at"]) if row["last_run_at"] else None,
        last_result=row["last_result"],
        created_at=from_iso(row["created_at"]),
        updated_at=from_iso(row["updated_at"]),
    )


def _row_to_run(row: sqlite3.Row) -> JobRun:
    return JobRun(
        run_id=row["run_id"],
        job_id=row["job_id"],
        status=RunStatus(row["status"]),
        attempt=row["attempt"],
        started_at=from_iso(row["started_at"]),
        finished_at=from_iso(row["finished_at"]) if row["finished_at"] else None,
        scan_run_id=row["scan_run_id"],
        summary=row["summary"],
        error=row["error"],
        webhook_at=from_iso(row["webhook_at"]) if row["webhook_at"] else None,
        file_hash=row["file_hash"],
        skipped=bool(row["skipped"]),
    )


class SchedulerStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        _ensure_schema(self._db_path)

    # ---- 任务 CRUD ------------------------------------------------------

    def create_job(self, job: ScheduledJob) -> None:
        with closing(_connect(self._db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO scheduled_jobs
                   (job_id, name, project, command, cron, enabled,
                    retry_attempts, webhook_url, gate_quality_min, export_report,
                    status, next_run_at, last_run_at, last_result, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job.job_id,
                    job.name,
                    job.project,
                    job.command.to_storage(),
                    job.cron,
                    int(job.enabled),
                    job.retry_attempts,
                    job.webhook_url,
                    job.gate_quality_min,
                    int(job.export_report),
                    job.status.value,
                    iso(job.next_run_at),
                    iso(job.last_run_at) if job.last_run_at else None,
                    job.last_result,
                    iso(job.created_at),
                    iso(job.updated_at),
                ),
            )
            conn.execute("COMMIT")

    def get_job(self, job_id: str) -> ScheduledJob | None:
        with closing(_connect(self._db_path)) as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return _row_to_job(row) if row else None

    def list_jobs(self) -> list[ScheduledJob]:
        with closing(_connect(self._db_path)) as conn:
            rows = conn.execute("SELECT * FROM scheduled_jobs ORDER BY created_at").fetchall()
        return [_row_to_job(r) for r in rows]

    def update_job(self, job_id: str, **changes: object) -> bool:
        """部分更新：键为列名（enabled/cron/retry_attempts/webhook_url/status/
        next_run_at/last_run_at/last_result），datetime 值自动转 ISO。

        webhook_url 传 None 表示置空；不传表示不变。
        """
        mapping: dict[str, str] = {
            "enabled": "enabled",
            "cron": "cron",
            "retry_attempts": "retry_attempts",
            "webhook_url": "webhook_url",
            "gate_quality_min": "gate_quality_min",
            "status": "status",
            "next_run_at": "next_run_at",
            "last_run_at": "last_run_at",
            "last_result": "last_result",
        }
        sets: list[str] = []
        values: list[object] = []
        for key, column in mapping.items():
            if key not in changes:
                continue
            value = changes[key]
            if key in ("next_run_at", "last_run_at"):
                value = iso(value)  # type: ignore[arg-type]
            elif key == "status":
                value = value.value if isinstance(value, JobStatus) else str(value)
            elif key == "enabled":
                value = 1 if value else 0
            sets.append(f"{column} = ?")
            values.append(value)
        if not sets:
            return True
        sets.append("updated_at = ?")
        values.append(iso(utcnow()))
        values.append(job_id)
        with closing(_connect(self._db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                f"UPDATE scheduled_jobs SET {', '.join(sets)} WHERE job_id = ?", values
            )
            conn.execute("COMMIT")
        return cur.rowcount > 0

    def delete_job(self, job_id: str) -> bool:
        with closing(_connect(self._db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("DELETE FROM scheduled_jobs WHERE job_id = ?", (job_id,))
            conn.execute("COMMIT")
        return cur.rowcount > 0

    # ---- 抢占与执行记录 --------------------------------------------------

    def claim_due_jobs(self, now: object) -> list[tuple[str, str, int]]:
        """原子抢占所有到期任务：置 running + 创建 run，返回 (job_id, run_id, attempt)。

        SQLite 单写者锁保证并发下同一任务只能被抢占一次（互斥）。
        """
        now_iso = iso(now)  # type: ignore[arg-type]
        claimed: list[tuple[str, str, int]] = []
        with closing(_connect(self._db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """SELECT job_id FROM scheduled_jobs
                   WHERE enabled = 1 AND status != 'running'
                     AND next_run_at <= ?""",
                (now_iso,),
            ).fetchall()
            for row in rows:
                job_id: str = row["job_id"]
                run_id = f"run_{job_id}_{utcnow().strftime('%Y%m%d%H%M%S')}_" + uuid.uuid4().hex[:6]
                attempt = (
                    1
                    + conn.execute(
                        "SELECT COUNT(*) AS n FROM job_runs WHERE job_id = ?",
                        (job_id,),
                    ).fetchone()["n"]
                )
                conn.execute(
                    "UPDATE scheduled_jobs SET status = 'running', updated_at = ? WHERE job_id = ?",
                    (iso(utcnow()), job_id),
                )
                conn.execute(
                    """INSERT INTO job_runs (run_id, job_id, status, attempt, started_at)
                       VALUES (?,?,?,?,?)""",
                    (run_id, job_id, "running", attempt, now_iso),
                )
                claimed.append((job_id, run_id, attempt))
            conn.execute("COMMIT")
        return claimed

    def claim_job(self, job_id: str, now: object) -> str | None:
        """手动触发：立即抢占单个任务；已在 running 返回 None（409）。"""
        now_iso = iso(now)  # type: ignore[arg-type]
        with closing(_connect(self._db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM scheduled_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None or row["status"] == "running":
                conn.execute("COMMIT")
                return None
            run_id = f"run_{job_id}_{utcnow().strftime('%Y%m%d%H%M%S')}_" + uuid.uuid4().hex[:6]
            attempt = (
                1
                + conn.execute(
                    "SELECT COUNT(*) AS n FROM job_runs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()["n"]
            )
            conn.execute(
                "UPDATE scheduled_jobs SET status = 'running', updated_at = ? WHERE job_id = ?",
                (iso(utcnow()), job_id),
            )
            conn.execute(
                """INSERT INTO job_runs (run_id, job_id, status, attempt, started_at)
                   VALUES (?,?,?,?,?)""",
                (run_id, job_id, "running", attempt, now_iso),
            )
            conn.execute("COMMIT")
        return run_id

    def finish_run(
        self,
        run_id: str,
        *,
        success: bool,
        scan_run_id: str | None = None,
        summary: str | None = None,
        error: str | None = None,
        next_run_at: object,
        job_status: JobStatus,
        file_hash: str | None = None,
        skipped: bool = False,
    ) -> None:
        """落执行结果并推进任务状态（成功 → idle + 下次计划；失败 → 重试或死信）。"""
        with closing(_connect(self._db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT job_id, status FROM job_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return
            if row["status"] == "cancelled":
                # V22（ADR-114）：run 已被取消——执行器结果作废，丢弃（job 状态
                # 也不动）；未提交自动回滚，不影响既有数据。
                return
            conn.execute(
                """UPDATE job_runs
                   SET status = ?, finished_at = ?, scan_run_id = ?, summary = ?,
                       error = ?, file_hash = ?, skipped = ?
                   WHERE run_id = ?""",
                (
                    "completed" if success else "failed",
                    iso(utcnow()),
                    scan_run_id,
                    summary,
                    error,
                    file_hash,
                    1 if skipped else 0,
                    run_id,
                ),
            )
            conn.execute(
                """UPDATE scheduled_jobs
                   SET status = ?, next_run_at = ?, last_run_at = ?,
                       last_result = ?, updated_at = ?
                   WHERE job_id = ?""",
                (
                    job_status.value,
                    iso(next_run_at),  # type: ignore[arg-type]
                    iso(utcnow()),
                    summary if success else error,
                    iso(utcnow()),
                    row["job_id"],
                ),
            )
            conn.execute("COMMIT")

    def prune_runs(self, max_per_job: int = 100) -> int:
        """裁剪超过保留上限的最旧运行历史（V13，ADR-088）。

        保留每个 job 最近 max_per_job 条 run；返回删除行数。
        """
        with closing(_connect(self._db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """DELETE FROM job_runs
                   WHERE run_id IN (
                       SELECT run_id FROM (
                           SELECT run_id,
                                  ROW_NUMBER() OVER (
                                      PARTITION BY job_id ORDER BY started_at DESC, run_id DESC
                                  ) AS rn
                           FROM job_runs
                       ) ranked
                       WHERE ranked.rn > ?
                   )""",
                (max_per_job,),
            )
            conn.execute("COMMIT")
        return cur.rowcount

    def recover_interrupted(self) -> None:
        """重启恢复：running 任务 → idle（run 标记 failed/interrupted），下次 tick 重调度。"""
        with closing(_connect(self._db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT run_id FROM job_runs WHERE status = 'running'").fetchall()
            for row in rows:
                conn.execute(
                    """UPDATE job_runs SET status = 'failed', finished_at = ?,
                       error = 'interrupted by restart'
                       WHERE run_id = ?""",
                    (iso(utcnow()), row["run_id"]),
                )
            conn.execute(
                """UPDATE scheduled_jobs SET status = 'idle', updated_at = ?
                   WHERE status = 'running'""",
                (iso(utcnow()),),
            )
            conn.execute("COMMIT")

    def cancel_run(self, job_id: str, *, error: str = "cancelled by user") -> str | None:
        """取消正在运行的任务（V22，ADR-114）：run → cancelled，job → idle。

        事务内原子判定：无 running run 返回 None（无操作）。执行器最终
        结果到达时由 `finish_run` 的 cancelled 分支丢弃——竞态靠单事务
        串行化，无锁。
        """
        with closing(_connect(self._db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT run_id FROM job_runs WHERE job_id = ? AND status = 'running'",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """UPDATE job_runs SET status = 'cancelled', finished_at = ?,
                   error = ? WHERE run_id = ?""",
                (iso(utcnow()), error, row["run_id"]),
            )
            conn.execute(
                """UPDATE scheduled_jobs SET status = 'idle', updated_at = ?
                   WHERE job_id = ?""",
                (iso(utcnow()), job_id),
            )
            conn.execute("COMMIT")
        return str(row["run_id"])

    # ---- 查询 ------------------------------------------------------------

    def list_runs(self, job_id: str, limit: int = 20) -> list[JobRun]:
        with closing(_connect(self._db_path)) as conn:
            rows = conn.execute(
                "SELECT * FROM job_runs WHERE job_id = ? ORDER BY started_at DESC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        return [_row_to_run(r) for r in rows]

    def get_run(self, run_id: str) -> JobRun | None:
        with closing(_connect(self._db_path)) as conn:
            row = conn.execute("SELECT * FROM job_runs WHERE run_id = ?", (run_id,)).fetchone()
        return _row_to_run(row) if row else None

    def last_successful_hash(self, job_id: str) -> str | None:
        """最近一次成功执行（未跳过）的 file_hash；无则 None（Step 53）。"""
        with closing(_connect(self._db_path)) as conn:
            row = conn.execute(
                """SELECT file_hash FROM job_runs
                   WHERE job_id = ? AND status = 'completed' AND skipped = 0
                     AND file_hash IS NOT NULL
                   ORDER BY started_at DESC LIMIT 1""",
                (job_id,),
            ).fetchone()
        return row["file_hash"] if row else None

    def save_webhook_at(self, run_id: str, at: object) -> None:
        with closing(_connect(self._db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE job_runs SET webhook_at = ? WHERE run_id = ?",
                (iso(at), run_id),  # type: ignore[arg-type]
            )
            conn.execute("COMMIT")

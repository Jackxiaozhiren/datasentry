"""元数据存储（Step 9）：SQLite 读写，MVP 闭环 = scan_runs/detector_runs/issues/evidence。

约定：
- 单连接 + 写锁（SDK/CLI 单进程场景）；WAL 模式
- 扫描结果整体单事务落库（save_scan）；JSON 复杂对象列、标量独立列（草案说明 1）
- 时间戳 ISO 8601 UTC 文本
- 占位表（contracts/jobs/feedback 等）建表无写路径（草案说明 5）
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from datasentry_core.models.enums import (
    RepairOperation,
    RepairProposalStatus,
    RepairRunStatus,
    Severity,
)
from datasentry_core.models.evidence import Evidence, utcnow
from datasentry_core.models.issue import Issue
from datasentry_core.models.repair import RepairProposal, RepairRun
from datasentry_core.models.scan import DetectorRun, ScanRun
from datasentry_core.storage.paths import project_db_path
from datasentry_core.storage.schema import migrate

_LOCAL_PROJECT_ID = "local"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class MetadataStore:
    """项目元数据库门面。open 即迁移；close 后不可用。"""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        migrate(self._conn)

    @classmethod
    def for_workspace(cls, workspace: Path) -> MetadataStore:
        """ADR-010：项目数据 → <workspace>/.datasentry/metadata.db。"""
        return cls(project_db_path(workspace))

    @property
    def db_path(self) -> Path:
        return self._db_path

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- 写入 ----------------------------------------------------------

    def save_scan(
        self,
        scan_run: ScanRun,
        detector_runs: list[DetectorRun],
        issues: list[Issue],
    ) -> None:
        """扫描结果整体单事务：scan_runs + detector_runs + issues + evidence。

        数据集不存在则注册（固定 local 项目占位，外键完整）。
        """
        with self._lock, self._conn:
            self._ensure_project()
            self._upsert_dataset(scan_run.dataset_id, scan_run.dataset_id, scan_run.started_at)
            self._conn.execute(
                """
                INSERT INTO scan_runs (
                    id, dataset_id, status, config, fingerprint, quality_score,
                    quality_breakdown, issues_count, started_at, finished_at,
                    error, reproducibility, llm_usage
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_run.id,
                    scan_run.dataset_id,
                    scan_run.status,
                    scan_run.config.model_dump_json(),
                    scan_run.fingerprint.model_dump_json(),
                    (
                        scan_run.quality_score.overall
                        if scan_run.quality_score is not None
                        else None
                    ),
                    (
                        scan_run.quality_score.model_dump_json()
                        if scan_run.quality_score is not None
                        else None
                    ),
                    json.dumps({k.value: v for k, v in scan_run.issues_count.items()}),
                    _iso(scan_run.started_at),
                    _iso(scan_run.finished_at) if scan_run.finished_at else None,
                    scan_run.error,
                    scan_run.reproducibility.model_dump_json(),
                    scan_run.llm_usage.model_dump_json(),
                ),
            )
            for run in detector_runs:
                self._conn.execute(
                    """
                    INSERT INTO detector_runs (
                        id, scan_run_id, detector_id, detector_version, status,
                        rows_scanned, duration_ms, issues_candidates, sampling, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.id,
                        run.scan_run_id,
                        run.detector_id,
                        run.detector_version,
                        run.status,
                        run.rows_scanned,
                        run.duration_ms,
                        run.issues_candidates,
                        run.sampling.model_dump_json() if run.sampling else None,
                        run.error,
                    ),
                )
            for issue in issues:
                self._conn.execute(
                    """
                    INSERT INTO issues (
                        id, scan_run_id, issue_type, title, description, dataset_id,
                        table_name, columns, quality_dimensions, severity, confidence,
                        priority_score, false_positive_risk, affected_count, affected_ratio,
                        affected_row_ids, detector_ids, ai_explanation, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        issue.id,
                        issue.scan_run_id,
                        issue.issue_type,
                        issue.title,
                        issue.description,
                        issue.dataset_id,
                        issue.table_name,
                        json.dumps(issue.columns),
                        json.dumps([d.value for d in issue.quality_dimensions]),
                        issue.severity.value,
                        issue.confidence,
                        issue.priority_score,
                        issue.false_positive_risk.value,
                        issue.affected_count,
                        issue.affected_ratio,
                        (
                            json.dumps(issue.affected_row_ids)
                            if issue.affected_row_ids is not None
                            else None
                        ),
                        json.dumps(issue.detector_ids),
                        issue.ai_explanation.model_dump_json() if issue.ai_explanation else None,
                        issue.status.value,
                        _iso(issue.created_at),
                    ),
                )
                for evidence in issue.evidence:
                    self._conn.execute(
                        """
                        INSERT INTO evidence (
                            evidence_id, issue_id, evidence_type, detector_id,
                            detector_version, description, data, confidence,
                            provenance, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            evidence.evidence_id,
                            issue.id,
                            evidence.evidence_type.value,
                            evidence.detector_id,
                            evidence.detector_version,
                            evidence.description,
                            json.dumps(evidence.data),
                            evidence.confidence,
                            (
                                evidence.provenance.model_dump_json()
                                if evidence.provenance
                                else None
                            ),
                            _iso(evidence.created_at),
                        ),
                    )

    # ---- 查询 ----------------------------------------------------------

    def list_scan_runs(self, dataset_id: str | None = None) -> list[ScanRun]:
        with self._lock:
            if dataset_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM scan_runs ORDER BY started_at DESC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM scan_runs WHERE dataset_id = ? ORDER BY started_at DESC",
                    (dataset_id,),
                ).fetchall()
        return [self._scan_run_from_row(r) for r in rows]

    def get_scan_run(self, scan_run_id: str) -> ScanRun | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM scan_runs WHERE id = ?", (scan_run_id,)
            ).fetchone()
        return self._scan_run_from_row(row) if row else None

    def get_detector_runs(self, scan_run_id: str) -> list[DetectorRun]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM detector_runs WHERE scan_run_id = ? ORDER BY rowid",
                (scan_run_id,),
            ).fetchall()
        result: list[DetectorRun] = []
        for row in rows:
            d = dict(row)
            result.append(
                DetectorRun(
                    id=d["id"],
                    scan_run_id=d["scan_run_id"],
                    detector_id=d["detector_id"],
                    detector_version=d["detector_version"],
                    status=d["status"],
                    rows_scanned=d["rows_scanned"],
                    duration_ms=d["duration_ms"],
                    issues_candidates=d["issues_candidates"],
                    sampling=json.loads(d["sampling"]) if d["sampling"] else None,
                    error=d["error"],
                )
            )
        return result

    def get_issues(self, scan_run_id: str) -> list[Issue]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM issues WHERE scan_run_id = ? ORDER BY priority_score DESC",
                (scan_run_id,),
            ).fetchall()
        issues = [self._issue_from_row(r) for r in rows]
        evidence_map = self._load_evidence(issues)
        for issue in issues:
            issue.evidence = evidence_map.get(issue.id, [])
        return issues

    # ---- 修复持久化（Step 21，15 章） ------------------------------------

    def save_repair_proposal(self, proposal: RepairProposal) -> None:
        """保存修复提案（proposal 转正，repair_proposals 表移出占位）。"""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO repair_proposals (
                    proposal_id, issue_id, issue_type, operation, target_columns,
                    target_row_ids, parameters, rationale, evidence_ids, risk_level,
                    reversibility, estimated_rows_changed, preconditions, postconditions,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.proposal_id,
                    proposal.issue_id,
                    proposal.issue_type,
                    proposal.operation.value,
                    json.dumps(proposal.target_columns),
                    json.dumps(proposal.target_row_ids) if proposal.target_row_ids else None,
                    json.dumps(proposal.parameters),
                    proposal.rationale,
                    json.dumps(proposal.evidence_ids),
                    proposal.risk_level.value,
                    proposal.reversibility,
                    proposal.estimated_rows_changed,
                    json.dumps(proposal.preconditions),
                    json.dumps(proposal.postconditions),
                    proposal.status.value,
                    _iso(proposal.created_at),
                ),
            )

    def get_repair_proposal(self, proposal_id: str) -> RepairProposal | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM repair_proposals WHERE proposal_id = ?", (proposal_id,)
            ).fetchone()
        return self._repair_proposal_from_row(row) if row else None

    def save_repair_run(self, run: RepairRun) -> None:
        """保存一次修复执行（repair_runs 表转正）。"""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO repair_runs (
                    id, dataset_id, proposal_id, fingerprint_before, fingerprint_after,
                    operations, approved_by, approval_kind, approved_at, status,
                    rollback_artifact, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.dataset_id,
                    run.proposal_id,
                    run.fingerprint_before,
                    run.fingerprint_after,
                    json.dumps([op.model_dump() for op in run.operations], default=str),
                    run.approved_by,
                    run.approval_kind,
                    _iso(run.approved_at) if run.approved_at else None,
                    run.status.value,
                    run.rollback_artifact,
                    _iso(run.created_at),
                ),
            )

    def get_repair_run(self, run_id: str) -> RepairRun | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM repair_runs WHERE id = ?", (run_id,)).fetchone()
        return self._repair_run_from_row(row) if row else None

    def list_repair_runs(self) -> list[RepairRun]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM repair_runs ORDER BY rowid DESC", ()
            ).fetchall()
        return [self._repair_run_from_row(r) for r in rows]

    def get_issue_by_id(self, issue_id: str) -> Issue | None:
        """跨扫描查找 Issue（附 evidence）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM issues WHERE id = ? ORDER BY priority_score DESC LIMIT 1",
                (issue_id,),
            ).fetchone()
        if row is None:
            return None
        issue = self._issue_from_row(row)
        issue.evidence = self._load_evidence([issue]).get(issue.id, [])
        return issue

    # ---- 内部 ----------------------------------------------------------

    def _ensure_project(self) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO projects (id, name, description, workspace_path, created_at)"
            " VALUES (?, 'local', '', '', ?)",
            (_LOCAL_PROJECT_ID, _iso(utcnow())),
        )

    def _upsert_dataset(self, dataset_id: str, name: str, created_at: datetime) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO datasets (id, project_id, name, created_at) VALUES (?, ?, ?, ?)",
            (dataset_id, _LOCAL_PROJECT_ID, name, _iso(created_at)),
        )

    def _load_evidence(self, issues: list[Issue]) -> dict[str, list[Evidence]]:
        if not issues:
            return {}
        placeholders = ",".join("?" * len(issues))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM evidence WHERE issue_id IN ({placeholders}) ORDER BY rowid",
                [i.id for i in issues],
            ).fetchall()
        result: dict[str, list[Evidence]] = {}
        for row in rows:
            d = dict(row)
            evidence = Evidence(
                evidence_id=d["evidence_id"],
                evidence_type=d["evidence_type"],
                detector_id=d["detector_id"],
                detector_version=d["detector_version"],
                description=d["description"],
                data=json.loads(d["data"]),
                confidence=d["confidence"],
                provenance=(json.loads(d["provenance"]) if d["provenance"] else None),
                created_at=datetime.fromisoformat(d["created_at"]),
            )
            result.setdefault(d["issue_id"], []).append(evidence)
        return result

    def _scan_run_from_row(self, row: sqlite3.Row) -> ScanRun:
        from datasentry_core.models.quality import QualityScore

        d = dict(row)
        return ScanRun(
            id=d["id"],
            dataset_id=d["dataset_id"],
            status=d["status"],
            config=json.loads(d["config"]),
            fingerprint=json.loads(d["fingerprint"]),
            quality_score=(
                QualityScore.model_validate(json.loads(d["quality_breakdown"]))
                if d["quality_breakdown"]
                else None
            ),
            issues_count={Severity(k): v for k, v in json.loads(d["issues_count"]).items()},
            started_at=datetime.fromisoformat(d["started_at"]),
            finished_at=datetime.fromisoformat(d["finished_at"]) if d["finished_at"] else None,
            error=d["error"],
            reproducibility=json.loads(d["reproducibility"]),
            llm_usage=json.loads(d["llm_usage"]),
        )

    def _issue_from_row(self, row: sqlite3.Row) -> Issue:
        from datasentry_core.models.enums import IssueStatus, QualityDimension
        from datasentry_core.models.llm import AIExplanation

        d = dict(row)
        return Issue(
            id=d["id"],
            scan_run_id=d["scan_run_id"],
            issue_type=d["issue_type"],
            title=d["title"],
            description=d["description"],
            dataset_id=d["dataset_id"],
            table_name=d["table_name"],
            columns=json.loads(d["columns"]),
            quality_dimensions=[QualityDimension(v) for v in json.loads(d["quality_dimensions"])],
            severity=d["severity"],
            confidence=d["confidence"],
            priority_score=d["priority_score"],
            false_positive_risk=d["false_positive_risk"],
            affected_count=d["affected_count"],
            affected_ratio=d["affected_ratio"],
            affected_row_ids=json.loads(d["affected_row_ids"]) if d["affected_row_ids"] else None,
            detector_ids=json.loads(d["detector_ids"]),
            ai_explanation=(
                AIExplanation.model_validate(json.loads(d["ai_explanation"]))
                if d["ai_explanation"]
                else None
            ),
            status=IssueStatus(d["status"]),
            created_at=datetime.fromisoformat(d["created_at"]),
            evidence=[],  # get_issues 内补
        )

    def _repair_proposal_from_row(self, row: sqlite3.Row) -> RepairProposal:
        from datasentry_core.models.repair import RepairProposal

        d = dict(row)
        return RepairProposal(
            proposal_id=d["proposal_id"],
            issue_id=d["issue_id"],
            issue_type=d["issue_type"],
            operation=RepairOperation(d["operation"]),
            target_columns=json.loads(d["target_columns"]),
            target_row_ids=json.loads(d["target_row_ids"]) if d["target_row_ids"] else None,
            parameters=json.loads(d["parameters"]),
            rationale=d["rationale"],
            evidence_ids=json.loads(d["evidence_ids"]),
            risk_level=d["risk_level"],
            reversibility=d["reversibility"],
            estimated_rows_changed=d["estimated_rows_changed"],
            preconditions=json.loads(d["preconditions"]),
            postconditions=json.loads(d["postconditions"]) if d["postconditions"] else [],
            status=RepairProposalStatus(d["status"]),
            created_at=datetime.fromisoformat(d["created_at"]),
        )

    def _repair_run_from_row(self, row: sqlite3.Row) -> RepairRun:
        from datasentry_core.models.repair import RepairOperationRecord, RepairRun

        d = dict(row)
        return RepairRun(
            id=d["id"],
            dataset_id=d["dataset_id"],
            proposal_id=d["proposal_id"],
            fingerprint_before=d["fingerprint_before"],
            fingerprint_after=d["fingerprint_after"],
            operations=[
                RepairOperationRecord.model_validate(op) for op in json.loads(d["operations"])
            ],
            approved_by=d["approved_by"],
            approval_kind=d["approval_kind"],
            approved_at=(datetime.fromisoformat(d["approved_at"]) if d["approved_at"] else None),
            status=RepairRunStatus(d["status"]),
            rollback_artifact=d["rollback_artifact"],
            created_at=datetime.fromisoformat(d["created_at"]),
        )

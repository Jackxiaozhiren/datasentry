"""元数据存储（Step 9）：SQLite 读写，MVP 闭环 = scan_runs/detector_runs/issues/evidence。

约定：
- 单连接 + 写锁（SDK/CLI 单进程场景）；WAL 模式
- 扫描结果整体单事务落库（save_scan）；JSON 复杂对象列、标量独立列（草案说明 1）
- 时间戳 ISO 8601 UTC 文本
- 占位表（contracts/jobs/feedback 等）建表无写路径（草案说明 5）
"""

from __future__ import annotations

import decimal
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from datasentry_core.models.enums import (
    RepairOperation,
    RepairProposalStatus,
    RepairRunStatus,
    Severity,
)
from datasentry_core.models.evidence import Evidence, utcnow
from datasentry_core.models.issue import Issue
from datasentry_core.models.llm import LLMInvocation
from datasentry_core.models.repair import RepairProposal, RepairRun
from datasentry_core.models.rules import Rule
from datasentry_core.models.scan import DetectorRun, ScanRun
from datasentry_core.storage.paths import project_db_path
from datasentry_core.storage.schema import migrate

_LOCAL_PROJECT_ID = "local"
_NEVER_EXPIRES = "9999-12-31T23:59:59+00:00"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _json_default(obj: Any) -> Any:
    """evidence/参数 JSON 兜底（Step 55：PG 源聚合结果可能带 Decimal）。

    检测器 evidence.data 契约是 JSON 可序列化；DuckDB postgres 扩展对
    numeric 聚合（avg/sum 等）返回 Decimal，此处统一收敛为 float。
    """
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


class MetadataStore:
    """项目元数据库门面。open 即迁移；close 后不可用。"""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # 跨进程并发写（V19，Step 105，ADR-105）：SQLite 文件锁忙等 5s
        # 而不是立刻抛 "database is locked"（WAL 由 migrate 设置）
        self._conn.execute("PRAGMA busy_timeout = 5000")
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
                            json.dumps(evidence.data, default=_json_default),
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

    def has_applied_repairs(self) -> bool:
        """workspace 是否存在已应用（非回滚）的修复记录——require_repair_validation 证据。

        Step 35 语义：修复证据按工作区级查询（修复副本与源文件指纹不同，
        按 dataset 匹配会让复扫死锁；证据只回答「是否走过修复闭环」）。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM repair_runs WHERE status = 'applied' LIMIT 1", ()
            ).fetchone()
        return row is not None

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

    # ---- LLM 调用审计（Step 27，13.11） -----------------------------------

    def record_llm_invocation(self, invocation: LLMInvocation) -> None:
        """记录单次 LLM 调用审计（llm_invocations 表，已含去敏约束）。"""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO llm_invocations (
                    invocation_id, task_type, template_version, provider_id, model,
                    input_tokens, output_tokens, cache_hit, latency_ms, status,
                    prompt_hash, masked_sample_count, injection_flagged,
                    error_message, pii_session_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invocation.invocation_id,
                    invocation.task_type,
                    invocation.template_version,
                    invocation.provider_id,
                    invocation.model,
                    invocation.input_tokens,
                    invocation.output_tokens,
                    int(invocation.cache_hit),
                    invocation.latency_ms,
                    invocation.status,
                    invocation.prompt_hash,
                    invocation.masked_sample_count,
                    int(invocation.injection_flagged),
                    invocation.error_message,
                    invocation.pii_session_id,
                    _iso(invocation.created_at),
                ),
            )

    def list_llm_invocations(self, limit: int = 20) -> list[LLMInvocation]:
        """最近 N 次调用（审计查询，按时间倒序）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM llm_invocations ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._invocation_from_row(r) for r in rows]

    def _invocation_from_row(self, row: sqlite3.Row) -> LLMInvocation:
        from datasentry_core.models.llm import LLMInvocation

        d = dict(row)
        return LLMInvocation(
            invocation_id=d["invocation_id"],
            task_type=d["task_type"],
            template_version=d["template_version"],
            provider_id=d["provider_id"],
            model=d["model"],
            input_tokens=d["input_tokens"],
            output_tokens=d["output_tokens"],
            cache_hit=bool(d["cache_hit"]),
            latency_ms=d["latency_ms"],
            status=d["status"],
            prompt_hash=d["prompt_hash"],
            masked_sample_count=d["masked_sample_count"],
            injection_flagged=bool(d["injection_flagged"]),
            error_message=d["error_message"],
            pii_session_id=d["pii_session_id"],
            created_at=datetime.fromisoformat(d["created_at"]),
        )

    # ---- 规则（Step 28，14.1/14.4 审批落库） -------------------------------

    def save_rule(self, rule: Rule) -> None:
        """保存规则（source=llm_candidate 的候选经审批后落库，14.4）。"""
        with self._lock, self._conn:
            self._ensure_project()
            self._conn.execute(
                """
                INSERT OR REPLACE INTO rules (
                    id, project_id, rule_version, type, severity, description,
                    when_json, then_json, expression, columns, source, enabled,
                    criticality_override, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule.id,
                    _LOCAL_PROJECT_ID,
                    rule.version,
                    rule.type.value,
                    rule.severity.value,
                    rule.description,
                    rule.when.model_dump_json() if rule.when else None,
                    rule.then.model_dump_json() if rule.then else None,
                    rule.expression,
                    json.dumps(rule.columns),
                    rule.source,
                    int(rule.enabled),
                    rule.criticality_override.value if rule.criticality_override else None,
                    rule.created_by,
                    _iso(rule.created_at),
                    _iso(rule.created_at),
                ),
            )

    def list_rules(self) -> list[Rule]:
        """规则列表（含 llm_candidate 已批准规则，按创建时间倒序）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM rules ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
        return [self._rule_from_row(r) for r in rows]

    def get_rule(self, rule_id: str) -> Rule | None:
        """按 ID 读取规则（不存在返回 None）。"""
        with self._lock:
            row = self._conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
        return self._rule_from_row(row) if row is not None else None

    def activate_rule(self, rule_id: str) -> Rule | None:
        """用户批准：候选规则 enabled 0→1（14.4）。不存在返回 None。"""
        with self._lock, self._conn:
            row = self._conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE rules SET enabled = 1, updated_at = ? WHERE id = ?",
                (_iso(utcnow()), rule_id),
            )
            updated = self._conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
        return self._rule_from_row(updated)

    def _rule_from_row(self, row: sqlite3.Row) -> Rule:
        from datasentry_core.models.enums import RuleType
        from datasentry_core.models.rules import Condition, Rule

        d = dict(row)
        return Rule(
            id=d["id"],
            type=RuleType(d["type"]),
            severity=Severity(d["severity"]),
            description=d["description"],
            when=Condition.model_validate_json(d["when_json"]) if d["when_json"] else None,
            then=Condition.model_validate_json(d["then_json"]) if d["then_json"] else None,
            expression=d["expression"],
            columns=json.loads(d["columns"]),
            source=d["source"],
            enabled=bool(d["enabled"]),
            criticality_override=d["criticality_override"],
            created_by=d["created_by"],
            created_at=datetime.fromisoformat(d["created_at"]),
            version=d["rule_version"],
        )

    # ---- LLM 缓存（Step 28，13.9 预算：同 prompt 命中缓存省 token） --------

    def get_llm_cache(self, cache_key: str) -> str | None:
        """按 key 取 LLM 响应缓存（未过期）；命中返回 response_json 原文。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT response_json, expires_at FROM llm_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if row is None:
            return None
        if row["expires_at"] and datetime.fromisoformat(row["expires_at"]) < utcnow():
            return None
        return str(row["response_json"])

    def put_llm_cache(self, cache_key: str, response_json: str, ttl_seconds: int = 0) -> None:
        """写 LLM 响应缓存；ttl_seconds<=0 表示不过期。"""
        expires_at = (
            _iso(utcnow() + timedelta(seconds=ttl_seconds)) if ttl_seconds > 0 else _NEVER_EXPIRES
        )
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO llm_cache "
                "(cache_key, response_json, created_at, expires_at)"
                " VALUES (?, ?, ?, ?)",
                (cache_key, response_json, _iso(utcnow()), expires_at),
            )

    # ---- PII 加密映射（Step 48，V2-A：AES-GCM 密文持久化，明文不落盘） ----

    def save_pii_mapping(
        self,
        session_id: str,
        ciphertext: str,
        key_version: str = "",
        created_at: datetime | None = None,
    ) -> None:
        """持久化加密后的脱敏映射（密文行；明文只在 vault 内存/解密时存在）。"""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO pii_mappings "
                "(session_id, ciphertext, key_version, created_at) VALUES (?, ?, ?, ?)",
                (session_id, ciphertext, key_version, _iso(created_at or utcnow())),
            )

    def get_pii_mapping(self, session_id: str) -> dict[str, Any] | None:
        """按 session 取加密映射行；不存在返回 None。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT session_id, ciphertext, key_version, created_at "
                "FROM pii_mappings WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "session_id": row["session_id"],
            "ciphertext": row["ciphertext"],
            "key_version": row["key_version"],
            "created_at": datetime.fromisoformat(row["created_at"]),
        }

    def list_pii_mappings(self, limit: int = 100) -> list[dict[str, Any]]:
        """加密会话列表（不含密文；轮换用 get_all_pii_mappings）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, key_version, created_at "
                "FROM pii_mappings ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "session_id": row["session_id"],
                "key_version": row["key_version"],
                "created_at": datetime.fromisoformat(row["created_at"]),
            }
            for row in rows
        ]

    def get_all_pii_mappings(self) -> list[dict[str, Any]]:
        """全部加密映射行（含密文，密钥轮换用）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, ciphertext, key_version FROM pii_mappings"
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_pii_mapping(self, session_id: str) -> bool:
        """删除单个加密会话；存在返回 True。"""
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM pii_mappings WHERE session_id = ?", (session_id,))
        return cur.rowcount > 0

    def count_pii_mappings(self) -> int:
        """加密会话总数（llm status 展示）。"""
        with self._lock:
            row = self._conn.execute("SELECT count(*) AS n FROM pii_mappings").fetchone()
        return int(row["n"])

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

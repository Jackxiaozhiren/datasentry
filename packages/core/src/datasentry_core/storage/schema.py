"""SQLite 元数据 Schema（冻结自 docs/04 草案）+ user_version 迁移。

迁移策略（ADR-012）：MVP 以 `PRAGMA user_version` 递增 + 幂等 DDL 演进；
Alembic 归 V1（草案注明"Step 后续"建立基线，MVP 阶段以模型单测守护字段兼容）。

约定（草案）：主键文本 ID（snake_case 前缀）；外键 ON DELETE CASCADE；
时间戳 TEXT（ISO 8601 UTC）；计数/比例 REAL；状态 TEXT + CHECK。
凭据不入库（data_sources.connection_ref 只存引用名）。
"""

from __future__ import annotations

import sqlite3

#: 当前 schema 版本（PRAGMA user_version）
SCHEMA_VERSION = 5

_SCHEMA_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    workspace_path  TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_sources (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_type     TEXT NOT NULL,
    path            TEXT,
    connection_ref  TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS datasets (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    data_source_id  TEXT REFERENCES data_sources(id) ON DELETE SET NULL,
    name            TEXT NOT NULL,
    table_name      TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_fingerprints (
    id                  TEXT PRIMARY KEY,
    dataset_id          TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    fingerprint_type    TEXT NOT NULL
                          CHECK (fingerprint_type IN ('full','sampled','metadata_only')),
    file_sha256         TEXT,
    schema_hash         TEXT NOT NULL,
    row_count           INTEGER NOT NULL,
    column_count        INTEGER NOT NULL,
    column_signature    TEXT NOT NULL,
    content_sample_hash TEXT,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_versions (
    id              TEXT PRIMARY KEY,
    dataset_id      TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    version_no      INTEGER NOT NULL,
    fingerprint_id  TEXT NOT NULL REFERENCES dataset_fingerprints(id),
    output_path     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (dataset_id, version_no)
);

CREATE TABLE IF NOT EXISTS scan_runs (
    id              TEXT PRIMARY KEY,
    dataset_id      TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    dataset_version_id TEXT REFERENCES dataset_versions(id) ON DELETE SET NULL,
    contract_id     TEXT REFERENCES contracts(id) ON DELETE SET NULL,
    status          TEXT NOT NULL
                      CHECK (status IN ('queued','running','completed','failed','cancelled')),
    config          TEXT NOT NULL,
    -- fingerprint: redundant copy saved with scan (ADR-012, no version rows in MVP)
    fingerprint     TEXT NOT NULL,           -- JSON: DatasetFingerprint
    quality_score   REAL,
    quality_breakdown TEXT,
    issues_count    TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    error           TEXT,
    reproducibility TEXT NOT NULL,
    llm_usage       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS detector_runs (
    id                  TEXT PRIMARY KEY,
    scan_run_id         TEXT NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    detector_id         TEXT NOT NULL,
    detector_version    TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('completed','skipped','failed')),
    rows_scanned        INTEGER NOT NULL,
    duration_ms         INTEGER NOT NULL,
    issues_candidates   INTEGER NOT NULL,
    sampling            TEXT,
    error               TEXT
);

CREATE TABLE IF NOT EXISTS issues (
    id                  TEXT PRIMARY KEY,
    scan_run_id         TEXT NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    issue_type          TEXT NOT NULL,
    title               TEXT NOT NULL,
    description         TEXT NOT NULL,
    dataset_id          TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    table_name          TEXT,
    columns             TEXT NOT NULL,
    quality_dimensions  TEXT NOT NULL,
    severity            TEXT NOT NULL CHECK (severity IN ('info','low','medium','high','critical')),
    confidence          REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    priority_score      REAL NOT NULL,
    false_positive_risk TEXT NOT NULL CHECK (false_positive_risk IN ('low','medium','high')),
    affected_count      INTEGER NOT NULL,
    affected_ratio      REAL NOT NULL,
    affected_row_ids    TEXT,
    detector_ids        TEXT NOT NULL,
    ai_explanation      TEXT,
    status              TEXT NOT NULL CHECK (status IN
                        ('open','confirmed','false_positive','accepted_exception',
                         'repair_proposed','repair_approved','repaired','resolved')),
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id     TEXT PRIMARY KEY,
    issue_id        TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    evidence_type   TEXT NOT NULL,
    detector_id     TEXT NOT NULL,
    detector_version TEXT NOT NULL,
    description     TEXT NOT NULL,
    data            TEXT NOT NULL,
    confidence      REAL NOT NULL DEFAULT 1.0,
    provenance      TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repair_proposals (
    proposal_id          TEXT PRIMARY KEY,
    issue_id             TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    issue_type           TEXT NOT NULL DEFAULT '',
    operation            TEXT NOT NULL,
    target_columns       TEXT NOT NULL,
    target_row_ids       TEXT,
    parameters           TEXT NOT NULL,
    rationale            TEXT NOT NULL,
    evidence_ids         TEXT NOT NULL,
    risk_level           TEXT NOT NULL CHECK (risk_level IN ('low','medium','high')),
    reversibility        TEXT NOT NULL CHECK (reversibility IN
                          ('fully_reversible','partially_reversible','irreversible')),
    estimated_rows_changed INTEGER NOT NULL,
    preconditions        TEXT NOT NULL,
    postconditions       TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'proposed'
                          CHECK (status IN ('proposed','previewed','approved','rejected',
                                            'applied','rolled_back')),
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repair_runs (
    id                  TEXT PRIMARY KEY,
    dataset_id          TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    proposal_id         TEXT,
    fingerprint_before  TEXT NOT NULL,
    fingerprint_after   TEXT,
    operations          TEXT NOT NULL,
    approved_by         TEXT NOT NULL,
    approval_kind       TEXT NOT NULL CHECK (approval_kind IN ('manual','yes_typed')),
    approved_at         TEXT,
    status              TEXT NOT NULL CHECK (status IN ('applied','rolled_back','failed')),
    rollback_artifact   TEXT,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_results (
    id              TEXT PRIMARY KEY,
    scan_run_id     TEXT NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    rule_id         TEXT NOT NULL,
    rule_version    INTEGER NOT NULL,
    failures        INTEGER NOT NULL,
    rows_tested     INTEGER NOT NULL,
    failure_ratio   REAL NOT NULL,
    example_row_ids TEXT,
    duration_ms     INTEGER NOT NULL,
    ran_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contracts (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    dataset_id  TEXT REFERENCES datasets(id) ON DELETE SET NULL,
    version     TEXT NOT NULL,
    checksum    TEXT NOT NULL,
    content     TEXT NOT NULL,
    requires_rescan INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rule_version        INTEGER NOT NULL DEFAULT 1,
    type                TEXT NOT NULL,
    severity            TEXT NOT NULL,
    description         TEXT NOT NULL,
    when_json           TEXT,
    then_json           TEXT,
    expression          TEXT,
    columns             TEXT NOT NULL,
    source              TEXT NOT NULL CHECK (source IN
                          ('user','contract','llm_candidate','builtin','learned')),
    enabled             INTEGER NOT NULL DEFAULT 1,
    criticality_override TEXT,
    created_by          TEXT NOT NULL DEFAULT 'local-user',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_invocations (
    invocation_id   TEXT PRIMARY KEY,
    task_type       TEXT NOT NULL,
    template_version TEXT NOT NULL,
    provider_id     TEXT NOT NULL,
    model           TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL,
    output_tokens   INTEGER NOT NULL,
    cache_hit       INTEGER NOT NULL DEFAULT 0,
    latency_ms      INTEGER NOT NULL,
    status          TEXT NOT NULL CHECK (status IN
                    ('ok','retried','schema_failed','failed','degraded')),
    prompt_hash     TEXT NOT NULL,
    masked_sample_count INTEGER NOT NULL DEFAULT 0,
    injection_flagged INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    pii_session_id  TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pii_mappings (
    session_id  TEXT PRIMARY KEY,
    ciphertext  TEXT NOT NULL,
    key_version TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key      TEXT PRIMARY KEY,
    response_json  TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id     TEXT PRIMARY KEY,
    event_type   TEXT NOT NULL,
    actor        TEXT NOT NULL,
    project_id   TEXT REFERENCES projects(id) ON DELETE CASCADE,
    resource_type TEXT,
    resource_id  TEXT,
    details      TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback_entries (
    feedback_id   TEXT PRIMARY KEY,
    issue_id      TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    label         TEXT NOT NULL CHECK (label IN
                  ('true_issue','false_positive','expected_exception',
                   'need_more_context','wrong_severity','wrong_repair')),
    severity_correction TEXT,
    repair_rating TEXT CHECK (repair_rating IN ('good','bad')),
    note          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback_effects (
    id          TEXT PRIMARY KEY,
    feedback_id TEXT NOT NULL REFERENCES feedback_entries(feedback_id) ON DELETE CASCADE,
    effect_type TEXT NOT NULL,
    target      TEXT NOT NULL,
    delta       REAL NOT NULL,
    reverted    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    job_type    TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('queued','running','completed','failed')),
    progress    TEXT NOT NULL DEFAULT '{}',
    result_url  TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id         TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    project        TEXT NOT NULL,
    command        TEXT NOT NULL,
    cron           TEXT NOT NULL,
    enabled        INTEGER NOT NULL DEFAULT 1,
    retry_attempts INTEGER NOT NULL DEFAULT 0,
    webhook_url    TEXT,
    gate_quality_min REAL,
    status         TEXT NOT NULL DEFAULT 'idle'
                   CHECK (status IN ('idle','queued','running','dead')),
    next_run_at    TEXT NOT NULL,
    last_run_at    TEXT,
    last_result    TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_runs (
    run_id      TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES scheduled_jobs(job_id) ON DELETE CASCADE,
    status      TEXT NOT NULL CHECK (status IN ('running','completed','failed')),
    attempt     INTEGER NOT NULL DEFAULT 0,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    scan_run_id TEXT,
    summary     TEXT,
    error       TEXT,
    webhook_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_scan_runs_dataset   ON scan_runs(dataset_id);
CREATE INDEX IF NOT EXISTS idx_issues_scan         ON issues(scan_run_id);
CREATE INDEX IF NOT EXISTS idx_issues_severity     ON issues(severity, priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_issues_dataset      ON issues(dataset_id);
CREATE INDEX IF NOT EXISTS idx_evidence_issue      ON evidence(issue_id);
CREATE INDEX IF NOT EXISTS idx_audit_project_time  ON audit_events(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status         ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_sched_jobs_due      ON scheduled_jobs(status, enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_job_runs_job        ON job_runs(job_id, started_at DESC);
"""

#: MVP 占位表（V1 启用，无写路径）：数据闭环外且依赖契约/反馈/作业机制
PLACEHOLDER_TABLES = (
    "data_sources",
    "dataset_versions",
    "dataset_fingerprints",
    "validation_results",
    "contracts",
    "rules",
    "llm_invocations",
    "llm_cache",
    "audit_events",
    "feedback_entries",
    "feedback_effects",
    "jobs",
)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """幂等补列：存在则跳过（调用方负责保证 ddl 不含 NOT NULL 且默认兼容）。"""
    exists = any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})").fetchall())
    if not exists:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def migrate(conn: sqlite3.Connection) -> None:
    """幂等迁移到 SCHEMA_VERSION；高于当前版本抛异常（防止旧代码打开新库）。"""
    conn.executescript(_SCHEMA_DDL)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"metadata db schema version {version} is newer than supported {SCHEMA_VERSION}"
        )
    # v1 → v2：repair 表转正（从占位移除），补 issue_type / proposal_id 列
    if version < 2:
        _ensure_column(
            conn, "repair_proposals", "issue_type", "issue_type TEXT NOT NULL DEFAULT ''"
        )
        _ensure_column(conn, "repair_runs", "proposal_id", "proposal_id TEXT")
    # v2 → v3：PII 加密映射（Step 48）：pii_mappings 表 + 审计列
    if version < 3:
        _ensure_column(conn, "llm_invocations", "pii_session_id", "pii_session_id TEXT")
    # v4 → v5：调度质量门禁（Step 52）：scheduled_jobs.gate_quality_min
    if version < 5:
        _ensure_column(conn, "scheduled_jobs", "gate_quality_min", "gate_quality_min REAL")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()

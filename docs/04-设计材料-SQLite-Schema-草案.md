# DataSentry AI — 设计材料 04：SQLite 元数据 Schema（草案）

> 对应「四十三」第 10 项与 44 章 W1 交付物「SQLite DDL 草案」。
> 状态：**草案**，随 Step 1 领域模型冻结后由 Step 后续（Alembic 基线）转为正式迁移。
> 约定：主键为文本 ID（`snake_case` 前缀）；外键全部 `ON DELETE CASCADE`（项目删除级联，52.1）；
> 时间戳一律 `TEXT`（ISO 8601 UTC）；计数/比例用 REAL；状态用 TEXT + CHECK。

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 项目（对应 Project）
CREATE TABLE projects (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    workspace_path  TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

-- 数据源（DataSource：文件或数据库连接，凭据仅存引用，不存原文）
CREATE TABLE data_sources (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_type     TEXT NOT NULL,           -- csv/parquet/jsonl/xlsx/sqlite/postgresql
    path            TEXT,                    -- 本地路径；数据库场景为 host/db 引用
    connection_ref  TEXT,                    -- 凭据引用名（存于全局配置，非本库）
    created_at      TEXT NOT NULL
);

-- 数据集（Dataset）
CREATE TABLE datasets (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    data_source_id  TEXT REFERENCES data_sources(id) ON DELETE SET NULL,
    name            TEXT NOT NULL,
    table_name      TEXT,                    -- 多表/DB 场景
    created_at      TEXT NOT NULL
);

-- 数据集版本（DatasetVersion：修复产生新版本，不可变）
CREATE TABLE dataset_versions (
    id              TEXT PRIMARY KEY,
    dataset_id      TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    version_no      INTEGER NOT NULL,
    fingerprint_id  TEXT NOT NULL REFERENCES dataset_fingerprints(id),
    output_path     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (dataset_id, version_no)
);

-- 数据指纹（DatasetFingerprint）
CREATE TABLE dataset_fingerprints (
    id                  TEXT PRIMARY KEY,
    dataset_id          TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    fingerprint_type    TEXT NOT NULL CHECK (fingerprint_type IN ('full','sampled','metadata_only')),
    file_sha256         TEXT,
    schema_hash         TEXT NOT NULL,
    row_count           INTEGER NOT NULL,
    column_count        INTEGER NOT NULL,
    column_signature    TEXT NOT NULL,       -- JSON: [[name, type], ...]
    content_sample_hash TEXT,
    created_at          TEXT NOT NULL
);

-- 扫描运行（ScanRun）
CREATE TABLE scan_runs (
    id              TEXT PRIMARY KEY,
    dataset_id      TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    dataset_version_id TEXT REFERENCES dataset_versions(id) ON DELETE SET NULL,
    contract_id     TEXT REFERENCES contracts(id) ON DELETE SET NULL,
    status          TEXT NOT NULL CHECK (status IN ('queued','running','completed','failed','cancelled')),
    config          TEXT NOT NULL,           -- JSON: ScanConfig
    quality_score   REAL,
    quality_breakdown TEXT,                  -- JSON: 维度得分
    issues_count    TEXT NOT NULL,           -- JSON: {severity: count}
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    error           TEXT,
    reproducibility TEXT NOT NULL,           -- JSON: ReproducibilityInfo
    llm_usage       TEXT NOT NULL DEFAULT '{}' -- JSON: LLMUsageSummary
);

-- 检测器运行（DetectorRun）
CREATE TABLE detector_runs (
    id                  TEXT PRIMARY KEY,
    scan_run_id         TEXT NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    detector_id         TEXT NOT NULL,
    detector_version    TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('completed','skipped','failed')),
    rows_scanned        INTEGER NOT NULL,
    duration_ms         INTEGER NOT NULL,
    issues_candidates   INTEGER NOT NULL,
    sampling            TEXT,                -- JSON: SamplingInfo
    error               TEXT
);

-- Issue
CREATE TABLE issues (
    id                  TEXT PRIMARY KEY,
    scan_run_id         TEXT NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    issue_type          TEXT NOT NULL,
    title               TEXT NOT NULL,
    description         TEXT NOT NULL,
    dataset_id          TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    table_name          TEXT,
    columns             TEXT NOT NULL,       -- JSON: [col, ...]
    quality_dimensions  TEXT NOT NULL,       -- JSON: [dim, ...]
    severity            TEXT NOT NULL CHECK (severity IN ('info','low','medium','high','critical')),
    confidence          REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    priority_score      REAL NOT NULL,
    false_positive_risk TEXT NOT NULL CHECK (false_positive_risk IN ('low','medium','high')),
    affected_count      INTEGER NOT NULL,
    affected_ratio      REAL NOT NULL,
    affected_row_ids    TEXT,                -- JSON: [row_id, ...]（大集合同一扫描另存）
    detector_ids        TEXT NOT NULL,       -- JSON: [detector_id, ...]
    ai_explanation      TEXT,                -- JSON: AIExplanation
    status              TEXT NOT NULL CHECK (status IN
                        ('open','confirmed','false_positive','accepted_exception',
                         'repair_proposed','repair_approved','repaired','resolved')),
    created_at          TEXT NOT NULL
);

-- 证据（Evidence）
CREATE TABLE evidence (
    evidence_id     TEXT PRIMARY KEY,
    issue_id        TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    evidence_type   TEXT NOT NULL,
    detector_id     TEXT NOT NULL,
    detector_version TEXT NOT NULL,
    description     TEXT NOT NULL,
    data            TEXT NOT NULL,           -- JSON
    confidence      REAL NOT NULL DEFAULT 1.0,
    provenance      TEXT NOT NULL,           -- JSON: EvidenceProvenance
    created_at      TEXT NOT NULL
);

-- 修复提案（RepairProposal）
CREATE TABLE repair_proposals (
    proposal_id          TEXT PRIMARY KEY,
    issue_id             TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    operation            TEXT NOT NULL,
    target_columns       TEXT NOT NULL,      -- JSON
    target_row_ids       TEXT,               -- JSON
    parameters           TEXT NOT NULL,      -- JSON
    rationale            TEXT NOT NULL,
    evidence_ids         TEXT NOT NULL,      -- JSON
    risk_level           TEXT NOT NULL CHECK (risk_level IN ('low','medium','high')),
    reversibility        TEXT NOT NULL CHECK (reversibility IN
                          ('fully_reversible','partially_reversible','irreversible')),
    estimated_rows_changed INTEGER NOT NULL,
    preconditions        TEXT NOT NULL,      -- JSON
    postconditions       TEXT NOT NULL,      -- JSON
    status               TEXT NOT NULL DEFAULT 'proposed'
                          CHECK (status IN ('proposed','previewed','approved','rejected','applied','rolled_back')),
    created_at           TEXT NOT NULL
);

-- 修复运行（RepairRun）
CREATE TABLE repair_runs (
    id                  TEXT PRIMARY KEY,
    dataset_id          TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    fingerprint_before  TEXT NOT NULL,
    fingerprint_after   TEXT,
    operations          TEXT NOT NULL,       -- JSON: 行级 before/after
    approved_by         TEXT NOT NULL,
    approval_kind       TEXT NOT NULL CHECK (approval_kind IN ('manual','yes_typed')),
    approved_at         TEXT,
    status              TEXT NOT NULL CHECK (status IN ('applied','rolled_back','failed')),
    rollback_artifact   TEXT,
    created_at          TEXT NOT NULL
);

-- 验证结果（ValidationResult）
CREATE TABLE validation_results (
    id              TEXT PRIMARY KEY,
    scan_run_id     TEXT NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
    rule_id         TEXT NOT NULL,
    rule_version    INTEGER NOT NULL,
    failures        INTEGER NOT NULL,
    rows_tested     INTEGER NOT NULL,
    failure_ratio   REAL NOT NULL,
    example_row_ids TEXT,                    -- JSON
    duration_ms     INTEGER NOT NULL,
    ran_at          TEXT NOT NULL
);

-- 契约（Contract，V1 启用；MVP 仅占位）
CREATE TABLE contracts (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    dataset_id  TEXT REFERENCES datasets(id) ON DELETE SET NULL,
    version     TEXT NOT NULL,
    checksum    TEXT NOT NULL,
    content     TEXT NOT NULL,               -- 契约 YAML 原文
    requires_rescan INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- 规则（Rule：契约内或独立）
CREATE TABLE rules (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rule_version        INTEGER NOT NULL DEFAULT 1,
    type                TEXT NOT NULL,
    severity            TEXT NOT NULL,
    description         TEXT NOT NULL,
    when_json           TEXT,                -- JSON: Condition
    then_json           TEXT,                -- JSON: Condition
    expression          TEXT,
    columns             TEXT NOT NULL,       -- JSON
    source              TEXT NOT NULL CHECK (source IN
                          ('user','contract','llm_candidate','builtin','learned')),
    enabled             INTEGER NOT NULL DEFAULT 1,
    criticality_override TEXT,
    created_by          TEXT NOT NULL DEFAULT 'local-user',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

-- LLM 调用审计（LLMInvocation；缓存键幂等）
CREATE TABLE llm_invocations (
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
    created_at      TEXT NOT NULL
);

-- LLM 缓存（53.1；键=掩码内容哈希，无原始数据）
CREATE TABLE llm_cache (
    cache_key      TEXT PRIMARY KEY,
    response_json  TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL
);

-- 审计事件（AuditEvent）
CREATE TABLE audit_events (
    event_id     TEXT PRIMARY KEY,
    event_type   TEXT NOT NULL,
    actor        TEXT NOT NULL,
    project_id   TEXT REFERENCES projects(id) ON DELETE CASCADE,
    resource_type TEXT,
    resource_id  TEXT,
    details      TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL
);

-- 反馈学习（FeedbackEntry，V1）
CREATE TABLE feedback_entries (
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

-- 反馈生效记录（可审计、可撤销，31.1）
CREATE TABLE feedback_effects (
    id          TEXT PRIMARY KEY,
    feedback_id TEXT NOT NULL REFERENCES feedback_entries(feedback_id) ON DELETE CASCADE,
    effect_type TEXT NOT NULL,
    target      TEXT NOT NULL,               -- detector+column 组合等
    delta       REAL NOT NULL,
    reverted    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

-- 作业（Job，24.1；服务重启可恢复）
CREATE TABLE jobs (
    job_id      TEXT PRIMARY KEY,
    job_type    TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('queued','running','completed','failed')),
    progress    TEXT NOT NULL DEFAULT '{}',  -- JSON: {phase, pct}
    result_url  TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- 索引
CREATE INDEX idx_scan_runs_dataset   ON scan_runs(dataset_id);
CREATE INDEX idx_issues_scan         ON issues(scan_run_id);
CREATE INDEX idx_issues_severity     ON issues(severity, priority_score DESC);
CREATE INDEX idx_issues_dataset      ON issues(dataset_id);
CREATE INDEX idx_evidence_issue      ON evidence(issue_id);
CREATE INDEX idx_audit_project_time  ON audit_events(project_id, created_at DESC);
CREATE INDEX idx_jobs_status         ON jobs(status);
```

## 草案说明

1. **JSON 列策略**：结构复杂且查询面小的对象（config/columns/evidence.data）以 JSON 存储，
   查询面大的标量（severity/confidence/status）独立成列——兼顾简单与可用性。
2. **凭据不入库**：`data_sources.connection_ref` 只存凭据引用名，真实凭据在全局配置
   （权限 600），兑现 49.2/28.6。
3. **审计不可变**：audit_events 无 UPDATE/DELETE 路径（应用层约束）。
4. **迁移工具**：Alembic 基线迁移在 Step 4（SQLite 元数据层）建立；本草案冻结前
   以模型单测守护字段兼容。
5. **占位表**：contracts/feedback_* 为 V1 预留，MVP 期可建表但无写入路径。

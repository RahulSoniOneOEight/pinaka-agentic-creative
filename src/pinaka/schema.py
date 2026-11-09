SCHEMA_VERSION = 1

SCHEMA_V1 = r"""
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    objective TEXT NOT NULL,
    mode TEXT NOT NULL,
    audience TEXT NOT NULL,
    output_format TEXT NOT NULL,
    language TEXT NOT NULL,
    duration_seconds TEXT NOT NULL,
    status TEXT NOT NULL,
    max_budget_minor INTEGER NOT NULL CHECK (max_budget_minor >= 0),
    currency TEXT NOT NULL,
    reserved_minor INTEGER NOT NULL DEFAULT 0 CHECK (reserved_minor >= 0),
    spent_minor INTEGER NOT NULL DEFAULT 0 CHECK (spent_minor >= 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_sets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK (version >= 1),
    source_ref TEXT NOT NULL,
    effective_from TEXT,
    values_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE(project_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fact_sets_one_current
    ON fact_sets(project_id) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS shots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stable_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    beat TEXT NOT NULL,
    duration_seconds TEXT NOT NULL,
    narrative TEXT NOT NULL,
    requirements_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, stable_key),
    UNIQUE(project_id, ordinal)
);

CREATE TABLE IF NOT EXISTS slots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
    stable_key TEXT NOT NULL,
    role TEXT NOT NULL,
    identity_requirement TEXT NOT NULL,
    fact_requirement TEXT NOT NULL,
    transformation_permission TEXT NOT NULL,
    text_zone TEXT,
    approved_sources_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(shot_id, stable_key)
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    subject_hash TEXT NOT NULL,
    output_hash TEXT,
    approved_at TEXT NOT NULL,
    invalidated_at TEXT,
    invalidation_reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_active_stage
    ON approvals(project_id, stage) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS approval_dependencies (
    approval_id TEXT NOT NULL REFERENCES approvals(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    entity_hash TEXT,
    PRIMARY KEY (approval_id, entity_type, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_approval_dependency_entity
    ON approval_dependencies(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    parent_artifact_id TEXT REFERENCES artifacts(id),
    operation_json TEXT NOT NULL,
    immutable INTEGER NOT NULL DEFAULT 0 CHECK (immutable IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'current',
    stale_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, relative_path)
);

CREATE TABLE IF NOT EXISTS artifact_dependencies (
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    entity_hash TEXT,
    PRIMARY KEY (artifact_id, entity_type, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_artifact_dependency_entity
    ON artifact_dependencies(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    request_json TEXT NOT NULL,
    response_json TEXT,
    status TEXT NOT NULL,
    estimated_cost_minor INTEGER NOT NULL CHECK (estimated_cost_minor >= 0),
    reserved_cost_minor INTEGER NOT NULL CHECK (reserved_cost_minor >= 0),
    actual_cost_minor INTEGER NOT NULL DEFAULT 0 CHECK (actual_cost_minor >= 0),
    currency TEXT NOT NULL,
    provider_job_id TEXT,
    error_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, provider, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_jobs_recovery
    ON jobs(project_id, status);

CREATE TABLE IF NOT EXISTS budget_ledger (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
    currency TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capability_checks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL,
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_project_sequence
    ON events(project_id, sequence);
"""

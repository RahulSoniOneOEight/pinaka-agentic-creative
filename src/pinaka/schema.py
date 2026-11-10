SCHEMA_VERSION = 7

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


SCHEMA_V2 = r"""
CREATE TABLE IF NOT EXISTS deliverables (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stable_key TEXT NOT NULL,
    name TEXT NOT NULL,
    objective TEXT NOT NULL,
    kind TEXT NOT NULL,
    output_format TEXT NOT NULL,
    language TEXT NOT NULL,
    duration_seconds TEXT,
    acceptance_targets_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, stable_key)
);

CREATE TABLE IF NOT EXISTS segments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    deliverable_id TEXT NOT NULL REFERENCES deliverables(id) ON DELETE CASCADE,
    stable_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    purpose TEXT NOT NULL,
    duration_seconds TEXT NOT NULL,
    narrative TEXT NOT NULL,
    constraints_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(deliverable_id, stable_key),
    UNIQUE(deliverable_id, ordinal)
);

ALTER TABLE shots ADD COLUMN deliverable_id TEXT REFERENCES deliverables(id);
ALTER TABLE shots ADD COLUMN segment_id TEXT REFERENCES segments(id);
CREATE INDEX IF NOT EXISTS idx_shots_deliverable ON shots(deliverable_id);
CREATE INDEX IF NOT EXISTS idx_shots_segment ON shots(segment_id);

CREATE TABLE IF NOT EXISTS grounding_bundles (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    source_ref TEXT NOT NULL,
    effective_from TEXT,
    values_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE(project_id, kind, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_grounding_one_current
    ON grounding_bundles(project_id, kind) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS provider_profiles (
    id TEXT PRIMARY KEY,
    stable_key TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    model TEXT NOT NULL,
    approved_roles_json TEXT NOT NULL,
    credential_ref TEXT NOT NULL,
    data_classification TEXT NOT NULL,
    limits_json TEXT NOT NULL,
    terms_json TEXT NOT NULL,
    fallback_profiles_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_profiles (
    id TEXT PRIMARY KEY,
    stable_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    integration_mode TEXT NOT NULL,
    executable_or_endpoint TEXT NOT NULL,
    supported_operations_json TEXT NOT NULL,
    limits_json TEXT NOT NULL,
    fallback_order_json TEXT NOT NULL,
    capability_status TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_operations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    tool_profile_id TEXT NOT NULL REFERENCES tool_profiles(id),
    provider_profile_id TEXT REFERENCES provider_profiles(id),
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL,
    output_declaration_json TEXT NOT NULL,
    limits_json TEXT NOT NULL,
    required_approval_stage TEXT,
    error_json TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, tool_profile_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_tool_operations_recovery
    ON tool_operations(project_id, status);

CREATE TABLE IF NOT EXISTS execution_receipts (
    id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE REFERENCES tool_operations(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    elapsed_ms INTEGER NOT NULL CHECK (elapsed_ms >= 0),
    output_artifact_ids_json TEXT NOT NULL,
    response_hash TEXT,
    actual_cost_minor INTEGER NOT NULL DEFAULT 0 CHECK (actual_cost_minor >= 0),
    currency TEXT,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS specialist_handoffs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    tool_profile_id TEXT NOT NULL REFERENCES tool_profiles(id),
    work_packet_hash TEXT NOT NULL,
    input_artifact_ids_json TEXT NOT NULL,
    output_artifact_ids_json TEXT NOT NULL,
    tool_version TEXT,
    operator_note TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_results (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL,
    findings_json TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS archives (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
    manifest_artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    manifest_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


SCHEMA_V3 = r"""
CREATE TABLE IF NOT EXISTS treatment_decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slot_id TEXT NOT NULL REFERENCES slots(id) ON DELETE CASCADE,
    artifact_id TEXT REFERENCES artifacts(id),
    verdict TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    criteria_json TEXT NOT NULL,
    rights_json TEXT,
    reasons_json TEXT NOT NULL,
    decision_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_treatment_slot
    ON treatment_decisions(slot_id, created_at);
CREATE INDEX IF NOT EXISTS idx_treatment_project
    ON treatment_decisions(project_id, created_at);

CREATE TABLE IF NOT EXISTS provider_requests (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    operation TEXT NOT NULL,
    requested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_requests_window
    ON provider_requests(provider, requested_at);
"""


SCHEMA_V4 = r"""
CREATE TABLE IF NOT EXISTS asset_use_contexts (
    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    use_class TEXT NOT NULL,
    monetized INTEGER NOT NULL CHECK (monetized IN (0, 1)),
    advertising INTEGER NOT NULL CHECK (advertising IN (0, 1)),
    modification_planned INTEGER NOT NULL CHECK (modification_planned IN (0, 1)),
    attribution_can_be_delivered INTEGER NOT NULL
        CHECK (attribution_can_be_delivered IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


SCHEMA_V5 = r"""
CREATE TABLE IF NOT EXISTS slot_assets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slot_id TEXT NOT NULL REFERENCES slots(id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    treatment_decision_id TEXT REFERENCES treatment_decisions(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(slot_id, role)
);
CREATE INDEX IF NOT EXISTS idx_slot_assets_project
    ON slot_assets(project_id);
CREATE INDEX IF NOT EXISTS idx_slot_assets_slot
    ON slot_assets(slot_id);
"""


SCHEMA_V6 = r"""
CREATE TABLE IF NOT EXISTS provider_spend (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    tier TEXT NOT NULL,
    purpose TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_spend_window
    ON provider_spend(provider, created_at);
"""


SCHEMA_V7 = r"""
ALTER TABLE projects ADD COLUMN parent_project_id TEXT REFERENCES projects(id);
CREATE INDEX IF NOT EXISTS idx_projects_parent ON projects(parent_project_id);

CREATE TABLE IF NOT EXISTS scene_plans (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scene_plans_project ON scene_plans(project_id, created_at);
"""


MIGRATIONS = {
    1: SCHEMA_V1,
    2: SCHEMA_V2,
    3: SCHEMA_V3,
    4: SCHEMA_V4,
    5: SCHEMA_V5,
    6: SCHEMA_V6,
    7: SCHEMA_V7,
}

SCHEMA_VERSION = 26

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


SCHEMA_V8 = r"""
CREATE TABLE IF NOT EXISTS audio_beds (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    verdict TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, role)
);
CREATE INDEX IF NOT EXISTS idx_audio_beds_project ON audio_beds(project_id);
"""


SCHEMA_V9 = r"""
CREATE TABLE IF NOT EXISTS workable_briefs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK (version >= 1),
    source_artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    source_content_hash TEXT NOT NULL,
    package_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE(project_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workable_briefs_current
    ON workable_briefs(project_id) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS brief_decisions (
    id TEXT PRIMARY KEY,
    brief_id TEXT NOT NULL REFERENCES workable_briefs(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    value_json TEXT NOT NULL,
    state TEXT NOT NULL,
    basis TEXT NOT NULL,
    risk TEXT NOT NULL,
    reversible INTEGER NOT NULL CHECK (reversible IN (0, 1)),
    alternatives_json TEXT NOT NULL,
    affected_shot_keys_json TEXT NOT NULL,
    review_gate TEXT,
    blocked_work_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(brief_id, field)
);
CREATE INDEX IF NOT EXISTS idx_brief_decisions_project_state
    ON brief_decisions(project_id, state);

CREATE TABLE IF NOT EXISTS creative_elements (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stable_key TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    specification_json TEXT NOT NULL,
    continuity_json TEXT NOT NULL,
    source_artifact_id TEXT REFERENCES artifacts(id),
    content_hash TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE(project_id, stable_key, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_creative_elements_current
    ON creative_elements(project_id, stable_key) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS shot_element_bindings (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
    element_id TEXT NOT NULL REFERENCES creative_elements(id),
    role TEXT NOT NULL,
    notes TEXT,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_shot_element_bindings_current
    ON shot_element_bindings(project_id, shot_id, role) WHERE is_current = 1;
CREATE INDEX IF NOT EXISTS idx_shot_element_bindings_element
    ON shot_element_bindings(element_id, is_current);

ALTER TABLE specialist_handoffs
    ADD COLUMN operation_id TEXT REFERENCES tool_operations(id);
ALTER TABLE specialist_handoffs
    ADD COLUMN work_packet_artifact_id TEXT REFERENCES artifacts(id);
ALTER TABLE specialist_handoffs
    ADD COLUMN result_packet_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_specialist_handoffs_operation
    ON specialist_handoffs(operation_id) WHERE operation_id IS NOT NULL;
"""


SCHEMA_V10 = r"""
CREATE TABLE IF NOT EXISTS studio_spend_approvals (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    scope_hash TEXT NOT NULL,
    max_cost_minor INTEGER NOT NULL CHECK (max_cost_minor > 100),
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    approved_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    consumed_operation_id TEXT REFERENCES tool_operations(id),
    created_at TEXT NOT NULL,
    consumed_at TEXT,
    UNIQUE(project_id, scope_hash, max_cost_minor, currency)
);
CREATE INDEX IF NOT EXISTS idx_studio_spend_approvals_project
    ON studio_spend_approvals(project_id, consumed_operation_id);

ALTER TABLE specialist_handoffs
    ADD COLUMN budget_job_id TEXT REFERENCES jobs(id);
ALTER TABLE specialist_handoffs
    ADD COLUMN spend_approval_id TEXT REFERENCES studio_spend_approvals(id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_specialist_handoffs_budget_job
    ON specialist_handoffs(budget_job_id) WHERE budget_job_id IS NOT NULL;
"""


SCHEMA_V11 = r"""
CREATE TABLE IF NOT EXISTS budget_plans (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK (version >= 1),
    creative_summary TEXT NOT NULL,
    target_minor INTEGER NOT NULL CHECK (target_minor > 0),
    planned_total_minor INTEGER NOT NULL CHECK (planned_total_minor >= 0),
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    tolerance_bps INTEGER NOT NULL CHECK (tolerance_bps >= 0 AND tolerance_bps <= 5000),
    tolerance_floor_minor INTEGER NOT NULL CHECK (tolerance_floor_minor >= 0),
    status TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE(project_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_budget_plans_current
    ON budget_plans(project_id) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS budget_plan_items (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES budget_plans(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stable_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    category TEXT NOT NULL,
    provider TEXT NOT NULL,
    operation TEXT NOT NULL,
    estimated_minor INTEGER NOT NULL CHECK (estimated_minor >= 0),
    priority TEXT NOT NULL,
    shot_id TEXT REFERENCES shots(id),
    slot_id TEXT REFERENCES slots(id),
    notes TEXT,
    status TEXT NOT NULL,
    job_id TEXT REFERENCES jobs(id),
    actual_minor INTEGER CHECK (actual_minor IS NULL OR actual_minor >= 0),
    variance_minor INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(plan_id, stable_key)
);
CREATE INDEX IF NOT EXISTS idx_budget_plan_items_plan
    ON budget_plan_items(plan_id, ordinal);
CREATE UNIQUE INDEX IF NOT EXISTS idx_budget_plan_items_job
    ON budget_plan_items(job_id) WHERE job_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS budget_plan_approvals (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    plan_id TEXT NOT NULL UNIQUE REFERENCES budget_plans(id) ON DELETE CASCADE,
    plan_hash TEXT NOT NULL,
    approved_total_minor INTEGER NOT NULL CHECK (approved_total_minor > 0),
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    approved_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


SCHEMA_V12 = r"""
CREATE TABLE budget_plans_v12 (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK (version >= 1),
    creative_summary TEXT NOT NULL,
    target_minor INTEGER NOT NULL CHECK (target_minor > 0),
    planned_total_minor INTEGER NOT NULL CHECK (planned_total_minor >= 0),
    currency TEXT NOT NULL,
    tolerance_bps INTEGER NOT NULL CHECK (tolerance_bps >= 0 AND tolerance_bps <= 10000),
    tolerance_floor_minor INTEGER NOT NULL CHECK (tolerance_floor_minor >= 0),
    status TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE(project_id, version)
);
INSERT INTO budget_plans_v12(
    id, project_id, version, creative_summary, target_minor, planned_total_minor,
    currency, tolerance_bps, tolerance_floor_minor, status, content_hash,
    is_current, created_at
)
SELECT
    id, project_id, version, creative_summary, target_minor, planned_total_minor,
    currency, tolerance_bps, tolerance_floor_minor, status, content_hash,
    is_current, created_at
FROM budget_plans;
DROP TABLE budget_plans;
ALTER TABLE budget_plans_v12 RENAME TO budget_plans;
CREATE UNIQUE INDEX IF NOT EXISTS idx_budget_plans_current
    ON budget_plans(project_id) WHERE is_current = 1;

CREATE TABLE budget_plan_approvals_v12 (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    plan_id TEXT NOT NULL UNIQUE REFERENCES budget_plans(id) ON DELETE CASCADE,
    plan_hash TEXT NOT NULL,
    approved_total_minor INTEGER NOT NULL CHECK (approved_total_minor > 0),
    currency TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
INSERT INTO budget_plan_approvals_v12(
    id, project_id, plan_id, plan_hash, approved_total_minor, currency,
    approved_by, reason, created_at
)
SELECT
    id, project_id, plan_id, plan_hash, approved_total_minor, currency,
    approved_by, reason, created_at
FROM budget_plan_approvals;
DROP TABLE budget_plan_approvals;
ALTER TABLE budget_plan_approvals_v12 RENAME TO budget_plan_approvals;

CREATE TABLE studio_spend_approvals_v12 (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    scope_hash TEXT NOT NULL,
    max_cost_minor INTEGER NOT NULL CHECK (max_cost_minor > 0),
    currency TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    consumed_operation_id TEXT REFERENCES tool_operations(id),
    created_at TEXT NOT NULL,
    consumed_at TEXT,
    UNIQUE(project_id, scope_hash, max_cost_minor, currency)
);
INSERT INTO studio_spend_approvals_v12(
    id, project_id, scope_hash, max_cost_minor, currency, approved_by, reason,
    consumed_operation_id, created_at, consumed_at
)
SELECT
    id, project_id, scope_hash, max_cost_minor, currency, approved_by, reason,
    consumed_operation_id, created_at, consumed_at
FROM studio_spend_approvals;
DROP TABLE studio_spend_approvals;
ALTER TABLE studio_spend_approvals_v12 RENAME TO studio_spend_approvals;
CREATE INDEX IF NOT EXISTS idx_studio_spend_approvals_project
    ON studio_spend_approvals(project_id, consumed_operation_id);
"""


SCHEMA_V13 = r"""
CREATE TABLE IF NOT EXISTS budget_variance_requests (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    plan_id TEXT NOT NULL REFERENCES budget_plans(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL REFERENCES budget_plan_items(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    estimated_minor INTEGER NOT NULL CHECK (estimated_minor >= 0),
    actual_minor INTEGER NOT NULL CHECK (actual_minor >= 0),
    allowed_minor INTEGER NOT NULL CHECK (allowed_minor >= 0),
    overage_minor INTEGER NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by TEXT,
    reason TEXT,
    decided_by TEXT,
    decision_reason TEXT,
    decided_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(job_id, actual_minor, currency)
);
CREATE INDEX IF NOT EXISTS idx_budget_variance_pending
    ON budget_variance_requests(project_id, status);
"""


SCHEMA_V14 = r"""
CREATE TABLE IF NOT EXISTS asset_costs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    plan_id TEXT REFERENCES budget_plans(id) ON DELETE SET NULL,
    item_id TEXT REFERENCES budget_plan_items(id) ON DELETE SET NULL,
    slot_id TEXT REFERENCES slots(id) ON DELETE SET NULL,
    artifact_id TEXT REFERENCES artifacts(id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    operation TEXT NOT NULL,
    currency TEXT NOT NULL,
    estimated_minor INTEGER NOT NULL CHECK (estimated_minor >= 0),
    actual_minor INTEGER NOT NULL CHECK (actual_minor >= 0),
    variance_minor INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asset_costs_project
    ON asset_costs(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_asset_costs_slot
    ON asset_costs(slot_id);
"""


SCHEMA_V15 = r"""
CREATE TABLE IF NOT EXISTS studio_productions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    operation_id TEXT NOT NULL UNIQUE REFERENCES tool_operations(id) ON DELETE CASCADE,
    transport TEXT NOT NULL,
    external_production_id TEXT,
    title TEXT NOT NULL,
    autonomy TEXT NOT NULL,
    budget_minor INTEGER NOT NULL CHECK (budget_minor >= 0),
    currency TEXT NOT NULL,
    status TEXT NOT NULL,
    packet_hash TEXT NOT NULL,
    last_error TEXT,
    opened_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_studio_productions_project
    ON studio_productions(project_id, status);

CREATE TABLE IF NOT EXISTS studio_jobs (
    id TEXT PRIMARY KEY,
    production_id TEXT NOT NULL REFERENCES studio_productions(id) ON DELETE CASCADE,
    external_job_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    detail_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(production_id, external_job_id)
);
"""


SCHEMA_V16 = r"""
CREATE TABLE budget_variance_requests_v16 (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'plan_item',
    plan_id TEXT REFERENCES budget_plans(id) ON DELETE CASCADE,
    item_id TEXT REFERENCES budget_plan_items(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    operation_id TEXT REFERENCES tool_operations(id) ON DELETE CASCADE,
    estimated_minor INTEGER NOT NULL CHECK (estimated_minor >= 0),
    actual_minor INTEGER NOT NULL CHECK (actual_minor >= 0),
    allowed_minor INTEGER NOT NULL CHECK (allowed_minor >= 0),
    overage_minor INTEGER NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by TEXT,
    reason TEXT,
    decided_by TEXT,
    decision_reason TEXT,
    decided_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(job_id, actual_minor, currency)
);
INSERT INTO budget_variance_requests_v16(
    id, project_id, kind, plan_id, item_id, job_id, operation_id, estimated_minor,
    actual_minor, allowed_minor, overage_minor, currency, status, requested_by,
    reason, decided_by, decision_reason, decided_at, created_at
)
SELECT
    id, project_id, 'plan_item', plan_id, item_id, job_id, NULL, estimated_minor,
    actual_minor, allowed_minor, overage_minor, currency, status, requested_by,
    reason, decided_by, decision_reason, decided_at, created_at
FROM budget_variance_requests;
DROP TABLE budget_variance_requests;
ALTER TABLE budget_variance_requests_v16 RENAME TO budget_variance_requests;
CREATE INDEX IF NOT EXISTS idx_budget_variance_pending
    ON budget_variance_requests(project_id, status);
"""


SCHEMA_V17 = r"""
CREATE TABLE IF NOT EXISTS publication_authorizations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    artifact_hash TEXT NOT NULL,
    channels_json TEXT NOT NULL,
    authorized_by TEXT NOT NULL,
    reference TEXT,
    authorized_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_publication_auth_project
    ON publication_authorizations(project_id, revoked_at);
"""


SCHEMA_V18 = r"""
CREATE TABLE IF NOT EXISTS shot_references (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    artifact_id TEXT REFERENCES artifacts(id) ON DELETE CASCADE,
    element_id TEXT REFERENCES creative_elements(id) ON DELETE CASCADE,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(shot_id, role)
);
CREATE INDEX IF NOT EXISTS idx_shot_references_project
    ON shot_references(project_id);
"""


SCHEMA_V19 = r"""
CREATE TABLE IF NOT EXISTS audio_cues (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    cue_key TEXT NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    shot_id TEXT REFERENCES shots(id) ON DELETE SET NULL,
    role TEXT NOT NULL,
    start_ms INTEGER NOT NULL CHECK (start_ms >= 0),
    duration_ms INTEGER NOT NULL CHECK (duration_ms > 0 AND duration_ms <= 10000),
    gain_db TEXT NOT NULL,
    loop INTEGER NOT NULL DEFAULT 0 CHECK (loop IN (0, 1)),
    verdict TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, cue_key)
);
CREATE INDEX IF NOT EXISTS idx_audio_cues_project ON audio_cues(project_id);
"""


SCHEMA_V20 = r"""
CREATE TABLE IF NOT EXISTS sourcing_decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slot_id TEXT NOT NULL REFERENCES slots(id) ON DELETE CASCADE,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    providers_json TEXT NOT NULL,
    candidates_considered INTEGER NOT NULL CHECK (candidates_considered >= 0),
    rejection_reason TEXT,
    evidence_json TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sourcing_decisions_slot
    ON sourcing_decisions(project_id, slot_id, created_at);
CREATE TABLE IF NOT EXISTS flow_steps (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    step TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    actor TEXT NOT NULL,
    tool TEXT,
    tool_version TEXT,
    evidence_json TEXT NOT NULL,
    approval_id TEXT,
    approved_by TEXT,
    next_action TEXT,
    trade_off TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flow_steps_project
    ON flow_steps(project_id, created_at);
"""


SCHEMA_V21 = r"""
ALTER TABLE slots ADD COLUMN treatment_ceiling TEXT NOT NULL DEFAULT 'ai_created';
ALTER TABLE slots ADD COLUMN exactness TEXT NOT NULL DEFAULT 'generic';
CREATE TABLE IF NOT EXISTS sourcing_candidates (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slot_id TEXT NOT NULL REFERENCES slots(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_asset_id TEXT NOT NULL,
    media_type TEXT NOT NULL,
    source_url TEXT NOT NULL,
    creator TEXT,
    licence TEXT NOT NULL,
    licence_url TEXT,
    eligibility TEXT NOT NULL,
    eligibility_reason TEXT,
    treatment TEXT NOT NULL,
    score INTEGER NOT NULL,
    selected_role TEXT,
    evidence_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, slot_id, provider, provider_asset_id)
);
CREATE INDEX IF NOT EXISTS idx_sourcing_candidates_slot
    ON sourcing_candidates(project_id, slot_id, created_at);
"""


SCHEMA_V22 = r"""
CREATE TABLE IF NOT EXISTS motion_levels (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
    level TEXT NOT NULL,
    source TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_motion_levels_shot
    ON motion_levels(project_id, shot_id, created_at);
"""


SCHEMA_V23 = r"""
CREATE TABLE IF NOT EXISTS ab_experiments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    axis TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    primary_metric TEXT NOT NULL,
    traffic_split TEXT NOT NULL,
    sample_plan TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'declared',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ab_variants (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES ab_experiments(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    artifact_hash TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(experiment_id, label)
);
CREATE INDEX IF NOT EXISTS idx_ab_experiments_project
    ON ab_experiments(project_id, created_at);
"""


SCHEMA_V24 = r"""
ALTER TABLE slots ADD COLUMN style_id TEXT;
ALTER TABLE slots ADD COLUMN media_requirement TEXT;
ALTER TABLE sourcing_candidates ADD COLUMN style_fit INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sourcing_candidates ADD COLUMN style_fit_reason TEXT;
ALTER TABLE motion_levels ADD COLUMN evidence_artifact_id TEXT;
CREATE TABLE IF NOT EXISTS shot_productions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    shot_id TEXT NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
    preset_id TEXT NOT NULL,
    style_id TEXT NOT NULL,
    appears TEXT NOT NULL,
    method TEXT NOT NULL,
    fallback TEXT NOT NULL,
    asset_source_json TEXT NOT NULL,
    exact_items_json TEXT NOT NULL,
    layers_json TEXT NOT NULL,
    motion_json TEXT NOT NULL,
    qa_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(shot_id)
);
CREATE TABLE IF NOT EXISTS scripts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    brief_id TEXT,
    content_hash TEXT NOT NULL,
    text TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    approved_by TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scripts_current
    ON scripts(project_id) WHERE is_current = 1;
"""


SCHEMA_V25 = r"""
CREATE TABLE IF NOT EXISTS change_requests (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    document_schema TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    change_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    current_hash TEXT,
    requested_change TEXT NOT NULL,
    reason TEXT NOT NULL,
    deadline TEXT,
    approver TEXT NOT NULL,
    approval_reference TEXT,
    version_scope TEXT NOT NULL DEFAULT 'none',
    document_hash TEXT,
    impact_json TEXT,
    classification TEXT,
    revision_project_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_change_requests_project
    ON change_requests(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_change_requests_status
    ON change_requests(project_id, status);

CREATE TABLE IF NOT EXISTS change_request_events (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES change_requests(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_change_request_events_request
    ON change_request_events(request_id, created_at);

ALTER TABLE projects ADD COLUMN change_request_id TEXT REFERENCES change_requests(id);
ALTER TABLE budget_plan_items ADD COLUMN variant_label TEXT;
ALTER TABLE budget_plan_items ADD COLUMN ab_experiment_id TEXT REFERENCES ab_experiments(id);
CREATE INDEX IF NOT EXISTS idx_budget_plan_items_variant
    ON budget_plan_items(plan_id, variant_label);

ALTER TABLE flow_steps ADD COLUMN execution_owner TEXT;
ALTER TABLE flow_steps ADD COLUMN studio_role TEXT;
ALTER TABLE flow_steps ADD COLUMN fallback_of TEXT;

CREATE TABLE IF NOT EXISTS ab_deliveries (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    experiment_id TEXT NOT NULL REFERENCES ab_experiments(id) ON DELETE CASCADE,
    output_dir TEXT NOT NULL,
    manifest_artifact_id TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, experiment_id, manifest_hash)
);
CREATE INDEX IF NOT EXISTS idx_ab_deliveries_project
    ON ab_deliveries(project_id, created_at);
"""


SCHEMA_V26 = r"""
CREATE TABLE IF NOT EXISTS stage_critiques (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    subject_hash TEXT,
    reviewer TEXT NOT NULL,
    reviewer_kind TEXT NOT NULL,
    verdict TEXT NOT NULL,
    findings_json TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stage_critiques_project
    ON stage_critiques(project_id, stage, created_at);
CREATE INDEX IF NOT EXISTS idx_stage_critiques_subject
    ON stage_critiques(project_id, stage, subject_id, created_at);

CREATE TABLE IF NOT EXISTS corrective_actions (
    id TEXT PRIMARY KEY,
    critique_id TEXT NOT NULL REFERENCES stage_critiques(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    finding_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    action TEXT NOT NULL,
    owner TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    closed_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_corrective_actions_project
    ON corrective_actions(project_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_corrective_actions_critique
    ON corrective_actions(critique_id, finding_id);
"""


MIGRATIONS = {
    1: SCHEMA_V1,
    2: SCHEMA_V2,
    3: SCHEMA_V3,
    4: SCHEMA_V4,
    5: SCHEMA_V5,
    6: SCHEMA_V6,
    7: SCHEMA_V7,
    8: SCHEMA_V8,
    9: SCHEMA_V9,
    10: SCHEMA_V10,
    11: SCHEMA_V11,
    12: SCHEMA_V12,
    13: SCHEMA_V13,
    14: SCHEMA_V14,
    15: SCHEMA_V15,
    16: SCHEMA_V16,
    17: SCHEMA_V17,
    18: SCHEMA_V18,
    19: SCHEMA_V19,
    20: SCHEMA_V20,
    21: SCHEMA_V21,
    22: SCHEMA_V22,
    23: SCHEMA_V23,
    24: SCHEMA_V24,
    25: SCHEMA_V25,
    26: SCHEMA_V26,
}

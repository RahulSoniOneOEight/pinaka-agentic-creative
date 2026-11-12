from __future__ import annotations

import json
import sqlite3
from collections import deque
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from .database import Database
from .enums import (
    ApprovalStatus,
    ArtifactStatus,
    BudgetEntryKind,
    BudgetItemStatus,
    BudgetPlanStatus,
    JobStatus,
    ProjectStatus,
    QAStatus,
    ToolOperationStatus,
)
from .errors import (
    ApprovalRequiredError,
    BudgetExceededError,
    ConflictError,
    IntegrityError,
    NotFoundError,
    VarianceApprovalRequiredError,
)
from .models import (
    ApprovalRequest,
    ArtifactCreate,
    BudgetPlanCreate,
    CapabilityResult,
    CreativeElementCreate,
    DeliverableCreate,
    DeliverableRecord,
    DependencyRef,
    ExecutionReceiptCreate,
    FactSetCreate,
    GroundingBundleCreate,
    JobRecord,
    JobRequest,
    Money,
    ProjectCreate,
    ProjectRecord,
    ProviderModelProfileCreate,
    SegmentCreate,
    ShotCreate,
    ShotElementBindingCreate,
    ShotReferenceCreate,
    SlotCreate,
    ToolOperationRequest,
    ToolProfileCreate,
    WorkableBriefCreate,
)
from .utils import canonical_json, new_id, sha256_json, slugify, utc_now


class Repository:
    """Atomic persistence operations and append-only audit events."""

    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        *,
        project_id: str | None,
        entity_type: str,
        entity_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO events(id, project_id, entity_type, entity_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("evt"),
                project_id,
                entity_type,
                entity_id,
                event_type,
                canonical_json(payload),
                utc_now(),
            ),
        )

    @staticmethod
    def _project_from_row(row: sqlite3.Row) -> ProjectRecord:
        currency = row["currency"]
        return ProjectRecord(
            id=row["id"],
            parent_project_id=row["parent_project_id"],
            slug=row["slug"],
            name=row["name"],
            objective=row["objective"],
            mode=row["mode"],
            audience=row["audience"],
            output_format=row["output_format"],
            language=row["language"],
            duration_seconds=Decimal(row["duration_seconds"]),
            status=row["status"],
            max_budget=Money.from_minor(row["max_budget_minor"], currency),
            reserved=Money.from_minor(row["reserved_minor"], currency),
            spent=Money.from_minor(row["spent_minor"], currency),
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> JobRecord:
        currency = row["currency"]
        return JobRecord(
            id=row["id"],
            project_id=row["project_id"],
            provider=row["provider"],
            endpoint=row["endpoint"],
            operation=row["operation"],
            idempotency_key=row["idempotency_key"],
            status=row["status"],
            estimated_cost=Money.from_minor(row["estimated_cost_minor"], currency),
            reserved_cost=Money.from_minor(row["reserved_cost_minor"], currency),
            actual_cost=Money.from_minor(row["actual_cost_minor"], currency),
            provider_job_id=row["provider_job_id"],
            request_hash=row["request_hash"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _require_project(
        connection: sqlite3.Connection, project_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not row:
            raise NotFoundError(f"Project not found: {project_id}")
        return row

    @staticmethod
    def _require_job(connection: sqlite3.Connection, job_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not row:
            raise NotFoundError(f"Job not found: {job_id}")
        return row

    def create_project(self, request: ProjectCreate) -> ProjectRecord:
        project_id = new_id("prj")
        now = utc_now()
        slug = slugify(request.name)
        with self.database.transaction() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO projects(
                        id, slug, name, objective, mode, audience, output_format, language,
                        duration_seconds, status, max_budget_minor, currency, parent_project_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        slug,
                        request.name,
                        request.objective,
                        request.mode.value,
                        request.audience,
                        request.output_format,
                        request.language,
                        str(request.duration_seconds),
                        ProjectStatus.DRAFT.value,
                        request.max_budget.minor,
                        request.max_budget.currency,
                        request.parent_project_id,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError(
                    f"A project with slug '{slug}' already exists"
                ) from error
            self._event(
                connection,
                project_id=project_id,
                entity_type="project",
                entity_id=project_id,
                event_type="project.created",
                payload={"slug": slug, "status": ProjectStatus.DRAFT.value},
            )
            return self._project_from_row(self._require_project(connection, project_id))

    def get_project(self, project_id: str) -> ProjectRecord:
        with self.database.read() as connection:
            return self._project_from_row(self._require_project(connection, project_id))

    def list_projects(self) -> list[ProjectRecord]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM projects ORDER BY created_at"
            ).fetchall()
            return [self._project_from_row(row) for row in rows]

    def list_child_projects(self, parent_project_id: str) -> list[ProjectRecord]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM projects WHERE parent_project_id = ? ORDER BY created_at",
                (parent_project_id,),
            ).fetchall()
            return [self._project_from_row(row) for row in rows]

    def record_scene_plan(
        self, project_id: str, artifact_id: str, content_hash: str
    ) -> dict[str, Any]:
        plan_id = new_id("scp")
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            connection.execute(
                "INSERT INTO scene_plans(id, project_id, artifact_id, content_hash, "
                "created_at) VALUES (?, ?, ?, ?, ?)",
                (plan_id, project_id, artifact_id, content_hash, now),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="scene_plan",
                entity_id=plan_id,
                event_type="scene_plan.recorded",
                payload={"artifact_id": artifact_id, "content_hash": content_hash},
            )
        return {
            "id": plan_id,
            "project_id": project_id,
            "artifact_id": artifact_id,
            "content_hash": content_hash,
            "created_at": now,
        }

    def latest_scene_plan(self, project_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM scene_plans WHERE project_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "project_id": row["project_id"],
                "artifact_id": row["artifact_id"],
                "content_hash": row["content_hash"],
                "created_at": row["created_at"],
            }

    @staticmethod
    def _brief_decision_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "field": row["field"],
            "value": json.loads(row["value_json"]),
            "state": row["state"],
            "basis": row["basis"],
            "risk": row["risk"],
            "reversible": bool(row["reversible"]),
            "alternatives_considered": json.loads(row["alternatives_json"]),
            "affected_shot_keys": json.loads(row["affected_shot_keys_json"]),
            "review_gate": row["review_gate"],
            "blocked_work": json.loads(row["blocked_work_json"]),
            "created_at": row["created_at"],
        }

    def _workable_brief_dict(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        decisions = connection.execute(
            "SELECT * FROM brief_decisions WHERE brief_id = ? ORDER BY field",
            (row["id"],),
        ).fetchall()
        package = json.loads(row["package_json"])
        package["decisions"] = [self._brief_decision_dict(item) for item in decisions]
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "version": row["version"],
            "source_artifact_id": row["source_artifact_id"],
            "source_content_hash": row["source_content_hash"],
            "content_hash": row["content_hash"],
            "is_current": bool(row["is_current"]),
            "created_at": row["created_at"],
            "package": package,
        }

    def record_workable_brief(
        self, project_id: str, request: WorkableBriefCreate
    ) -> dict[str, Any]:
        now = utc_now()
        payload = request.model_dump(mode="json")
        content_hash = sha256_json(payload)
        brief_id = new_id("brief")
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            source = connection.execute(
                "SELECT * FROM artifacts WHERE id = ? AND project_id = ?",
                (request.source_artifact_id, project_id),
            ).fetchone()
            if not source:
                raise NotFoundError(
                    f"Brief source artifact not found in project: {request.source_artifact_id}"
                )
            if source["content_hash"] != request.source_content_hash:
                raise IntegrityError("Brief source artifact hash does not match")
            if source["kind"] != "original" or not bool(source["immutable"]):
                raise IntegrityError(
                    "A workable brief must derive from a protected original artifact"
                )
            current = connection.execute(
                "SELECT * FROM workable_briefs WHERE project_id = ? AND is_current = 1",
                (project_id,),
            ).fetchone()
            if current and current["content_hash"] == content_hash:
                return self._workable_brief_dict(connection, current)
            version = int(current["version"]) + 1 if current else 1
            if current:
                connection.execute(
                    "UPDATE workable_briefs SET is_current = 0 WHERE id = ?",
                    (current["id"],),
                )
            connection.execute(
                """
                INSERT INTO workable_briefs(
                    id, project_id, version, source_artifact_id, source_content_hash,
                    package_json, content_hash, is_current, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    brief_id,
                    project_id,
                    version,
                    request.source_artifact_id,
                    request.source_content_hash,
                    canonical_json(payload),
                    content_hash,
                    now,
                ),
            )
            for decision in request.decisions:
                connection.execute(
                    """
                    INSERT INTO brief_decisions(
                        id, brief_id, project_id, field, value_json, state, basis,
                        risk, reversible, alternatives_json, affected_shot_keys_json,
                        review_gate, blocked_work_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("decision"),
                        brief_id,
                        project_id,
                        decision.field,
                        canonical_json(decision.value),
                        decision.state.value,
                        decision.basis,
                        decision.risk.value,
                        int(decision.reversible),
                        canonical_json(decision.alternatives_considered),
                        canonical_json(decision.affected_shot_keys),
                        decision.review_gate.value if decision.review_gate else None,
                        canonical_json(decision.blocked_work),
                        now,
                    ),
                )
            invalidated = {"approvals": [], "artifacts": []}
            if current:
                invalidated = self._invalidate_dependency_graph(
                    connection,
                    project_id=project_id,
                    entity_type="workable_brief",
                    entity_id=current["id"],
                    reason="Current workable brief changed",
                )
            self._event(
                connection,
                project_id=project_id,
                entity_type="workable_brief",
                entity_id=brief_id,
                event_type="workable_brief.recorded",
                payload={
                    "version": version,
                    "content_hash": content_hash,
                    "source_artifact_id": request.source_artifact_id,
                    "invalidated": invalidated,
                },
            )
            row = connection.execute(
                "SELECT * FROM workable_briefs WHERE id = ?", (brief_id,)
            ).fetchone()
            return self._workable_brief_dict(connection, row)

    def latest_workable_brief(self, project_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            row = connection.execute(
                "SELECT * FROM workable_briefs WHERE project_id = ? AND is_current = 1",
                (project_id,),
            ).fetchone()
            return self._workable_brief_dict(connection, row) if row else None

    def get_workable_brief(self, brief_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM workable_briefs WHERE id = ?", (brief_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Workable brief not found: {brief_id}")
            return self._workable_brief_dict(connection, row)

    @staticmethod
    def _deliverable_from_row(row: sqlite3.Row) -> DeliverableRecord:
        return DeliverableRecord(
            id=row["id"],
            project_id=row["project_id"],
            stable_key=row["stable_key"],
            name=row["name"],
            objective=row["objective"],
            kind=row["kind"],
            output_format=row["output_format"],
            language=row["language"],
            duration_seconds=(
                Decimal(row["duration_seconds"])
                if row["duration_seconds"] is not None
                else None
            ),
            acceptance_targets=json.loads(row["acceptance_targets_json"]),
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_deliverable(
        self, project_id: str, request: DeliverableCreate
    ) -> DeliverableRecord:
        deliverable_id = new_id("del")
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            existing = connection.execute(
                "SELECT * FROM deliverables WHERE project_id = ? AND stable_key = ?",
                (project_id, request.stable_key),
            ).fetchone()
            if existing:
                record = self._deliverable_from_row(existing)
                observed = {
                    key: record.model_dump(mode="json")[key]
                    for key in type(request).model_fields
                }
                if sha256_json(observed) == sha256_json(
                    request.model_dump(mode="json")
                ):
                    return record
                raise ConflictError(
                    f"Deliverable key already exists with different content: "
                    f"{request.stable_key}"
                )
            try:
                connection.execute(
                    """
                    INSERT INTO deliverables(
                        id, project_id, stable_key, name, objective, kind, output_format,
                        language, duration_seconds, acceptance_targets_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        deliverable_id,
                        project_id,
                        request.stable_key,
                        request.name,
                        request.objective,
                        request.kind.value,
                        request.output_format,
                        request.language,
                        (
                            str(request.duration_seconds)
                            if request.duration_seconds is not None
                            else None
                        ),
                        canonical_json(request.acceptance_targets),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError(
                    f"Deliverable key already exists: {request.stable_key}"
                ) from error
            self._event(
                connection,
                project_id=project_id,
                entity_type="deliverable",
                entity_id=deliverable_id,
                event_type="deliverable.created",
                payload=request.model_dump(mode="json"),
            )
            row = connection.execute(
                "SELECT * FROM deliverables WHERE id = ?", (deliverable_id,)
            ).fetchone()
            return self._deliverable_from_row(row)

    def list_deliverables(self, project_id: str) -> list[DeliverableRecord]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM deliverables WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
            return [self._deliverable_from_row(row) for row in rows]

    @staticmethod
    def _segment_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "deliverable_id": row["deliverable_id"],
            "stable_key": row["stable_key"],
            "ordinal": row["ordinal"],
            "purpose": row["purpose"],
            "duration_seconds": row["duration_seconds"],
            "narrative": row["narrative"],
            "constraints": json.loads(row["constraints_json"]),
            "version": row["version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_segment(self, project_id: str, request: SegmentCreate) -> dict[str, Any]:
        segment_id = new_id("seg")
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            deliverable = connection.execute(
                "SELECT id FROM deliverables WHERE id = ? AND project_id = ?",
                (request.deliverable_id, project_id),
            ).fetchone()
            if not deliverable:
                raise NotFoundError(
                    f"Deliverable not found in project: {request.deliverable_id}"
                )
            existing = connection.execute(
                """
                SELECT * FROM segments
                WHERE deliverable_id = ? AND stable_key = ?
                """,
                (request.deliverable_id, request.stable_key),
            ).fetchone()
            if existing:
                observed = self._segment_dict(existing)
                comparable = {
                    "deliverable_id": observed["deliverable_id"],
                    "stable_key": observed["stable_key"],
                    "ordinal": observed["ordinal"],
                    "purpose": observed["purpose"],
                    "duration_seconds": observed["duration_seconds"],
                    "narrative": observed["narrative"],
                    "constraints": observed["constraints"],
                }
                if sha256_json(comparable) == sha256_json(
                    request.model_dump(mode="json")
                ):
                    return observed
                raise ConflictError(
                    f"Segment key already exists with different content: "
                    f"{request.stable_key}"
                )
            try:
                connection.execute(
                    """
                    INSERT INTO segments(
                        id, project_id, deliverable_id, stable_key, ordinal, purpose,
                        duration_seconds, narrative, constraints_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        segment_id,
                        project_id,
                        request.deliverable_id,
                        request.stable_key,
                        request.ordinal,
                        request.purpose,
                        str(request.duration_seconds),
                        request.narrative,
                        canonical_json(request.constraints),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError(
                    f"Segment key or ordinal already exists: "
                    f"{request.stable_key}/{request.ordinal}"
                ) from error
            self._event(
                connection,
                project_id=project_id,
                entity_type="segment",
                entity_id=segment_id,
                event_type="segment.created",
                payload=request.model_dump(mode="json"),
            )
            row = connection.execute(
                "SELECT * FROM segments WHERE id = ?", (segment_id,)
            ).fetchone()
            return self._segment_dict(row)

    def list_segments(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM segments WHERE project_id = ? ORDER BY deliverable_id, ordinal",
                (project_id,),
            ).fetchall()
            return [self._segment_dict(row) for row in rows]

    def set_project_status(
        self,
        project_id: str,
        status: ProjectStatus,
        *,
        reason: str,
        expected_status: ProjectStatus | None = None,
    ) -> ProjectRecord:
        now = utc_now()
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            if expected_status and project["status"] != expected_status.value:
                raise ConflictError(
                    f"Project status changed: expected {expected_status.value}, "
                    f"observed {project['status']}"
                )
            previous = project["status"]
            connection.execute(
                """
                UPDATE projects
                SET status = ?, version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (status.value, now, project_id),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="project",
                entity_id=project_id,
                event_type="project.status_changed",
                payload={"from": previous, "to": status.value, "reason": reason},
            )
            return self._project_from_row(self._require_project(connection, project_id))

    @staticmethod
    def _fact_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "version": row["version"],
            "source_ref": row["source_ref"],
            "effective_from": row["effective_from"],
            "values": json.loads(row["values_json"]),
            "evidence_refs": json.loads(row["evidence_refs_json"]),
            "content_hash": row["content_hash"],
            "is_current": bool(row["is_current"]),
            "created_at": row["created_at"],
        }

    def current_fact_set(self, project_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            row = connection.execute(
                "SELECT * FROM fact_sets WHERE project_id = ? AND is_current = 1",
                (project_id,),
            ).fetchone()
            return self._fact_dict(row) if row else None

    @staticmethod
    def _grounding_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "kind": row["kind"],
            "version": row["version"],
            "source_ref": row["source_ref"],
            "effective_from": row["effective_from"],
            "values": json.loads(row["values_json"]),
            "evidence_refs": json.loads(row["evidence_refs_json"]),
            "content_hash": row["content_hash"],
            "is_current": bool(row["is_current"]),
            "created_at": row["created_at"],
        }

    def current_grounding_bundles(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                """
                SELECT * FROM grounding_bundles
                WHERE project_id = ? AND is_current = 1
                ORDER BY kind
                """,
                (project_id,),
            ).fetchall()
            return [self._grounding_dict(row) for row in rows]

    def add_grounding_bundle(
        self, project_id: str, request: GroundingBundleCreate
    ) -> dict[str, Any]:
        now = utc_now()
        bundle_id = new_id("ground")
        content = request.model_dump(mode="json")
        content_hash = sha256_json(content)
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            previous = connection.execute(
                """
                SELECT * FROM grounding_bundles
                WHERE project_id = ? AND kind = ? AND is_current = 1
                """,
                (project_id, request.kind.value),
            ).fetchone()
            if previous and previous["content_hash"] == content_hash:
                return self._grounding_dict(previous)
            version = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1
                    FROM grounding_bundles WHERE project_id = ? AND kind = ?
                    """,
                    (project_id, request.kind.value),
                ).fetchone()[0]
            )
            if previous:
                connection.execute(
                    "UPDATE grounding_bundles SET is_current = 0 WHERE id = ?",
                    (previous["id"],),
                )
            connection.execute(
                """
                INSERT INTO grounding_bundles(
                    id, project_id, kind, version, source_ref, effective_from,
                    values_json, evidence_refs_json, content_hash, is_current, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    bundle_id,
                    project_id,
                    request.kind.value,
                    version,
                    request.source_ref,
                    request.effective_from,
                    canonical_json(request.values),
                    canonical_json(request.evidence_refs),
                    content_hash,
                    now,
                ),
            )
            invalidated = {"approvals": [], "artifacts": []}
            if previous:
                invalidated = self._invalidate_dependency_graph(
                    connection,
                    project_id=project_id,
                    entity_type="grounding_bundle",
                    entity_id=previous["id"],
                    reason=(
                        f"{request.kind.value} grounding replaced by version {version}"
                    ),
                )
                self._rollback_status_after_grounding_change(
                    connection, project_id, now
                )
            if project["status"] == ProjectStatus.BRIEF_APPROVED.value:
                connection.execute(
                    """
                    UPDATE projects
                    SET status = ?, version = version + 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (ProjectStatus.GROUNDING_LOCKED.value, now, project_id),
                )
                self._event(
                    connection,
                    project_id=project_id,
                    entity_type="project",
                    entity_id=project_id,
                    event_type="project.status_changed",
                    payload={
                        "from": ProjectStatus.BRIEF_APPROVED.value,
                        "to": ProjectStatus.GROUNDING_LOCKED.value,
                        "reason": f"{request.kind.value} grounding v{version} locked",
                    },
                )
            self._event(
                connection,
                project_id=project_id,
                entity_type="grounding_bundle",
                entity_id=bundle_id,
                event_type="grounding_bundle.created",
                payload={
                    "kind": request.kind.value,
                    "version": version,
                    "content_hash": content_hash,
                    "replaces": previous["id"] if previous else None,
                    "invalidated": invalidated,
                },
            )
            row = connection.execute(
                "SELECT * FROM grounding_bundles WHERE id = ?", (bundle_id,)
            ).fetchone()
            return self._grounding_dict(row)

    def add_fact_set(self, project_id: str, request: FactSetCreate) -> dict[str, Any]:
        now = utc_now()
        fact_id = new_id("fact")
        content = {
            "source_ref": request.source_ref,
            "effective_from": request.effective_from,
            "values": request.values,
            "evidence_refs": request.evidence_refs,
        }
        content_hash = sha256_json(content)
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            previous = connection.execute(
                "SELECT * FROM fact_sets WHERE project_id = ? AND is_current = 1",
                (project_id,),
            ).fetchone()
            if previous and previous["content_hash"] == content_hash:
                return self._fact_dict(previous)

            version = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM fact_sets WHERE project_id = ?",
                    (project_id,),
                ).fetchone()[0]
            )
            if previous:
                connection.execute(
                    "UPDATE fact_sets SET is_current = 0 WHERE id = ?",
                    (previous["id"],),
                )
            connection.execute(
                """
                INSERT INTO fact_sets(
                    id, project_id, version, source_ref, effective_from, values_json,
                    evidence_refs_json, content_hash, is_current, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    fact_id,
                    project_id,
                    version,
                    request.source_ref,
                    request.effective_from,
                    canonical_json(request.values),
                    canonical_json(request.evidence_refs),
                    content_hash,
                    now,
                ),
            )
            invalidated = {"approvals": [], "artifacts": []}
            if previous:
                invalidated = self._invalidate_dependency_graph(
                    connection,
                    project_id=project_id,
                    entity_type="fact_set",
                    entity_id=previous["id"],
                    reason=f"Fact set replaced by version {version}",
                )
                self._rollback_status_after_fact_change(connection, project_id, now)
            if project["status"] == ProjectStatus.BRIEF_APPROVED.value:
                connection.execute(
                    """
                    UPDATE projects
                    SET status = ?, version = version + 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (ProjectStatus.FACTS_LOCKED.value, now, project_id),
                )
                self._event(
                    connection,
                    project_id=project_id,
                    entity_type="project",
                    entity_id=project_id,
                    event_type="project.status_changed",
                    payload={
                        "from": ProjectStatus.BRIEF_APPROVED.value,
                        "to": ProjectStatus.FACTS_LOCKED.value,
                        "reason": f"Fact set v{version} locked",
                    },
                )
            self._event(
                connection,
                project_id=project_id,
                entity_type="fact_set",
                entity_id=fact_id,
                event_type="fact_set.created",
                payload={
                    "version": version,
                    "content_hash": content_hash,
                    "replaces": previous["id"] if previous else None,
                    "invalidated": invalidated,
                },
            )
            row = connection.execute(
                "SELECT * FROM fact_sets WHERE id = ?", (fact_id,)
            ).fetchone()
            return self._fact_dict(row)

    def _rollback_status_after_fact_change(
        self, connection: sqlite3.Connection, project_id: str, now: str
    ) -> None:
        project = self._require_project(connection, project_id)
        pipeline_order = [
            ProjectStatus.DRAFT,
            ProjectStatus.PLANNED,
            ProjectStatus.BRIEF_APPROVED,
            ProjectStatus.FACTS_LOCKED,
            ProjectStatus.GROUNDING_LOCKED,
            ProjectStatus.SOURCING,
            ProjectStatus.ANIMATIC_READY,
            ProjectStatus.ANIMATIC_APPROVED,
            ProjectStatus.FINAL_CANDIDATE,
            ProjectStatus.FINAL_APPROVED,
            ProjectStatus.ARCHIVED,
        ]
        current = ProjectStatus(project["status"])
        if current in pipeline_order and pipeline_order.index(
            current
        ) > pipeline_order.index(ProjectStatus.FACTS_LOCKED):
            connection.execute(
                """
                UPDATE projects
                SET status = ?, version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (ProjectStatus.FACTS_LOCKED.value, now, project_id),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="project",
                entity_id=project_id,
                event_type="project.rolled_back",
                payload={
                    "from": current.value,
                    "to": ProjectStatus.FACTS_LOCKED.value,
                    "reason": "Current fact set changed",
                },
            )

    def _rollback_status_after_grounding_change(
        self, connection: sqlite3.Connection, project_id: str, now: str
    ) -> None:
        project = self._require_project(connection, project_id)
        pipeline_order = [
            ProjectStatus.DRAFT,
            ProjectStatus.PLANNED,
            ProjectStatus.BRIEF_APPROVED,
            ProjectStatus.FACTS_LOCKED,
            ProjectStatus.GROUNDING_LOCKED,
            ProjectStatus.SOURCING,
            ProjectStatus.ANIMATIC_READY,
            ProjectStatus.ANIMATIC_APPROVED,
            ProjectStatus.FINAL_CANDIDATE,
            ProjectStatus.FINAL_APPROVED,
            ProjectStatus.ARCHIVED,
        ]
        current = ProjectStatus(project["status"])
        if current in pipeline_order and pipeline_order.index(
            current
        ) > pipeline_order.index(ProjectStatus.GROUNDING_LOCKED):
            connection.execute(
                """
                UPDATE projects
                SET status = ?, version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (ProjectStatus.GROUNDING_LOCKED.value, now, project_id),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="project",
                entity_id=project_id,
                event_type="project.rolled_back",
                payload={
                    "from": current.value,
                    "to": ProjectStatus.GROUNDING_LOCKED.value,
                    "reason": "Current grounding bundle changed",
                },
            )

    def _invalidate_dependency_graph(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: str,
        entity_type: str,
        entity_id: str,
        reason: str,
    ) -> dict[str, list[str]]:
        queue = deque([(entity_type, entity_id)])
        visited: set[tuple[str, str]] = set()
        invalidated_approvals: list[str] = []
        stale_artifacts: list[str] = []
        now = utc_now()

        while queue:
            dependency_type, dependency_id = queue.popleft()
            if (dependency_type, dependency_id) in visited:
                continue
            visited.add((dependency_type, dependency_id))

            approval_rows = connection.execute(
                """
                SELECT a.id
                FROM approvals a
                JOIN approval_dependencies d ON d.approval_id = a.id
                WHERE a.project_id = ? AND a.status = ?
                  AND d.entity_type = ? AND d.entity_id = ?
                """,
                (
                    project_id,
                    ApprovalStatus.ACTIVE.value,
                    dependency_type,
                    dependency_id,
                ),
            ).fetchall()
            for approval in approval_rows:
                approval_id = approval["id"]
                connection.execute(
                    """
                    UPDATE approvals
                    SET status = ?, invalidated_at = ?, invalidation_reason = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        ApprovalStatus.INVALIDATED.value,
                        now,
                        reason,
                        approval_id,
                        ApprovalStatus.ACTIVE.value,
                    ),
                )
                invalidated_approvals.append(approval_id)
                self._event(
                    connection,
                    project_id=project_id,
                    entity_type="approval",
                    entity_id=approval_id,
                    event_type="approval.invalidated",
                    payload={
                        "dependency_type": dependency_type,
                        "dependency_id": dependency_id,
                        "reason": reason,
                    },
                )
                queue.append(("approval", approval_id))

            artifact_rows = connection.execute(
                """
                SELECT a.id
                FROM artifacts a
                JOIN artifact_dependencies d ON d.artifact_id = a.id
                WHERE a.project_id = ? AND a.status = ?
                  AND d.entity_type = ? AND d.entity_id = ?
                """,
                (
                    project_id,
                    ArtifactStatus.CURRENT.value,
                    dependency_type,
                    dependency_id,
                ),
            ).fetchall()
            for artifact in artifact_rows:
                artifact_id = artifact["id"]
                connection.execute(
                    "UPDATE artifacts SET status = ?, stale_reason = ? WHERE id = ?",
                    (ArtifactStatus.STALE.value, reason, artifact_id),
                )
                stale_artifacts.append(artifact_id)
                self._event(
                    connection,
                    project_id=project_id,
                    entity_type="artifact",
                    entity_id=artifact_id,
                    event_type="artifact.marked_stale",
                    payload={
                        "dependency_type": dependency_type,
                        "dependency_id": dependency_id,
                        "reason": reason,
                    },
                )
                queue.append(("artifact", artifact_id))

        return {"approvals": invalidated_approvals, "artifacts": stale_artifacts}

    @staticmethod
    def _shot_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "stable_key": row["stable_key"],
            "ordinal": row["ordinal"],
            "beat": row["beat"],
            "duration_seconds": row["duration_seconds"],
            "narrative": row["narrative"],
            "requirements": json.loads(row["requirements_json"]),
            "version": row["version"],
        }

    def add_shot(self, project_id: str, request: ShotCreate) -> dict[str, Any]:
        shot_id = new_id("shot")
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            try:
                connection.execute(
                    """
                    INSERT INTO shots(
                        id, project_id, stable_key, ordinal, beat, duration_seconds,
                        narrative, requirements_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        shot_id,
                        project_id,
                        request.stable_key,
                        request.ordinal,
                        request.beat,
                        str(request.duration_seconds),
                        request.narrative,
                        canonical_json(request.requirements),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError(
                    f"Shot key or ordinal already exists: {request.stable_key}/{request.ordinal}"
                ) from error
            self._event(
                connection,
                project_id=project_id,
                entity_type="shot",
                entity_id=shot_id,
                event_type="shot.created",
                payload=request.model_dump(mode="json"),
            )
            row = connection.execute(
                "SELECT * FROM shots WHERE id = ?", (shot_id,)
            ).fetchone()
            return self._shot_dict(row)

    def get_shot(self, shot_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM shots WHERE id = ?", (shot_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Shot not found: {shot_id}")
            return self._shot_dict(row)

    def list_shots(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM shots WHERE project_id = ? ORDER BY ordinal",
                (project_id,),
            ).fetchall()
            return [self._shot_dict(row) for row in rows]

    def add_slot(self, project_id: str, request: SlotCreate) -> dict[str, Any]:
        slot_id = new_id("slot")
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            shot = connection.execute(
                "SELECT id FROM shots WHERE id = ? AND project_id = ?",
                (request.shot_id, project_id),
            ).fetchone()
            if not shot:
                raise NotFoundError(f"Shot not found in project: {request.shot_id}")
            try:
                connection.execute(
                    """
                    INSERT INTO slots(
                        id, project_id, shot_id, stable_key, role, identity_requirement,
                        fact_requirement, transformation_permission, text_zone,
                        approved_sources_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        slot_id,
                        project_id,
                        request.shot_id,
                        request.stable_key,
                        request.role,
                        request.identity_requirement,
                        request.fact_requirement,
                        request.transformation_permission,
                        request.text_zone,
                        canonical_json(request.approved_sources),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError(
                    f"Slot key already exists: {request.stable_key}"
                ) from error
            self._event(
                connection,
                project_id=project_id,
                entity_type="slot",
                entity_id=slot_id,
                event_type="slot.created",
                payload=request.model_dump(mode="json"),
            )
            return {
                "id": slot_id,
                "project_id": project_id,
                **request.model_dump(mode="json"),
                "version": 1,
            }

    @staticmethod
    def _creative_element_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "stable_key": row["stable_key"],
            "version": row["version"],
            "kind": row["kind"],
            "name": row["name"],
            "specification": json.loads(row["specification_json"]),
            "continuity": json.loads(row["continuity_json"]),
            "source_artifact_id": row["source_artifact_id"],
            "content_hash": row["content_hash"],
            "is_current": bool(row["is_current"]),
            "created_at": row["created_at"],
        }

    def record_creative_element(
        self, project_id: str, request: CreativeElementCreate
    ) -> dict[str, Any]:
        now = utc_now()
        payload = request.model_dump(mode="json")
        content_hash = sha256_json(payload)
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            if request.source_artifact_id:
                source = connection.execute(
                    "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                    (request.source_artifact_id, project_id),
                ).fetchone()
                if not source:
                    raise NotFoundError(
                        "Element source artifact not found in project: "
                        f"{request.source_artifact_id}"
                    )
            current = connection.execute(
                """
                SELECT * FROM creative_elements
                WHERE project_id = ? AND stable_key = ? AND is_current = 1
                """,
                (project_id, request.stable_key),
            ).fetchone()
            if current and current["content_hash"] == content_hash:
                return self._creative_element_dict(current)
            version = int(current["version"]) + 1 if current else 1
            if current:
                connection.execute(
                    "UPDATE creative_elements SET is_current = 0 WHERE id = ?",
                    (current["id"],),
                )
            element_id = new_id("element")
            connection.execute(
                """
                INSERT INTO creative_elements(
                    id, project_id, stable_key, version, kind, name,
                    specification_json, continuity_json, source_artifact_id,
                    content_hash, is_current, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    element_id,
                    project_id,
                    request.stable_key,
                    version,
                    request.kind.value,
                    request.name,
                    canonical_json(request.specification),
                    canonical_json(request.continuity),
                    request.source_artifact_id,
                    content_hash,
                    now,
                ),
            )
            rebound_count = 0
            if current:
                bindings = connection.execute(
                    """
                    SELECT * FROM shot_element_bindings
                    WHERE element_id = ? AND is_current = 1
                    """,
                    (current["id"],),
                ).fetchall()
                for binding in bindings:
                    connection.execute(
                        """
                        UPDATE shot_element_bindings
                        SET is_current = 0, updated_at = ? WHERE id = ?
                        """,
                        (now, binding["id"]),
                    )
                    rebound_id = new_id("binding")
                    connection.execute(
                        """
                        INSERT INTO shot_element_bindings(
                            id, project_id, shot_id, element_id, role, notes,
                            is_current, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            rebound_id,
                            project_id,
                            binding["shot_id"],
                            element_id,
                            binding["role"],
                            binding["notes"],
                            now,
                            now,
                        ),
                    )
                    rebound_count += 1
                    self._event(
                        connection,
                        project_id=project_id,
                        entity_type="shot_element_binding",
                        entity_id=rebound_id,
                        event_type="shot_element.rebound",
                        payload={
                            "from_binding_id": binding["id"],
                            "from_element_id": current["id"],
                            "to_element_id": element_id,
                        },
                    )
            invalidated = {"approvals": [], "artifacts": []}
            if current:
                invalidated = self._invalidate_dependency_graph(
                    connection,
                    project_id=project_id,
                    entity_type="creative_element",
                    entity_id=current["id"],
                    reason=f"Creative element {request.stable_key} changed",
                )
            self._event(
                connection,
                project_id=project_id,
                entity_type="creative_element",
                entity_id=element_id,
                event_type="creative_element.recorded",
                payload={
                    "stable_key": request.stable_key,
                    "version": version,
                    "content_hash": content_hash,
                    "rebound_shot_count": rebound_count,
                    "invalidated": invalidated,
                },
            )
            row = connection.execute(
                "SELECT * FROM creative_elements WHERE id = ?", (element_id,)
            ).fetchone()
            return self._creative_element_dict(row)

    def get_creative_element(self, element_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM creative_elements WHERE id = ?", (element_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Creative element not found: {element_id}")
            return self._creative_element_dict(row)

    def list_creative_elements(
        self, project_id: str, *, current_only: bool = True
    ) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            query = "SELECT * FROM creative_elements WHERE project_id = ?"
            if current_only:
                query += " AND is_current = 1"
            query += " ORDER BY stable_key, version"
            rows = connection.execute(query, (project_id,)).fetchall()
            return [self._creative_element_dict(row) for row in rows]

    @staticmethod
    def _shot_element_binding_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "shot_id": row["shot_id"],
            "element_id": row["element_id"],
            "role": row["role"],
            "notes": row["notes"],
            "is_current": bool(row["is_current"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def bind_element_to_shot(
        self, project_id: str, request: ShotElementBindingCreate
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            shot = connection.execute(
                "SELECT id FROM shots WHERE id = ? AND project_id = ?",
                (request.shot_id, project_id),
            ).fetchone()
            if not shot:
                raise NotFoundError(f"Shot not found in project: {request.shot_id}")
            element = connection.execute(
                """
                SELECT id FROM creative_elements
                WHERE id = ? AND project_id = ? AND is_current = 1
                """,
                (request.element_id, project_id),
            ).fetchone()
            if not element:
                raise NotFoundError(
                    f"Current creative element not found in project: {request.element_id}"
                )
            current = connection.execute(
                """
                SELECT * FROM shot_element_bindings
                WHERE project_id = ? AND shot_id = ? AND role = ? AND is_current = 1
                """,
                (project_id, request.shot_id, request.role),
            ).fetchone()
            if (
                current
                and current["element_id"] == request.element_id
                and current["notes"] == request.notes
            ):
                return self._shot_element_binding_dict(current)
            if current:
                connection.execute(
                    "UPDATE shot_element_bindings SET is_current = 0, updated_at = ? WHERE id = ?",
                    (now, current["id"]),
                )
            binding_id = new_id("binding")
            connection.execute(
                """
                INSERT INTO shot_element_bindings(
                    id, project_id, shot_id, element_id, role, notes,
                    is_current, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    binding_id,
                    project_id,
                    request.shot_id,
                    request.element_id,
                    request.role,
                    request.notes,
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="shot_element_binding",
                entity_id=binding_id,
                event_type="shot_element.bound",
                payload={
                    **request.model_dump(mode="json"),
                    "replaced_binding_id": current["id"] if current else None,
                },
            )
            row = connection.execute(
                "SELECT * FROM shot_element_bindings WHERE id = ?", (binding_id,)
            ).fetchone()
            return self._shot_element_binding_dict(row)

    def list_shot_element_bindings(
        self, project_id: str, *, shot_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            if shot_id:
                rows = connection.execute(
                    """
                    SELECT * FROM shot_element_bindings
                    WHERE project_id = ? AND shot_id = ? AND is_current = 1
                    ORDER BY role
                    """,
                    (project_id, shot_id),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM shot_element_bindings
                    WHERE project_id = ? AND is_current = 1
                    ORDER BY shot_id, role
                    """,
                    (project_id,),
                ).fetchall()
            return [self._shot_element_binding_dict(row) for row in rows]

    def active_approval(self, project_id: str, stage: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            row = connection.execute(
                "SELECT * FROM approvals WHERE project_id = ? AND stage = ? AND status = ?",
                (project_id, stage, ApprovalStatus.ACTIVE.value),
            ).fetchone()
            return self._approval_dict(connection, row) if row else None

    @staticmethod
    def _approval_dict(
        connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        dependencies = connection.execute(
            """
            SELECT entity_type, entity_id, entity_hash
            FROM approval_dependencies WHERE approval_id = ?
            """,
            (row["id"],),
        ).fetchall()
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "stage": row["stage"],
            "status": row["status"],
            "approved_by": row["approved_by"],
            "scope": json.loads(row["scope_json"]),
            "subject_hash": row["subject_hash"],
            "output_hash": row["output_hash"],
            "approved_at": row["approved_at"],
            "invalidated_at": row["invalidated_at"],
            "invalidation_reason": row["invalidation_reason"],
            "dependencies": [dict(item) for item in dependencies],
        }

    def create_approval(
        self,
        project_id: str,
        request: ApprovalRequest,
        dependencies: Iterable[DependencyRef],
        subject_hash: str,
        *,
        expected_project_status: ProjectStatus,
        resulting_project_status: ProjectStatus | None,
    ) -> dict[str, Any]:
        approval_id = new_id("appr")
        now = utc_now()
        dependencies = tuple(dependencies)
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            if project["status"] != expected_project_status.value:
                raise ConflictError(
                    f"Project status changed: expected {expected_project_status.value}, "
                    f"observed {project['status']}"
                )
            try:
                connection.execute(
                    """
                    INSERT INTO approvals(
                        id, project_id, stage, status, approved_by, scope_json,
                        subject_hash, output_hash, approved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval_id,
                        project_id,
                        request.stage.value,
                        ApprovalStatus.ACTIVE.value,
                        request.approved_by,
                        canonical_json(request.scope),
                        subject_hash,
                        request.output_hash,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError(
                    f"An active {request.stage.value} approval already exists"
                ) from error
            for dependency in dependencies:
                connection.execute(
                    """
                    INSERT INTO approval_dependencies(approval_id, entity_type, entity_id, entity_hash)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        approval_id,
                        dependency.entity_type,
                        dependency.entity_id,
                        dependency.entity_hash,
                    ),
                )
            self._event(
                connection,
                project_id=project_id,
                entity_type="approval",
                entity_id=approval_id,
                event_type="approval.created",
                payload={
                    "stage": request.stage.value,
                    "approved_by": request.approved_by,
                    "subject_hash": subject_hash,
                    "dependency_count": len(dependencies),
                },
            )
            if resulting_project_status:
                connection.execute(
                    """
                    UPDATE projects
                    SET status = ?, version = version + 1, updated_at = ?
                    WHERE id = ?
                    """,
                    (resulting_project_status.value, now, project_id),
                )
                self._event(
                    connection,
                    project_id=project_id,
                    entity_type="project",
                    entity_id=project_id,
                    event_type="project.status_changed",
                    payload={
                        "from": project["status"],
                        "to": resulting_project_status.value,
                        "reason": f"{request.stage.value} approval {approval_id} recorded",
                    },
                )
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            return self._approval_dict(connection, row)

    def register_artifact(
        self, project_id: str, request: ArtifactCreate
    ) -> dict[str, Any]:
        artifact_id = new_id("art")
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            if request.parent_artifact_id:
                parent = connection.execute(
                    "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                    (request.parent_artifact_id, project_id),
                ).fetchone()
                if not parent:
                    raise NotFoundError(
                        f"Parent artifact not found in project: {request.parent_artifact_id}"
                    )
            try:
                connection.execute(
                    """
                    INSERT INTO artifacts(
                        id, project_id, kind, relative_path, content_hash, parent_artifact_id,
                        operation_json, immutable, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        project_id,
                        request.kind.value,
                        request.relative_path,
                        request.content_hash,
                        request.parent_artifact_id,
                        canonical_json(request.operation),
                        int(request.immutable),
                        ArtifactStatus.CURRENT.value,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError(
                    f"Artifact path already registered: {request.relative_path}"
                ) from error
            dependencies = list(request.dependencies)
            if request.parent_artifact_id:
                dependencies.append(
                    DependencyRef(
                        entity_type="artifact", entity_id=request.parent_artifact_id
                    )
                )
            for dependency in dependencies:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO artifact_dependencies(
                        artifact_id, entity_type, entity_id, entity_hash
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        dependency.entity_type,
                        dependency.entity_id,
                        dependency.entity_hash,
                    ),
                )
            self._event(
                connection,
                project_id=project_id,
                entity_type="artifact",
                entity_id=artifact_id,
                event_type="artifact.registered",
                payload={
                    "kind": request.kind.value,
                    "relative_path": request.relative_path,
                    "content_hash": request.content_hash,
                    "immutable": request.immutable,
                },
            )
            row = connection.execute(
                "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
            return self._artifact_dict(connection, row)

    @staticmethod
    def _artifact_dict(
        connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        dependencies = connection.execute(
            """
            SELECT entity_type, entity_id, entity_hash
            FROM artifact_dependencies WHERE artifact_id = ?
            """,
            (row["id"],),
        ).fetchall()
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "kind": row["kind"],
            "relative_path": row["relative_path"],
            "content_hash": row["content_hash"],
            "parent_artifact_id": row["parent_artifact_id"],
            "operation": json.loads(row["operation_json"]),
            "immutable": bool(row["immutable"]),
            "status": row["status"],
            "stale_reason": row["stale_reason"],
            "created_at": row["created_at"],
            "dependencies": [dict(item) for item in dependencies],
        }

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Artifact not found: {artifact_id}")
            return self._artifact_dict(connection, row)

    def artifact_by_path(
        self, project_id: str, relative_path: str
    ) -> dict[str, Any] | None:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            row = connection.execute(
                "SELECT * FROM artifacts WHERE project_id = ? AND relative_path = ?",
                (project_id, relative_path),
            ).fetchone()
            return self._artifact_dict(connection, row) if row else None

    def list_artifacts(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
            return [self._artifact_dict(connection, row) for row in rows]

    def get_slot(self, slot_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM slots WHERE id = ?", (slot_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Slot not found: {slot_id}")
            return {
                "id": row["id"],
                "project_id": row["project_id"],
                "shot_id": row["shot_id"],
                "stable_key": row["stable_key"],
                "role": row["role"],
                "identity_requirement": row["identity_requirement"],
                "fact_requirement": row["fact_requirement"],
                "transformation_permission": row["transformation_permission"],
                "text_zone": row["text_zone"],
                "approved_sources": json.loads(row["approved_sources_json"]),
                "version": row["version"],
            }

    def list_slots(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM slots WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "project_id": row["project_id"],
                    "shot_id": row["shot_id"],
                    "stable_key": row["stable_key"],
                    "role": row["role"],
                    "identity_requirement": row["identity_requirement"],
                    "fact_requirement": row["fact_requirement"],
                    "transformation_permission": row["transformation_permission"],
                    "text_zone": row["text_zone"],
                    "approved_sources": json.loads(row["approved_sources_json"]),
                    "version": row["version"],
                }
                for row in rows
            ]

    def register_treatment_decision(
        self,
        project_id: str,
        decision: dict[str, Any],
        *,
        decision_hash: str,
        artifact_id: str | None,
        decided_by: str,
        created_at: str,
    ) -> dict[str, Any]:
        decision_id = new_id("trt")
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            slot = connection.execute(
                "SELECT id FROM slots WHERE id = ? AND project_id = ?",
                (decision["slot_id"], project_id),
            ).fetchone()
            if not slot:
                raise NotFoundError(f"Slot not found in project: {decision['slot_id']}")
            if artifact_id:
                artifact = connection.execute(
                    "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                    (artifact_id, project_id),
                ).fetchone()
                if not artifact:
                    raise NotFoundError(f"Artifact not found in project: {artifact_id}")
            connection.execute(
                """
                INSERT INTO treatment_decisions(
                    id, project_id, slot_id, artifact_id, verdict, decided_by,
                    criteria_json, rights_json, reasons_json, decision_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    project_id,
                    decision["slot_id"],
                    artifact_id,
                    decision["verdict"],
                    decided_by,
                    canonical_json(decision["criteria"]),
                    canonical_json(decision["rights"])
                    if decision.get("rights")
                    else None,
                    canonical_json(decision["reasons"]),
                    decision_hash,
                    created_at,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="treatment_decision",
                entity_id=decision_id,
                event_type="treatment.decided",
                payload={
                    "slot_id": decision["slot_id"],
                    "artifact_id": artifact_id,
                    "verdict": decision["verdict"],
                    "decision_hash": decision_hash,
                },
            )
            return {
                "id": decision_id,
                "project_id": project_id,
                "slot_id": decision["slot_id"],
                "artifact_id": artifact_id,
                "verdict": decision["verdict"],
                "decided_by": decided_by,
                "criteria": decision["criteria"],
                "rights": decision.get("rights"),
                "reasons": decision["reasons"],
                "decision_hash": decision_hash,
                "created_at": created_at,
            }

    def list_treatment_decisions(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM treatment_decisions WHERE project_id = ? "
                "ORDER BY created_at",
                (project_id,),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "project_id": row["project_id"],
                    "slot_id": row["slot_id"],
                    "artifact_id": row["artifact_id"],
                    "verdict": row["verdict"],
                    "decided_by": row["decided_by"],
                    "criteria": json.loads(row["criteria_json"]),
                    "rights": (
                        json.loads(row["rights_json"]) if row["rights_json"] else None
                    ),
                    "reasons": json.loads(row["reasons_json"]),
                    "decision_hash": row["decision_hash"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def latest_treatment_for_slot(self, slot_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM treatment_decisions WHERE slot_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (slot_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "slot_id": row["slot_id"],
                "artifact_id": row["artifact_id"],
                "verdict": row["verdict"],
                "decided_by": row["decided_by"],
                "criteria": json.loads(row["criteria_json"]),
                "rights": (
                    json.loads(row["rights_json"]) if row["rights_json"] else None
                ),
                "reasons": json.loads(row["reasons_json"]),
                "decision_hash": row["decision_hash"],
                "created_at": row["created_at"],
            }

    def upsert_audio_bed(
        self,
        project_id: str,
        artifact_id: str,
        role: str,
        *,
        verdict: str,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            artifact = connection.execute(
                "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                (artifact_id, project_id),
            ).fetchone()
            if not artifact:
                raise NotFoundError(f"Artifact not found in project: {artifact_id}")
            existing = connection.execute(
                "SELECT * FROM audio_beds WHERE project_id = ? AND role = ?",
                (project_id, role),
            ).fetchone()
            if existing and existing["artifact_id"] == artifact_id:
                return dict(existing)
            if existing:
                connection.execute(
                    "UPDATE audio_beds SET artifact_id = ?, verdict = ?, "
                    "decision_json = ?, updated_at = ? WHERE id = ?",
                    (
                        artifact_id,
                        verdict,
                        canonical_json(decision),
                        now,
                        existing["id"],
                    ),
                )
                self._event(
                    connection,
                    project_id=project_id,
                    entity_type="audio_bed",
                    entity_id=existing["id"],
                    event_type="audio_bed.replaced",
                    payload={
                        "role": role,
                        "previous_artifact_id": existing["artifact_id"],
                        "artifact_id": artifact_id,
                    },
                )
                row = connection.execute(
                    "SELECT * FROM audio_beds WHERE id = ?", (existing["id"],)
                ).fetchone()
                return dict(row)
            bed_id = new_id("abed")
            connection.execute(
                "INSERT INTO audio_beds(id, project_id, artifact_id, role, verdict, "
                "decision_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    bed_id,
                    project_id,
                    artifact_id,
                    role,
                    verdict,
                    canonical_json(decision),
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="audio_bed",
                entity_id=bed_id,
                event_type="audio_bed.assigned",
                payload={"role": role, "artifact_id": artifact_id, "verdict": verdict},
            )
            row = connection.execute(
                "SELECT * FROM audio_beds WHERE id = ?", (bed_id,)
            ).fetchone()
            return dict(row)

    def list_audio_beds(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM audio_beds WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _slot_asset_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "slot_id": row["slot_id"],
            "artifact_id": row["artifact_id"],
            "role": row["role"],
            "treatment_decision_id": row["treatment_decision_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def upsert_slot_asset(
        self,
        project_id: str,
        slot_id: str,
        artifact_id: str,
        role: str,
        *,
        treatment_decision_id: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            slot = connection.execute(
                "SELECT id FROM slots WHERE id = ? AND project_id = ?",
                (slot_id, project_id),
            ).fetchone()
            if not slot:
                raise NotFoundError(f"Slot not found in project: {slot_id}")
            artifact = connection.execute(
                "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                (artifact_id, project_id),
            ).fetchone()
            if not artifact:
                raise NotFoundError(f"Artifact not found in project: {artifact_id}")
            existing = connection.execute(
                "SELECT * FROM slot_assets WHERE slot_id = ? AND role = ?",
                (slot_id, role),
            ).fetchone()
            if existing and existing["artifact_id"] == artifact_id:
                return self._slot_asset_dict(existing)
            if existing:
                connection.execute(
                    "UPDATE slot_assets SET artifact_id = ?, "
                    "treatment_decision_id = ?, updated_at = ? WHERE id = ?",
                    (artifact_id, treatment_decision_id, now, existing["id"]),
                )
                self._event(
                    connection,
                    project_id=project_id,
                    entity_type="slot_asset",
                    entity_id=existing["id"],
                    event_type="slot_asset.replaced",
                    payload={
                        "slot_id": slot_id,
                        "role": role,
                        "previous_artifact_id": existing["artifact_id"],
                        "artifact_id": artifact_id,
                    },
                )
                row = connection.execute(
                    "SELECT * FROM slot_assets WHERE id = ?", (existing["id"],)
                ).fetchone()
                return self._slot_asset_dict(row)
            slot_asset_id = new_id("sla")
            connection.execute(
                """
                INSERT INTO slot_assets(
                    id, project_id, slot_id, artifact_id, role,
                    treatment_decision_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slot_asset_id,
                    project_id,
                    slot_id,
                    artifact_id,
                    role,
                    treatment_decision_id,
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="slot_asset",
                entity_id=slot_asset_id,
                event_type="slot_asset.assigned",
                payload={
                    "slot_id": slot_id,
                    "role": role,
                    "artifact_id": artifact_id,
                },
            )
            row = connection.execute(
                "SELECT * FROM slot_assets WHERE id = ?", (slot_asset_id,)
            ).fetchone()
            return self._slot_asset_dict(row)

    def list_slot_assets(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM slot_assets WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
            return [self._slot_asset_dict(row) for row in rows]

    def slot_asset_by_role(self, slot_id: str, role: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM slot_assets WHERE slot_id = ? AND role = ?",
                (slot_id, role),
            ).fetchone()
            return self._slot_asset_dict(row) if row else None

    def swap_slot_asset_roles(
        self, project_id: str, slot_id: str, *, reason: str = ""
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM slot_assets WHERE slot_id = ? AND role IN "
                "('primary', 'backup')",
                (slot_id,),
            ).fetchall()
            roles = {row["role"]: row for row in rows}
            if "primary" not in roles or "backup" not in roles:
                raise ConflictError(
                    "Both a primary and a backup asset are required to promote"
                )
            # UNIQUE(slot_id, role) forbids a direct swap, so park the current
            # primary under a temporary role for the duration of the transaction.
            connection.execute(
                "UPDATE slot_assets SET role = 'swapping', updated_at = ? WHERE id = ?",
                (now, roles["primary"]["id"]),
            )
            connection.execute(
                "UPDATE slot_assets SET role = 'primary', updated_at = ? WHERE id = ?",
                (now, roles["backup"]["id"]),
            )
            connection.execute(
                "UPDATE slot_assets SET role = 'backup', updated_at = ? WHERE id = ?",
                (now, roles["primary"]["id"]),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="slot_asset",
                entity_id=slot_id,
                event_type="slot_asset.promoted",
                payload={
                    "slot_id": slot_id,
                    "new_primary_artifact_id": roles["backup"]["artifact_id"],
                    "previous_primary_artifact_id": roles["primary"]["artifact_id"],
                    "reason": reason,
                },
            )
        return {
            "slot_id": slot_id,
            "primary_artifact_id": roles["backup"]["artifact_id"],
            "backup_artifact_id": roles["primary"]["artifact_id"],
        }

    def get_asset_use_context(self, project_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            row = connection.execute(
                "SELECT * FROM asset_use_contexts WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "project_id": row["project_id"],
                "use_class": row["use_class"],
                "monetized": bool(row["monetized"]),
                "advertising": bool(row["advertising"]),
                "modification_planned": bool(row["modification_planned"]),
                "attribution_can_be_delivered": bool(
                    row["attribution_can_be_delivered"]
                ),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def set_asset_use_context(
        self, project_id: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            connection.execute(
                """
                INSERT INTO asset_use_contexts(
                    project_id, use_class, monetized, advertising,
                    modification_planned, attribution_can_be_delivered,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    use_class = excluded.use_class,
                    monetized = excluded.monetized,
                    advertising = excluded.advertising,
                    modification_planned = excluded.modification_planned,
                    attribution_can_be_delivered =
                        excluded.attribution_can_be_delivered,
                    updated_at = excluded.updated_at
                """,
                (
                    project_id,
                    context["use_class"],
                    int(bool(context["monetized"])),
                    int(bool(context["advertising"])),
                    int(bool(context["modification_planned"])),
                    int(bool(context["attribution_can_be_delivered"])),
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="asset_use_context",
                entity_id=project_id,
                event_type="asset_use_context.set",
                payload=context,
            )
        return {
            "project_id": project_id,
            **context,
            "updated_at": now,
        }

    def record_provider_spend(
        self,
        provider: str,
        amount_cents: int,
        tier: str,
        purpose: str,
        *,
        notifications: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        spend_id = new_id("spd")
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO provider_spend(id, provider, amount_cents, tier, purpose, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (spend_id, provider, amount_cents, tier, purpose, now),
            )
            self._event(
                connection,
                project_id=None,
                entity_type="provider_spend",
                entity_id=spend_id,
                event_type="provider_spend.recorded",
                payload={
                    "provider": provider,
                    "amount_cents": amount_cents,
                    "tier": tier,
                    "purpose": purpose,
                },
            )
            for message in notifications:
                self._event(
                    connection,
                    project_id=None,
                    entity_type="provider_spend",
                    entity_id=spend_id,
                    event_type="budget.threshold_reached",
                    payload={"provider": provider, "message": message},
                )
        return {
            "id": spend_id,
            "provider": provider,
            "amount_cents": amount_cents,
            "tier": tier,
            "purpose": purpose,
            "created_at": now,
            "notifications": list(notifications),
        }

    def provider_spend_total_cents(self, provider: str) -> int:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM provider_spend "
                "WHERE provider = ?",
                (provider,),
            ).fetchone()
            return int(row["total"])

    def list_provider_spend(self, provider: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM provider_spend WHERE provider = ? ORDER BY created_at",
                (provider,),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "provider": row["provider"],
                    "amount_cents": row["amount_cents"],
                    "tier": row["tier"],
                    "purpose": row["purpose"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def record_provider_request(self, provider: str, operation: str) -> dict[str, Any]:
        request_id = new_id("req")
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO provider_requests(id, provider, operation, requested_at) "
                "VALUES (?, ?, ?, ?)",
                (request_id, provider, operation, now),
            )
        return {
            "id": request_id,
            "provider": provider,
            "operation": operation,
            "requested_at": now,
        }

    def count_provider_requests(self, provider: str, since: str) -> int:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM provider_requests "
                "WHERE provider = ? AND requested_at >= ?",
                (provider, since),
            ).fetchone()
            return int(row["total"])

    @staticmethod
    def _provider_profile_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "stable_key": row["stable_key"],
            "provider": row["provider"],
            "endpoint": row["endpoint"],
            "model": row["model"],
            "approved_roles": json.loads(row["approved_roles_json"]),
            "credential_ref": row["credential_ref"],
            "data_classification": row["data_classification"],
            "limits": json.loads(row["limits_json"]),
            "terms": json.loads(row["terms_json"]),
            "fallback_profiles": json.loads(row["fallback_profiles_json"]),
            "enabled": bool(row["enabled"]),
            "version": row["version"],
            "content_hash": row["content_hash"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def register_provider_profile(
        self, request: ProviderModelProfileCreate
    ) -> dict[str, Any]:
        now = utc_now()
        payload = request.model_dump(mode="json")
        content_hash = sha256_json(payload)
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM provider_profiles WHERE stable_key = ?",
                (request.stable_key,),
            ).fetchone()
            if existing and existing["content_hash"] == content_hash:
                return self._provider_profile_dict(existing)
            if existing:
                connection.execute(
                    """
                    UPDATE provider_profiles SET
                        provider = ?, endpoint = ?, model = ?, approved_roles_json = ?,
                        credential_ref = ?, data_classification = ?, limits_json = ?,
                        terms_json = ?, fallback_profiles_json = ?, enabled = ?,
                        version = version + 1, content_hash = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        request.provider,
                        request.endpoint,
                        request.model,
                        canonical_json(request.approved_roles),
                        request.credential_ref,
                        request.data_classification.value,
                        canonical_json(request.limits),
                        canonical_json(request.terms),
                        canonical_json(request.fallback_profiles),
                        int(request.enabled),
                        content_hash,
                        now,
                        existing["id"],
                    ),
                )
                profile_id = existing["id"]
                event_type = "provider_profile.updated"
            else:
                profile_id = new_id("provider")
                connection.execute(
                    """
                    INSERT INTO provider_profiles(
                        id, stable_key, provider, endpoint, model, approved_roles_json,
                        credential_ref, data_classification, limits_json, terms_json,
                        fallback_profiles_json, enabled, content_hash, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile_id,
                        request.stable_key,
                        request.provider,
                        request.endpoint,
                        request.model,
                        canonical_json(request.approved_roles),
                        request.credential_ref,
                        request.data_classification.value,
                        canonical_json(request.limits),
                        canonical_json(request.terms),
                        canonical_json(request.fallback_profiles),
                        int(request.enabled),
                        content_hash,
                        now,
                        now,
                    ),
                )
                event_type = "provider_profile.registered"
            self._event(
                connection,
                project_id=None,
                entity_type="provider_profile",
                entity_id=profile_id,
                event_type=event_type,
                payload={
                    "stable_key": request.stable_key,
                    "content_hash": content_hash,
                    "enabled": request.enabled,
                },
            )
            row = connection.execute(
                "SELECT * FROM provider_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            return self._provider_profile_dict(row)

    def list_provider_profiles(self) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM provider_profiles ORDER BY stable_key"
            ).fetchall()
            return [self._provider_profile_dict(row) for row in rows]

    @staticmethod
    def _tool_profile_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "stable_key": row["stable_key"],
            "name": row["name"],
            "integration_mode": row["integration_mode"],
            "executable_or_endpoint": row["executable_or_endpoint"],
            "supported_operations": json.loads(row["supported_operations_json"]),
            "limits": json.loads(row["limits_json"]),
            "fallback_order": json.loads(row["fallback_order_json"]),
            "capability_status": row["capability_status"],
            "version": row["version"],
            "content_hash": row["content_hash"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def register_tool_profile(self, request: ToolProfileCreate) -> dict[str, Any]:
        now = utc_now()
        payload = request.model_dump(mode="json")
        content_hash = sha256_json(payload)
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM tool_profiles WHERE stable_key = ?",
                (request.stable_key,),
            ).fetchone()
            if existing and existing["content_hash"] == content_hash:
                return self._tool_profile_dict(existing)
            if existing:
                connection.execute(
                    """
                    UPDATE tool_profiles SET
                        name = ?, integration_mode = ?, executable_or_endpoint = ?,
                        supported_operations_json = ?, limits_json = ?,
                        fallback_order_json = ?, capability_status = ?,
                        version = version + 1, content_hash = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        request.name,
                        request.integration_mode.value,
                        request.executable_or_endpoint,
                        canonical_json(request.supported_operations),
                        canonical_json(request.limits),
                        canonical_json(request.fallback_order),
                        request.capability_status.value,
                        content_hash,
                        now,
                        existing["id"],
                    ),
                )
                profile_id = existing["id"]
                event_type = "tool_profile.updated"
            else:
                profile_id = new_id("tool")
                connection.execute(
                    """
                    INSERT INTO tool_profiles(
                        id, stable_key, name, integration_mode, executable_or_endpoint,
                        supported_operations_json, limits_json, fallback_order_json,
                        capability_status, content_hash, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile_id,
                        request.stable_key,
                        request.name,
                        request.integration_mode.value,
                        request.executable_or_endpoint,
                        canonical_json(request.supported_operations),
                        canonical_json(request.limits),
                        canonical_json(request.fallback_order),
                        request.capability_status.value,
                        content_hash,
                        now,
                        now,
                    ),
                )
                event_type = "tool_profile.registered"
            self._event(
                connection,
                project_id=None,
                entity_type="tool_profile",
                entity_id=profile_id,
                event_type=event_type,
                payload={
                    "stable_key": request.stable_key,
                    "content_hash": content_hash,
                    "capability_status": request.capability_status.value,
                },
            )
            row = connection.execute(
                "SELECT * FROM tool_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            return self._tool_profile_dict(row)

    def list_tool_profiles(self) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM tool_profiles ORDER BY stable_key"
            ).fetchall()
            return [self._tool_profile_dict(row) for row in rows]

    def get_tool_profile(self, profile_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM tool_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Tool profile not found: {profile_id}")
            return self._tool_profile_dict(row)

    @staticmethod
    def _tool_operation_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "tool_profile_id": row["tool_profile_id"],
            "provider_profile_id": row["provider_profile_id"],
            "operation": row["operation"],
            "idempotency_key": row["idempotency_key"],
            "request_hash": row["request_hash"],
            "status": row["status"],
            "request": json.loads(row["request_json"]),
            "output_declaration": json.loads(row["output_declaration_json"]),
            "limits": json.loads(row["limits_json"]),
            "required_approval_stage": row["required_approval_stage"],
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def reserve_tool_operation(
        self, project_id: str, request: ToolOperationRequest, request_hash: str
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            tool = connection.execute(
                "SELECT * FROM tool_profiles WHERE id = ?", (request.tool_profile_id,)
            ).fetchone()
            if not tool:
                raise NotFoundError(
                    f"Tool profile not found: {request.tool_profile_id}"
                )
            supported = set(json.loads(tool["supported_operations_json"]))
            if request.operation not in supported:
                raise IntegrityError(
                    f"Tool profile {tool['stable_key']} does not support {request.operation}"
                )
            if request.provider_profile_id:
                provider = connection.execute(
                    "SELECT * FROM provider_profiles WHERE id = ?",
                    (request.provider_profile_id,),
                ).fetchone()
                if not provider:
                    raise NotFoundError(
                        f"Provider profile not found: {request.provider_profile_id}"
                    )
                if not bool(provider["enabled"]):
                    raise ConflictError(
                        f"Provider profile is disabled: {provider['stable_key']}"
                    )
            existing = connection.execute(
                """
                SELECT * FROM tool_operations
                WHERE project_id = ? AND tool_profile_id = ? AND idempotency_key = ?
                """,
                (project_id, request.tool_profile_id, request.idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise ConflictError(
                        "Tool operation idempotency key was used for another request"
                    )
                return self._tool_operation_dict(existing)
            operation_id = new_id("op")
            connection.execute(
                """
                INSERT INTO tool_operations(
                    id, project_id, tool_profile_id, provider_profile_id, operation,
                    idempotency_key, request_hash, status, request_json,
                    output_declaration_json, limits_json, required_approval_stage,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    project_id,
                    request.tool_profile_id,
                    request.provider_profile_id,
                    request.operation,
                    request.idempotency_key,
                    request_hash,
                    ToolOperationStatus.AUTHORIZED.value,
                    canonical_json(request.model_dump(mode="json")),
                    canonical_json(request.output_declaration),
                    canonical_json(request.limits),
                    (
                        request.required_approval_stage.value
                        if request.required_approval_stage
                        else None
                    ),
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="tool_operation",
                entity_id=operation_id,
                event_type="tool_operation.authorized",
                payload={
                    "request_hash": request_hash,
                    "tool_profile_id": request.tool_profile_id,
                    "provider_profile_id": request.provider_profile_id,
                },
            )
            row = connection.execute(
                "SELECT * FROM tool_operations WHERE id = ?", (operation_id,)
            ).fetchone()
            return self._tool_operation_dict(row)

    def get_tool_operation(self, operation_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM tool_operations WHERE id = ?", (operation_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Tool operation not found: {operation_id}")
            return self._tool_operation_dict(row)

    def tool_operation_by_idempotency(
        self, project_id: str, tool_profile_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT * FROM tool_operations
                WHERE project_id = ? AND tool_profile_id = ? AND idempotency_key = ?
                """,
                (project_id, tool_profile_id, idempotency_key),
            ).fetchone()
            return self._tool_operation_dict(row) if row else None

    def mark_tool_operation_running(self, operation_id: str) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM tool_operations WHERE id = ?", (operation_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Tool operation not found: {operation_id}")
            status = ToolOperationStatus(row["status"])
            if status == ToolOperationStatus.RUNNING:
                return self._tool_operation_dict(row)
            if status != ToolOperationStatus.AUTHORIZED:
                raise ConflictError(
                    f"Tool operation cannot start from status {status.value}"
                )
            connection.execute(
                """
                UPDATE tool_operations
                SET status = ?, started_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (ToolOperationStatus.RUNNING.value, now, now, operation_id),
            )
            self._event(
                connection,
                project_id=row["project_id"],
                entity_type="tool_operation",
                entity_id=operation_id,
                event_type="tool_operation.started",
                payload={"from": status.value, "to": ToolOperationStatus.RUNNING.value},
            )
            updated = connection.execute(
                "SELECT * FROM tool_operations WHERE id = ?", (operation_id,)
            ).fetchone()
            return self._tool_operation_dict(updated)

    def record_execution_receipt(
        self, operation_id: str, request: ExecutionReceiptCreate
    ) -> dict[str, Any]:
        now = utc_now()
        if request.actual_cost and request.actual_cost.minor > 0:
            raise IntegrityError(
                "Paid execution must settle through a controller-budgeted provider job"
            )
        with self.database.transaction() as connection:
            operation = connection.execute(
                "SELECT * FROM tool_operations WHERE id = ?", (operation_id,)
            ).fetchone()
            if not operation:
                raise NotFoundError(f"Tool operation not found: {operation_id}")
            specialist = connection.execute(
                "SELECT id FROM specialist_handoffs WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if specialist:
                raise IntegrityError(
                    "Specialist operations must settle through their governed handoff"
                )
            existing = connection.execute(
                "SELECT * FROM execution_receipts WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            cost = request.actual_cost
            actual_minor = cost.minor if cost else 0
            currency = cost.currency if cost else None
            receipt_content = {
                "status": request.status.value,
                "elapsed_ms": request.elapsed_ms,
                "output_artifact_ids": request.output_artifact_ids,
                "response_hash": request.response_hash,
                "actual_cost_minor": actual_minor,
                "currency": currency,
                "details": request.details,
            }
            if existing:
                observed = {
                    "status": existing["status"],
                    "elapsed_ms": existing["elapsed_ms"],
                    "output_artifact_ids": json.loads(
                        existing["output_artifact_ids_json"]
                    ),
                    "response_hash": existing["response_hash"],
                    "actual_cost_minor": existing["actual_cost_minor"],
                    "currency": existing["currency"],
                    "details": json.loads(existing["details_json"]),
                }
                if sha256_json(observed) != sha256_json(receipt_content):
                    raise ConflictError(
                        "Execution receipt already exists with different content"
                    )
                return {"id": existing["id"], "operation_id": operation_id, **observed}
            operation_status = ToolOperationStatus(operation["status"])
            if operation_status not in {
                ToolOperationStatus.AUTHORIZED,
                ToolOperationStatus.RUNNING,
            }:
                raise ConflictError(
                    f"Operation in {operation_status.value} cannot receive a new receipt"
                )
            for artifact_id in request.output_artifact_ids:
                artifact = connection.execute(
                    "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                    (artifact_id, operation["project_id"]),
                ).fetchone()
                if not artifact:
                    raise IntegrityError(
                        f"Receipt output artifact is not in the project: {artifact_id}"
                    )
            receipt_id = new_id("receipt")
            connection.execute(
                """
                INSERT INTO execution_receipts(
                    id, operation_id, status, elapsed_ms, output_artifact_ids_json,
                    response_hash, actual_cost_minor, currency, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    operation_id,
                    request.status.value,
                    request.elapsed_ms,
                    canonical_json(request.output_artifact_ids),
                    request.response_hash,
                    actual_minor,
                    currency,
                    canonical_json(request.details),
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE tool_operations
                SET status = ?, finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (request.status.value, now, now, operation_id),
            )
            self._event(
                connection,
                project_id=operation["project_id"],
                entity_type="execution_receipt",
                entity_id=receipt_id,
                event_type="execution_receipt.recorded",
                payload={
                    "operation_id": operation_id,
                    "status": request.status.value,
                    "output_count": len(request.output_artifact_ids),
                    "actual_cost_minor": actual_minor,
                },
            )
            return {"id": receipt_id, "operation_id": operation_id, **receipt_content}

    def get_execution_receipt(self, operation_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM execution_receipts WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "operation_id": operation_id,
                "status": row["status"],
                "elapsed_ms": row["elapsed_ms"],
                "output_artifact_ids": json.loads(row["output_artifact_ids_json"]),
                "response_hash": row["response_hash"],
                "actual_cost_minor": row["actual_cost_minor"],
                "currency": row["currency"],
                "details": json.loads(row["details_json"]),
                "created_at": row["created_at"],
            }

    def list_tool_operations(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM tool_operations WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
            return [self._tool_operation_dict(row) for row in rows]

    @staticmethod
    def _studio_spend_approval_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "scope_hash": row["scope_hash"],
            "max_cost_minor": row["max_cost_minor"],
            "currency": row["currency"],
            "approved_by": row["approved_by"],
            "reason": row["reason"],
            "consumed_operation_id": row["consumed_operation_id"],
            "created_at": row["created_at"],
            "consumed_at": row["consumed_at"],
        }

    def record_studio_spend_approval(
        self,
        project_id: str,
        *,
        scope_hash: str,
        max_cost: Money,
        approved_by: str,
        reason: str,
    ) -> dict[str, Any]:
        if max_cost.minor <= 0:
            raise IntegrityError(
                "Studio spend approvals must authorize a positive amount"
            )
        now = utc_now()
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            if project["currency"] != max_cost.currency:
                raise IntegrityError(
                    "Studio spend approvals must use the project currency "
                    f"{project['currency']}"
                )
            existing = connection.execute(
                """
                SELECT * FROM studio_spend_approvals
                WHERE project_id = ? AND scope_hash = ?
                  AND max_cost_minor = ? AND currency = ?
                """,
                (project_id, scope_hash, max_cost.minor, max_cost.currency),
            ).fetchone()
            if existing:
                return self._studio_spend_approval_dict(existing)
            approval_id = new_id("spendapproval")
            connection.execute(
                """
                INSERT INTO studio_spend_approvals(
                    id, project_id, scope_hash, max_cost_minor, currency,
                    approved_by, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    project_id,
                    scope_hash,
                    max_cost.minor,
                    max_cost.currency,
                    approved_by,
                    reason,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="studio_spend_approval",
                entity_id=approval_id,
                event_type="studio_spend.approved",
                payload={
                    "scope_hash": scope_hash,
                    "max_cost_minor": max_cost.minor,
                    "currency": max_cost.currency,
                    "approved_by": approved_by,
                },
            )
            row = connection.execute(
                "SELECT * FROM studio_spend_approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            return self._studio_spend_approval_dict(row)

    def get_studio_spend_approval(self, approval_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM studio_spend_approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Studio spend approval not found: {approval_id}")
            return self._studio_spend_approval_dict(row)

    @staticmethod
    def _specialist_handoff_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "tool_profile_id": row["tool_profile_id"],
            "operation_id": row["operation_id"],
            "budget_job_id": row["budget_job_id"],
            "spend_approval_id": row["spend_approval_id"],
            "work_packet_artifact_id": row["work_packet_artifact_id"],
            "work_packet_hash": row["work_packet_hash"],
            "input_artifact_ids": json.loads(row["input_artifact_ids_json"]),
            "output_artifact_ids": json.loads(row["output_artifact_ids_json"]),
            "result_packet_hash": row["result_packet_hash"],
            "tool_version": row["tool_version"],
            "operator_note": row["operator_note"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def reserve_studio_work(
        self,
        project_id: str,
        *,
        tool_request: ToolOperationRequest,
        tool_request_hash: str,
        budget_request: JobRequest,
        budget_request_hash: str,
        work_packet_artifact_id: str,
        work_packet_hash: str,
        input_artifact_ids: tuple[str, ...],
        spend_approval_id: str | None,
        approval_scope_hash: str,
        requires_spend_approval: bool,
    ) -> tuple[dict[str, Any], dict[str, Any], JobRecord]:
        """Atomically reserve budget, authorize Studio work and record its handoff."""

        now = utc_now()
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            existing = connection.execute(
                """
                SELECT * FROM tool_operations
                WHERE project_id = ? AND tool_profile_id = ? AND idempotency_key = ?
                """,
                (
                    project_id,
                    tool_request.tool_profile_id,
                    tool_request.idempotency_key,
                ),
            ).fetchone()
            if existing:
                if existing["request_hash"] != tool_request_hash:
                    raise ConflictError(
                        "Studio idempotency key was used for a different work packet"
                    )
                handoff = connection.execute(
                    "SELECT * FROM specialist_handoffs WHERE operation_id = ?",
                    (existing["id"],),
                ).fetchone()
                if not handoff or not handoff["budget_job_id"]:
                    raise IntegrityError(
                        "Existing Studio operation is missing its budget reservation"
                    )
                job = self._require_job(connection, handoff["budget_job_id"])
                return (
                    self._tool_operation_dict(existing),
                    self._specialist_handoff_dict(handoff),
                    self._job_from_row(job),
                )
            tool = connection.execute(
                "SELECT * FROM tool_profiles WHERE id = ?",
                (tool_request.tool_profile_id,),
            ).fetchone()
            if not tool:
                raise NotFoundError(
                    f"Tool profile not found: {tool_request.tool_profile_id}"
                )
            if tool_request.operation not in set(
                json.loads(tool["supported_operations_json"])
            ):
                raise IntegrityError(
                    f"Tool profile {tool['stable_key']} does not support "
                    f"{tool_request.operation}"
                )
            if tool_request.provider_profile_id:
                raise IntegrityError(
                    "Studio budget reservations cannot also bind a provider profile"
                )
            packet = connection.execute(
                """
                SELECT id, content_hash FROM artifacts
                WHERE id = ? AND project_id = ?
                """,
                (work_packet_artifact_id, project_id),
            ).fetchone()
            if not packet or packet["content_hash"] != work_packet_hash:
                raise IntegrityError("Studio work packet artifact/hash mismatch")
            for artifact_id in input_artifact_ids:
                artifact = connection.execute(
                    "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                    (artifact_id, project_id),
                ).fetchone()
                if not artifact:
                    raise IntegrityError(
                        f"Studio input artifact is not in the project: {artifact_id}"
                    )
            if budget_request.provider != "openmontage":
                raise IntegrityError(
                    "Studio budget jobs must use the openmontage provider"
                )
            if project["currency"] != budget_request.estimated_cost.currency:
                raise IntegrityError(
                    "Studio spending must reserve in the project currency "
                    f"{project['currency']}"
                )
            requested_minor = budget_request.estimated_cost.minor
            if requires_spend_approval:
                if not spend_approval_id:
                    raise ApprovalRequiredError(
                        "This Studio run requires human spend approval"
                    )
                approval = connection.execute(
                    "SELECT * FROM studio_spend_approvals WHERE id = ?",
                    (spend_approval_id,),
                ).fetchone()
                if not approval:
                    raise NotFoundError(
                        f"Studio spend approval not found: {spend_approval_id}"
                    )
                if (
                    approval["project_id"] != project_id
                    or approval["scope_hash"] != approval_scope_hash
                    or approval["max_cost_minor"] != requested_minor
                    or approval["currency"] != project["currency"]
                ):
                    raise ApprovalRequiredError(
                        "Studio spend approval does not match this exact work request"
                    )
                if approval["consumed_operation_id"]:
                    raise ConflictError(
                        "Studio spend approval has already been consumed"
                    )
            elif spend_approval_id:
                raise IntegrityError(
                    "Studio runs below the automatic limit do not use a spend approval"
                )
            exposure = project["spent_minor"] + project["reserved_minor"]
            if exposure + requested_minor > project["max_budget_minor"]:
                available = project["max_budget_minor"] - exposure
                raise BudgetExceededError(
                    f"Insufficient budget: requested {requested_minor} minor units, "
                    f"available {available}"
                )
            operation_id = new_id("op")
            job_id = new_id("job")
            handoff_id = new_id("handoff")
            connection.execute(
                """
                INSERT INTO jobs(
                    id, project_id, provider, endpoint, operation, idempotency_key,
                    request_hash, request_json, status, estimated_cost_minor,
                    reserved_cost_minor, currency, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    project_id,
                    budget_request.provider,
                    budget_request.endpoint,
                    budget_request.operation,
                    budget_request.idempotency_key,
                    budget_request_hash,
                    canonical_json(budget_request.model_dump(mode="json")),
                    JobStatus.BUDGET_RESERVED.value,
                    requested_minor,
                    requested_minor,
                    budget_request.estimated_cost.currency,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE projects
                SET reserved_minor = reserved_minor + ?, version = version + 1,
                    updated_at = ? WHERE id = ?
                """,
                (requested_minor, now, project_id),
            )
            self._ledger(
                connection,
                project_id=project_id,
                job_id=job_id,
                kind=BudgetEntryKind.RESERVE,
                amount_minor=requested_minor,
                currency="USD",
                note="Controller-approved Studio run reservation",
            )
            connection.execute(
                """
                INSERT INTO tool_operations(
                    id, project_id, tool_profile_id, provider_profile_id, operation,
                    idempotency_key, request_hash, status, request_json,
                    output_declaration_json, limits_json, required_approval_stage,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    project_id,
                    tool_request.tool_profile_id,
                    None,
                    tool_request.operation,
                    tool_request.idempotency_key,
                    tool_request_hash,
                    ToolOperationStatus.AUTHORIZED.value,
                    canonical_json(tool_request.model_dump(mode="json")),
                    canonical_json(tool_request.output_declaration),
                    canonical_json(tool_request.limits),
                    (
                        tool_request.required_approval_stage.value
                        if tool_request.required_approval_stage
                        else None
                    ),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO specialist_handoffs(
                    id, project_id, tool_profile_id, work_packet_hash,
                    input_artifact_ids_json, output_artifact_ids_json, status,
                    created_at, updated_at, operation_id, work_packet_artifact_id,
                    budget_job_id, spend_approval_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    handoff_id,
                    project_id,
                    tool_request.tool_profile_id,
                    work_packet_hash,
                    canonical_json(input_artifact_ids),
                    canonical_json(()),
                    ToolOperationStatus.AUTHORIZED.value,
                    now,
                    now,
                    operation_id,
                    work_packet_artifact_id,
                    job_id,
                    spend_approval_id,
                ),
            )
            if spend_approval_id:
                connection.execute(
                    """
                    UPDATE studio_spend_approvals
                    SET consumed_operation_id = ?, consumed_at = ? WHERE id = ?
                    """,
                    (operation_id, now, spend_approval_id),
                )
            self._event(
                connection,
                project_id=project_id,
                entity_type="job",
                entity_id=job_id,
                event_type="job.budget_reserved",
                payload={
                    "request_hash": budget_request_hash,
                    "reserved_minor": requested_minor,
                    "purpose": "openmontage_run",
                },
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="tool_operation",
                entity_id=operation_id,
                event_type="tool_operation.authorized",
                payload={
                    "request_hash": tool_request_hash,
                    "tool_profile_id": tool_request.tool_profile_id,
                    "budget_job_id": job_id,
                },
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="specialist_handoff",
                entity_id=handoff_id,
                event_type="specialist_handoff.authorized",
                payload={
                    "operation_id": operation_id,
                    "work_packet_hash": work_packet_hash,
                    "budget_job_id": job_id,
                    "spend_approval_id": spend_approval_id,
                },
            )
            operation = connection.execute(
                "SELECT * FROM tool_operations WHERE id = ?", (operation_id,)
            ).fetchone()
            handoff = connection.execute(
                "SELECT * FROM specialist_handoffs WHERE id = ?", (handoff_id,)
            ).fetchone()
            job = self._require_job(connection, job_id)
            return (
                self._tool_operation_dict(operation),
                self._specialist_handoff_dict(handoff),
                self._job_from_row(job),
            )

    def record_specialist_handoff(
        self,
        project_id: str,
        *,
        tool_profile_id: str,
        operation_id: str,
        work_packet_artifact_id: str,
        work_packet_hash: str,
        input_artifact_ids: tuple[str, ...],
        status: str = "authorized",
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            operation = connection.execute(
                """
                SELECT id FROM tool_operations
                WHERE id = ? AND project_id = ? AND tool_profile_id = ?
                """,
                (operation_id, project_id, tool_profile_id),
            ).fetchone()
            if not operation:
                raise IntegrityError(
                    "Specialist handoff operation does not match project/tool"
                )
            packet = connection.execute(
                """
                SELECT id, content_hash FROM artifacts
                WHERE id = ? AND project_id = ?
                """,
                (work_packet_artifact_id, project_id),
            ).fetchone()
            if not packet or packet["content_hash"] != work_packet_hash:
                raise IntegrityError("Specialist work packet artifact/hash mismatch")
            for artifact_id in input_artifact_ids:
                artifact = connection.execute(
                    "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                    (artifact_id, project_id),
                ).fetchone()
                if not artifact:
                    raise IntegrityError(
                        f"Specialist input artifact is not in the project: {artifact_id}"
                    )
            existing = connection.execute(
                "SELECT * FROM specialist_handoffs WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing:
                observed = self._specialist_handoff_dict(existing)
                expected = {
                    "work_packet_artifact_id": work_packet_artifact_id,
                    "work_packet_hash": work_packet_hash,
                    "input_artifact_ids": list(input_artifact_ids),
                }
                if any(observed[key] != value for key, value in expected.items()):
                    raise ConflictError(
                        "Specialist handoff operation already has another work packet"
                    )
                return observed
            handoff_id = new_id("handoff")
            connection.execute(
                """
                INSERT INTO specialist_handoffs(
                    id, project_id, tool_profile_id, work_packet_hash,
                    input_artifact_ids_json, output_artifact_ids_json, status,
                    created_at, updated_at, operation_id, work_packet_artifact_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    handoff_id,
                    project_id,
                    tool_profile_id,
                    work_packet_hash,
                    canonical_json(input_artifact_ids),
                    canonical_json(()),
                    status,
                    now,
                    now,
                    operation_id,
                    work_packet_artifact_id,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="specialist_handoff",
                entity_id=handoff_id,
                event_type="specialist_handoff.authorized",
                payload={
                    "operation_id": operation_id,
                    "work_packet_hash": work_packet_hash,
                    "input_artifact_ids": input_artifact_ids,
                },
            )
            row = connection.execute(
                "SELECT * FROM specialist_handoffs WHERE id = ?", (handoff_id,)
            ).fetchone()
            return self._specialist_handoff_dict(row)

    def specialist_handoff_for_operation(self, operation_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM specialist_handoffs WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if not row:
                raise NotFoundError(
                    f"Specialist handoff not found for operation: {operation_id}"
                )
            return self._specialist_handoff_dict(row)

    @staticmethod
    def _studio_production_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "operation_id": row["operation_id"],
            "transport": row["transport"],
            "external_production_id": row["external_production_id"],
            "title": row["title"],
            "autonomy": row["autonomy"],
            "budget_minor": row["budget_minor"],
            "currency": row["currency"],
            "status": row["status"],
            "packet_hash": row["packet_hash"],
            "last_error": row["last_error"],
            "opened_at": row["opened_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def open_studio_production(
        self,
        project_id: str,
        *,
        operation_id: str,
        transport: str,
        title: str,
        autonomy: str,
        budget_minor: int,
        currency: str,
        packet_hash: str,
        external_production_id: str | None,
        status: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            existing = connection.execute(
                "SELECT * FROM studio_productions WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing:
                return self._studio_production_dict(existing)
            production_id = new_id("studio")
            connection.execute(
                """
                INSERT INTO studio_productions(
                    id, project_id, operation_id, transport, external_production_id,
                    title, autonomy, budget_minor, currency, status, packet_hash,
                    opened_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    production_id,
                    project_id,
                    operation_id,
                    transport,
                    external_production_id,
                    title,
                    autonomy,
                    budget_minor,
                    currency,
                    status,
                    packet_hash,
                    now,
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="studio_production",
                entity_id=production_id,
                event_type="studio_production.opened",
                payload={
                    "operation_id": operation_id,
                    "external_production_id": external_production_id,
                    "budget_minor": budget_minor,
                    "currency": currency,
                    "status": status,
                },
            )
            row = connection.execute(
                "SELECT * FROM studio_productions WHERE id = ?", (production_id,)
            ).fetchone()
            return self._studio_production_dict(row)

    def get_studio_production(self, operation_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM studio_productions WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            return self._studio_production_dict(row) if row else None

    def update_studio_production(
        self,
        production_id: str,
        *,
        status: str | None = None,
        external_production_id: str | None = None,
        last_error: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM studio_productions WHERE id = ?", (production_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Studio production not found: {production_id}")
            connection.execute(
                """
                UPDATE studio_productions
                SET status = COALESCE(?, status),
                    external_production_id = COALESCE(?, external_production_id),
                    last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, external_production_id, last_error, now, production_id),
            )
            self._event(
                connection,
                project_id=row["project_id"],
                entity_type="studio_production",
                entity_id=production_id,
                event_type="studio_production.updated",
                payload={"from": row["status"], "to": status},
            )
            updated = connection.execute(
                "SELECT * FROM studio_productions WHERE id = ?", (production_id,)
            ).fetchone()
            return self._studio_production_dict(updated)

    @staticmethod
    def _studio_job_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "production_id": row["production_id"],
            "external_job_id": row["external_job_id"],
            "phase": row["phase"],
            "status": row["status"],
            "request_hash": row["request_hash"],
            "detail": json.loads(row["detail_json"]) if row["detail_json"] else None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def record_studio_job(
        self,
        production_id: str,
        *,
        external_job_id: str,
        phase: str,
        status: str,
        request_hash: str,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM studio_productions WHERE id = ?", (production_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Studio production not found: {production_id}")
            existing = connection.execute(
                """
                SELECT * FROM studio_jobs
                WHERE production_id = ? AND external_job_id = ?
                """,
                (production_id, external_job_id),
            ).fetchone()
            if existing:
                return self._studio_job_dict(existing)
            job_id = new_id("studiojob")
            connection.execute(
                """
                INSERT INTO studio_jobs(
                    id, production_id, external_job_id, phase, status, request_hash,
                    detail_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    production_id,
                    external_job_id,
                    phase,
                    status,
                    request_hash,
                    canonical_json(detail or {}),
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=row["project_id"],
                entity_type="studio_job",
                entity_id=job_id,
                event_type="studio_job.submitted",
                payload={
                    "production_id": production_id,
                    "external_job_id": external_job_id,
                    "phase": phase,
                },
            )
            created = connection.execute(
                "SELECT * FROM studio_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return self._studio_job_dict(created)

    def update_studio_job(
        self,
        job_id: str,
        *,
        status: str,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM studio_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Studio job not found: {job_id}")
            connection.execute(
                """
                UPDATE studio_jobs
                SET status = ?, detail_json = COALESCE(?, detail_json), updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    canonical_json(detail) if detail is not None else None,
                    now,
                    job_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM studio_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return self._studio_job_dict(updated)

    @staticmethod
    def _publication_authorization_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "artifact_id": row["artifact_id"],
            "artifact_hash": row["artifact_hash"],
            "channels": json.loads(row["channels_json"]),
            "authorized_by": row["authorized_by"],
            "reference": row["reference"],
            "authorized_at": row["authorized_at"],
            "expires_at": row["expires_at"],
            "revoked_at": row["revoked_at"],
            "created_at": row["created_at"],
        }

    def record_publication_authorization(
        self,
        project_id: str,
        *,
        artifact_id: str,
        artifact_hash: str,
        channels: tuple[str, ...],
        authorized_by: str,
        reference: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            artifact = connection.execute(
                """
                SELECT id, content_hash FROM artifacts
                WHERE id = ? AND project_id = ?
                """,
                (artifact_id, project_id),
            ).fetchone()
            if not artifact or artifact["content_hash"] != artifact_hash:
                raise IntegrityError("Publication artifact/hash mismatch")
            connection.execute(
                """
                UPDATE publication_authorizations
                SET revoked_at = ?
                WHERE project_id = ? AND revoked_at IS NULL
                """,
                (now, project_id),
            )
            authorization_id = new_id("pubauth")
            connection.execute(
                """
                INSERT INTO publication_authorizations(
                    id, project_id, artifact_id, artifact_hash, channels_json,
                    authorized_by, reference, authorized_at, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    authorization_id,
                    project_id,
                    artifact_id,
                    artifact_hash,
                    canonical_json(channels),
                    authorized_by,
                    reference,
                    now,
                    expires_at,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="publication_authorization",
                entity_id=authorization_id,
                event_type="publication.authorized",
                payload={
                    "artifact_id": artifact_id,
                    "artifact_hash": artifact_hash,
                    "channels": channels,
                    "authorized_by": authorized_by,
                },
            )
            row = connection.execute(
                "SELECT * FROM publication_authorizations WHERE id = ?",
                (authorization_id,),
            ).fetchone()
            return self._publication_authorization_dict(row)

    @staticmethod
    def _shot_reference_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "shot_id": row["shot_id"],
            "role": row["role"],
            "artifact_id": row["artifact_id"],
            "element_id": row["element_id"],
            "notes": row["notes"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def upsert_shot_reference(
        self, project_id: str, request: ShotReferenceCreate
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            shot = connection.execute(
                "SELECT id FROM shots WHERE id = ? AND project_id = ?",
                (request.shot_id, project_id),
            ).fetchone()
            if not shot:
                raise NotFoundError(f"Shot not found in project: {request.shot_id}")
            if request.artifact_id:
                artifact = connection.execute(
                    "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                    (request.artifact_id, project_id),
                ).fetchone()
                if not artifact:
                    raise NotFoundError(
                        f"Reference artifact not found: {request.artifact_id}"
                    )
            if request.element_id:
                element = connection.execute(
                    """
                    SELECT id FROM creative_elements
                    WHERE id = ? AND project_id = ? AND is_current = 1
                    """,
                    (request.element_id, project_id),
                ).fetchone()
                if not element:
                    raise NotFoundError(
                        f"Current element not found: {request.element_id}"
                    )
            existing = connection.execute(
                "SELECT * FROM shot_references WHERE shot_id = ? AND role = ?",
                (request.shot_id, request.role),
            ).fetchone()
            if existing:
                connection.execute(
                    """
                    UPDATE shot_references
                    SET artifact_id = ?, element_id = ?, notes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        request.artifact_id,
                        request.element_id,
                        request.notes,
                        now,
                        existing["id"],
                    ),
                )
                reference_id = existing["id"]
                event_type = "shot_reference.updated"
            else:
                reference_id = new_id("ref")
                connection.execute(
                    """
                    INSERT INTO shot_references(
                        id, project_id, shot_id, role, artifact_id, element_id,
                        notes, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reference_id,
                        project_id,
                        request.shot_id,
                        request.role,
                        request.artifact_id,
                        request.element_id,
                        request.notes,
                        now,
                        now,
                    ),
                )
                event_type = "shot_reference.created"
            self._event(
                connection,
                project_id=project_id,
                entity_type="shot_reference",
                entity_id=reference_id,
                event_type=event_type,
                payload={
                    "shot_id": request.shot_id,
                    "role": request.role,
                    "artifact_id": request.artifact_id,
                    "element_id": request.element_id,
                },
            )
            row = connection.execute(
                "SELECT * FROM shot_references WHERE id = ?", (reference_id,)
            ).fetchone()
            return self._shot_reference_dict(row)

    def list_shot_references(
        self, project_id: str, *, shot_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            if shot_id:
                rows = connection.execute(
                    """
                    SELECT * FROM shot_references
                    WHERE project_id = ? AND shot_id = ? ORDER BY role
                    """,
                    (project_id, shot_id),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM shot_references
                    WHERE project_id = ? ORDER BY shot_id, role
                    """,
                    (project_id,),
                ).fetchall()
            return [self._shot_reference_dict(row) for row in rows]

    def current_publication_authorization(
        self, project_id: str
    ) -> dict[str, Any] | None:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            row = connection.execute(
                """
                SELECT * FROM publication_authorizations
                WHERE project_id = ? AND revoked_at IS NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            return self._publication_authorization_dict(row) if row else None

    def studio_jobs(self, production_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM studio_jobs WHERE production_id = ? ORDER BY created_at",
                (production_id,),
            ).fetchall()
            return [self._studio_job_dict(row) for row in rows]

    def complete_specialist_handoff(
        self,
        operation_id: str,
        *,
        status: str,
        output_artifact_ids: tuple[str, ...],
        result_packet_hash: str,
        tool_version: str | None = None,
        operator_note: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM specialist_handoffs WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if not row:
                raise NotFoundError(
                    f"Specialist handoff not found for operation: {operation_id}"
                )
            for artifact_id in output_artifact_ids:
                artifact = connection.execute(
                    "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                    (artifact_id, row["project_id"]),
                ).fetchone()
                if not artifact:
                    raise IntegrityError(
                        f"Specialist output artifact is not in the project: {artifact_id}"
                    )
            if row["result_packet_hash"]:
                observed = self._specialist_handoff_dict(row)
                if (
                    observed["result_packet_hash"] != result_packet_hash
                    or observed["output_artifact_ids"] != list(output_artifact_ids)
                    or observed["status"] != status
                ):
                    raise ConflictError(
                        "Specialist handoff already completed with another result"
                    )
                return observed
            connection.execute(
                """
                UPDATE specialist_handoffs
                SET output_artifact_ids_json = ?, result_packet_hash = ?,
                    tool_version = ?, operator_note = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    canonical_json(output_artifact_ids),
                    result_packet_hash,
                    tool_version,
                    operator_note,
                    status,
                    now,
                    row["id"],
                ),
            )
            self._event(
                connection,
                project_id=row["project_id"],
                entity_type="specialist_handoff",
                entity_id=row["id"],
                event_type="specialist_handoff.completed",
                payload={
                    "operation_id": operation_id,
                    "status": status,
                    "result_packet_hash": result_packet_hash,
                    "output_artifact_ids": output_artifact_ids,
                },
            )
            updated = connection.execute(
                "SELECT * FROM specialist_handoffs WHERE id = ?", (row["id"],)
            ).fetchone()
            return self._specialist_handoff_dict(updated)

    def record_specialist_result(
        self,
        operation_id: str,
        request: ExecutionReceiptCreate,
        *,
        result_packet_hash: str,
        tool_version: str | None = None,
        operator_note: str | None = None,
        approved_variance_minor: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Atomically settle a specialist operation and its linked handoff."""

        now = utc_now()
        with self.database.transaction() as connection:
            operation = connection.execute(
                "SELECT * FROM tool_operations WHERE id = ?", (operation_id,)
            ).fetchone()
            if not operation:
                raise NotFoundError(f"Tool operation not found: {operation_id}")
            handoff = connection.execute(
                "SELECT * FROM specialist_handoffs WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if not handoff:
                raise NotFoundError(
                    f"Specialist handoff not found for operation: {operation_id}"
                )
            budget_job = (
                self._require_job(connection, handoff["budget_job_id"])
                if handoff["budget_job_id"]
                else None
            )
            cost = request.actual_cost
            if budget_job and cost is None:
                cost = Money(amount=Decimal(0), currency=budget_job["currency"])
            actual_minor = cost.minor if cost else 0
            currency = cost.currency if cost else None
            if budget_job:
                if currency != budget_job["currency"]:
                    raise IntegrityError(
                        "Studio actual cost currency does not match its reservation"
                    )
                if actual_minor > budget_job["reserved_cost_minor"]:
                    if (
                        approved_variance_minor is None
                        or approved_variance_minor != actual_minor
                    ):
                        raise BudgetExceededError(
                            "Studio actual cost exceeds the controller reservation; "
                            "manual reconciliation is required"
                        )
                    project_row = self._require_project(
                        connection, operation["project_id"]
                    )
                    if (
                        project_row["spent_minor"] + actual_minor
                        > project_row["max_budget_minor"]
                    ):
                        raise BudgetExceededError(
                            "Studio actual cost would exceed the project budget"
                        )
            elif actual_minor:
                raise IntegrityError(
                    "Paid specialist execution is missing a controller budget reservation"
                )
            elif cost is None:
                project = self._require_project(connection, operation["project_id"])
                currency = project["currency"]
            receipt_content = {
                "status": request.status.value,
                "elapsed_ms": request.elapsed_ms,
                "output_artifact_ids": request.output_artifact_ids,
                "response_hash": request.response_hash,
                "actual_cost_minor": actual_minor,
                "currency": currency,
                "details": request.details,
            }
            existing = connection.execute(
                "SELECT * FROM execution_receipts WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if existing:
                observed = {
                    "status": existing["status"],
                    "elapsed_ms": existing["elapsed_ms"],
                    "output_artifact_ids": json.loads(
                        existing["output_artifact_ids_json"]
                    ),
                    "response_hash": existing["response_hash"],
                    "actual_cost_minor": existing["actual_cost_minor"],
                    "currency": existing["currency"],
                    "details": json.loads(existing["details_json"]),
                }
                handoff_dict = self._specialist_handoff_dict(handoff)
                if (
                    sha256_json(observed) != sha256_json(receipt_content)
                    or handoff_dict["result_packet_hash"] != result_packet_hash
                    or handoff_dict["output_artifact_ids"]
                    != list(request.output_artifact_ids)
                ):
                    raise ConflictError(
                        "Specialist operation already has a different terminal result"
                    )
                receipt = {
                    "id": existing["id"],
                    "operation_id": operation_id,
                    **observed,
                    "created_at": existing["created_at"],
                }
                return receipt, handoff_dict
            operation_status = ToolOperationStatus(operation["status"])
            if operation_status not in {
                ToolOperationStatus.AUTHORIZED,
                ToolOperationStatus.RUNNING,
            }:
                raise ConflictError(
                    f"Operation in {operation_status.value} cannot receive a new result"
                )
            for artifact_id in request.output_artifact_ids:
                artifact = connection.execute(
                    "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                    (artifact_id, operation["project_id"]),
                ).fetchone()
                if not artifact:
                    raise IntegrityError(
                        f"Specialist output artifact is not in the project: {artifact_id}"
                    )
            if handoff["result_packet_hash"]:
                raise ConflictError(
                    "Specialist handoff has a result without a matching receipt"
                )
            if budget_job:
                job_status = JobStatus(budget_job["status"])
                terminal_jobs = {
                    JobStatus.SUCCEEDED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                }
                if job_status in terminal_jobs:
                    raise ConflictError(
                        f"Studio budget job is already terminal: {job_status.value}"
                    )
                if request.status == ToolOperationStatus.OUTCOME_UNKNOWN:
                    if actual_minor:
                        raise IntegrityError(
                            "An unknown Studio outcome cannot declare a settled charge"
                        )
                    connection.execute(
                        """
                        UPDATE jobs SET status = ?, error_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            JobStatus.SUBMISSION_UNKNOWN.value,
                            canonical_json(
                                {
                                    "type": "StudioOutcomeUnknown",
                                    "operation_id": operation_id,
                                }
                            ),
                            now,
                            budget_job["id"],
                        ),
                    )
                    self._event(
                        connection,
                        project_id=operation["project_id"],
                        entity_type="job",
                        entity_id=budget_job["id"],
                        event_type="job.status_changed",
                        payload={
                            "from": job_status.value,
                            "to": JobStatus.SUBMISSION_UNKNOWN.value,
                        },
                    )
                else:
                    target_job_status = {
                        ToolOperationStatus.SUCCEEDED: JobStatus.SUCCEEDED,
                        ToolOperationStatus.FAILED: JobStatus.FAILED,
                        ToolOperationStatus.CANCELLED: JobStatus.CANCELLED,
                    }[request.status]
                    reserved = budget_job["reserved_cost_minor"]
                    connection.execute(
                        """
                        UPDATE jobs
                        SET status = ?, reserved_cost_minor = 0, actual_cost_minor = ?,
                            response_json = ?, updated_at = ? WHERE id = ?
                        """,
                        (
                            target_job_status.value,
                            actual_minor,
                            canonical_json(
                                {
                                    "operation_id": operation_id,
                                    "result_packet_hash": result_packet_hash,
                                }
                            ),
                            now,
                            budget_job["id"],
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE projects
                        SET reserved_minor = reserved_minor - ?,
                            spent_minor = spent_minor + ?, version = version + 1,
                            updated_at = ? WHERE id = ?
                        """,
                        (
                            reserved,
                            actual_minor,
                            now,
                            operation["project_id"],
                        ),
                    )
                    self._ledger(
                        connection,
                        project_id=operation["project_id"],
                        job_id=budget_job["id"],
                        kind=BudgetEntryKind.RELEASE,
                        amount_minor=reserved,
                        currency=budget_job["currency"],
                        note="Released terminal Studio run reservation",
                    )
                    if actual_minor:
                        self._ledger(
                            connection,
                            project_id=operation["project_id"],
                            job_id=budget_job["id"],
                            kind=BudgetEntryKind.CHARGE,
                            amount_minor=actual_minor,
                            currency=budget_job["currency"],
                            note="OpenMontage Studio run cost",
                        )
                    self._event(
                        connection,
                        project_id=operation["project_id"],
                        entity_type="job",
                        entity_id=budget_job["id"],
                        event_type="job.settled",
                        payload={
                            "from": job_status.value,
                            "to": target_job_status.value,
                            "reserved_released_minor": reserved,
                            "actual_cost_minor": actual_minor,
                        },
                    )
            receipt_id = new_id("receipt")
            connection.execute(
                """
                INSERT INTO execution_receipts(
                    id, operation_id, status, elapsed_ms, output_artifact_ids_json,
                    response_hash, actual_cost_minor, currency, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    operation_id,
                    request.status.value,
                    request.elapsed_ms,
                    canonical_json(request.output_artifact_ids),
                    request.response_hash,
                    actual_minor,
                    currency,
                    canonical_json(request.details),
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE tool_operations
                SET status = ?, finished_at = ?, updated_at = ? WHERE id = ?
                """,
                (request.status.value, now, now, operation_id),
            )
            connection.execute(
                """
                UPDATE specialist_handoffs
                SET output_artifact_ids_json = ?, result_packet_hash = ?,
                    tool_version = ?, operator_note = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    canonical_json(request.output_artifact_ids),
                    result_packet_hash,
                    tool_version,
                    operator_note,
                    request.status.value,
                    now,
                    handoff["id"],
                ),
            )
            self._event(
                connection,
                project_id=operation["project_id"],
                entity_type="execution_receipt",
                entity_id=receipt_id,
                event_type="execution_receipt.recorded",
                payload={
                    "operation_id": operation_id,
                    "status": request.status.value,
                    "output_count": len(request.output_artifact_ids),
                    "actual_cost_minor": actual_minor,
                },
            )
            self._event(
                connection,
                project_id=operation["project_id"],
                entity_type="specialist_handoff",
                entity_id=handoff["id"],
                event_type="specialist_handoff.completed",
                payload={
                    "operation_id": operation_id,
                    "status": request.status.value,
                    "result_packet_hash": result_packet_hash,
                    "output_artifact_ids": request.output_artifact_ids,
                },
            )
            updated_handoff = connection.execute(
                "SELECT * FROM specialist_handoffs WHERE id = ?", (handoff["id"],)
            ).fetchone()
            receipt = {
                "id": receipt_id,
                "operation_id": operation_id,
                **receipt_content,
                "created_at": now,
            }
            return receipt, self._specialist_handoff_dict(updated_handoff)

    def register_archive(
        self, project_id: str, manifest_artifact_id: str, manifest_hash: str
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            if project["status"] != ProjectStatus.FINAL_APPROVED.value:
                raise ConflictError("Only a final-approved project can be archived")
            artifact = connection.execute(
                """
                SELECT id, content_hash FROM artifacts
                WHERE id = ? AND project_id = ?
                """,
                (manifest_artifact_id, project_id),
            ).fetchone()
            if not artifact or artifact["content_hash"] != manifest_hash:
                raise IntegrityError("Archive manifest artifact/hash mismatch")
            existing = connection.execute(
                "SELECT * FROM archives WHERE project_id = ?", (project_id,)
            ).fetchone()
            if existing:
                if (
                    existing["manifest_artifact_id"] != manifest_artifact_id
                    or existing["manifest_hash"] != manifest_hash
                ):
                    raise ConflictError(
                        "Project already has a different archive manifest"
                    )
                return dict(existing)
            archive_id = new_id("archive")
            connection.execute(
                """
                INSERT INTO archives(
                    id, project_id, manifest_artifact_id, manifest_hash, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (archive_id, project_id, manifest_artifact_id, manifest_hash, now),
            )
            connection.execute(
                """
                UPDATE projects
                SET status = ?, version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (ProjectStatus.ARCHIVED.value, now, project_id),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="archive",
                entity_id=archive_id,
                event_type="project.archived",
                payload={
                    "manifest_artifact_id": manifest_artifact_id,
                    "manifest_hash": manifest_hash,
                },
            )
            row = connection.execute(
                "SELECT * FROM archives WHERE id = ?", (archive_id,)
            ).fetchone()
            return dict(row)

    def get_archive(self, project_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            row = connection.execute(
                "SELECT * FROM archives WHERE project_id = ?", (project_id,)
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _qa_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "artifact_id": row["artifact_id"],
            "check_name": row["check_name"],
            "status": row["status"],
            "findings": json.loads(row["findings_json"]),
            "evidence_hash": row["evidence_hash"],
            "created_at": row["created_at"],
        }

    def record_qa_result(
        self,
        *,
        project_id: str,
        artifact_id: str,
        check_name: str,
        status: str,
        findings: dict[str, Any],
        evidence_hash: str,
    ) -> dict[str, Any]:
        if status not in {item.value for item in QAStatus}:
            raise IntegrityError(f"Unsupported QA status: {status}")
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            artifact = connection.execute(
                "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                (artifact_id, project_id),
            ).fetchone()
            if not artifact:
                raise NotFoundError(
                    f"QA subject artifact not found in project: {artifact_id}"
                )
            existing = connection.execute(
                """
                SELECT * FROM qa_results
                WHERE project_id = ? AND artifact_id = ? AND check_name = ?
                  AND evidence_hash = ?
                """,
                (project_id, artifact_id, check_name, evidence_hash),
            ).fetchone()
            if existing:
                return self._qa_dict(existing)
            qa_id = new_id("qa")
            connection.execute(
                """
                INSERT INTO qa_results(
                    id, project_id, artifact_id, check_name, status,
                    findings_json, evidence_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    qa_id,
                    project_id,
                    artifact_id,
                    check_name,
                    status,
                    canonical_json(findings),
                    evidence_hash,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="qa_result",
                entity_id=qa_id,
                event_type="qa.recorded",
                payload={
                    "artifact_id": artifact_id,
                    "check_name": check_name,
                    "status": status,
                    "evidence_hash": evidence_hash,
                },
            )
            row = connection.execute(
                "SELECT * FROM qa_results WHERE id = ?", (qa_id,)
            ).fetchone()
            return self._qa_dict(row)

    def list_qa_results(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM qa_results WHERE project_id = ? ORDER BY created_at, check_name",
                (project_id,),
            ).fetchall()
            return [self._qa_dict(row) for row in rows]

    @staticmethod
    def _ledger(
        connection: sqlite3.Connection,
        *,
        project_id: str,
        job_id: str,
        kind: BudgetEntryKind,
        amount_minor: int,
        currency: str,
        note: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO budget_ledger(id, project_id, job_id, kind, amount_minor, currency, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("led"),
                project_id,
                job_id,
                kind.value,
                amount_minor,
                currency,
                note,
                utc_now(),
            ),
        )

    def reserve_job(
        self, project_id: str, request: JobRequest, request_hash: str
    ) -> JobRecord:
        now = utc_now()
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            existing = connection.execute(
                """
                SELECT * FROM jobs
                WHERE project_id = ? AND provider = ? AND idempotency_key = ?
                """,
                (project_id, request.provider, request.idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise ConflictError(
                        "Idempotency key was already used for a different request"
                    )
                return self._job_from_row(existing)
            if project["currency"] != request.estimated_cost.currency:
                raise IntegrityError(
                    f"Job currency {request.estimated_cost.currency} does not match "
                    f"project currency {project['currency']}"
                )
            exposure = project["spent_minor"] + project["reserved_minor"]
            if exposure + request.estimated_cost.minor > project["max_budget_minor"]:
                available = project["max_budget_minor"] - exposure
                raise BudgetExceededError(
                    f"Insufficient budget: requested {request.estimated_cost.minor} minor units, "
                    f"available {available}"
                )
            job_id = new_id("job")
            connection.execute(
                """
                INSERT INTO jobs(
                    id, project_id, provider, endpoint, operation, idempotency_key,
                    request_hash, request_json, status, estimated_cost_minor,
                    reserved_cost_minor, currency, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    project_id,
                    request.provider,
                    request.endpoint,
                    request.operation,
                    request.idempotency_key,
                    request_hash,
                    canonical_json(request.model_dump(mode="json")),
                    JobStatus.BUDGET_RESERVED.value,
                    request.estimated_cost.minor,
                    request.estimated_cost.minor,
                    request.estimated_cost.currency,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE projects
                SET reserved_minor = reserved_minor + ?, version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (request.estimated_cost.minor, now, project_id),
            )
            self._ledger(
                connection,
                project_id=project_id,
                job_id=job_id,
                kind=BudgetEntryKind.RESERVE,
                amount_minor=request.estimated_cost.minor,
                currency=request.estimated_cost.currency,
                note="Controller-approved reservation",
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="job",
                entity_id=job_id,
                event_type="job.budget_reserved",
                payload={
                    "request_hash": request_hash,
                    "reserved_minor": request.estimated_cost.minor,
                },
            )
            return self._job_from_row(self._require_job(connection, job_id))

    def get_job(self, job_id: str) -> JobRecord:
        with self.database.read() as connection:
            return self._job_from_row(self._require_job(connection, job_id))

    def update_job_status(
        self,
        job_id: str,
        *,
        expected: set[JobStatus],
        target: JobStatus,
        provider_job_id: str | None = None,
        response: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> JobRecord:
        now = utc_now()
        with self.database.transaction() as connection:
            job = self._require_job(connection, job_id)
            if JobStatus(job["status"]) not in expected:
                expected_values = ", ".join(sorted(item.value for item in expected))
                raise ConflictError(
                    f"Job {job_id} must be in one of [{expected_values}], observed {job['status']}"
                )
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, provider_job_id = COALESCE(?, provider_job_id),
                    response_json = COALESCE(?, response_json),
                    error_json = COALESCE(?, error_json), updated_at = ?
                WHERE id = ?
                """,
                (
                    target.value,
                    provider_job_id,
                    canonical_json(response) if response is not None else None,
                    canonical_json(error) if error is not None else None,
                    now,
                    job_id,
                ),
            )
            self._event(
                connection,
                project_id=job["project_id"],
                entity_type="job",
                entity_id=job_id,
                event_type="job.status_changed",
                payload={"from": job["status"], "to": target.value},
            )
            return self._job_from_row(self._require_job(connection, job_id))

    def settle_job(
        self,
        job_id: str,
        *,
        target: JobStatus,
        actual_cost: Money,
        response: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        approved_variance_minor: int | None = None,
        asset_artifact_id: str | None = None,
    ) -> JobRecord:
        if target not in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
            raise IntegrityError(f"Invalid terminal status: {target.value}")
        now = utc_now()
        with self.database.transaction() as connection:
            job = self._require_job(connection, job_id)
            current = JobStatus(job["status"])
            terminal = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
            if current in terminal:
                if current == target and job["actual_cost_minor"] == actual_cost.minor:
                    return self._job_from_row(job)
                raise ConflictError(f"Job is already terminal: {current.value}")
            if actual_cost.currency != job["currency"]:
                raise IntegrityError(
                    "Actual cost currency does not match the reservation"
                )
            budget_item = connection.execute(
                """
                SELECT i.id, i.plan_id, i.project_id, i.estimated_minor,
                       p.tolerance_bps, p.tolerance_floor_minor, p.currency
                FROM budget_plan_items i
                JOIN budget_plans p ON p.id = i.plan_id
                WHERE i.job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if actual_cost.minor > job["reserved_cost_minor"]:
                if not budget_item:
                    raise BudgetExceededError(
                        "Actual cost exceeds the approved reservation; "
                        "manual reconciliation is required"
                    )
                allowance = max(
                    budget_item["estimated_minor"]
                    * budget_item["tolerance_bps"]
                    // 10000,
                    budget_item["tolerance_floor_minor"],
                )
                allowed_minor = budget_item["estimated_minor"] + allowance
                variance_approved = (
                    approved_variance_minor is not None
                    and approved_variance_minor == actual_cost.minor
                )
                if actual_cost.minor > allowed_minor and not variance_approved:
                    raise VarianceApprovalRequiredError(
                        "Actual cost exceeds the plan's variance tolerance; "
                        "approval is required",
                        job_id=job_id,
                        plan_id=budget_item["plan_id"],
                        item_id=budget_item["id"],
                        estimated_minor=budget_item["estimated_minor"],
                        actual_minor=actual_cost.minor,
                        allowed_minor=allowed_minor,
                        currency=budget_item["currency"],
                    )
                project_row = self._require_project(connection, job["project_id"])
                if (
                    project_row["spent_minor"] + actual_cost.minor
                    > project_row["max_budget_minor"]
                ):
                    raise BudgetExceededError(
                        "Actual cost would exceed the project budget; "
                        "further approval is required"
                    )
            reserved = job["reserved_cost_minor"]
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, reserved_cost_minor = 0, actual_cost_minor = ?,
                    response_json = COALESCE(?, response_json),
                    error_json = COALESCE(?, error_json), updated_at = ?
                WHERE id = ?
                """,
                (
                    target.value,
                    actual_cost.minor,
                    canonical_json(response) if response is not None else None,
                    canonical_json(error) if error is not None else None,
                    now,
                    job_id,
                ),
            )
            connection.execute(
                """
                UPDATE projects
                SET reserved_minor = reserved_minor - ?, spent_minor = spent_minor + ?,
                    version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (reserved, actual_cost.minor, now, job["project_id"]),
            )
            self._ledger(
                connection,
                project_id=job["project_id"],
                job_id=job_id,
                kind=BudgetEntryKind.RELEASE,
                amount_minor=reserved,
                currency=job["currency"],
                note="Released terminal job reservation",
            )
            if actual_cost.minor:
                self._ledger(
                    connection,
                    project_id=job["project_id"],
                    job_id=job_id,
                    kind=BudgetEntryKind.CHARGE,
                    amount_minor=actual_cost.minor,
                    currency=job["currency"],
                    note=f"Provider cost for {target.value} job",
                )
            self.settle_budget_item_for_job(connection, job_id, actual_cost.minor)
            if asset_artifact_id:
                self._record_asset_cost_tx(
                    connection, job_id, asset_artifact_id, actual_cost.minor, now
                )
            self._event(
                connection,
                project_id=job["project_id"],
                entity_type="job",
                entity_id=job_id,
                event_type="job.settled",
                payload={
                    "from": job["status"],
                    "to": target.value,
                    "reserved_released_minor": reserved,
                    "actual_cost_minor": actual_cost.minor,
                },
            )
            return self._job_from_row(self._require_job(connection, job_id))

    @staticmethod
    def _budget_item_dict(row: sqlite3.Row, currency: str) -> dict[str, Any]:
        return {
            "id": row["id"],
            "plan_id": row["plan_id"],
            "project_id": row["project_id"],
            "stable_key": row["stable_key"],
            "ordinal": row["ordinal"],
            "category": row["category"],
            "provider": row["provider"],
            "operation": row["operation"],
            "estimated_cost": Money.from_minor(
                row["estimated_minor"], currency
            ).model_dump(mode="json"),
            "estimated_minor": row["estimated_minor"],
            "priority": row["priority"],
            "shot_id": row["shot_id"],
            "slot_id": row["slot_id"],
            "notes": row["notes"],
            "status": row["status"],
            "job_id": row["job_id"],
            "actual_minor": row["actual_minor"],
            "variance_minor": row["variance_minor"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _budget_plan_dict(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        items = connection.execute(
            "SELECT * FROM budget_plan_items WHERE plan_id = ? ORDER BY ordinal",
            (row["id"],),
        ).fetchall()
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "version": row["version"],
            "creative_summary": row["creative_summary"],
            "target": Money.from_minor(row["target_minor"], "USD").model_dump(
                mode="json"
            ),
            "target_minor": row["target_minor"],
            "planned_total": Money.from_minor(
                row["planned_total_minor"], "USD"
            ).model_dump(mode="json"),
            "planned_total_minor": row["planned_total_minor"],
            "currency": row["currency"],
            "tolerance_bps": row["tolerance_bps"],
            "tolerance_floor_minor": row["tolerance_floor_minor"],
            "status": row["status"],
            "content_hash": row["content_hash"],
            "is_current": bool(row["is_current"]),
            "created_at": row["created_at"],
            "items": [self._budget_item_dict(item, row["currency"]) for item in items],
        }

    def record_budget_plan(
        self,
        project_id: str,
        request: BudgetPlanCreate,
        *,
        status: str,
        target_minor: int,
    ) -> dict[str, Any]:
        now = utc_now()
        payload = request.model_dump(mode="json")
        content_hash = sha256_json(payload)
        planned_total = request.planned_total_minor
        currency = request.currency
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            if project["currency"] != currency:
                raise IntegrityError(
                    "Creative budget plan currency must match the project budget "
                    f"currency {project['currency']}"
                )
            current = connection.execute(
                "SELECT * FROM budget_plans WHERE project_id = ? AND is_current = 1",
                (project_id,),
            ).fetchone()
            if current and current["content_hash"] == content_hash:
                return self._budget_plan_dict(connection, current)
            version = int(current["version"]) + 1 if current else 1
            if current:
                connection.execute(
                    "UPDATE budget_plans SET is_current = 0 WHERE id = ?",
                    (current["id"],),
                )
            plan_id = new_id("budgetplan")
            connection.execute(
                """
                INSERT INTO budget_plans(
                    id, project_id, version, creative_summary, target_minor,
                    planned_total_minor, currency, tolerance_bps,
                    tolerance_floor_minor, status, content_hash, is_current, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    plan_id,
                    project_id,
                    version,
                    request.creative_summary,
                    target_minor,
                    planned_total,
                    currency,
                    request.tolerance_bps,
                    Money(
                        amount=request.tolerance_floor_amount, currency=currency
                    ).minor,
                    status,
                    content_hash,
                    now,
                ),
            )
            for item in request.items:
                if item.shot_id:
                    shot = connection.execute(
                        "SELECT id FROM shots WHERE id = ? AND project_id = ?",
                        (item.shot_id, project_id),
                    ).fetchone()
                    if not shot:
                        raise NotFoundError(
                            f"Budget item shot not found in project: {item.shot_id}"
                        )
                if item.slot_id:
                    slot = connection.execute(
                        "SELECT id FROM slots WHERE id = ? AND project_id = ?",
                        (item.slot_id, project_id),
                    ).fetchone()
                    if not slot:
                        raise NotFoundError(
                            f"Budget item slot not found in project: {item.slot_id}"
                        )
                connection.execute(
                    """
                    INSERT INTO budget_plan_items(
                        id, plan_id, project_id, stable_key, ordinal, category,
                        provider, operation, estimated_minor, priority, shot_id,
                        slot_id, notes, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("budgetitem"),
                        plan_id,
                        project_id,
                        item.stable_key,
                        item.ordinal,
                        item.category.value,
                        item.provider,
                        item.operation,
                        item.estimated_cost.minor,
                        item.priority.value,
                        item.shot_id,
                        item.slot_id,
                        item.notes,
                        BudgetItemStatus.PLANNED.value,
                        now,
                        now,
                    ),
                )
            self._event(
                connection,
                project_id=project_id,
                entity_type="budget_plan",
                entity_id=plan_id,
                event_type="budget_plan.recorded",
                payload={
                    "version": version,
                    "status": status,
                    "content_hash": content_hash,
                    "planned_total_minor": planned_total,
                },
            )
            row = connection.execute(
                "SELECT * FROM budget_plans WHERE id = ?", (plan_id,)
            ).fetchone()
            return self._budget_plan_dict(connection, row)

    def get_budget_plan(self, plan_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM budget_plans WHERE id = ?", (plan_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Budget plan not found: {plan_id}")
            return self._budget_plan_dict(connection, row)

    def current_budget_plan(self, project_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            row = connection.execute(
                "SELECT * FROM budget_plans WHERE project_id = ? AND is_current = 1",
                (project_id,),
            ).fetchone()
            return self._budget_plan_dict(connection, row) if row else None

    def record_budget_plan_approval(
        self,
        project_id: str,
        *,
        plan_id: str,
        plan_hash: str,
        approved_total: Money,
        approved_by: str,
        reason: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            plan = connection.execute(
                "SELECT * FROM budget_plans WHERE id = ? AND project_id = ?",
                (plan_id, project_id),
            ).fetchone()
            if not plan:
                raise NotFoundError(f"Budget plan not found in project: {plan_id}")
            if plan["content_hash"] != plan_hash:
                raise IntegrityError("Budget plan content changed before approval")
            if plan["planned_total_minor"] != approved_total.minor:
                raise IntegrityError("Approval total does not match the planned total")
            existing = connection.execute(
                "SELECT * FROM budget_plan_approvals WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            if existing:
                if (
                    existing["plan_hash"] != plan_hash
                    or existing["approved_total_minor"] != approved_total.minor
                ):
                    raise ConflictError("Budget plan already has a different approval")
                return self._budget_approval_dict(existing)
            approval_id = new_id("budgetapproval")
            connection.execute(
                """
                INSERT INTO budget_plan_approvals(
                    id, project_id, plan_id, plan_hash, approved_total_minor,
                    currency, approved_by, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    project_id,
                    plan_id,
                    plan_hash,
                    approved_total.minor,
                    approved_total.currency,
                    approved_by,
                    reason,
                    now,
                ),
            )
            connection.execute(
                "UPDATE budget_plans SET status = ? WHERE id = ?",
                (BudgetPlanStatus.APPROVED.value, plan_id),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="budget_plan",
                entity_id=plan_id,
                event_type="budget_plan.approved",
                payload={
                    "approval_id": approval_id,
                    "approved_total_minor": approved_total.minor,
                    "approved_by": approved_by,
                },
            )
            row = connection.execute(
                "SELECT * FROM budget_plan_approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            return self._budget_approval_dict(row)

    @staticmethod
    def _budget_approval_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "plan_id": row["plan_id"],
            "plan_hash": row["plan_hash"],
            "approved_total": Money.from_minor(
                row["approved_total_minor"], row["currency"]
            ).model_dump(mode="json"),
            "approved_total_minor": row["approved_total_minor"],
            "approved_by": row["approved_by"],
            "reason": row["reason"],
            "created_at": row["created_at"],
        }

    def budget_plan_approval(self, plan_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM budget_plan_approvals WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            return self._budget_approval_dict(row) if row else None

    def link_budget_item_job(
        self, project_id: str, plan_id: str, item_key: str, job_id: str
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            item = connection.execute(
                """
                SELECT * FROM budget_plan_items
                WHERE plan_id = ? AND project_id = ? AND stable_key = ?
                """,
                (plan_id, project_id, item_key),
            ).fetchone()
            if not item:
                raise NotFoundError(f"Budget item not found in plan: {item_key}")
            if item["job_id"]:
                if item["job_id"] == job_id:
                    return self._budget_item_dict(item, project["currency"])
                raise ConflictError("Budget item already reserved for another job")
            job = connection.execute(
                "SELECT id FROM jobs WHERE id = ? AND project_id = ?",
                (job_id, project_id),
            ).fetchone()
            if not job:
                raise IntegrityError(f"Budget item job is not in the project: {job_id}")
            connection.execute(
                """
                UPDATE budget_plan_items
                SET status = ?, job_id = ?, updated_at = ? WHERE id = ?
                """,
                (BudgetItemStatus.RESERVED.value, job_id, now, item["id"]),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="budget_plan_item",
                entity_id=item["id"],
                event_type="budget_item.reserved",
                payload={
                    "plan_id": plan_id,
                    "stable_key": item_key,
                    "job_id": job_id,
                    "estimated_minor": item["estimated_minor"],
                },
            )
            updated = connection.execute(
                "SELECT * FROM budget_plan_items WHERE id = ?", (item["id"],)
            ).fetchone()
            return self._budget_item_dict(updated, project["currency"])

    def settle_budget_item_for_job(
        self, connection: sqlite3.Connection, job_id: str, actual_minor: int
    ) -> None:
        now = utc_now()
        item = connection.execute(
            "SELECT * FROM budget_plan_items WHERE job_id = ?", (job_id,)
        ).fetchone()
        if not item or item["status"] in {
            BudgetItemStatus.SETTLED.value,
            BudgetItemStatus.SKIPPED.value,
        }:
            return
        variance = actual_minor - item["estimated_minor"]
        connection.execute(
            """
            UPDATE budget_plan_items
            SET status = ?, actual_minor = ?, variance_minor = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                BudgetItemStatus.SETTLED.value,
                actual_minor,
                variance,
                now,
                item["id"],
            ),
        )
        self._event(
            connection,
            project_id=item["project_id"],
            entity_type="budget_plan_item",
            entity_id=item["id"],
            event_type="budget_item.settled",
            payload={
                "job_id": job_id,
                "estimated_minor": item["estimated_minor"],
                "actual_minor": actual_minor,
                "variance_minor": variance,
            },
        )

    @staticmethod
    def _variance_request_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "kind": row["kind"],
            "plan_id": row["plan_id"],
            "item_id": row["item_id"],
            "job_id": row["job_id"],
            "operation_id": row["operation_id"],
            "estimated": Money.from_minor(
                row["estimated_minor"], row["currency"]
            ).model_dump(mode="json"),
            "actual": Money.from_minor(row["actual_minor"], row["currency"]).model_dump(
                mode="json"
            ),
            "allowed": Money.from_minor(
                row["allowed_minor"], row["currency"]
            ).model_dump(mode="json"),
            "overage": Money.from_minor(
                row["overage_minor"], row["currency"]
            ).model_dump(mode="json"),
            "estimated_minor": row["estimated_minor"],
            "actual_minor": row["actual_minor"],
            "allowed_minor": row["allowed_minor"],
            "overage_minor": row["overage_minor"],
            "currency": row["currency"],
            "status": row["status"],
            "requested_by": row["requested_by"],
            "reason": row["reason"],
            "decided_by": row["decided_by"],
            "decision_reason": row["decision_reason"],
            "decided_at": row["decided_at"],
            "created_at": row["created_at"],
        }

    def record_variance_request(
        self,
        project_id: str,
        *,
        plan_id: str | None,
        item_id: str | None,
        job_id: str,
        estimated_minor: int,
        actual_minor: int,
        allowed_minor: int,
        currency: str,
        requested_by: str | None,
        reason: str | None,
        kind: str = "plan_item",
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            existing = connection.execute(
                """
                SELECT * FROM budget_variance_requests
                WHERE job_id = ? AND actual_minor = ? AND currency = ?
                """,
                (job_id, actual_minor, currency),
            ).fetchone()
            if existing:
                return self._variance_request_dict(existing)
            request_id = new_id("variance")
            connection.execute(
                """
                INSERT INTO budget_variance_requests(
                    id, project_id, kind, plan_id, item_id, job_id, operation_id,
                    estimated_minor, actual_minor, allowed_minor, overage_minor,
                    currency, status, requested_by, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    project_id,
                    kind,
                    plan_id,
                    item_id,
                    job_id,
                    operation_id,
                    estimated_minor,
                    actual_minor,
                    allowed_minor,
                    actual_minor - allowed_minor,
                    currency,
                    "pending",
                    requested_by,
                    reason,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="budget_variance_request",
                entity_id=request_id,
                event_type="budget_variance.requested",
                payload={
                    "job_id": job_id,
                    "plan_id": plan_id,
                    "estimated_minor": estimated_minor,
                    "actual_minor": actual_minor,
                    "allowed_minor": allowed_minor,
                    "currency": currency,
                },
            )
            row = connection.execute(
                "SELECT * FROM budget_variance_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            return self._variance_request_dict(row)

    def get_variance_request(self, request_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM budget_variance_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            if not row:
                raise NotFoundError(f"Variance request not found: {request_id}")
            return self._variance_request_dict(row)

    @staticmethod
    def _asset_cost_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "job_id": row["job_id"],
            "plan_id": row["plan_id"],
            "item_id": row["item_id"],
            "slot_id": row["slot_id"],
            "artifact_id": row["artifact_id"],
            "provider": row["provider"],
            "operation": row["operation"],
            "currency": row["currency"],
            "estimated": Money.from_minor(
                row["estimated_minor"], row["currency"]
            ).model_dump(mode="json"),
            "actual": Money.from_minor(row["actual_minor"], row["currency"]).model_dump(
                mode="json"
            ),
            "variance": Money.from_minor(
                abs(row["variance_minor"]), row["currency"]
            ).model_dump(mode="json"),
            "variance_direction": (
                "over"
                if row["variance_minor"] > 0
                else "under"
                if row["variance_minor"] < 0
                else "even"
            ),
            "estimated_minor": row["estimated_minor"],
            "actual_minor": row["actual_minor"],
            "variance_minor": row["variance_minor"],
            "created_at": row["created_at"],
        }

    def _record_asset_cost_tx(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        artifact_id: str,
        actual_minor: int,
        now: str,
    ) -> None:
        item = connection.execute(
            "SELECT * FROM budget_plan_items WHERE job_id = ?", (job_id,)
        ).fetchone()
        if not item:
            return
        existing = connection.execute(
            "SELECT id FROM asset_costs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if existing:
            return
        job = connection.execute(
            "SELECT provider, operation, currency FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        cost_id = new_id("assetcost")
        connection.execute(
            """
            INSERT INTO asset_costs(
                id, project_id, job_id, plan_id, item_id, slot_id, artifact_id,
                provider, operation, currency, estimated_minor, actual_minor,
                variance_minor, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cost_id,
                item["project_id"],
                job_id,
                item["plan_id"],
                item["id"],
                item["slot_id"],
                artifact_id,
                job["provider"],
                job["operation"],
                job["currency"],
                item["estimated_minor"],
                actual_minor,
                actual_minor - item["estimated_minor"],
                now,
            ),
        )
        self._event(
            connection,
            project_id=item["project_id"],
            entity_type="asset_cost",
            entity_id=cost_id,
            event_type="asset_cost.recorded",
            payload={
                "job_id": job_id,
                "slot_id": item["slot_id"],
                "artifact_id": artifact_id,
                "estimated_minor": item["estimated_minor"],
                "actual_minor": actual_minor,
            },
        )

    def discard_artifact(self, artifact_id: str) -> bool:
        """Remove an artifact registered in this run; refuse if it is referenced."""

        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
            if not row:
                return False
            dependents = connection.execute(
                """
                SELECT COUNT(*) FROM artifact_dependencies
                WHERE entity_type = 'artifact' AND entity_id = ?
                """,
                (artifact_id,),
            ).fetchone()[0]
            if dependents:
                raise ConflictError(
                    "Artifact is referenced by other records and cannot be discarded"
                )
            connection.execute(
                "DELETE FROM artifact_dependencies WHERE artifact_id = ?",
                (artifact_id,),
            )
            connection.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
            self._event(
                connection,
                project_id=row["project_id"],
                entity_type="artifact",
                entity_id=artifact_id,
                event_type="artifact.discarded",
                payload={"relative_path": row["relative_path"]},
            )
            return True

    def raise_project_budget(
        self,
        project_id: str,
        *,
        new_max: Money,
        raised_by: str,
        reason: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            if new_max.currency != project["currency"]:
                raise IntegrityError(f"Budget currency must stay {project['currency']}")
            if new_max.minor <= project["max_budget_minor"]:
                raise IntegrityError("A budget raise must increase the maximum")
            committed = project["spent_minor"] + project["reserved_minor"]
            if new_max.minor < committed:
                raise IntegrityError(
                    "The new budget cannot be below committed and reserved spend"
                )
            connection.execute(
                """
                UPDATE projects
                SET max_budget_minor = ?, version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (new_max.minor, now, project_id),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="project",
                entity_id=project_id,
                event_type="budget.raised",
                payload={
                    "from_minor": project["max_budget_minor"],
                    "to_minor": new_max.minor,
                    "currency": new_max.currency,
                    "raised_by": raised_by,
                    "reason": reason,
                },
            )
            updated = self._require_project(connection, project_id)
            return self._project_from_row(updated).model_dump(mode="json")

    def record_overage_reconciliation(
        self,
        project_id: str,
        *,
        job_id: str,
        actual_cost: Money,
        reconciled_by: str,
        reason: str,
    ) -> None:
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            self._event(
                connection,
                project_id=project_id,
                entity_type="job",
                entity_id=job_id,
                event_type="budget.overage_reconciled",
                payload={
                    "actual_cost_minor": actual_cost.minor,
                    "currency": actual_cost.currency,
                    "reconciled_by": reconciled_by,
                    "reason": reason,
                },
            )

    def record_asset_cost(
        self,
        project_id: str,
        *,
        job_id: str,
        plan_id: str | None,
        item_id: str | None,
        slot_id: str | None,
        artifact_id: str | None,
        provider: str,
        operation: str,
        currency: str,
        estimated_minor: int,
        actual_minor: int,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            existing = connection.execute(
                "SELECT * FROM asset_costs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if existing:
                return self._asset_cost_dict(existing)
            cost_id = new_id("assetcost")
            connection.execute(
                """
                INSERT INTO asset_costs(
                    id, project_id, job_id, plan_id, item_id, slot_id, artifact_id,
                    provider, operation, currency, estimated_minor, actual_minor,
                    variance_minor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cost_id,
                    project_id,
                    job_id,
                    plan_id,
                    item_id,
                    slot_id,
                    artifact_id,
                    provider,
                    operation,
                    currency,
                    estimated_minor,
                    actual_minor,
                    actual_minor - estimated_minor,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="asset_cost",
                entity_id=cost_id,
                event_type="asset_cost.recorded",
                payload={
                    "job_id": job_id,
                    "slot_id": slot_id,
                    "artifact_id": artifact_id,
                    "estimated_minor": estimated_minor,
                    "actual_minor": actual_minor,
                },
            )
            row = connection.execute(
                "SELECT * FROM asset_costs WHERE id = ?", (cost_id,)
            ).fetchone()
            return self._asset_cost_dict(row)

    def list_asset_costs(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM asset_costs WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
            return [self._asset_cost_dict(row) for row in rows]

    def artifact_by_content_hash(
        self, project_id: str, content_hash: str
    ) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT * FROM artifacts
                WHERE project_id = ? AND content_hash = ? AND status = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (project_id, content_hash, ArtifactStatus.CURRENT.value),
            ).fetchone()
            if not row:
                return None
            return self._artifact_dict(connection, row)

    def approved_variance_for_job(
        self, job_id: str, actual_minor: int
    ) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT * FROM budget_variance_requests
                WHERE job_id = ? AND actual_minor = ? AND status = 'approved'
                ORDER BY created_at DESC LIMIT 1
                """,
                (job_id, actual_minor),
            ).fetchone()
            return self._variance_request_dict(row) if row else None

    def pending_variance_requests(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                """
                SELECT * FROM budget_variance_requests
                WHERE project_id = ? AND status = 'pending' ORDER BY created_at
                """,
                (project_id,),
            ).fetchall()
            return [self._variance_request_dict(row) for row in rows]

    def decide_variance_request(
        self,
        request_id: str,
        *,
        decision: str,
        decided_by: str,
        decision_reason: str,
    ) -> dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            raise IntegrityError(f"Unsupported variance decision: {decision}")
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM budget_variance_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            if not row:
                raise NotFoundError(f"Variance request not found: {request_id}")
            if row["status"] != "pending":
                raise ConflictError(f"Variance request is already {row['status']}")
            connection.execute(
                """
                UPDATE budget_variance_requests
                SET status = ?, decided_by = ?, decision_reason = ?, decided_at = ?
                WHERE id = ?
                """,
                (decision, decided_by, decision_reason, now, request_id),
            )
            self._event(
                connection,
                project_id=row["project_id"],
                entity_type="budget_variance_request",
                entity_id=request_id,
                event_type=f"budget_variance.{decision}",
                payload={
                    "job_id": row["job_id"],
                    "actual_minor": row["actual_minor"],
                    "allowed_minor": row["allowed_minor"],
                    "decided_by": decided_by,
                },
            )
            updated = connection.execute(
                "SELECT * FROM budget_variance_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            return self._variance_request_dict(updated)

    def skip_budget_item(self, item_id: str, *, reason: str) -> None:
        now = utc_now()
        with self.database.transaction() as connection:
            item = connection.execute(
                "SELECT * FROM budget_plan_items WHERE id = ?", (item_id,)
            ).fetchone()
            if not item or item["status"] != BudgetItemStatus.RESERVED.value:
                return
            connection.execute(
                """
                UPDATE budget_plan_items
                SET status = ?, updated_at = ? WHERE id = ?
                """,
                (BudgetItemStatus.SKIPPED.value, now, item_id),
            )
            self._event(
                connection,
                project_id=item["project_id"],
                entity_type="budget_plan_item",
                entity_id=item_id,
                event_type="budget_item.skipped",
                payload={"reason": reason, "job_id": item["job_id"]},
            )

    def recoverable_jobs(self, project_id: str | None = None) -> list[JobRecord]:
        statuses = (
            JobStatus.BUDGET_RESERVED.value,
            JobStatus.SUBMITTING.value,
            JobStatus.SUBMITTED.value,
            JobStatus.RUNNING.value,
            JobStatus.SUBMISSION_UNKNOWN.value,
        )
        placeholders = ",".join("?" for _ in statuses)
        with self.database.read() as connection:
            if project_id:
                self._require_project(connection, project_id)
                rows = connection.execute(
                    f"SELECT * FROM jobs WHERE project_id = ? AND status IN ({placeholders}) "
                    "ORDER BY created_at",
                    (project_id, *statuses),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT * FROM jobs WHERE status IN ({placeholders}) ORDER BY created_at",
                    statuses,
                ).fetchall()
            return [self._job_from_row(row) for row in rows]

    def record_capability(self, result: CapabilityResult) -> None:
        with self.database.transaction() as connection:
            check_id = new_id("cap")
            connection.execute(
                """
                INSERT INTO capability_checks(id, name, status, detail, checked_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (check_id, result.name, result.status.value, result.detail, utc_now()),
            )
            self._event(
                connection,
                project_id=None,
                entity_type="capability_check",
                entity_id=check_id,
                event_type="capability.checked",
                payload={"name": result.name, "status": result.status.value},
            )

    def latest_capability(self, name: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT * FROM capability_checks
                WHERE name = ? ORDER BY checked_at DESC, rowid DESC LIMIT 1
                """,
                (name,),
            ).fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "name": row["name"],
                "status": row["status"],
                "detail": row["detail"],
                "checked_at": row["checked_at"],
            }

    def events(self, project_id: str | None = None) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            if project_id:
                self._require_project(connection, project_id)
                rows = connection.execute(
                    "SELECT * FROM events WHERE project_id = ? ORDER BY sequence",
                    (project_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM events ORDER BY sequence"
                ).fetchall()
            return [
                {
                    "sequence": row["sequence"],
                    "id": row["id"],
                    "project_id": row["project_id"],
                    "entity_type": row["entity_type"],
                    "entity_id": row["entity_id"],
                    "event_type": row["event_type"],
                    "payload": json.loads(row["payload_json"]),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def budget_ledger(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM budget_ledger WHERE project_id = ? ORDER BY rowid",
                (project_id,),
            ).fetchall()
            return [dict(row) for row in rows]

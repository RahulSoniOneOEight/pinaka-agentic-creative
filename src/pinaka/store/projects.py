from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from typing import Any

from ..enums import (
    ProjectStatus,
)
from ..errors import (
    ConflictError,
    NotFoundError,
)
from ..models import (
    DeliverableCreate,
    DeliverableRecord,
    ProjectCreate,
    ProjectRecord,
    SegmentCreate,
)
from ..utils import canonical_json, new_id, sha256_json, slugify, utc_now


class ProjectsStore:
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
                        change_request_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        request.change_request_id,
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
                payload={
                    "slug": slug,
                    "status": ProjectStatus.DRAFT.value,
                    "parent_project_id": request.parent_project_id,
                    "change_request_id": request.change_request_id,
                },
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

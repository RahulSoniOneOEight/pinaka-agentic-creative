from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..errors import (
    ConflictError,
    IntegrityError,
    NotFoundError,
)
from ..utils import canonical_json, new_id, utc_now


class StudioJobsStore:
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

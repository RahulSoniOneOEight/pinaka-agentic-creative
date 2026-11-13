from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..errors import (
    ConflictError,
    NotFoundError,
)
from ..models import (
    ShotCreate,
    SlotCreate,
)
from ..utils import canonical_json, new_id, utc_now


class ShotsStore:
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
                        approved_sources_json, treatment_ceiling, exactness,
                        style_id, media_requirement, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        request.treatment_ceiling,
                        request.exactness,
                        request.style_id,
                        request.media_requirement,
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
                "treatment_ceiling": row["treatment_ceiling"],
                "exactness": row["exactness"],
                "style_id": row["style_id"],
                "media_requirement": row["media_requirement"],
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
                    "treatment_ceiling": row["treatment_ceiling"],
                    "exactness": row["exactness"],
                    "style_id": row["style_id"],
                    "media_requirement": row["media_requirement"],
                    "version": row["version"],
                }
                for row in rows
            ]

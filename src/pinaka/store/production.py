from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..contracts.planning import ScriptCreate
from ..errors import ConflictError, IntegrityError, NotFoundError
from ..production import ShotProductionCreate
from ..utils import canonical_json, new_id, utc_now


class ProductionStore:
    """Per-shot production contracts and approved scripts."""

    @staticmethod
    def _shot_production(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "shot_id": row["shot_id"],
            "preset_id": row["preset_id"],
            "style_id": row["style_id"],
            "appears": row["appears"],
            "method": row["method"],
            "fallback": row["fallback"],
            "asset_source": json.loads(row["asset_source_json"]),
            "exact_items": json.loads(row["exact_items_json"]),
            "layers": json.loads(row["layers_json"]),
            "motion": json.loads(row["motion_json"]),
            "qa": json.loads(row["qa_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _script(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "brief_id": row["brief_id"],
            "content_hash": row["content_hash"],
            "text": row["text"],
            "is_current": bool(row["is_current"]),
            "approved_by": row["approved_by"],
            "approved_at": row["approved_at"],
            "created_at": row["created_at"],
        }

    def upsert_shot_production(
        self, project_id: str, request: ShotProductionCreate
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
            existing = connection.execute(
                "SELECT id FROM shot_productions WHERE shot_id = ?",
                (request.shot_id,),
            ).fetchone()
            production_id = existing["id"] if existing else new_id("shprod")
            if existing:
                connection.execute(
                    """
                    UPDATE shot_productions
                    SET preset_id = ?, style_id = ?, appears = ?, method = ?,
                        fallback = ?, asset_source_json = ?, exact_items_json = ?,
                        layers_json = ?, motion_json = ?, qa_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        request.preset_id,
                        request.style_id,
                        request.appears,
                        request.method,
                        request.fallback,
                        canonical_json(request.asset_source),
                        canonical_json(request.exact_items),
                        canonical_json(
                            [layer.model_dump(mode="json") for layer in request.layers]
                        ),
                        canonical_json(request.motion),
                        canonical_json(request.qa),
                        now,
                        production_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO shot_productions(
                        id, project_id, shot_id, preset_id, style_id, appears,
                        method, fallback, asset_source_json, exact_items_json,
                        layers_json, motion_json, qa_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        production_id,
                        project_id,
                        request.shot_id,
                        request.preset_id,
                        request.style_id,
                        request.appears,
                        request.method,
                        request.fallback,
                        canonical_json(request.asset_source),
                        canonical_json(request.exact_items),
                        canonical_json(
                            [layer.model_dump(mode="json") for layer in request.layers]
                        ),
                        canonical_json(request.motion),
                        canonical_json(request.qa),
                        now,
                        now,
                    ),
                )
            self._event(
                connection,
                project_id=project_id,
                entity_type="shot_production",
                entity_id=production_id,
                event_type="production.shot_recorded",
                payload={
                    "shot_id": request.shot_id,
                    "preset_id": request.preset_id,
                    "style_id": request.style_id,
                    "method": request.method,
                    "layer_count": len(request.layers),
                },
            )
            row = connection.execute(
                "SELECT * FROM shot_productions WHERE id = ?", (production_id,)
            ).fetchone()
            return self._shot_production(row)

    def list_shot_productions(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                """
                SELECT p.* FROM shot_productions p
                JOIN shots s ON s.id = p.shot_id
                WHERE p.project_id = ?
                ORDER BY s.ordinal
                """,
                (project_id,),
            ).fetchall()
            return [self._shot_production(row) for row in rows]

    def get_shot_production(self, shot_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM shot_productions WHERE shot_id = ?", (shot_id,)
            ).fetchone()
            return self._shot_production(row) if row else None

    def record_script(
        self, project_id: str, request: ScriptCreate, *, content_hash: str
    ) -> dict[str, Any]:
        now = utc_now()
        script_id = new_id("script")
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            current = connection.execute(
                "SELECT * FROM scripts WHERE project_id = ? AND is_current = 1",
                (project_id,),
            ).fetchone()
            if current and current["content_hash"] == content_hash:
                return self._script(current)
            if current:
                connection.execute(
                    "UPDATE scripts SET is_current = 0 WHERE id = ?", (current["id"],)
                )
            connection.execute(
                """
                INSERT INTO scripts(
                    id, project_id, brief_id, content_hash, text, is_current,
                    approved_by, approved_at, created_at
                ) VALUES (?, ?, ?, ?, ?, 1, NULL, NULL, ?)
                """,
                (
                    script_id,
                    project_id,
                    request.brief_id,
                    content_hash,
                    request.text,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="script",
                entity_id=script_id,
                event_type="script.recorded",
                payload={"content_hash": content_hash, "brief_id": request.brief_id},
            )
            row = connection.execute(
                "SELECT * FROM scripts WHERE id = ?", (script_id,)
            ).fetchone()
            return self._script(row)

    def current_script(self, project_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            row = connection.execute(
                "SELECT * FROM scripts WHERE project_id = ? AND is_current = 1",
                (project_id,),
            ).fetchone()
            return self._script(row) if row else None

    def approve_script(
        self, project_id: str, script_id: str, *, approved_by: str
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            row = connection.execute(
                "SELECT * FROM scripts WHERE id = ? AND project_id = ?",
                (script_id, project_id),
            ).fetchone()
            if not row:
                raise NotFoundError(f"Script not found: {script_id}")
            if not row["is_current"]:
                raise ConflictError("Only the current script can be approved")
            connection.execute(
                "UPDATE scripts SET approved_by = ?, approved_at = ? WHERE id = ?",
                (approved_by, now, script_id),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="script",
                entity_id=script_id,
                event_type="script.approved",
                payload={
                    "approved_by": approved_by,
                    "content_hash": row["content_hash"],
                },
            )
            updated = connection.execute(
                "SELECT * FROM scripts WHERE id = ?", (script_id,)
            ).fetchone()
            return self._script(updated)

    def require_approved_script(self, project_id: str) -> dict[str, Any]:
        script = self.current_script(project_id)
        if not script:
            raise IntegrityError("An approved narration script is required")
        if not script["approved_by"]:
            raise IntegrityError("The current narration script is not approved")
        return script

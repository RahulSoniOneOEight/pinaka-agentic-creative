from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..errors import (
    NotFoundError,
)
from ..models import (
    CreativeElementCreate,
    ShotElementBindingCreate,
    ShotReferenceCreate,
)
from ..utils import canonical_json, new_id, sha256_json, utc_now


class ElementsStore:
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

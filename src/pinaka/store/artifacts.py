from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..enums import (
    ArtifactStatus,
    ProjectStatus,
)
from ..errors import (
    ConflictError,
    IntegrityError,
    NotFoundError,
)
from ..models import (
    ArtifactCreate,
    DependencyRef,
)
from ..utils import canonical_json, new_id, utc_now


class ArtifactsStore:
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

from __future__ import annotations

import json
import sqlite3
from collections import deque
from typing import Any

from ..enums import (
    ApprovalStatus,
    ArtifactStatus,
    ProjectStatus,
)
from ..models import (
    FactSetCreate,
    GroundingBundleCreate,
)
from ..utils import canonical_json, new_id, sha256_json, utc_now


class GroundingStore:
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

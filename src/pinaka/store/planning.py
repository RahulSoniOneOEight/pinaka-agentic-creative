from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..errors import (
    IntegrityError,
    NotFoundError,
)
from ..models import (
    WorkableBriefCreate,
)
from ..utils import canonical_json, new_id, sha256_json, utc_now


class PlanningStore:
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

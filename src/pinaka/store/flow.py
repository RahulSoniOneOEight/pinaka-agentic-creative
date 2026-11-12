from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..contracts.flow import FlowStepCreate, MotionLevelCreate, SourcingDecisionCreate
from ..errors import NotFoundError
from ..utils import canonical_json, new_id, utc_now


class FlowStore:
    """Append-only sourcing decisions and G0-G7 step records."""

    @staticmethod
    def _decision_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "slot_id": row["slot_id"],
            "decision": row["decision"],
            "reason": row["reason"],
            "providers": json.loads(row["providers_json"]),
            "candidates_considered": row["candidates_considered"],
            "rejection_reason": row["rejection_reason"],
            "evidence": json.loads(row["evidence_json"]),
            "approved_by": row["approved_by"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _step_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "step": row["step"],
            "status": row["status"],
            "reason": row["reason"],
            "actor": row["actor"],
            "tool": row["tool"],
            "tool_version": row["tool_version"],
            "evidence": json.loads(row["evidence_json"]),
            "approval_id": row["approval_id"],
            "approved_by": row["approved_by"],
            "next_action": row["next_action"],
            "trade_off": row["trade_off"],
            "created_at": row["created_at"],
        }

    def record_sourcing_decision(
        self, project_id: str, request: SourcingDecisionCreate
    ) -> dict[str, Any]:
        decision_id = new_id("src")
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            slot = connection.execute(
                "SELECT id FROM slots WHERE id = ? AND project_id = ?",
                (request.slot_id, project_id),
            ).fetchone()
            if not slot:
                raise NotFoundError(f"Slot not found in project: {request.slot_id}")
            connection.execute(
                """
                INSERT INTO sourcing_decisions(
                    id, project_id, slot_id, decision, reason, providers_json,
                    candidates_considered, rejection_reason, evidence_json,
                    approved_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    project_id,
                    request.slot_id,
                    request.decision,
                    request.reason,
                    canonical_json(request.providers),
                    request.candidates_considered,
                    request.rejection_reason,
                    canonical_json(request.evidence),
                    request.approved_by,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="sourcing_decision",
                entity_id=decision_id,
                event_type="sourcing.decision_recorded",
                payload={
                    "slot_id": request.slot_id,
                    "decision": request.decision,
                    "providers": list(request.providers),
                    "candidates_considered": request.candidates_considered,
                },
            )
            return {
                "id": decision_id,
                "project_id": project_id,
                **request.model_dump(mode="json"),
                "created_at": now,
            }

    def current_sourcing_decision(
        self, project_id: str, slot_id: str
    ) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT * FROM sourcing_decisions
                WHERE project_id = ? AND slot_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (project_id, slot_id),
            ).fetchone()
            return self._decision_from_row(row) if row else None

    def list_sourcing_decisions(
        self, project_id: str, slot_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            if slot_id is None:
                rows = connection.execute(
                    """
                    SELECT * FROM sourcing_decisions
                    WHERE project_id = ?
                    ORDER BY created_at, rowid
                    """,
                    (project_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM sourcing_decisions
                    WHERE project_id = ? AND slot_id = ?
                    ORDER BY created_at, rowid
                    """,
                    (project_id, slot_id),
                ).fetchall()
            return [self._decision_from_row(row) for row in rows]

    def record_flow_step(
        self, project_id: str, request: FlowStepCreate
    ) -> dict[str, Any]:
        step_id = new_id("flw")
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            connection.execute(
                """
                INSERT INTO flow_steps(
                    id, project_id, step, status, reason, actor, tool,
                    tool_version, evidence_json, approval_id, approved_by,
                    next_action, trade_off, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step_id,
                    project_id,
                    request.step,
                    request.status,
                    request.reason,
                    request.actor,
                    request.tool,
                    request.tool_version,
                    canonical_json(request.evidence),
                    request.approval_id,
                    request.approved_by,
                    request.next_action,
                    request.trade_off,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="flow_step",
                entity_id=step_id,
                event_type="flow.step_recorded",
                payload={
                    "step": request.step,
                    "status": request.status,
                    "reason": request.reason,
                },
            )
            return {
                "id": step_id,
                "project_id": project_id,
                **request.model_dump(mode="json"),
                "created_at": now,
            }

    def list_flow_steps(
        self, project_id: str, step: str | None = None
    ) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            if step is None:
                rows = connection.execute(
                    """
                    SELECT * FROM flow_steps
                    WHERE project_id = ?
                    ORDER BY created_at, rowid
                    """,
                    (project_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM flow_steps
                    WHERE project_id = ? AND step = ?
                    ORDER BY created_at, rowid
                    """,
                    (project_id, step),
                ).fetchall()
            return [self._step_from_row(row) for row in rows]

    def record_motion_level(
        self, project_id: str, request: MotionLevelCreate
    ) -> dict[str, Any]:
        level_id = new_id("mot")
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            shot = connection.execute(
                "SELECT id FROM shots WHERE id = ? AND project_id = ?",
                (request.shot_id, project_id),
            ).fetchone()
            if not shot:
                raise NotFoundError(f"Shot not found in project: {request.shot_id}")
            connection.execute(
                """
                INSERT INTO motion_levels(
                    id, project_id, shot_id, level, source, evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    level_id,
                    project_id,
                    request.shot_id,
                    request.level,
                    request.source,
                    canonical_json(request.evidence),
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="motion_level",
                entity_id=level_id,
                event_type="motion.level_recorded",
                payload={
                    "shot_id": request.shot_id,
                    "level": request.level,
                    "source": request.source,
                },
            )
            return {
                "id": level_id,
                "project_id": project_id,
                **request.model_dump(mode="json"),
                "created_at": now,
            }

    def list_motion_levels(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                """
                SELECT * FROM motion_levels
                WHERE project_id = ?
                ORDER BY created_at DESC, rowid DESC
                """,
                (project_id,),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "project_id": row["project_id"],
                    "shot_id": row["shot_id"],
                    "level": row["level"],
                    "source": row["source"],
                    "evidence": json.loads(row["evidence_json"]),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

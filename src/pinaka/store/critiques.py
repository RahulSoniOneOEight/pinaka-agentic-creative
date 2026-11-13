from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..contracts.critique import StageCritiqueCreate, StageCritiqueRecord
from ..errors import NotFoundError
from ..utils import canonical_json, new_id, utc_now


class CritiquesStore:
    @staticmethod
    def _critique_from_row(row: sqlite3.Row) -> StageCritiqueRecord:
        return StageCritiqueRecord(
            id=row["id"],
            project_id=row["project_id"],
            stage=row["stage"],
            subject_type=row["subject_type"],
            subject_id=row["subject_id"],
            subject_hash=row["subject_hash"],
            reviewer=row["reviewer"],
            reviewer_kind=row["reviewer_kind"],
            verdict=row["verdict"],
            findings=tuple(json.loads(row["findings_json"])),
            notes=row["notes"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _action_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "critique_id": row["critique_id"],
            "project_id": row["project_id"],
            "finding_id": row["finding_id"],
            "severity": row["severity"],
            "action": row["action"],
            "owner": row["owner"],
            "status": row["status"],
            "evidence": json.loads(row["evidence_json"]),
            "created_at": row["created_at"],
            "closed_at": row["closed_at"],
            "closed_by": row["closed_by"],
        }

    def record_stage_critique(
        self, project_id: str, request: StageCritiqueCreate
    ) -> dict[str, Any]:
        critique_id = new_id("crit")
        now = utc_now()
        findings: list[dict[str, Any]] = []
        for finding in request.findings:
            item = finding.model_dump(mode="json")
            if not item.get("finding_id"):
                item["finding_id"] = new_id("cf")
            findings.append(item)
        actions = []
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            connection.execute(
                """
                INSERT INTO stage_critiques(
                    id, project_id, stage, subject_type, subject_id,
                    subject_hash, reviewer, reviewer_kind, verdict,
                    findings_json, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    critique_id,
                    project_id,
                    request.stage,
                    request.subject_type,
                    request.subject_id,
                    request.subject_hash,
                    request.reviewer,
                    request.reviewer_kind,
                    request.verdict,
                    canonical_json(findings),
                    request.notes,
                    now,
                ),
            )
            for finding in findings:
                if finding["status"] != "open" or finding["severity"] not in {
                    "blocker",
                    "high",
                }:
                    continue
                action_id = new_id("cact")
                connection.execute(
                    """
                    INSERT INTO corrective_actions(
                        id, critique_id, project_id, finding_id, severity,
                        action, owner, status, evidence_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
                    """,
                    (
                        action_id,
                        critique_id,
                        project_id,
                        finding["finding_id"],
                        finding["severity"],
                        finding["corrective_action"],
                        request.reviewer,
                        canonical_json(finding.get("evidence", ())),
                        now,
                    ),
                )
                actions.append(
                    {
                        "id": action_id,
                        "critique_id": critique_id,
                        "project_id": project_id,
                        "finding_id": finding["finding_id"],
                        "severity": finding["severity"],
                        "action": finding["corrective_action"],
                        "owner": request.reviewer,
                        "status": "open",
                        "evidence": list(finding.get("evidence", ())),
                        "created_at": now,
                    }
                )
            self._event(
                connection,
                project_id=project_id,
                entity_type="stage_critique",
                entity_id=critique_id,
                event_type="critique.recorded",
                payload={
                    "stage": request.stage,
                    "subject_id": request.subject_id,
                    "verdict": request.verdict,
                    "findings": len(findings),
                    "actions": len(actions),
                },
            )
            row = connection.execute(
                "SELECT * FROM stage_critiques WHERE id = ?", (critique_id,)
            ).fetchone()
            return {
                "critique": self._critique_from_row(row).model_dump(mode="json"),
                "actions": actions,
            }

    def get_stage_critique(self, critique_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM stage_critiques WHERE id = ?", (critique_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Stage critique not found: {critique_id}")
            return self._critique_from_row(row).model_dump(mode="json")

    def list_stage_critiques(
        self, project_id: str, stage: str | None = None
    ) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            if stage:
                rows = connection.execute(
                    """
                    SELECT * FROM stage_critiques
                    WHERE project_id = ? AND stage = ?
                    ORDER BY created_at, rowid
                    """,
                    (project_id, stage),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM stage_critiques WHERE project_id = ? ORDER BY created_at, rowid",
                    (project_id,),
                ).fetchall()
            return [
                self._critique_from_row(row).model_dump(mode="json") for row in rows
            ]

    def latest_stage_critique(
        self,
        project_id: str,
        stage: str,
        *,
        subject_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            if subject_id:
                row = connection.execute(
                    """
                    SELECT * FROM stage_critiques
                    WHERE project_id = ? AND stage = ? AND subject_id = ?
                    ORDER BY created_at DESC, rowid DESC LIMIT 1
                    """,
                    (project_id, stage, subject_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM stage_critiques
                    WHERE project_id = ? AND stage = ?
                    ORDER BY created_at DESC, rowid DESC LIMIT 1
                    """,
                    (project_id, stage),
                ).fetchone()
            return self._critique_from_row(row).model_dump(mode="json") if row else None

    def list_corrective_actions(
        self,
        project_id: str,
        *,
        critique_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["project_id = ?"]
        values: list[Any] = [project_id]
        if critique_id:
            clauses.append("critique_id = ?")
            values.append(critique_id)
        if status:
            clauses.append("status = ?")
            values.append(status)
        query = (
            "SELECT * FROM corrective_actions WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at, rowid"
        )
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(query, tuple(values)).fetchall()
            return [self._action_from_row(row) for row in rows]

    def close_corrective_action(
        self,
        action_id: str,
        *,
        closed_by: str,
        evidence: tuple[str, ...],
        result: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM corrective_actions WHERE id = ?", (action_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Corrective action not found: {action_id}")
            current = json.loads(row["evidence_json"])
            connection.execute(
                """
                UPDATE corrective_actions
                SET status = 'closed', evidence_json = ?, closed_at = ?, closed_by = ?
                WHERE id = ?
                """,
                (
                    canonical_json([*current, *evidence, f"result:{result}"]),
                    now,
                    closed_by,
                    action_id,
                ),
            )
            self._event(
                connection,
                project_id=row["project_id"],
                entity_type="corrective_action",
                entity_id=action_id,
                event_type="corrective_action.closed",
                payload={
                    "critique_id": row["critique_id"],
                    "finding_id": row["finding_id"],
                    "closed_by": closed_by,
                },
            )
            updated = connection.execute(
                "SELECT * FROM corrective_actions WHERE id = ?", (action_id,)
            ).fetchone()
            return self._action_from_row(updated)

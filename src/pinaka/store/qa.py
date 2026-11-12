from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..enums import (
    QAStatus,
)
from ..errors import (
    IntegrityError,
    NotFoundError,
)
from ..utils import canonical_json, new_id, utc_now


class QaStore:
    @staticmethod
    def _qa_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "artifact_id": row["artifact_id"],
            "check_name": row["check_name"],
            "status": row["status"],
            "findings": json.loads(row["findings_json"]),
            "evidence_hash": row["evidence_hash"],
            "created_at": row["created_at"],
        }

    def record_qa_result(
        self,
        *,
        project_id: str,
        artifact_id: str,
        check_name: str,
        status: str,
        findings: dict[str, Any],
        evidence_hash: str,
    ) -> dict[str, Any]:
        if status not in {item.value for item in QAStatus}:
            raise IntegrityError(f"Unsupported QA status: {status}")
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            artifact = connection.execute(
                "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                (artifact_id, project_id),
            ).fetchone()
            if not artifact:
                raise NotFoundError(
                    f"QA subject artifact not found in project: {artifact_id}"
                )
            existing = connection.execute(
                """
                SELECT * FROM qa_results
                WHERE project_id = ? AND artifact_id = ? AND check_name = ?
                  AND evidence_hash = ?
                """,
                (project_id, artifact_id, check_name, evidence_hash),
            ).fetchone()
            if existing:
                return self._qa_dict(existing)
            qa_id = new_id("qa")
            connection.execute(
                """
                INSERT INTO qa_results(
                    id, project_id, artifact_id, check_name, status,
                    findings_json, evidence_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    qa_id,
                    project_id,
                    artifact_id,
                    check_name,
                    status,
                    canonical_json(findings),
                    evidence_hash,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="qa_result",
                entity_id=qa_id,
                event_type="qa.recorded",
                payload={
                    "artifact_id": artifact_id,
                    "check_name": check_name,
                    "status": status,
                    "evidence_hash": evidence_hash,
                },
            )
            row = connection.execute(
                "SELECT * FROM qa_results WHERE id = ?", (qa_id,)
            ).fetchone()
            return self._qa_dict(row)

    def list_qa_results(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM qa_results WHERE project_id = ? ORDER BY created_at, check_name",
                (project_id,),
            ).fetchall()
            return [self._qa_dict(row) for row in rows]

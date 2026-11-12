from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..errors import (
    ConflictError,
    NotFoundError,
)
from ..utils import canonical_json, new_id, utc_now


class AssetsStore:
    def register_treatment_decision(
        self,
        project_id: str,
        decision: dict[str, Any],
        *,
        decision_hash: str,
        artifact_id: str | None,
        decided_by: str,
        created_at: str,
    ) -> dict[str, Any]:
        decision_id = new_id("trt")
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            slot = connection.execute(
                "SELECT id FROM slots WHERE id = ? AND project_id = ?",
                (decision["slot_id"], project_id),
            ).fetchone()
            if not slot:
                raise NotFoundError(f"Slot not found in project: {decision['slot_id']}")
            if artifact_id:
                artifact = connection.execute(
                    "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                    (artifact_id, project_id),
                ).fetchone()
                if not artifact:
                    raise NotFoundError(f"Artifact not found in project: {artifact_id}")
            connection.execute(
                """
                INSERT INTO treatment_decisions(
                    id, project_id, slot_id, artifact_id, verdict, decided_by,
                    criteria_json, rights_json, reasons_json, decision_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    project_id,
                    decision["slot_id"],
                    artifact_id,
                    decision["verdict"],
                    decided_by,
                    canonical_json(decision["criteria"]),
                    canonical_json(decision["rights"])
                    if decision.get("rights")
                    else None,
                    canonical_json(decision["reasons"]),
                    decision_hash,
                    created_at,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="treatment_decision",
                entity_id=decision_id,
                event_type="treatment.decided",
                payload={
                    "slot_id": decision["slot_id"],
                    "artifact_id": artifact_id,
                    "verdict": decision["verdict"],
                    "decision_hash": decision_hash,
                },
            )
            return {
                "id": decision_id,
                "project_id": project_id,
                "slot_id": decision["slot_id"],
                "artifact_id": artifact_id,
                "verdict": decision["verdict"],
                "decided_by": decided_by,
                "criteria": decision["criteria"],
                "rights": decision.get("rights"),
                "reasons": decision["reasons"],
                "decision_hash": decision_hash,
                "created_at": created_at,
            }

    def list_treatment_decisions(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM treatment_decisions WHERE project_id = ? "
                "ORDER BY created_at",
                (project_id,),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "project_id": row["project_id"],
                    "slot_id": row["slot_id"],
                    "artifact_id": row["artifact_id"],
                    "verdict": row["verdict"],
                    "decided_by": row["decided_by"],
                    "criteria": json.loads(row["criteria_json"]),
                    "rights": (
                        json.loads(row["rights_json"]) if row["rights_json"] else None
                    ),
                    "reasons": json.loads(row["reasons_json"]),
                    "decision_hash": row["decision_hash"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def latest_treatment_for_slot(self, slot_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM treatment_decisions WHERE slot_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (slot_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "slot_id": row["slot_id"],
                "artifact_id": row["artifact_id"],
                "verdict": row["verdict"],
                "decided_by": row["decided_by"],
                "criteria": json.loads(row["criteria_json"]),
                "rights": (
                    json.loads(row["rights_json"]) if row["rights_json"] else None
                ),
                "reasons": json.loads(row["reasons_json"]),
                "decision_hash": row["decision_hash"],
                "created_at": row["created_at"],
            }

    @staticmethod
    def _slot_asset_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "slot_id": row["slot_id"],
            "artifact_id": row["artifact_id"],
            "role": row["role"],
            "treatment_decision_id": row["treatment_decision_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def upsert_slot_asset(
        self,
        project_id: str,
        slot_id: str,
        artifact_id: str,
        role: str,
        *,
        treatment_decision_id: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            slot = connection.execute(
                "SELECT id FROM slots WHERE id = ? AND project_id = ?",
                (slot_id, project_id),
            ).fetchone()
            if not slot:
                raise NotFoundError(f"Slot not found in project: {slot_id}")
            artifact = connection.execute(
                "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                (artifact_id, project_id),
            ).fetchone()
            if not artifact:
                raise NotFoundError(f"Artifact not found in project: {artifact_id}")
            existing = connection.execute(
                "SELECT * FROM slot_assets WHERE slot_id = ? AND role = ?",
                (slot_id, role),
            ).fetchone()
            if existing and existing["artifact_id"] == artifact_id:
                return self._slot_asset_dict(existing)
            if existing:
                connection.execute(
                    "UPDATE slot_assets SET artifact_id = ?, "
                    "treatment_decision_id = ?, updated_at = ? WHERE id = ?",
                    (artifact_id, treatment_decision_id, now, existing["id"]),
                )
                self._event(
                    connection,
                    project_id=project_id,
                    entity_type="slot_asset",
                    entity_id=existing["id"],
                    event_type="slot_asset.replaced",
                    payload={
                        "slot_id": slot_id,
                        "role": role,
                        "previous_artifact_id": existing["artifact_id"],
                        "artifact_id": artifact_id,
                    },
                )
                row = connection.execute(
                    "SELECT * FROM slot_assets WHERE id = ?", (existing["id"],)
                ).fetchone()
                return self._slot_asset_dict(row)
            slot_asset_id = new_id("sla")
            connection.execute(
                """
                INSERT INTO slot_assets(
                    id, project_id, slot_id, artifact_id, role,
                    treatment_decision_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slot_asset_id,
                    project_id,
                    slot_id,
                    artifact_id,
                    role,
                    treatment_decision_id,
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="slot_asset",
                entity_id=slot_asset_id,
                event_type="slot_asset.assigned",
                payload={
                    "slot_id": slot_id,
                    "role": role,
                    "artifact_id": artifact_id,
                },
            )
            row = connection.execute(
                "SELECT * FROM slot_assets WHERE id = ?", (slot_asset_id,)
            ).fetchone()
            return self._slot_asset_dict(row)

    def list_slot_assets(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM slot_assets WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
            return [self._slot_asset_dict(row) for row in rows]

    def slot_asset_by_role(self, slot_id: str, role: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM slot_assets WHERE slot_id = ? AND role = ?",
                (slot_id, role),
            ).fetchone()
            return self._slot_asset_dict(row) if row else None

    def swap_slot_asset_roles(
        self, project_id: str, slot_id: str, *, reason: str = ""
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM slot_assets WHERE slot_id = ? AND role IN "
                "('primary', 'backup')",
                (slot_id,),
            ).fetchall()
            roles = {row["role"]: row for row in rows}
            if "primary" not in roles or "backup" not in roles:
                raise ConflictError(
                    "Both a primary and a backup asset are required to promote"
                )
            # UNIQUE(slot_id, role) forbids a direct swap, so park the current
            # primary under a temporary role for the duration of the transaction.
            connection.execute(
                "UPDATE slot_assets SET role = 'swapping', updated_at = ? WHERE id = ?",
                (now, roles["primary"]["id"]),
            )
            connection.execute(
                "UPDATE slot_assets SET role = 'primary', updated_at = ? WHERE id = ?",
                (now, roles["backup"]["id"]),
            )
            connection.execute(
                "UPDATE slot_assets SET role = 'backup', updated_at = ? WHERE id = ?",
                (now, roles["primary"]["id"]),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="slot_asset",
                entity_id=slot_id,
                event_type="slot_asset.promoted",
                payload={
                    "slot_id": slot_id,
                    "new_primary_artifact_id": roles["backup"]["artifact_id"],
                    "previous_primary_artifact_id": roles["primary"]["artifact_id"],
                    "reason": reason,
                },
            )
        return {
            "slot_id": slot_id,
            "primary_artifact_id": roles["backup"]["artifact_id"],
            "backup_artifact_id": roles["primary"]["artifact_id"],
        }

    def get_asset_use_context(self, project_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            row = connection.execute(
                "SELECT * FROM asset_use_contexts WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "project_id": row["project_id"],
                "use_class": row["use_class"],
                "monetized": bool(row["monetized"]),
                "advertising": bool(row["advertising"]),
                "modification_planned": bool(row["modification_planned"]),
                "attribution_can_be_delivered": bool(
                    row["attribution_can_be_delivered"]
                ),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def set_asset_use_context(
        self, project_id: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            connection.execute(
                """
                INSERT INTO asset_use_contexts(
                    project_id, use_class, monetized, advertising,
                    modification_planned, attribution_can_be_delivered,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    use_class = excluded.use_class,
                    monetized = excluded.monetized,
                    advertising = excluded.advertising,
                    modification_planned = excluded.modification_planned,
                    attribution_can_be_delivered =
                        excluded.attribution_can_be_delivered,
                    updated_at = excluded.updated_at
                """,
                (
                    project_id,
                    context["use_class"],
                    int(bool(context["monetized"])),
                    int(bool(context["advertising"])),
                    int(bool(context["modification_planned"])),
                    int(bool(context["attribution_can_be_delivered"])),
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="asset_use_context",
                entity_id=project_id,
                event_type="asset_use_context.set",
                payload=context,
            )
        return {
            "project_id": project_id,
            **context,
            "updated_at": now,
        }

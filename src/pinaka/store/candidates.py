from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..contracts.flow import SourcingCandidateCreate
from ..errors import ConflictError, NotFoundError
from ..utils import canonical_json, new_id, utc_now


class CandidatesStore:
    """The per-slot candidate ledger behind the two-pick."""

    @staticmethod
    def _candidate_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "slot_id": row["slot_id"],
            "provider": row["provider"],
            "provider_asset_id": row["provider_asset_id"],
            "media_type": row["media_type"],
            "source_url": row["source_url"],
            "creator": row["creator"],
            "licence": row["licence"],
            "licence_url": row["licence_url"],
            "eligibility": row["eligibility"],
            "eligibility_reason": row["eligibility_reason"],
            "treatment": row["treatment"],
            "score": row["score"],
            "style_fit": row["style_fit"],
            "style_fit_reason": row["style_fit_reason"],
            "selected_role": row["selected_role"],
            "evidence": json.loads(row["evidence_json"]),
            "metadata": json.loads(row["metadata_json"]),
            "created_at": row["created_at"],
        }

    def record_sourcing_candidate(
        self,
        project_id: str,
        request: SourcingCandidateCreate,
        *,
        score: int,
        style_fit: int = 0,
        style_fit_reason: str | None = None,
    ) -> dict[str, Any]:
        candidate_id = new_id("cand")
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            slot = connection.execute(
                "SELECT id FROM slots WHERE id = ? AND project_id = ?",
                (request.slot_id, project_id),
            ).fetchone()
            if not slot:
                raise NotFoundError(f"Slot not found in project: {request.slot_id}")
            try:
                connection.execute(
                    """
                    INSERT INTO sourcing_candidates(
                        id, project_id, slot_id, provider, provider_asset_id,
                        media_type, source_url, creator, licence, licence_url,
                        eligibility, eligibility_reason, treatment, score,
                        style_fit, style_fit_reason,
                        selected_role, evidence_json, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate_id,
                        project_id,
                        request.slot_id,
                        request.provider,
                        request.provider_asset_id,
                        request.media_type,
                        request.source_url,
                        request.creator,
                        request.licence,
                        request.licence_url,
                        request.eligibility,
                        request.eligibility_reason,
                        request.treatment,
                        score,
                        style_fit,
                        style_fit_reason,
                        None,
                        canonical_json(request.evidence),
                        canonical_json(request.metadata),
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError(
                    "Candidate already recorded for this slot/provider/asset"
                ) from error
            self._event(
                connection,
                project_id=project_id,
                entity_type="sourcing_candidate",
                entity_id=candidate_id,
                event_type="sourcing.candidate_recorded",
                payload={
                    "slot_id": request.slot_id,
                    "provider": request.provider,
                    "provider_asset_id": request.provider_asset_id,
                    "eligibility": request.eligibility,
                    "treatment": request.treatment,
                    "score": score,
                },
            )
            return {
                "id": candidate_id,
                "project_id": project_id,
                **request.model_dump(mode="json"),
                "score": score,
                "style_fit": style_fit,
                "style_fit_reason": style_fit_reason,
                "selected_role": None,
                "created_at": now,
            }

    def list_sourcing_candidates(
        self, project_id: str, slot_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            if slot_id is None:
                rows = connection.execute(
                    """
                    SELECT * FROM sourcing_candidates
                    WHERE project_id = ?
                    ORDER BY slot_id, score DESC, created_at
                    """,
                    (project_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM sourcing_candidates
                    WHERE project_id = ? AND slot_id = ?
                    ORDER BY score DESC, created_at
                    """,
                    (project_id, slot_id),
                ).fetchall()
            return [self._candidate_from_row(row) for row in rows]

    def get_sourcing_candidate(self, candidate_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM sourcing_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Candidate not found: {candidate_id}")
            return self._candidate_from_row(row)

    def set_slot_picks(
        self,
        project_id: str,
        slot_id: str,
        *,
        primary_candidate_id: str,
        backup_candidate_id: str,
        decided_by: str,
        reason: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                """
                SELECT * FROM sourcing_candidates
                WHERE project_id = ? AND slot_id = ?
                """,
                (project_id, slot_id),
            ).fetchall()
            by_id = {row["id"]: row for row in rows}
            for candidate_id in (primary_candidate_id, backup_candidate_id):
                candidate = by_id.get(candidate_id)
                if not candidate:
                    raise NotFoundError(
                        f"Candidate not found in this slot: {candidate_id}"
                    )
                if candidate["eligibility"] != "eligible":
                    raise ConflictError(
                        f"Candidate {candidate_id} is ineligible and cannot be picked"
                    )
            primary = by_id[primary_candidate_id]
            backup = by_id[backup_candidate_id]
            if primary_candidate_id == backup_candidate_id:
                raise ConflictError("Primary and backup must be different candidates")
            if (primary["provider"], primary["provider_asset_id"]) == (
                backup["provider"],
                backup["provider_asset_id"],
            ):
                raise ConflictError("Backup must come from a different provider asset")
            connection.execute(
                """
                UPDATE sourcing_candidates
                SET selected_role = NULL
                WHERE project_id = ? AND slot_id = ?
                """,
                (project_id, slot_id),
            )
            connection.execute(
                "UPDATE sourcing_candidates SET selected_role = 'primary' WHERE id = ?",
                (primary_candidate_id,),
            )
            connection.execute(
                "UPDATE sourcing_candidates SET selected_role = 'backup' WHERE id = ?",
                (backup_candidate_id,),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="slot",
                entity_id=slot_id,
                event_type="sourcing.two_pick_recorded",
                payload={
                    "primary_candidate_id": primary_candidate_id,
                    "backup_candidate_id": backup_candidate_id,
                    "decided_by": decided_by,
                    "reason": reason,
                },
            )
            return {
                "slot_id": slot_id,
                "primary_candidate_id": primary_candidate_id,
                "backup_candidate_id": backup_candidate_id,
                "decided_by": decided_by,
                "reason": reason,
                "decided_at": now,
            }

    def slot_picks(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                """
                SELECT * FROM sourcing_candidates
                WHERE project_id = ? AND selected_role IS NOT NULL
                ORDER BY slot_id, selected_role
                """,
                (project_id,),
            ).fetchall()
            picks: dict[str, dict[str, Any]] = {}
            for row in rows:
                candidate = self._candidate_from_row(row)
                entry = picks.setdefault(row["slot_id"], {"slot_id": row["slot_id"]})
                entry[row["selected_role"]] = candidate
            return list(picks.values())

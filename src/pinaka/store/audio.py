from __future__ import annotations

from typing import Any

from ..errors import (
    NotFoundError,
)
from ..utils import canonical_json, new_id, utc_now


class AudioStore:
    def upsert_audio_bed(
        self,
        project_id: str,
        artifact_id: str,
        role: str,
        *,
        verdict: str,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            artifact = connection.execute(
                "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                (artifact_id, project_id),
            ).fetchone()
            if not artifact:
                raise NotFoundError(f"Artifact not found in project: {artifact_id}")
            existing = connection.execute(
                "SELECT * FROM audio_beds WHERE project_id = ? AND role = ?",
                (project_id, role),
            ).fetchone()
            if existing and existing["artifact_id"] == artifact_id:
                return dict(existing)
            if existing:
                connection.execute(
                    "UPDATE audio_beds SET artifact_id = ?, verdict = ?, "
                    "decision_json = ?, updated_at = ? WHERE id = ?",
                    (
                        artifact_id,
                        verdict,
                        canonical_json(decision),
                        now,
                        existing["id"],
                    ),
                )
                self._event(
                    connection,
                    project_id=project_id,
                    entity_type="audio_bed",
                    entity_id=existing["id"],
                    event_type="audio_bed.replaced",
                    payload={
                        "role": role,
                        "previous_artifact_id": existing["artifact_id"],
                        "artifact_id": artifact_id,
                    },
                )
                row = connection.execute(
                    "SELECT * FROM audio_beds WHERE id = ?", (existing["id"],)
                ).fetchone()
                return dict(row)
            bed_id = new_id("abed")
            connection.execute(
                "INSERT INTO audio_beds(id, project_id, artifact_id, role, verdict, "
                "decision_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    bed_id,
                    project_id,
                    artifact_id,
                    role,
                    verdict,
                    canonical_json(decision),
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="audio_bed",
                entity_id=bed_id,
                event_type="audio_bed.assigned",
                payload={"role": role, "artifact_id": artifact_id, "verdict": verdict},
            )
            row = connection.execute(
                "SELECT * FROM audio_beds WHERE id = ?", (bed_id,)
            ).fetchone()
            return dict(row)

    def list_audio_beds(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM audio_beds WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def upsert_audio_cue(
        self,
        project_id: str,
        *,
        cue_key: str,
        artifact_id: str,
        role: str,
        start_ms: int,
        duration_ms: int,
        gain_db: str,
        loop: bool,
        shot_id: str | None,
        verdict: str,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            artifact = connection.execute(
                "SELECT id FROM artifacts WHERE id = ? AND project_id = ?",
                (artifact_id, project_id),
            ).fetchone()
            if not artifact:
                raise NotFoundError(f"Artifact not found in project: {artifact_id}")
            if shot_id is not None:
                shot = connection.execute(
                    "SELECT id FROM shots WHERE id = ? AND project_id = ?",
                    (shot_id, project_id),
                ).fetchone()
                if not shot:
                    raise NotFoundError(f"Shot not found in project: {shot_id}")
            existing = connection.execute(
                "SELECT * FROM audio_cues WHERE project_id = ? AND cue_key = ?",
                (project_id, cue_key),
            ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE audio_cues SET artifact_id = ?, shot_id = ?, role = ?, "
                    "start_ms = ?, duration_ms = ?, gain_db = ?, loop = ?, verdict = ?, "
                    "decision_json = ?, updated_at = ? WHERE id = ?",
                    (
                        artifact_id,
                        shot_id,
                        role,
                        start_ms,
                        duration_ms,
                        gain_db,
                        int(loop),
                        verdict,
                        canonical_json(decision),
                        now,
                        existing["id"],
                    ),
                )
                self._event(
                    connection,
                    project_id=project_id,
                    entity_type="audio_cue",
                    entity_id=existing["id"],
                    event_type="audio_cue.replaced",
                    payload={
                        "cue_key": cue_key,
                        "previous_artifact_id": existing["artifact_id"],
                        "artifact_id": artifact_id,
                    },
                )
                row = connection.execute(
                    "SELECT * FROM audio_cues WHERE id = ?", (existing["id"],)
                ).fetchone()
                return dict(row)
            cue_id = new_id("acue")
            connection.execute(
                "INSERT INTO audio_cues(id, project_id, cue_key, artifact_id, shot_id, "
                "role, start_ms, duration_ms, gain_db, loop, verdict, decision_json, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cue_id,
                    project_id,
                    cue_key,
                    artifact_id,
                    shot_id,
                    role,
                    start_ms,
                    duration_ms,
                    gain_db,
                    int(loop),
                    verdict,
                    canonical_json(decision),
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="audio_cue",
                entity_id=cue_id,
                event_type="audio_cue.assigned",
                payload={
                    "cue_key": cue_key,
                    "role": role,
                    "artifact_id": artifact_id,
                    "start_ms": start_ms,
                },
            )
            row = connection.execute(
                "SELECT * FROM audio_cues WHERE id = ?", (cue_id,)
            ).fetchone()
            return dict(row)

    def list_audio_cues(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM audio_cues WHERE project_id = ? ORDER BY start_ms, cue_key",
                (project_id,),
            ).fetchall()
            return [dict(row) for row in rows]

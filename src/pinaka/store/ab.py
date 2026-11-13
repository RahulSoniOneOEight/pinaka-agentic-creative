from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..contracts.flow import ABExperimentCreate, ABVariantBind
from ..errors import ConflictError, IntegrityError, NotFoundError
from ..utils import canonical_json, new_id, utc_now


class ExperimentsStore:
    """A/B experiment contracts and their bound variant renders."""

    @staticmethod
    def _experiment(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "axis": row["axis"],
            "hypothesis": row["hypothesis"],
            "primary_metric": row["primary_metric"],
            "traffic_split": row["traffic_split"],
            "sample_plan": row["sample_plan"],
            "status": row["status"],
            "created_at": row["created_at"],
        }

    @staticmethod
    def _variant(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "experiment_id": row["experiment_id"],
            "project_id": row["project_id"],
            "label": row["label"],
            "artifact_id": row["artifact_id"],
            "artifact_hash": row["artifact_hash"],
            "notes": row["notes"],
            "created_at": row["created_at"],
        }

    def create_ab_experiment(
        self, project_id: str, request: ABExperimentCreate
    ) -> dict[str, Any]:
        experiment_id = new_id("abx")
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            connection.execute(
                """
                INSERT INTO ab_experiments(
                    id, project_id, axis, hypothesis, primary_metric,
                    traffic_split, sample_plan, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'declared', ?)
                """,
                (
                    experiment_id,
                    project_id,
                    request.axis,
                    request.hypothesis,
                    request.primary_metric,
                    request.traffic_split,
                    request.sample_plan,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="ab_experiment",
                entity_id=experiment_id,
                event_type="ab.experiment_declared",
                payload=request.model_dump(mode="json"),
            )
            return {
                "id": experiment_id,
                "project_id": project_id,
                **request.model_dump(mode="json"),
                "status": "declared",
                "created_at": now,
            }

    def get_ab_experiment(self, experiment_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM ab_experiments WHERE id = ?", (experiment_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"A/B experiment not found: {experiment_id}")
            return self._experiment(row)

    def list_ab_experiments(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM ab_experiments WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
            return [self._experiment(row) for row in rows]

    def list_ab_variants(self, experiment_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM ab_variants WHERE experiment_id = ? ORDER BY label",
                (experiment_id,),
            ).fetchall()
            return [self._variant(row) for row in rows]

    def bind_ab_variant(
        self, project_id: str, request: ABVariantBind
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            experiment = connection.execute(
                "SELECT * FROM ab_experiments WHERE id = ? AND project_id = ?",
                (request.experiment_id, project_id),
            ).fetchone()
            if not experiment:
                raise NotFoundError(
                    f"A/B experiment not found: {request.experiment_id}"
                )
            artifact = connection.execute(
                "SELECT * FROM artifacts WHERE id = ? AND project_id = ?",
                (request.artifact_id, project_id),
            ).fetchone()
            if not artifact:
                raise NotFoundError(
                    f"Artifact not found in project: {request.artifact_id}"
                )
            existing = connection.execute(
                "SELECT * FROM ab_variants WHERE experiment_id = ? AND label = ?",
                (request.experiment_id, request.label),
            ).fetchone()
            if existing:
                raise ConflictError(
                    f"Variant {request.label} is already bound to this experiment"
                )
            other = connection.execute(
                """
                SELECT * FROM ab_variants
                WHERE experiment_id = ? AND artifact_hash = ?
                """,
                (request.experiment_id, artifact["content_hash"]),
            ).fetchone()
            if other:
                raise IntegrityError(
                    "A and B must be different renders; this artifact is already bound"
                )
            variant_id = new_id("abv")
            connection.execute(
                """
                INSERT INTO ab_variants(
                    id, experiment_id, project_id, label, artifact_id,
                    artifact_hash, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    variant_id,
                    request.experiment_id,
                    project_id,
                    request.label,
                    request.artifact_id,
                    artifact["content_hash"],
                    request.notes,
                    now,
                ),
            )
            connection.execute(
                "UPDATE ab_experiments SET status = 'running' WHERE id = ?",
                (request.experiment_id,),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="ab_experiment",
                entity_id=request.experiment_id,
                event_type="ab.variant_bound",
                payload={
                    "label": request.label,
                    "artifact_id": request.artifact_id,
                    "artifact_hash": artifact["content_hash"],
                },
            )
            return {
                "id": variant_id,
                "experiment_id": request.experiment_id,
                "project_id": project_id,
                **request.model_dump(mode="json"),
                "artifact_hash": artifact["content_hash"],
                "created_at": now,
            }

    def record_ab_delivery(
        self,
        project_id: str,
        *,
        experiment_id: str,
        output_dir: str,
        manifest_artifact_id: str,
        manifest_hash: str,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        delivery_id = new_id("abdel")
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            connection.execute(
                """
                INSERT INTO ab_deliveries(
                    id, project_id, experiment_id, output_dir,
                    manifest_artifact_id, manifest_hash, manifest_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery_id,
                    project_id,
                    experiment_id,
                    output_dir,
                    manifest_artifact_id,
                    manifest_hash,
                    canonical_json(manifest),
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="ab_delivery",
                entity_id=delivery_id,
                event_type="ab.delivery_packaged",
                payload={
                    "experiment_id": experiment_id,
                    "manifest_hash": manifest_hash,
                    "manifest_artifact_id": manifest_artifact_id,
                },
            )
            return {
                "id": delivery_id,
                "project_id": project_id,
                "experiment_id": experiment_id,
                "output_dir": output_dir,
                "manifest_artifact_id": manifest_artifact_id,
                "manifest_hash": manifest_hash,
                "manifest": manifest,
                "created_at": now,
            }

    def latest_ab_delivery(
        self, project_id: str, experiment_id: str | None = None
    ) -> dict[str, Any] | None:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            if experiment_id:
                row = connection.execute(
                    """
                    SELECT * FROM ab_deliveries
                    WHERE project_id = ? AND experiment_id = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (project_id, experiment_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM ab_deliveries
                    WHERE project_id = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (project_id,),
                ).fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "project_id": row["project_id"],
                "experiment_id": row["experiment_id"],
                "output_dir": row["output_dir"],
                "manifest_artifact_id": row["manifest_artifact_id"],
                "manifest_hash": row["manifest_hash"],
                "manifest": json.loads(row["manifest_json"]),
                "created_at": row["created_at"],
            }

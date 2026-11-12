from __future__ import annotations

import sqlite3
from typing import Any

from ..errors import (
    ConflictError,
    IntegrityError,
    NotFoundError,
)
from ..models import (
    Money,
)
from ..utils import new_id, utc_now


class CostsStore:
    @staticmethod
    def _variance_request_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "kind": row["kind"],
            "plan_id": row["plan_id"],
            "item_id": row["item_id"],
            "job_id": row["job_id"],
            "operation_id": row["operation_id"],
            "estimated": Money.from_minor(
                row["estimated_minor"], row["currency"]
            ).model_dump(mode="json"),
            "actual": Money.from_minor(row["actual_minor"], row["currency"]).model_dump(
                mode="json"
            ),
            "allowed": Money.from_minor(
                row["allowed_minor"], row["currency"]
            ).model_dump(mode="json"),
            "overage": Money.from_minor(
                row["overage_minor"], row["currency"]
            ).model_dump(mode="json"),
            "estimated_minor": row["estimated_minor"],
            "actual_minor": row["actual_minor"],
            "allowed_minor": row["allowed_minor"],
            "overage_minor": row["overage_minor"],
            "currency": row["currency"],
            "status": row["status"],
            "requested_by": row["requested_by"],
            "reason": row["reason"],
            "decided_by": row["decided_by"],
            "decision_reason": row["decision_reason"],
            "decided_at": row["decided_at"],
            "created_at": row["created_at"],
        }

    def record_variance_request(
        self,
        project_id: str,
        *,
        plan_id: str | None,
        item_id: str | None,
        job_id: str,
        estimated_minor: int,
        actual_minor: int,
        allowed_minor: int,
        currency: str,
        requested_by: str | None,
        reason: str | None,
        kind: str = "plan_item",
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            existing = connection.execute(
                """
                SELECT * FROM budget_variance_requests
                WHERE job_id = ? AND actual_minor = ? AND currency = ?
                """,
                (job_id, actual_minor, currency),
            ).fetchone()
            if existing:
                return self._variance_request_dict(existing)
            request_id = new_id("variance")
            connection.execute(
                """
                INSERT INTO budget_variance_requests(
                    id, project_id, kind, plan_id, item_id, job_id, operation_id,
                    estimated_minor, actual_minor, allowed_minor, overage_minor,
                    currency, status, requested_by, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    project_id,
                    kind,
                    plan_id,
                    item_id,
                    job_id,
                    operation_id,
                    estimated_minor,
                    actual_minor,
                    allowed_minor,
                    actual_minor - allowed_minor,
                    currency,
                    "pending",
                    requested_by,
                    reason,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="budget_variance_request",
                entity_id=request_id,
                event_type="budget_variance.requested",
                payload={
                    "job_id": job_id,
                    "plan_id": plan_id,
                    "estimated_minor": estimated_minor,
                    "actual_minor": actual_minor,
                    "allowed_minor": allowed_minor,
                    "currency": currency,
                },
            )
            row = connection.execute(
                "SELECT * FROM budget_variance_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            return self._variance_request_dict(row)

    def get_variance_request(self, request_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM budget_variance_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            if not row:
                raise NotFoundError(f"Variance request not found: {request_id}")
            return self._variance_request_dict(row)

    @staticmethod
    def _asset_cost_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "job_id": row["job_id"],
            "plan_id": row["plan_id"],
            "item_id": row["item_id"],
            "slot_id": row["slot_id"],
            "artifact_id": row["artifact_id"],
            "provider": row["provider"],
            "operation": row["operation"],
            "currency": row["currency"],
            "estimated": Money.from_minor(
                row["estimated_minor"], row["currency"]
            ).model_dump(mode="json"),
            "actual": Money.from_minor(row["actual_minor"], row["currency"]).model_dump(
                mode="json"
            ),
            "variance": Money.from_minor(
                abs(row["variance_minor"]), row["currency"]
            ).model_dump(mode="json"),
            "variance_direction": (
                "over"
                if row["variance_minor"] > 0
                else "under"
                if row["variance_minor"] < 0
                else "even"
            ),
            "estimated_minor": row["estimated_minor"],
            "actual_minor": row["actual_minor"],
            "variance_minor": row["variance_minor"],
            "created_at": row["created_at"],
        }

    def _record_asset_cost_tx(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        artifact_id: str,
        actual_minor: int,
        now: str,
    ) -> None:
        item = connection.execute(
            "SELECT * FROM budget_plan_items WHERE job_id = ?", (job_id,)
        ).fetchone()
        if not item:
            return
        existing = connection.execute(
            "SELECT id FROM asset_costs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if existing:
            return
        job = connection.execute(
            "SELECT provider, operation, currency FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        cost_id = new_id("assetcost")
        connection.execute(
            """
            INSERT INTO asset_costs(
                id, project_id, job_id, plan_id, item_id, slot_id, artifact_id,
                provider, operation, currency, estimated_minor, actual_minor,
                variance_minor, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cost_id,
                item["project_id"],
                job_id,
                item["plan_id"],
                item["id"],
                item["slot_id"],
                artifact_id,
                job["provider"],
                job["operation"],
                job["currency"],
                item["estimated_minor"],
                actual_minor,
                actual_minor - item["estimated_minor"],
                now,
            ),
        )
        self._event(
            connection,
            project_id=item["project_id"],
            entity_type="asset_cost",
            entity_id=cost_id,
            event_type="asset_cost.recorded",
            payload={
                "job_id": job_id,
                "slot_id": item["slot_id"],
                "artifact_id": artifact_id,
                "estimated_minor": item["estimated_minor"],
                "actual_minor": actual_minor,
            },
        )

    def record_asset_cost(
        self,
        project_id: str,
        *,
        job_id: str,
        plan_id: str | None,
        item_id: str | None,
        slot_id: str | None,
        artifact_id: str | None,
        provider: str,
        operation: str,
        currency: str,
        estimated_minor: int,
        actual_minor: int,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            existing = connection.execute(
                "SELECT * FROM asset_costs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if existing:
                return self._asset_cost_dict(existing)
            cost_id = new_id("assetcost")
            connection.execute(
                """
                INSERT INTO asset_costs(
                    id, project_id, job_id, plan_id, item_id, slot_id, artifact_id,
                    provider, operation, currency, estimated_minor, actual_minor,
                    variance_minor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cost_id,
                    project_id,
                    job_id,
                    plan_id,
                    item_id,
                    slot_id,
                    artifact_id,
                    provider,
                    operation,
                    currency,
                    estimated_minor,
                    actual_minor,
                    actual_minor - estimated_minor,
                    now,
                ),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="asset_cost",
                entity_id=cost_id,
                event_type="asset_cost.recorded",
                payload={
                    "job_id": job_id,
                    "slot_id": slot_id,
                    "artifact_id": artifact_id,
                    "estimated_minor": estimated_minor,
                    "actual_minor": actual_minor,
                },
            )
            row = connection.execute(
                "SELECT * FROM asset_costs WHERE id = ?", (cost_id,)
            ).fetchone()
            return self._asset_cost_dict(row)

    def list_asset_costs(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM asset_costs WHERE project_id = ? ORDER BY created_at",
                (project_id,),
            ).fetchall()
            return [self._asset_cost_dict(row) for row in rows]

    def approved_variance_for_job(
        self, job_id: str, actual_minor: int
    ) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT * FROM budget_variance_requests
                WHERE job_id = ? AND actual_minor = ? AND status = 'approved'
                ORDER BY created_at DESC LIMIT 1
                """,
                (job_id, actual_minor),
            ).fetchone()
            return self._variance_request_dict(row) if row else None

    def pending_variance_requests(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                """
                SELECT * FROM budget_variance_requests
                WHERE project_id = ? AND status = 'pending' ORDER BY created_at
                """,
                (project_id,),
            ).fetchall()
            return [self._variance_request_dict(row) for row in rows]

    def decide_variance_request(
        self,
        request_id: str,
        *,
        decision: str,
        decided_by: str,
        decision_reason: str,
    ) -> dict[str, Any]:
        if decision not in {"approved", "rejected"}:
            raise IntegrityError(f"Unsupported variance decision: {decision}")
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM budget_variance_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            if not row:
                raise NotFoundError(f"Variance request not found: {request_id}")
            if row["status"] != "pending":
                raise ConflictError(f"Variance request is already {row['status']}")
            connection.execute(
                """
                UPDATE budget_variance_requests
                SET status = ?, decided_by = ?, decision_reason = ?, decided_at = ?
                WHERE id = ?
                """,
                (decision, decided_by, decision_reason, now, request_id),
            )
            self._event(
                connection,
                project_id=row["project_id"],
                entity_type="budget_variance_request",
                entity_id=request_id,
                event_type=f"budget_variance.{decision}",
                payload={
                    "job_id": row["job_id"],
                    "actual_minor": row["actual_minor"],
                    "allowed_minor": row["allowed_minor"],
                    "decided_by": decided_by,
                },
            )
            updated = connection.execute(
                "SELECT * FROM budget_variance_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            return self._variance_request_dict(updated)

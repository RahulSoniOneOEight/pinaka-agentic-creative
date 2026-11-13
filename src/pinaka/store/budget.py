from __future__ import annotations

import sqlite3
from typing import Any

from ..enums import (
    BudgetItemStatus,
    BudgetPlanStatus,
)
from ..errors import (
    ConflictError,
    IntegrityError,
    NotFoundError,
)
from ..models import (
    BudgetPlanCreate,
    Money,
)
from ..utils import new_id, sha256_json, utc_now


class BudgetStore:
    @staticmethod
    def _budget_item_dict(row: sqlite3.Row, currency: str) -> dict[str, Any]:
        return {
            "id": row["id"],
            "plan_id": row["plan_id"],
            "project_id": row["project_id"],
            "stable_key": row["stable_key"],
            "ordinal": row["ordinal"],
            "category": row["category"],
            "provider": row["provider"],
            "operation": row["operation"],
            "estimated_cost": Money.from_minor(
                row["estimated_minor"], currency
            ).model_dump(mode="json"),
            "estimated_minor": row["estimated_minor"],
            "priority": row["priority"],
            "shot_id": row["shot_id"],
            "slot_id": row["slot_id"],
            "variant_label": row["variant_label"],
            "ab_experiment_id": row["ab_experiment_id"],
            "notes": row["notes"],
            "status": row["status"],
            "job_id": row["job_id"],
            "actual_minor": row["actual_minor"],
            "variance_minor": row["variance_minor"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _budget_plan_dict(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        items = connection.execute(
            "SELECT * FROM budget_plan_items WHERE plan_id = ? ORDER BY ordinal",
            (row["id"],),
        ).fetchall()
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "version": row["version"],
            "creative_summary": row["creative_summary"],
            "target": Money.from_minor(row["target_minor"], "USD").model_dump(
                mode="json"
            ),
            "target_minor": row["target_minor"],
            "planned_total": Money.from_minor(
                row["planned_total_minor"], "USD"
            ).model_dump(mode="json"),
            "planned_total_minor": row["planned_total_minor"],
            "currency": row["currency"],
            "tolerance_bps": row["tolerance_bps"],
            "tolerance_floor_minor": row["tolerance_floor_minor"],
            "status": row["status"],
            "content_hash": row["content_hash"],
            "is_current": bool(row["is_current"]),
            "created_at": row["created_at"],
            "items": [self._budget_item_dict(item, row["currency"]) for item in items],
        }

    def record_budget_plan(
        self,
        project_id: str,
        request: BudgetPlanCreate,
        *,
        status: str,
        target_minor: int,
    ) -> dict[str, Any]:
        now = utc_now()
        payload = request.model_dump(mode="json")
        content_hash = sha256_json(payload)
        planned_total = request.planned_total_minor
        currency = request.currency
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            if project["currency"] != currency:
                raise IntegrityError(
                    "Creative budget plan currency must match the project budget "
                    f"currency {project['currency']}"
                )
            current = connection.execute(
                "SELECT * FROM budget_plans WHERE project_id = ? AND is_current = 1",
                (project_id,),
            ).fetchone()
            if current and current["content_hash"] == content_hash:
                return self._budget_plan_dict(connection, current)
            version = int(current["version"]) + 1 if current else 1
            if current:
                connection.execute(
                    "UPDATE budget_plans SET is_current = 0 WHERE id = ?",
                    (current["id"],),
                )
            plan_id = new_id("budgetplan")
            connection.execute(
                """
                INSERT INTO budget_plans(
                    id, project_id, version, creative_summary, target_minor,
                    planned_total_minor, currency, tolerance_bps,
                    tolerance_floor_minor, status, content_hash, is_current, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    plan_id,
                    project_id,
                    version,
                    request.creative_summary,
                    target_minor,
                    planned_total,
                    currency,
                    request.tolerance_bps,
                    Money(
                        amount=request.tolerance_floor_amount, currency=currency
                    ).minor,
                    status,
                    content_hash,
                    now,
                ),
            )
            for item in request.items:
                if item.shot_id:
                    shot = connection.execute(
                        "SELECT id FROM shots WHERE id = ? AND project_id = ?",
                        (item.shot_id, project_id),
                    ).fetchone()
                    if not shot:
                        raise NotFoundError(
                            f"Budget item shot not found in project: {item.shot_id}"
                        )
                if item.slot_id:
                    slot = connection.execute(
                        "SELECT id FROM slots WHERE id = ? AND project_id = ?",
                        (item.slot_id, project_id),
                    ).fetchone()
                    if not slot:
                        raise NotFoundError(
                            f"Budget item slot not found in project: {item.slot_id}"
                        )
                connection.execute(
                    """
                    INSERT INTO budget_plan_items(
                        id, plan_id, project_id, stable_key, ordinal, category,
                        provider, operation, estimated_minor, priority, shot_id,
                        slot_id, variant_label, ab_experiment_id, notes, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("budgetitem"),
                        plan_id,
                        project_id,
                        item.stable_key,
                        item.ordinal,
                        item.category.value,
                        item.provider,
                        item.operation,
                        item.estimated_cost.minor,
                        item.priority.value,
                        item.shot_id,
                        item.slot_id,
                        item.variant_label,
                        item.ab_experiment_id,
                        item.notes,
                        BudgetItemStatus.PLANNED.value,
                        now,
                        now,
                    ),
                )
            self._event(
                connection,
                project_id=project_id,
                entity_type="budget_plan",
                entity_id=plan_id,
                event_type="budget_plan.recorded",
                payload={
                    "version": version,
                    "status": status,
                    "content_hash": content_hash,
                    "planned_total_minor": planned_total,
                },
            )
            row = connection.execute(
                "SELECT * FROM budget_plans WHERE id = ?", (plan_id,)
            ).fetchone()
            return self._budget_plan_dict(connection, row)

    def get_budget_plan(self, plan_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM budget_plans WHERE id = ?", (plan_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Budget plan not found: {plan_id}")
            return self._budget_plan_dict(connection, row)

    def current_budget_plan(self, project_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            row = connection.execute(
                "SELECT * FROM budget_plans WHERE project_id = ? AND is_current = 1",
                (project_id,),
            ).fetchone()
            return self._budget_plan_dict(connection, row) if row else None

    def record_budget_plan_approval(
        self,
        project_id: str,
        *,
        plan_id: str,
        plan_hash: str,
        approved_total: Money,
        approved_by: str,
        reason: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            plan = connection.execute(
                "SELECT * FROM budget_plans WHERE id = ? AND project_id = ?",
                (plan_id, project_id),
            ).fetchone()
            if not plan:
                raise NotFoundError(f"Budget plan not found in project: {plan_id}")
            if plan["content_hash"] != plan_hash:
                raise IntegrityError("Budget plan content changed before approval")
            if plan["planned_total_minor"] != approved_total.minor:
                raise IntegrityError("Approval total does not match the planned total")
            existing = connection.execute(
                "SELECT * FROM budget_plan_approvals WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            if existing:
                if (
                    existing["plan_hash"] != plan_hash
                    or existing["approved_total_minor"] != approved_total.minor
                ):
                    raise ConflictError("Budget plan already has a different approval")
                return self._budget_approval_dict(existing)
            approval_id = new_id("budgetapproval")
            connection.execute(
                """
                INSERT INTO budget_plan_approvals(
                    id, project_id, plan_id, plan_hash, approved_total_minor,
                    currency, approved_by, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    project_id,
                    plan_id,
                    plan_hash,
                    approved_total.minor,
                    approved_total.currency,
                    approved_by,
                    reason,
                    now,
                ),
            )
            connection.execute(
                "UPDATE budget_plans SET status = ? WHERE id = ?",
                (BudgetPlanStatus.APPROVED.value, plan_id),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="budget_plan",
                entity_id=plan_id,
                event_type="budget_plan.approved",
                payload={
                    "approval_id": approval_id,
                    "approved_total_minor": approved_total.minor,
                    "approved_by": approved_by,
                },
            )
            row = connection.execute(
                "SELECT * FROM budget_plan_approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            return self._budget_approval_dict(row)

    @staticmethod
    def _budget_approval_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "plan_id": row["plan_id"],
            "plan_hash": row["plan_hash"],
            "approved_total": Money.from_minor(
                row["approved_total_minor"], row["currency"]
            ).model_dump(mode="json"),
            "approved_total_minor": row["approved_total_minor"],
            "approved_by": row["approved_by"],
            "reason": row["reason"],
            "created_at": row["created_at"],
        }

    def budget_plan_approval(self, plan_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM budget_plan_approvals WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            return self._budget_approval_dict(row) if row else None

    def link_budget_item_job(
        self, project_id: str, plan_id: str, item_key: str, job_id: str
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            item = connection.execute(
                """
                SELECT * FROM budget_plan_items
                WHERE plan_id = ? AND project_id = ? AND stable_key = ?
                """,
                (plan_id, project_id, item_key),
            ).fetchone()
            if not item:
                raise NotFoundError(f"Budget item not found in plan: {item_key}")
            if item["job_id"]:
                if item["job_id"] == job_id:
                    return self._budget_item_dict(item, project["currency"])
                raise ConflictError("Budget item already reserved for another job")
            job = connection.execute(
                "SELECT id FROM jobs WHERE id = ? AND project_id = ?",
                (job_id, project_id),
            ).fetchone()
            if not job:
                raise IntegrityError(f"Budget item job is not in the project: {job_id}")
            connection.execute(
                """
                UPDATE budget_plan_items
                SET status = ?, job_id = ?, updated_at = ? WHERE id = ?
                """,
                (BudgetItemStatus.RESERVED.value, job_id, now, item["id"]),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="budget_plan_item",
                entity_id=item["id"],
                event_type="budget_item.reserved",
                payload={
                    "plan_id": plan_id,
                    "stable_key": item_key,
                    "job_id": job_id,
                    "estimated_minor": item["estimated_minor"],
                },
            )
            updated = connection.execute(
                "SELECT * FROM budget_plan_items WHERE id = ?", (item["id"],)
            ).fetchone()
            return self._budget_item_dict(updated, project["currency"])

    def settle_budget_item_for_job(
        self, connection: sqlite3.Connection, job_id: str, actual_minor: int
    ) -> None:
        now = utc_now()
        item = connection.execute(
            "SELECT * FROM budget_plan_items WHERE job_id = ?", (job_id,)
        ).fetchone()
        if not item or item["status"] in {
            BudgetItemStatus.SETTLED.value,
            BudgetItemStatus.SKIPPED.value,
        }:
            return
        variance = actual_minor - item["estimated_minor"]
        connection.execute(
            """
            UPDATE budget_plan_items
            SET status = ?, actual_minor = ?, variance_minor = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                BudgetItemStatus.SETTLED.value,
                actual_minor,
                variance,
                now,
                item["id"],
            ),
        )
        self._event(
            connection,
            project_id=item["project_id"],
            entity_type="budget_plan_item",
            entity_id=item["id"],
            event_type="budget_item.settled",
            payload={
                "job_id": job_id,
                "estimated_minor": item["estimated_minor"],
                "actual_minor": actual_minor,
                "variance_minor": variance,
            },
        )

    def raise_project_budget(
        self,
        project_id: str,
        *,
        new_max: Money,
        raised_by: str,
        reason: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            if new_max.currency != project["currency"]:
                raise IntegrityError(f"Budget currency must stay {project['currency']}")
            if new_max.minor <= project["max_budget_minor"]:
                raise IntegrityError("A budget raise must increase the maximum")
            committed = project["spent_minor"] + project["reserved_minor"]
            if new_max.minor < committed:
                raise IntegrityError(
                    "The new budget cannot be below committed and reserved spend"
                )
            connection.execute(
                """
                UPDATE projects
                SET max_budget_minor = ?, version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (new_max.minor, now, project_id),
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="project",
                entity_id=project_id,
                event_type="budget.raised",
                payload={
                    "from_minor": project["max_budget_minor"],
                    "to_minor": new_max.minor,
                    "currency": new_max.currency,
                    "raised_by": raised_by,
                    "reason": reason,
                },
            )
            updated = self._require_project(connection, project_id)
            return self._project_from_row(updated).model_dump(mode="json")

    def record_overage_reconciliation(
        self,
        project_id: str,
        *,
        job_id: str,
        actual_cost: Money,
        reconciled_by: str,
        reason: str,
    ) -> None:
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            self._event(
                connection,
                project_id=project_id,
                entity_type="job",
                entity_id=job_id,
                event_type="budget.overage_reconciled",
                payload={
                    "actual_cost_minor": actual_cost.minor,
                    "currency": actual_cost.currency,
                    "reconciled_by": reconciled_by,
                    "reason": reason,
                },
            )

    def skip_budget_item(self, item_id: str, *, reason: str) -> None:
        now = utc_now()
        with self.database.transaction() as connection:
            item = connection.execute(
                "SELECT * FROM budget_plan_items WHERE id = ?", (item_id,)
            ).fetchone()
            if not item or item["status"] != BudgetItemStatus.RESERVED.value:
                return
            connection.execute(
                """
                UPDATE budget_plan_items
                SET status = ?, updated_at = ? WHERE id = ?
                """,
                (BudgetItemStatus.SKIPPED.value, now, item_id),
            )
            self._event(
                connection,
                project_id=item["project_id"],
                entity_type="budget_plan_item",
                entity_id=item_id,
                event_type="budget_item.skipped",
                payload={"reason": reason, "job_id": item["job_id"]},
            )

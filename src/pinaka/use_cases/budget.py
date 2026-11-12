from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from ..budget import TieredBudget, evaluate_spend, spend_summary
from ..enums import (
    ArtifactStatus,
    BudgetItemStatus,
    BudgetPlanStatus,
    SlotAssetRole,
)
from ..errors import (
    ApprovalRequiredError,
    BudgetExceededError,
    ConflictError,
    IntegrityError,
    NotFoundError,
    QuotaExceededError,
    RightsEligibilityError,
)
from ..models import (
    BudgetPlanApprovalCreate,
    BudgetPlanCreate,
    JobRequest,
    Money,
    ProjectRecord,
    StudioScope,
)
from ..policy.guards import (
    require_active_project,
)
from ._shared import (
    DEFAULT_CREATIVE_TARGET_MINOR,
    DEFAULT_RUN_CAP_MINOR,
    GENERATED_SOURCES,
    SPEND_CURRENCIES,
)


class BudgetUseCases:
    def reserve_provider_spend(
        self,
        provider: str,
        *,
        amount_usd: Any,
        purpose: str,
        budget: TieredBudget | None = None,
    ) -> dict[str, Any]:
        """Charge a paid call to the primary tier, then the bonus tier."""

        budget = budget or TieredBudget.load()
        amount_cents = int((Decimal(str(amount_usd)) * 100).to_integral_value())
        # One write transaction: the total that the decision reads is the total
        # the append sees, so two callers cannot both pass the last cent.
        with self.database.transaction() as connection:
            already = self.repository.provider_spend_total_cents(
                provider, connection=connection
            )
            decision = evaluate_spend(already, amount_cents, budget)
            if not decision.allowed:
                raise BudgetExceededError(decision.reason)
            record = self.repository.record_provider_spend(
                provider,
                amount_cents,
                decision.tier,
                purpose,
                notifications=decision.notifications,
                connection=connection,
            )
        return {
            "decision": decision.model_dump(mode="json"),
            "record": record,
            "notifications": list(decision.notifications),
        }

    def provider_spend_summary(
        self, provider: str, *, budget: TieredBudget | None = None
    ) -> dict[str, Any]:
        budget = budget or TieredBudget.load()
        spent = self.repository.provider_spend_total_cents(provider)
        return {
            **spend_summary(spent, budget),
            "entries": self.repository.list_provider_spend(provider),
        }

    def reserve_provider_request(
        self,
        provider: str,
        operation: str,
        *,
        limit: int,
        window_seconds: int,
    ) -> dict[str, Any]:
        """Refuse a provider request once a rolling window is exhausted."""

        cutoff = (
            (datetime.now(timezone.utc) - timedelta(seconds=window_seconds))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        used = self.repository.count_provider_requests(provider, cutoff)
        if used >= limit:
            raise QuotaExceededError(
                f"{provider} request quota reached: {used}/{limit} in "
                f"{window_seconds}s; wait for the window to roll over"
            )
        self.repository.record_provider_request(provider, operation)
        return {
            "provider": provider,
            "operation": operation,
            "used": used + 1,
            "limit": limit,
            "window_seconds": window_seconds,
        }

    @staticmethod
    def _auto_run_cap_minor(currency: str) -> int:
        if currency not in DEFAULT_RUN_CAP_MINOR:
            raise IntegrityError(
                f"Unsupported spending currency {currency!r}; "
                f"supported: {', '.join(SPEND_CURRENCIES)}"
            )
        return DEFAULT_RUN_CAP_MINOR[currency]

    @classmethod
    def _resolve_run_cap(cls, project: ProjectRecord, scope: StudioScope) -> Money:
        currency = project.max_budget.currency
        auto_minor = cls._auto_run_cap_minor(currency)
        if scope.max_amount is None:
            return Money.from_minor(
                auto_minor if scope.allow_paid_generation else 0, currency
            )
        if scope.max_currency != currency:
            raise IntegrityError(
                f"Studio runs must reserve in the project currency {currency}"
            )
        return Money(amount=scope.max_amount, currency=currency)

    def plan_creative_budget(
        self, project_id: str, request: BudgetPlanCreate
    ) -> dict[str, Any]:
        """Plan one creative against a currency target and decide on approval."""

        project = self.repository.get_project(project_id)
        require_active_project(
            project,
            message=f"Cannot plan a budget for a {project.status.value} project",
        )
        self._reject_sensitive_payload(request.model_dump(mode="json"), "budget_plan")
        currency = request.currency
        self._auto_run_cap_minor(currency)
        if currency != project.max_budget.currency:
            raise IntegrityError(
                "Creative budget plan currency must match the project budget "
                f"currency {project.max_budget.currency}"
            )
        target_minor = (
            Money(amount=request.target_amount, currency=currency).minor
            if request.target_amount is not None
            else DEFAULT_CREATIVE_TARGET_MINOR[currency]
        )
        planned_minor = request.planned_total_minor
        status = (
            BudgetPlanStatus.WITHIN_TARGET
            if planned_minor <= target_minor
            else BudgetPlanStatus.APPROVAL_REQUIRED
        )
        plan = self.repository.record_budget_plan(
            project_id, request, status=status.value, target_minor=target_minor
        )
        return {
            "plan": plan,
            "target": Money.from_minor(target_minor, currency).model_dump(mode="json"),
            "planned_total": Money.from_minor(planned_minor, currency).model_dump(
                mode="json"
            ),
            "overage": Money.from_minor(
                max(0, planned_minor - target_minor), currency
            ).model_dump(mode="json"),
            "within_target": status == BudgetPlanStatus.WITHIN_TARGET,
            "requires_approval": status == BudgetPlanStatus.APPROVAL_REQUIRED,
            "summary": self.budget_summary(project_id),
        }

    def approve_budget_plan(
        self, project_id: str, request: BudgetPlanApprovalCreate
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        require_active_project(
            project,
            message=f"Cannot approve a budget for a {project.status.value} project",
        )
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "budget_plan_approval"
        )
        plan = self.repository.get_budget_plan(request.plan_id)
        if plan["project_id"] != project_id:
            raise ConflictError("Budget plan belongs to a different project")
        if not plan["is_current"]:
            raise ConflictError("A superseded budget plan cannot be approved")
        if plan["status"] == BudgetPlanStatus.WITHIN_TARGET.value:
            raise IntegrityError(
                "A plan within the automatic target does not need approval"
            )
        if plan["status"] == BudgetPlanStatus.APPROVED.value:
            existing = self.repository.budget_plan_approval(plan["id"])
            if existing and existing["approved_by"] == request.approved_by:
                return {"plan": plan, "approval": existing}
            raise ConflictError("Budget plan is already approved")
        approval = self.repository.record_budget_plan_approval(
            project_id,
            plan_id=plan["id"],
            plan_hash=plan["content_hash"],
            approved_total=Money.from_minor(
                plan["planned_total_minor"], plan["currency"]
            ),
            approved_by=request.approved_by,
            reason=request.reason,
        )
        return {
            "plan": self.repository.get_budget_plan(plan["id"]),
            "approval": approval,
        }

    def budget_plan(self, project_id: str) -> dict[str, Any] | None:
        return self.repository.current_budget_plan(project_id)

    def budget_summary(self, project_id: str) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        plan = self.repository.current_budget_plan(project_id)
        available_minor = (
            project.max_budget.minor - project.spent.minor - project.reserved.minor
        )
        base = {
            "project_id": project_id,
            "currency": project.max_budget.currency,
            "project_budget": project.max_budget.model_dump(mode="json"),
            "project_reserved": project.reserved.model_dump(mode="json"),
            "project_spent": project.spent.model_dump(mode="json"),
            "available": Money.from_minor(
                available_minor, project.max_budget.currency
            ).model_dump(mode="json"),
            "automatic_run_cap": Money.from_minor(
                DEFAULT_RUN_CAP_MINOR.get(
                    project.max_budget.currency, project.max_budget.minor
                ),
                project.max_budget.currency,
            ).model_dump(mode="json"),
        }
        if not plan:
            return {
                **base,
                "plan": None,
                "pending_variances": self.repository.pending_variance_requests(
                    project_id
                ),
                "requires_approval": False,
                "requires_further_approval": False,
            }
        settled = [
            item
            for item in plan["items"]
            if item["status"] == BudgetItemStatus.SETTLED.value
        ]
        reserved = [
            item
            for item in plan["items"]
            if item["status"] == BudgetItemStatus.RESERVED.value
        ]
        settled_actual = sum(item["actual_minor"] or 0 for item in settled)
        settled_estimate = sum(item["estimated_minor"] for item in settled)
        variance = settled_actual - settled_estimate
        allowance = max(
            plan["planned_total_minor"] * plan["tolerance_bps"] // 10000,
            plan["tolerance_floor_minor"],
        )
        outstanding_required = [
            item
            for item in plan["items"]
            if item["priority"] == "required"
            and item["status"]
            in {BudgetItemStatus.PLANNED.value, BudgetItemStatus.RESERVED.value}
        ]
        return {
            **base,
            "plan": {
                "id": plan["id"],
                "version": plan["version"],
                "status": plan["status"],
                "content_hash": plan["content_hash"],
                "target": plan["target"],
                "planned_total": plan["planned_total"],
                "planned_total_minor": plan["planned_total_minor"],
                "tolerance_bps": plan["tolerance_bps"],
                "tolerance_allowance": Money.from_minor(
                    allowance, plan["currency"]
                ).model_dump(mode="json"),
                "tolerance_allowance_minor": allowance,
            },
            "items": {
                "total": len(plan["items"]),
                "planned": sum(
                    1
                    for item in plan["items"]
                    if item["status"] == BudgetItemStatus.PLANNED.value
                ),
                "reserved": len(reserved),
                "settled": len(settled),
                "outstanding_required": len(outstanding_required),
            },
            "execution": {
                "settled_estimated": Money.from_minor(
                    settled_estimate, plan["currency"]
                ).model_dump(mode="json"),
                "settled_actual": Money.from_minor(
                    settled_actual, plan["currency"]
                ).model_dump(mode="json"),
                "variance": Money.from_minor(
                    abs(variance), plan["currency"]
                ).model_dump(mode="json"),
                "variance_direction": (
                    "over" if variance > 0 else "under" if variance < 0 else "even"
                ),
                "variance_minor": variance,
                "within_tolerance": variance <= allowance,
                "remaining_in_plan": Money.from_minor(
                    max(0, plan["planned_total_minor"] - settled_actual),
                    plan["currency"],
                ).model_dump(mode="json"),
            },
            "pending_variances": self.repository.pending_variance_requests(project_id),
            "requires_approval": plan["status"]
            == BudgetPlanStatus.APPROVAL_REQUIRED.value,
            "requires_further_approval": bool(
                self.repository.pending_variance_requests(project_id)
            ),
            "plan_exceeded_tolerance": variance > allowance,
            "assets": self.asset_costs(project_id),
        }

    def _enforce_generation_routing(self, project_id: str, slot_id: str) -> None:
        """Paid generation is only allowed where the slot routing permits it."""

        slot = self.repository.get_slot(slot_id)
        if slot["project_id"] != project_id:
            raise ConflictError("Slot belongs to a different project")
        approved = {source.lower() for source in slot["approved_sources"]}
        if not approved & GENERATED_SOURCES:
            raise RightsEligibilityError(
                "Paid generation is not an approved source for this slot; use owned, "
                "stock or deterministic composition, or record a new routing decision"
            )
        primary = self.repository.slot_asset_by_role(
            slot_id, SlotAssetRole.PRIMARY.value
        )
        if primary:
            artifact = self.repository.get_artifact(primary["artifact_id"])
            if artifact["status"] != ArtifactStatus.STALE.value:
                raise ConflictError(
                    "A current primary asset already satisfies this slot; replace it "
                    "before paying for generation"
                )

    def reserve_budget_item(
        self,
        project_id: str,
        plan_id: str,
        item_key: str,
        request: JobRequest,
    ) -> dict[str, Any]:
        """Reserve one planned line item before a provider call."""

        plan = self.repository.get_budget_plan(plan_id)
        if plan["project_id"] != project_id:
            raise ConflictError("Budget plan belongs to a different project")
        if not plan["is_current"]:
            raise ConflictError("A superseded budget plan cannot authorize work")
        if plan["status"] not in {
            BudgetPlanStatus.WITHIN_TARGET.value,
            BudgetPlanStatus.APPROVED.value,
        }:
            raise ApprovalRequiredError(
                "Budget plan requires human approval before execution"
            )
        item = next(
            (entry for entry in plan["items"] if entry["stable_key"] == item_key),
            None,
        )
        if not item:
            raise NotFoundError(f"Budget item not found in plan: {item_key}")
        if item["status"] != BudgetItemStatus.PLANNED.value:
            raise ConflictError(f"Budget item {item_key} is already {item['status']}")
        if (
            request.estimated_cost.minor != item["estimated_minor"]
            or request.estimated_cost.currency != plan["currency"]
        ):
            raise IntegrityError(
                "Job estimate must match the approved budget plan item"
            )
        if (
            request.provider != item["provider"]
            or request.operation != item["operation"]
        ):
            raise IntegrityError(
                "Job provider and operation must match the approved budget plan item"
            )
        if item["slot_id"]:
            self._enforce_generation_routing(project_id, item["slot_id"])
        job = self.reserve_job(project_id, request)
        linked = self.repository.link_budget_item_job(
            project_id, plan_id, item_key, job.id
        )
        return {"job": job.model_dump(mode="json"), "item": linked}

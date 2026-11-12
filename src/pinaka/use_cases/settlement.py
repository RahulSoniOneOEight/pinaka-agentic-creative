from __future__ import annotations

from typing import Any

from ..enums import (
    JobStatus,
    ToolOperationStatus,
)
from ..errors import (
    ConflictError,
    IntegrityError,
    VarianceApprovalRequiredError,
)
from ..models import (
    BudgetRaiseCreate,
    Money,
    OverageReconcileCreate,
    StudioResultPacketImport,
    VarianceDecisionCreate,
)
from ..policy.guards import (
    require_active_project,
)


class SettlementUseCases:
    def settle_budget_item(
        self,
        project_id: str,
        job_id: str,
        *,
        actual_cost: Money,
        requested_by: str | None = None,
        reason: str | None = None,
        asset_artifact_id: str | None = None,
    ) -> dict[str, Any]:
        """Settle a planned job, raising an approval request when over tolerance."""

        job = self.repository.get_job(job_id)
        if job.project_id != project_id:
            raise ConflictError("Job belongs to a different project")
        try:
            settled = self.settle_job(
                job_id,
                target=JobStatus.SUCCEEDED,
                actual_cost=actual_cost,
                asset_artifact_id=asset_artifact_id,
            )
        except VarianceApprovalRequiredError as error:
            request = self.repository.record_variance_request(
                project_id,
                plan_id=error.plan_id,
                item_id=error.item_id,
                job_id=error.job_id,
                estimated_minor=error.estimated_minor,
                actual_minor=error.actual_minor,
                allowed_minor=error.allowed_minor,
                currency=error.currency,
                requested_by=requested_by,
                reason=reason,
            )
            return {
                "status": "approval_required",
                "message": (
                    "Actual cost is beyond the plan tolerance. Approve or reject "
                    f"request {request['id']} to proceed."
                ),
                "request": request,
                "summary": self.budget_summary(project_id),
            }
        return {
            "status": "settled",
            "job": settled.model_dump(mode="json"),
            "summary": self.budget_summary(project_id),
        }

    def variance_requests(self, project_id: str) -> list[dict[str, Any]]:
        return self.repository.pending_variance_requests(project_id)

    def raise_project_budget(
        self, project_id: str, request: BudgetRaiseCreate
    ) -> dict[str, Any]:
        """Human-authorized increase of the project budget ceiling."""

        project = self.repository.get_project(project_id)
        require_active_project(
            project,
            message=f"Cannot raise the budget of a {project.status.value} project",
        )
        self._reject_sensitive_payload(request.model_dump(mode="json"), "budget_raise")
        updated = self.repository.raise_project_budget(
            project_id,
            new_max=request.new_max,
            raised_by=request.raised_by,
            reason=request.reason,
        )
        return {"project": updated, "summary": self.budget_summary(project_id)}

    def reconcile_overage(
        self, project_id: str, request: OverageReconcileCreate
    ) -> dict[str, Any]:
        """Record a human-accepted provider overrun and settle the job."""

        job = self.repository.get_job(request.job_id)
        if job.project_id != project_id:
            raise ConflictError("Job belongs to a different project")
        if job.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            raise ConflictError(f"Job is already terminal: {job.status.value}")
        if job.estimated_cost.currency != request.actual_cost.currency:
            raise IntegrityError("Reconciled cost currency must match the job")
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "overage_reconcile"
        )
        settled = self.settle_job(
            request.job_id,
            target=JobStatus.SUCCEEDED,
            actual_cost=request.actual_cost,
            approved_variance_minor=request.actual_cost.minor,
        )
        self.repository.record_overage_reconciliation(
            project_id,
            job_id=request.job_id,
            actual_cost=request.actual_cost,
            reconciled_by=request.reconciled_by,
            reason=request.reason,
        )
        return {
            "job": settled.model_dump(mode="json"),
            "summary": self.budget_summary(project_id),
        }

    def asset_costs(self, project_id: str) -> dict[str, Any]:
        """Per-asset generation cost ledger with project and provider totals."""

        project = self.repository.get_project(project_id)
        costs = self.repository.list_asset_costs(project_id)
        slots = {slot["id"]: slot for slot in self.repository.list_slots(project_id)}
        currency = project.max_budget.currency
        rows = []
        by_provider: dict[str, dict[str, int]] = {}
        total_estimated = 0
        total_actual = 0
        for cost in costs:
            slot = slots.get(cost["slot_id"]) if cost["slot_id"] else None
            row = {
                **cost,
                "slot_key": slot["stable_key"] if slot else None,
                "slot_role": slot["role"] if slot else None,
            }
            rows.append(row)
            total_estimated += cost["estimated_minor"]
            total_actual += cost["actual_minor"]
            bucket = by_provider.setdefault(
                cost["provider"], {"estimated_minor": 0, "actual_minor": 0, "count": 0}
            )
            bucket["estimated_minor"] += cost["estimated_minor"]
            bucket["actual_minor"] += cost["actual_minor"]
            bucket["count"] += 1
        variance = total_actual - total_estimated
        return {
            "project_id": project_id,
            "currency": currency,
            "count": len(rows),
            "total_estimated": Money.from_minor(total_estimated, currency).model_dump(
                mode="json"
            ),
            "total_actual": Money.from_minor(total_actual, currency).model_dump(
                mode="json"
            ),
            "total_variance": Money.from_minor(abs(variance), currency).model_dump(
                mode="json"
            ),
            "total_variance_minor": variance,
            "total_variance_direction": (
                "over" if variance > 0 else "under" if variance < 0 else "even"
            ),
            "by_provider": {
                provider: {
                    "count": values["count"],
                    "estimated": Money.from_minor(
                        values["estimated_minor"], currency
                    ).model_dump(mode="json"),
                    "actual": Money.from_minor(
                        values["actual_minor"], currency
                    ).model_dump(mode="json"),
                }
                for provider, values in sorted(by_provider.items())
            },
            "assets": rows,
        }

    def decide_variance(
        self, project_id: str, request: VarianceDecisionCreate
    ) -> dict[str, Any]:
        """Apply a human decision to a pending budget variance request."""

        variance = self.repository.get_variance_request(request.request_id)
        if variance["project_id"] != project_id:
            raise ConflictError("Variance request belongs to a different project")
        if variance["status"] != "pending":
            raise ConflictError(f"Variance request is already {variance['status']}")
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "variance_decision"
        )
        job = self.repository.get_job(variance["job_id"])
        if job.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            raise ConflictError(f"Job is already terminal: {job.status.value}")
        actual = Money.from_minor(variance["actual_minor"], variance["currency"])
        if variance["kind"] == "studio_run":
            if request.decision == "approve":
                decision = self.repository.decide_variance_request(
                    variance["id"],
                    decision="approved",
                    decided_by=request.decided_by,
                    decision_reason=request.reason,
                )
                return {
                    "decision": decision,
                    "job": job.model_dump(mode="json"),
                    "next": "run studio-run again to collect and settle the run",
                    "summary": self.budget_summary(project_id),
                }
            self.repository.decide_variance_request(
                variance["id"],
                decision="rejected",
                decided_by=request.decided_by,
                decision_reason=request.reason,
            )
            cancelled = self.import_studio_result(
                StudioResultPacketImport(
                    operation_id=variance["operation_id"],
                    status=ToolOperationStatus.CANCELLED,
                    elapsed_ms=0,
                    actual_cost=Money(amount=0, currency=variance["currency"]),
                    details={
                        "integration": "openmontage",
                        "cancelled_reason": request.reason,
                    },
                )
            )
            production = self.repository.get_studio_production(variance["operation_id"])
            if production:
                self.repository.update_studio_production(
                    production["id"], status="cancelled"
                )
            return {
                "decision": self.repository.get_variance_request(variance["id"]),
                "job": cancelled["receipt"],
                "summary": self.budget_summary(project_id),
            }
        if request.decision == "approve":
            settled = self.repository.settle_job(
                variance["job_id"],
                target=JobStatus.SUCCEEDED,
                actual_cost=actual,
                approved_variance_minor=variance["actual_minor"],
            )
            decision = self.repository.decide_variance_request(
                variance["id"],
                decision="approved",
                decided_by=request.decided_by,
                decision_reason=request.reason,
            )
            return {
                "decision": decision,
                "job": settled.model_dump(mode="json"),
                "summary": self.budget_summary(project_id),
            }
        self.repository.skip_budget_item(
            variance["item_id"], reason=f"Variance rejected: {request.reason}"
        )
        cancelled = self.repository.settle_job(
            variance["job_id"],
            target=JobStatus.CANCELLED,
            actual_cost=Money(amount=0, currency=variance["currency"]),
        )
        decision = self.repository.decide_variance_request(
            variance["id"],
            decision="rejected",
            decided_by=request.decided_by,
            decision_reason=request.reason,
        )
        return {
            "decision": decision,
            "job": cancelled.model_dump(mode="json"),
            "reported_actual": variance["actual"],
            "summary": self.budget_summary(project_id),
        }

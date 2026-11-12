from __future__ import annotations

from typing import Any

from ..contracts.flow import FlowStepCreate, SourcingDecisionCreate
from ..errors import ConflictError, IntegrityError

REQUIRED_FLOW_STEPS: tuple[str, ...] = (
    "g0_intake",
    "g1_classification",
    "g2_creative_plan",
    "g3_sourcing",
    "g4_storyboard",
    "g5_production",
    "g6_qa",
    "g7_delivery",
)

TERMINAL_FLOW_STATUSES = {"done", "skipped", "not_applicable"}


class FlowUseCases:
    """Generation-last evidence and the append-only G0-G7 ledger."""

    def record_sourcing_decision(
        self, project_id: str, request: SourcingDecisionCreate
    ) -> dict[str, Any]:
        return self.repository.record_sourcing_decision(project_id, request)

    def sourcing_decisions(
        self, project_id: str, slot_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self.repository.list_sourcing_decisions(project_id, slot_id)

    def require_generated_decision(
        self, project_id: str, slot_id: str
    ) -> dict[str, Any]:
        """Refuse paid generation until the slot has a 'no other way' record."""

        decision = self.repository.current_sourcing_decision(project_id, slot_id)
        if not decision:
            raise IntegrityError(
                "Paid generation requires a recorded sourcing decision for the slot. "
                "Search owned and stock sources first, then record the decision with "
                "providers, candidates considered and the rejection reason."
            )
        if decision["decision"] != "generated":
            raise IntegrityError(
                f"Slot sourcing decision is {decision['decision']!r}, not 'generated'; "
                "use the selected owned/stock asset instead of paying to generate"
            )
        return decision

    def record_flow_step(
        self, project_id: str, request: FlowStepCreate
    ) -> dict[str, Any]:
        return self.repository.record_flow_step(project_id, request)

    def flow_log(
        self, project_id: str, step: str | None = None
    ) -> list[dict[str, Any]]:
        return self.repository.list_flow_steps(project_id, step)

    def flow_completeness(self, project_id: str) -> dict[str, Any]:
        """Report required G0-G7 steps that are missing or not terminal."""

        steps = self.repository.list_flow_steps(project_id)
        latest: dict[str, dict[str, Any]] = {}
        for entry in steps:
            latest[entry["step"]] = entry
        missing = [step for step in REQUIRED_FLOW_STEPS if step not in latest]
        incomplete = [
            step
            for step in REQUIRED_FLOW_STEPS
            if step in latest and latest[step]["status"] not in TERMINAL_FLOW_STATUSES
        ]
        unapproved_skips = [
            step
            for step in REQUIRED_FLOW_STEPS
            if step in latest
            and latest[step]["status"] in {"skipped", "not_applicable"}
            and not (latest[step].get("approval_id") or latest[step].get("reason"))
        ]
        return {
            "required": list(REQUIRED_FLOW_STEPS),
            "recorded": sorted(latest),
            "missing": missing,
            "incomplete": incomplete,
            "unapproved_skips": unapproved_skips,
            "complete": not missing and not incomplete and not unapproved_skips,
            "steps": [latest[step] for step in REQUIRED_FLOW_STEPS if step in latest],
        }

    def require_flow_complete(self, project_id: str) -> dict[str, Any]:
        report = self.flow_completeness(project_id)
        if not report["complete"]:
            problems = []
            if report["missing"]:
                problems.append("missing " + ", ".join(report["missing"]))
            if report["incomplete"]:
                problems.append("incomplete " + ", ".join(report["incomplete"]))
            if report["unapproved_skips"]:
                problems.append(
                    "unapproved skip " + ", ".join(report["unapproved_skips"])
                )
            raise ConflictError("Flow ledger is not complete: " + "; ".join(problems))
        return report

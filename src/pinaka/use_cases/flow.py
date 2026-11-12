from __future__ import annotations

from typing import Any

from ..contracts.flow import (
    FlowStepCreate,
    SlotPickCreate,
    SourcingCandidateCreate,
    SourcingDecisionCreate,
)
from ..creative_rules import CEILING_ORDER
from ..enums import ArtifactKind
from ..errors import ConflictError, IntegrityError
from ..utils import canonical_json, sha256_bytes

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

TREATMENT_CEILING = {
    "as_is": "as_is",
    "correct": "match",
    "ai_enhance": "ai_enhance",
    "composite": "composite",
    "generate": "ai_created",
}

TREATMENT_PENALTY = {
    "as_is": 0,
    "correct": 2,
    "ai_enhance": 6,
    "composite": 10,
    "generate": 20,
}

NON_VISUAL_SLOT_ROLES = {
    "captions",
    "narration",
    "music",
    "cta",
    "hook",
    "claim_or_proof",
}


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

    def record_sourcing_candidate(
        self, project_id: str, request: SourcingCandidateCreate
    ) -> dict[str, Any]:
        slot = self.repository.get_slot(request.slot_id)
        if slot["project_id"] != project_id:
            raise ConflictError("Slot belongs to a different project")
        requested = TREATMENT_CEILING[request.treatment]
        ceiling = slot.get("treatment_ceiling", "ai_created")
        if CEILING_ORDER.index(requested) > CEILING_ORDER.index(ceiling):
            raise IntegrityError(
                f"Treatment {request.treatment!r} exceeds the slot ceiling "
                f"{ceiling!r}; pick a lower treatment or change the slot policy"
            )
        score = 0
        if request.eligibility == "eligible":
            score = 100 - TREATMENT_PENALTY[request.treatment]
        return self.repository.record_sourcing_candidate(
            project_id, request, score=score
        )

    def sourcing_candidates(
        self, project_id: str, slot_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self.repository.list_sourcing_candidates(project_id, slot_id)

    def pick_slot_assets(
        self, project_id: str, request: SlotPickCreate
    ) -> dict[str, Any]:
        return self.repository.set_slot_picks(
            project_id,
            request.slot_id,
            primary_candidate_id=request.primary_candidate_id,
            backup_candidate_id=request.backup_candidate_id,
            decided_by=request.decided_by,
            reason=request.reason,
        )

    def slot_pick_summary(self, project_id: str) -> dict[str, Any]:
        slots = self.repository.list_slots(project_id)
        picks = {
            entry["slot_id"]: entry for entry in self.repository.slot_picks(project_id)
        }
        rows = []
        for slot in slots:
            pick = picks.get(slot["id"], {})
            rows.append(
                {
                    "slot_id": slot["id"],
                    "stable_key": slot["stable_key"],
                    "role": slot["role"],
                    "primary": pick.get("primary"),
                    "backup": pick.get("backup"),
                    "complete": bool(pick.get("primary") and pick.get("backup")),
                }
            )
        return {
            "slots": rows,
            "complete": all(
                row["complete"]
                for row in rows
                if row["role"] not in NON_VISUAL_SLOT_ROLES
            ),
            "missing": [
                row["stable_key"]
                for row in rows
                if row["role"] not in NON_VISUAL_SLOT_ROLES and not row["complete"]
            ],
        }

    def require_slot_picks(self, project_id: str) -> dict[str, Any]:
        summary = self.slot_pick_summary(project_id)
        if not summary["complete"]:
            raise ConflictError(
                "Every visual slot needs a primary and an independent backup "
                "before final approval; missing: " + ", ".join(summary["missing"])
            )
        return summary

    def write_sourcing_manifest(self, project_id: str) -> dict[str, Any]:
        """Write one hashed manifest of slots, decisions, candidates and picks."""

        slots = self.repository.list_slots(project_id)
        decisions = self.repository.list_sourcing_decisions(project_id)
        candidates = self.repository.list_sourcing_candidates(project_id)
        picks = self.repository.slot_picks(project_id)
        manifest = {
            "schema": "sourcing-manifest/v1",
            "project_id": project_id,
            "slots": slots,
            "decisions": decisions,
            "candidates": candidates,
            "picks": picks,
            "counts": {
                "slots": len(slots),
                "decisions": len(decisions),
                "candidates": len(candidates),
                "picks": len(picks),
            },
        }
        content = canonical_json(manifest).encode("utf-8")
        content_hash = sha256_bytes(content)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"sourcing/manifest-{content_hash[:12]}.json",
            content=content,
            operation={"type": "sourcing_manifest", "counts": manifest["counts"]},
        )
        return {
            "manifest": manifest,
            "manifest_hash": content_hash,
            "artifact": artifact,
        }

    def current_sourcing_manifest(self, project_id: str) -> dict[str, Any] | None:
        manifests = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if artifact["relative_path"].startswith("sourcing/manifest-")
        ]
        if not manifests:
            return None
        artifact = max(manifests, key=lambda item: item["created_at"])
        return {"artifact": artifact, "manifest_hash": artifact["content_hash"]}

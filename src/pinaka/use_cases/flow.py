from __future__ import annotations

import json
from typing import Any

from ..contracts.effects import EffectsPlanCreate
from ..contracts.flow import (
    ABExperimentCreate,
    ABVariantBind,
    FlowStepCreate,
    MotionLevelCreate,
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

MOTION_ORDER = ("M0", "M1", "M2", "M3", "M4", "M5")

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

    def record_motion_level(
        self, project_id: str, request: MotionLevelCreate
    ) -> dict[str, Any]:
        return self.repository.record_motion_level(project_id, request)

    def motion_levels(self, project_id: str) -> dict[str, Any]:
        shots = self.repository.list_shots(project_id)
        latest: dict[str, dict[str, Any]] = {}
        for entry in self.repository.list_motion_levels(project_id):
            latest.setdefault(entry["shot_id"], entry)
        rows = []
        for shot in shots:
            requirements = shot.get("requirements") or {}
            required = "M1" if requirements.get("subject") else "M0"
            entry = latest.get(shot["id"])
            level = entry["level"] if entry else None
            rows.append(
                {
                    "shot_id": shot["id"],
                    "stable_key": shot["stable_key"],
                    "required": required,
                    "level": level,
                    "source": entry["source"] if entry else None,
                    "passes": (
                        level is not None
                        and MOTION_ORDER.index(level) >= MOTION_ORDER.index(required)
                    ),
                }
            )
        return {
            "shots": rows,
            "complete": all(row["passes"] for row in rows),
            "missing": [row["stable_key"] for row in rows if row["level"] is None],
            "below_minimum": [
                row["stable_key"]
                for row in rows
                if row["level"] is not None and not row["passes"]
            ],
        }

    def require_motion_levels(self, project_id: str) -> dict[str, Any]:
        summary = self.motion_levels(project_id)
        if not summary["complete"]:
            problems = []
            if summary["missing"]:
                problems.append("no level: " + ", ".join(summary["missing"]))
            if summary["below_minimum"]:
                problems.append("below M1: " + ", ".join(summary["below_minimum"]))
            raise ConflictError(
                "Stills must become motion before animatic approval; "
                + "; ".join(problems)
            )
        return summary

    def production_record(self, project_id: str) -> dict[str, Any]:
        """Export the full production record as hashed JSON and Markdown."""

        project = self.repository.get_project(project_id)
        brief = self.repository.latest_workable_brief(project_id)
        artifacts = self.repository.list_artifacts(project_id)
        approvals = self.repository.list_approvals(project_id)
        qa_results = self.repository.list_qa_results(project_id)
        body = {
            "schema": "production-record/v1",
            "project": project.model_dump(mode="json"),
            "brief": brief,
            "production_contract": (
                self.current_production_contract(project_id) or {}
            ).get("contract"),
            "shots": self.repository.list_shots(project_id),
            "slots": self.repository.list_slots(project_id),
            "sourcing_decisions": self.repository.list_sourcing_decisions(project_id),
            "candidates": self.repository.list_sourcing_candidates(project_id),
            "picks": self.repository.slot_picks(project_id),
            "motion": self.motion_levels(project_id),
            "ab": self.ab_status(project_id),
            "flow": self.flow_completeness(project_id),
            "approvals": approvals,
            "qa": qa_results,
            "artifacts": [
                {
                    "id": artifact["id"],
                    "path": artifact["relative_path"],
                    "hash": artifact["content_hash"],
                    "kind": artifact["kind"],
                }
                for artifact in artifacts
            ],
            "spend": {
                "budget": project.max_budget.model_dump(mode="json"),
                "spent": project.spent.model_dump(mode="json"),
            },
        }
        content = canonical_json(body).encode("utf-8")
        content_hash = sha256_bytes(content)
        json_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"records/production-record-{content_hash[:12]}.json",
            content=content,
            operation={"type": "production_record", "format": "json"},
        )
        markdown = self._production_record_markdown(body)
        markdown_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"records/PRODUCTION-RECORD-{content_hash[:12]}.md",
            content=markdown.encode("utf-8"),
            operation={"type": "production_record", "format": "markdown"},
        )
        return {
            "record": body,
            "record_hash": content_hash,
            "json_artifact": json_artifact,
            "markdown_artifact": markdown_artifact,
        }

    @staticmethod
    def _production_record_markdown(body: dict[str, Any]) -> str:
        project = body["project"]
        flow = body["flow"]
        lines = [
            f"# Production record - {project['name']}",
            "",
            f"- Project: `{project['id']}`",
            f"- Status: {project['status']}",
            f"- Flow complete: {flow['complete']}",
            f"- Shots: {len(body['shots'])}",
            f"- Slots: {len(body['slots'])}",
            f"- Sourcing decisions: {len(body['sourcing_decisions'])}",
            f"- Candidates: {len(body['candidates'])}",
            f"- Motion complete: {body['motion']['complete']}",
            f"- Approvals: {len(body['approvals'])}",
            f"- QA results: {len(body['qa'])}",
            "",
            "## Flow",
            "",
        ]
        for step in flow["steps"]:
            lines.append(
                f"- {step['step']}: {step['status']} "
                f"({step.get('reason') or 'no reason'})"
            )
        lines.extend(["", "## Slots", ""])
        for slot in body["slots"]:
            pick = next(
                (item for item in body["picks"] if item["slot_id"] == slot["id"]),
                {},
            )
            lines.append(
                f"- {slot['stable_key']} ({slot['role']}): "
                f"primary={bool(pick.get('primary'))} "
                f"backup={bool(pick.get('backup'))}"
            )
        lines.extend(["", "## Artifacts", ""])
        for artifact in body["artifacts"]:
            lines.append(f"- `{artifact['id']}` {artifact['path']} {artifact['hash']}")
        lines.append("")
        return "\n".join(lines)

    def create_ab_experiment(
        self, project_id: str, request: ABExperimentCreate
    ) -> dict[str, Any]:
        return self.repository.create_ab_experiment(project_id, request)

    def bind_ab_variant(
        self, project_id: str, request: ABVariantBind
    ) -> dict[str, Any]:
        return self.repository.bind_ab_variant(project_id, request)

    def ab_status(self, project_id: str) -> dict[str, Any]:
        experiments = []
        for experiment in self.repository.list_ab_experiments(project_id):
            variants = self.repository.list_ab_variants(experiment["id"])
            labels = {variant["label"] for variant in variants}
            experiments.append(
                {
                    **experiment,
                    "variants": variants,
                    "complete": {"A", "B"} <= labels,
                    "missing": sorted({"A", "B"} - labels),
                }
            )
        return {
            "experiments": experiments,
            "complete": bool(experiments)
            and all(item["complete"] for item in experiments),
            "declared": len(experiments),
        }

    def require_ab_complete(self, project_id: str) -> dict[str, Any]:
        status = self.ab_status(project_id)
        if status["declared"] == 0:
            raise ConflictError(
                "No A/B experiment is declared; declare the axis, hypothesis, "
                "metric and split before delivery"
            )
        incomplete = [
            item["id"] for item in status["experiments"] if not item["complete"]
        ]
        if incomplete:
            raise ConflictError(
                "A/B variants are incomplete for: " + ", ".join(incomplete)
            )
        return status

    def quality_bar(
        self, project_id: str, *, artifact_id: str | None = None
    ) -> dict[str, Any]:
        """Check every delivery gate, not only technical QA."""

        gates: list[dict[str, Any]] = []

        def gate(name: str, passed: bool, detail: str) -> None:
            gates.append({"gate": name, "passed": passed, "detail": detail})

        brief = self.repository.latest_workable_brief(project_id)
        gate("brief", brief is not None, "workable brief recorded")
        try:
            contract = self.require_production_contract(project_id)
            gate(
                "production_contract",
                contract is not None or brief is None,
                "brief frozen into a production contract",
            )
        except IntegrityError as error:
            gate("production_contract", False, str(error))

        slots = self.repository.list_slots(project_id)
        decisions = {
            item["slot_id"]: item
            for item in self.repository.list_sourcing_decisions(project_id)
        }
        missing_decisions = [
            slot["stable_key"] for slot in slots if slot["id"] not in decisions
        ]
        gate(
            "sourcing_decisions",
            not missing_decisions,
            "missing: " + ", ".join(missing_decisions)
            if missing_decisions
            else "all slots decided",
        )
        picks = self.slot_pick_summary(project_id)
        gate(
            "two_pick",
            picks["complete"],
            "missing: " + ", ".join(picks["missing"])
            if picks["missing"]
            else "primary and backup for every visual slot",
        )
        motion = self.motion_levels(project_id)
        gate(
            "motion",
            motion["complete"],
            "every character shot at M1 or above"
            if motion["complete"]
            else f"missing {motion['missing']}, below minimum {motion['below_minimum']}",
        )
        flow = self.flow_completeness(project_id)
        gate(
            "flow_ledger",
            flow["complete"],
            "G0-G7 complete"
            if flow["complete"]
            else f"missing {flow['missing']}, incomplete {flow['incomplete']}",
        )
        ab = self.ab_status(project_id)
        gate(
            "ab",
            ab["complete"],
            "A and B bound"
            if ab["complete"]
            else f"declared {ab['declared']} experiments",
        )
        effects = self.current_effects_plan(project_id)
        gate(
            "effects_plan",
            effects is not None or not slots,
            "effects and sound plan recorded"
            if effects
            else "no effects or sound-design plan",
        )
        change_requests = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if artifact["relative_path"].startswith("delivery/CHANGE-REQUEST-")
            and artifact["relative_path"].endswith(".md")
        ]
        gate(
            "change_request",
            bool(change_requests),
            "CHANGE-REQUEST.md generated"
            if change_requests
            else "no change-request template",
        )
        if artifact_id:
            checks = [
                result
                for result in self.repository.list_qa_results(project_id)
                if result["artifact_id"] == artifact_id
            ]
            failures = [item for item in checks if item["status"] == "fail"]
            gate(
                "technical_qa",
                bool(checks) and not failures,
                f"{len(checks)} checks, {len(failures)} failing"
                if checks
                else "no QA for the artifact",
            )
        return {
            "project_id": project_id,
            "gates": gates,
            "passed": all(item["passed"] for item in gates),
            "failed": [item["gate"] for item in gates if not item["passed"]],
        }

    def record_effects_plan(
        self, project_id: str, request: EffectsPlanCreate
    ) -> dict[str, Any]:
        shot_keys = {
            shot["stable_key"] for shot in self.repository.list_shots(project_id)
        }
        if shot_keys:
            unknown = sorted({cue.shot_key for cue in request.cues} - shot_keys)
            if unknown:
                raise ConflictError(
                    "Effects plan references unknown shots: " + ", ".join(unknown)
                )
        body = {
            "schema": "effects-plan/v1",
            "project_id": project_id,
            **request.model_dump(mode="json"),
        }
        content = canonical_json(body).encode("utf-8")
        content_hash = sha256_bytes(content)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"plans/effects-{content_hash[:12]}.json",
            content=content,
            operation={
                "type": "effects_plan",
                "cue_count": len(request.cues),
                "effects": sorted({cue.kind for cue in request.cues}),
            },
        )
        return {
            "plan": body,
            "plan_hash": content_hash,
            "artifact": artifact,
        }

    def current_effects_plan(self, project_id: str) -> dict[str, Any] | None:
        plans = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if artifact["relative_path"].startswith("plans/effects-")
        ]
        if not plans:
            return None
        artifact = max(plans, key=lambda item: item["created_at"])
        return {
            "artifact": artifact,
            "plan": json.loads(self.read_artifact_text(artifact["id"])),
        }

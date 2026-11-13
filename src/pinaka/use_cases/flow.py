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
from ..errors import (
    ApprovalRequiredError,
    ConflictError,
    IntegrityError,
    NotFoundError,
)
from ..motion_verify import verify_frames, verify_video_segment
from ..production import style_fit
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
        evidence_by_step: dict[str, list[str]] = {}
        for entry in steps:
            latest[entry["step"]] = entry
            bucket = evidence_by_step.setdefault(entry["step"], [])
            for item in entry.get("evidence", ()):
                if item not in bucket:
                    bucket.append(item)
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
            "steps": [
                {
                    **latest[step],
                    "evidence": evidence_by_step.get(step, []),
                }
                for step in REQUIRED_FLOW_STEPS
                if step in latest
            ],
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
        slot_style = slot.get("style_id")
        asset_class = request.asset_class
        if not asset_class:
            asset_class = "photo" if request.media_type == "image" else "footage"
        fit, fit_reasons = (
            style_fit(
                slot_style, asset_class=asset_class, media_type=request.media_type
            )
            if slot_style
            else (0, [])
        )
        if request.eligibility == "eligible" and slot_style and fit <= 0:
            raise IntegrityError(
                "Candidate does not fit the slot style: " + "; ".join(fit_reasons)
            )
        return self.repository.record_sourcing_candidate(
            project_id,
            request,
            score=score,
            style_fit=fit,
            style_fit_reason="; ".join(fit_reasons) if fit_reasons else None,
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

    def write_motion_evidence(
        self,
        project_id: str,
        *,
        shot_id: str,
        level: str,
        frames: list[Any],
        mask: Any | None = None,
        source: str = "composition",
    ) -> dict[str, Any]:
        report = verify_frames(frames, mask=mask)
        body = {
            "schema": "motion-evidence/v1",
            "project_id": project_id,
            "shot_id": shot_id,
            "level": level,
            "source": source,
            **report,
        }
        content = canonical_json(body).encode("utf-8")
        digest = sha256_bytes(content)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"motion/evidence-{digest[:12]}.json",
            content=content,
            operation={
                "type": "motion_evidence",
                "shot_id": shot_id,
                "level": level,
                "verdict": report["verdict"],
            },
        )
        return {"artifact": artifact, "evidence": body, "evidence_hash": digest}

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
            evidence = None
            evidence_id = entry.get("evidence_artifact_id") if entry else None
            if evidence_id:
                try:
                    evidence = json.loads(self.read_artifact_text(evidence_id))
                except (json.JSONDecodeError, NotFoundError):
                    evidence = None
            evidence_pass = bool(evidence and evidence.get("verdict") == "pass")
            rows.append(
                {
                    "shot_id": shot["id"],
                    "stable_key": shot["stable_key"],
                    "required": required,
                    "level": level,
                    "source": entry["source"] if entry else None,
                    "evidence_artifact_id": evidence_id,
                    "evidence_verdict": (evidence.get("verdict") if evidence else None),
                    "passes": (
                        level is not None
                        and MOTION_ORDER.index(level) >= MOTION_ORDER.index(required)
                        and (level == "M0" or evidence_pass)
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
                if row["level"] is not None
                and MOTION_ORDER.index(row["level"])
                < MOTION_ORDER.index(row["required"])
            ],
            "missing_evidence": [
                row["stable_key"]
                for row in rows
                if row["level"] not in {None, "M0"} and not row["evidence_artifact_id"]
            ],
            "failed_evidence": [
                row["stable_key"]
                for row in rows
                if row["level"] not in {None, "M0"}
                and row["evidence_artifact_id"]
                and row["evidence_verdict"] != "pass"
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
            if summary["missing_evidence"]:
                problems.append(
                    "no motion evidence: " + ", ".join(summary["missing_evidence"])
                )
            if summary["failed_evidence"]:
                problems.append(
                    "evidence failed: " + ", ".join(summary["failed_evidence"])
                )
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
            "shot_productions": self.repository.list_shot_productions(project_id),
            "script": self.repository.current_script(project_id),
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
            "every character shot at M1 or above with passing evidence"
            if motion["complete"]
            else (
                f"missing {motion['missing']}, "
                f"below minimum {motion['below_minimum']}, "
                f"no evidence {motion['missing_evidence']}, "
                f"failed evidence {motion['failed_evidence']}"
            ),
        )
        animatic = self.repository.active_approval(project_id, "animatic")
        if animatic and animatic.get("scope", {}).get("render"):
            try:
                self.require_qa_pass(project_id, str(animatic["scope"]["render"]))
                gate("animatic_qa", True, "approved render has passing QA")
            except ApprovalRequiredError as error:
                gate("animatic_qa", False, str(error))
        else:
            gate("animatic_qa", True, "no animatic approval yet")
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
        productions = self.repository.list_shot_productions(project_id)
        secondary_kinds = {"particles", "light_glow", "blur", "parallax"}
        secondary_by_shot: dict[str, int] = {}
        if effects:
            for cue in effects["plan"].get("cues", ()):
                if cue["kind"] in secondary_kinds:
                    key = cue["shot_key"]
                    secondary_by_shot[key] = secondary_by_shot.get(key, 0) + 1
        shot_rows = self.repository.list_shots(project_id)
        shots_by_id = {shot["id"]: shot for shot in shot_rows}
        missing_secondary = [
            shots_by_id[item["shot_id"]]["stable_key"]
            for item in productions
            if item["shot_id"] in shots_by_id
            and secondary_by_shot.get(shots_by_id[item["shot_id"]]["stable_key"], 0)
            == 0
        ]
        gate(
            "secondary_motion",
            not missing_secondary,
            "secondary motion per shot"
            if not missing_secondary
            else "missing: " + ", ".join(missing_secondary),
        )
        try:
            creative = self.require_creative_plan(project_id)
            gate(
                "creative_plan",
                True,
                f"recipe {creative['plan'].recipe_selection.recipe.id}",
            )
        except IntegrityError as error:
            gate("creative_plan", False, str(error))
        try:
            reuse = self.require_reuse_decision(project_id)
            gate("reuse_decision", True, f"decision {reuse['id']}")
        except IntegrityError as error:
            gate("reuse_decision", False, str(error))
        productions = self.repository.list_shot_productions(project_id)
        shots = self.repository.list_shots(project_id)
        gate(
            "shot_productions",
            len(productions) == len(shots) and bool(shots),
            f"{len(productions)}/{len(shots)} shots have production contracts",
        )
        bad_layers = [
            item["shot_id"]
            for item in productions
            if not item["layers"]
            or (
                any(layer["role"] == "character" for layer in item["layers"])
                and not any(layer["role"] == "background" for layer in item["layers"])
            )
        ]
        gate(
            "layer_plan",
            not bad_layers,
            "every shot has a layer stack"
            if not bad_layers
            else "bad layers: " + ", ".join(bad_layers),
        )
        gate(
            "preset_style",
            bool(productions)
            and all(item["preset_id"] and item["style_id"] for item in productions),
            "preset and style recorded on every shot",
        )
        query_plans = {
            (artifact.get("operation") or {}).get("style_id")
            for artifact in self.repository.list_artifacts(project_id)
            if (artifact.get("operation") or {}).get("type") == "style_query_plan"
        }
        sourceable = [
            slot for slot in slots if slot.get("media_requirement") != "procedural"
        ]
        missing_queries = [
            slot["stable_key"]
            for slot in sourceable
            if slot.get("style_id") and slot["style_id"] not in query_plans
        ]
        gate(
            "style_query_plan",
            not missing_queries,
            "query plan per sourceable slot"
            if not missing_queries
            else "missing: " + ", ".join(missing_queries),
        )
        candidate_by_id = {
            candidate["id"]: candidate
            for candidate in self.repository.list_sourcing_candidates(project_id)
        }
        selected_ids = []
        for pick in picks["slots"]:
            for role in ("primary", "backup"):
                item = pick.get(role)
                if item:
                    selected_ids.append(item["id"])
        off_style = [
            candidate_id
            for candidate_id in selected_ids
            if candidate_by_id.get(candidate_id, {}).get("style_fit", 0) <= 0
        ]
        gate(
            "style_fit",
            not off_style,
            "selected candidates fit the style"
            if not off_style
            else "off-style selected: " + ", ".join(off_style),
        )
        bad_exact = []
        for production in productions:
            for item in production.get("exact_items", ()):
                source = (production.get("asset_source") or {}).get(item)
                if not source or source in {"generated", "stock_photo"}:
                    bad_exact.append(f"{production['shot_id']}:{item}")
        gate(
            "exact_items",
            not bad_exact,
            "exact items have approved sources"
            if not bad_exact
            else "unsourced: " + ", ".join(bad_exact),
        )
        missing_fallback = [
            production["shot_id"]
            for production in productions
            if not production.get("fallback")
        ]
        gate(
            "shot_fallback",
            not missing_fallback,
            "every shot has a fallback"
            if not missing_fallback
            else "missing: " + ", ".join(missing_fallback),
        )
        try:
            script = self.require_script_binding(project_id)
            gate(
                "script",
                True,
                f"approved script {script['content_hash'][:12]} bound to "
                f"brief {script['brief_id']}",
            )
        except IntegrityError as error:
            gate("script", False, str(error))
        if productions:
            try:
                self.require_motion_plan(project_id)
                gate("motion_plan", True, "level, action, parts, secondary, camera")
            except IntegrityError as error:
                gate("motion_plan", False, str(error))
            try:
                budget = self.require_motion_budget(project_id)
                gate(
                    "motion_budget",
                    True,
                    f"{len(budget['motion_budget']['shots'])} shots, "
                    f"{budget['motion_budget']['total_minor']} minor",
                )
            except IntegrityError as error:
                gate("motion_budget", False, str(error))
        try:
            delivery = self.require_delivery_metadata(project_id)
            gate(
                "delivery_metadata",
                True,
                f"{len(delivery['credits'])} credit lines",
            )
        except ConflictError as error:
            gate("delivery_metadata", False, str(error))
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
            motion_report = self.final_motion_report(project_id, artifact_id)
            gate(
                "final_motion",
                motion_report["passed"],
                "verified character motion in the final render"
                if motion_report["passed"]
                else "final render failed motion verification",
            )
            continuity = self.motion_continuity_report(
                project_id,
                artifact_id,
                loop_required=self.loop_is_required(project_id),
            )
            gate(
                "motion_continuity",
                continuity["passed"],
                (
                    f"position {continuity['checks']['position']['value']}, "
                    f"scale {continuity['checks']['scale']['value']}, "
                    f"colour {continuity['checks']['colour']['value']}, "
                    f"identity {continuity['checks']['identity']['value']}, "
                    f"loop {continuity['checks']['loop']['value'] if continuity['checks']['loop']['required'] else 'n/a'}"
                )
                if continuity["passed"]
                else "motion continuity failed",
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

    def require_qa_pass(
        self, project_id: str, artifact_id: str
    ) -> list[dict[str, Any]]:
        checks = [
            result
            for result in self.repository.list_qa_results(project_id)
            if result["artifact_id"] == artifact_id
        ]
        if not checks:
            raise ApprovalRequiredError(
                "Animatic approval requires recorded QA for the exact render"
            )
        if any(result["status"] == "fail" for result in checks):
            raise ApprovalRequiredError(
                "Animatic approval cannot use a render with failing QA"
            )
        return checks

    def latest_animatic_render(self, project_id: str) -> dict[str, Any] | None:
        renders = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if (artifact.get("operation") or {}).get("type") == "composition_render"
        ]
        if not renders:
            return None
        return max(renders, key=lambda item: item["created_at"])

    def final_motion_report(self, project_id: str, artifact_id: str) -> dict[str, Any]:
        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")
        path = self.artifact_store.resolve(project_id, artifact["relative_path"])
        shots = self.repository.list_shots(project_id)
        rows = []
        start = 0.0
        for shot in shots:
            duration = float(shot["duration_seconds"])
            report = verify_video_segment(
                str(path), start_seconds=start, duration_seconds=duration
            )
            rows.append(
                {
                    "shot_id": shot["id"],
                    "stable_key": shot["stable_key"],
                    "start_seconds": start,
                    "duration_seconds": duration,
                    **report,
                }
            )
            start += duration
        return {
            "artifact_id": artifact_id,
            "shots": rows,
            "passed": bool(rows) and all(row["verdict"] == "pass" for row in rows),
        }

    def require_final_motion(self, project_id: str, artifact_id: str) -> dict[str, Any]:
        report = self.final_motion_report(project_id, artifact_id)
        if not report["passed"]:
            failed = [
                row["stable_key"] for row in report["shots"] if row["verdict"] != "pass"
            ]
            raise ApprovalRequiredError(
                "Final render has no verified character motion: " + ", ".join(failed)
            )
        return report

    def motion_continuity_report(
        self,
        project_id: str,
        artifact_id: str,
        *,
        loop_required: bool = False,
    ) -> dict[str, Any]:
        """Check identity, scale, position, colour and loop continuity."""

        from math import dist

        from ..motion_verify import loop_seam

        report = self.final_motion_report(project_id, artifact_id)
        rows = report["shots"]
        missing = [
            row["stable_key"]
            for row in rows
            if row.get("centroid") is None
            or row.get("bbox") is None
            or row.get("dominant_color") is None
        ]
        centroids = [row.get("centroid") for row in rows]
        boxes = [row.get("bbox") for row in rows]
        colours = [row.get("dominant_color") for row in rows]

        drift = 0.0
        if len(centroids) > 1 and all(centroids):
            for index in range(1, len(centroids)):
                dx = centroids[index][0] - centroids[index - 1][0]
                dy = centroids[index][1] - centroids[index - 1][1]
                drift = max(drift, (dx * dx + dy * dy) ** 0.5)

        box_areas = [box[2] * box[3] for box in boxes if box]
        scale_ratio = (
            max(box_areas) / min(box_areas) if box_areas and min(box_areas) > 0 else 0.0
        )
        aspects = [box[2] / box[3] for box in boxes if box and box[3] > 0]
        aspect_delta = (
            max(aspects) / min(aspects) if aspects and min(aspects) > 0 else 1.0
        )

        colour_distance = 0.0
        if len(colours) > 1 and all(colours):
            colour_distance = max(
                dist(colours[left], colours[right]) / 441.67295593
                for left in range(len(colours))
                for right in range(left + 1, len(colours))
            )

        seam = (
            loop_seam(
                str(
                    self.artifact_store.resolve(
                        project_id,
                        self.repository.get_artifact(artifact_id)["relative_path"],
                    )
                )
            )
            if loop_required
            else {"seam": None}
        )

        checks = {
            "position": {
                "passed": drift <= 0.35,
                "value": round(drift, 4),
                "threshold": 0.35,
            },
            "scale": {
                "passed": scale_ratio <= 3.0,
                "value": round(scale_ratio, 4),
                "threshold": 3.0,
            },
            "colour": {
                "passed": colour_distance <= 0.22,
                "value": round(colour_distance, 4),
                "threshold": 0.22,
            },
            "identity": {
                "passed": aspect_delta <= 1.35
                and colour_distance <= 0.22
                and scale_ratio <= 3.0,
                "value": round(aspect_delta, 4),
                "threshold": 1.35,
                "components": {
                    "aspect_delta": round(aspect_delta, 4),
                    "colour_distance": round(colour_distance, 4),
                    "scale_ratio": round(scale_ratio, 4),
                },
            },
            "loop": {
                "required": loop_required,
                "passed": (
                    None
                    if not loop_required
                    else seam.get("seam") is not None and seam["seam"] <= 0.12
                ),
                "value": seam.get("seam"),
                "threshold": 0.12,
            },
        }
        required_checks = ("position", "scale", "colour", "identity")
        passed = (
            not missing
            and all(checks[name]["passed"] for name in required_checks)
            and (not loop_required or bool(checks["loop"]["passed"]))
        )
        return {
            "artifact_id": artifact_id,
            "shots": rows,
            "centroid_drift": round(drift, 4),
            "area_ratio": round(scale_ratio, 4),
            "colour_distance": round(colour_distance, 4),
            "loop_required": loop_required,
            "checks": checks,
            "missing_motion": missing,
            "passed": passed,
        }

    def loop_is_required(self, project_id: str) -> bool:
        """A seamless-loop recipe makes the loop seam a release gate."""

        creative = self.current_creative_plan(project_id)
        if not creative:
            return False
        return creative["plan"].recipe_selection.recipe.id == "seamless-loop"

    def require_motion_continuity(
        self,
        project_id: str,
        artifact_id: str,
        *,
        loop_required: bool = False,
    ) -> dict[str, Any]:
        report = self.motion_continuity_report(
            project_id, artifact_id, loop_required=loop_required
        )
        if not report["passed"]:
            failed = [
                name
                for name, check in report["checks"].items()
                if check["passed"] is False
            ]
            raise ApprovalRequiredError(
                "Motion continuity failed: "
                f"checks={failed}, "
                f"drift={report['centroid_drift']}, "
                f"area_ratio={report['area_ratio']}, "
                f"colour={report['colour_distance']}, "
                f"missing={report['missing_motion']}"
            )
        return report

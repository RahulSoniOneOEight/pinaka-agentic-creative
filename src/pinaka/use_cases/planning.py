from __future__ import annotations

import json
from typing import Any

from ..enums import (
    ArtifactKind,
)
from ..errors import (
    ConfigurationError,
    IntegrityError,
)
from ..models import (
    ArtifactCreate,
    CreativeElementCreate,
    DependencyRef,
    ShotCreate,
    SlotCreate,
    TextBriefRequest,
    WorkableBriefCreate,
)
from ..planning import (
    DEFAULT_DURATION_SECONDS,
    analyze_text,
    build_brief,
    propose_elements,
    propose_shots,
    scene_plan_markdown,
)
from ..policy.guards import (
    require_active_project,
)
from ..scripts import SuppliedScriptAdapter, script_prompt
from ..utils import canonical_json, sha256_bytes


class PlanningUseCases:
    def record_scene_plan(self, project_id: str, markdown: str) -> dict[str, Any]:
        """Store the SCENE_PLAN document and its hash for review."""

        self.repository.get_project(project_id)
        content = markdown.encode("utf-8")
        content_hash = sha256_bytes(content)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"inputs/SCENE_PLAN-{content_hash[:12]}.md",
            content=content,
            operation={"type": "scene_plan"},
        )
        plan = self.repository.record_scene_plan(
            project_id, artifact["id"], content_hash
        )
        return {
            "artifact_id": artifact["id"],
            "relative_path": artifact["relative_path"],
            "content_hash": content_hash,
            "plan": plan,
        }

    def scene_plan(self, project_id: str) -> dict[str, Any] | None:
        plan = self.repository.latest_scene_plan(project_id)
        if not plan:
            return None
        text = self.read_artifact_text(plan["artifact_id"])
        return {**plan, "markdown": text}

    def record_workable_brief(
        self, project_id: str, request: WorkableBriefCreate
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        require_active_project(
            project,
            message=f"Cannot change the workable brief for a {project.status.value} project",
        )
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "workable_brief"
        )
        return self.repository.record_workable_brief(project_id, request)

    def workable_brief(self, project_id: str) -> dict[str, Any] | None:
        return self.repository.latest_workable_brief(project_id)

    def plan_from_text(
        self, project_id: str, request: TextBriefRequest
    ) -> dict[str, Any]:
        """Preserve raw text and derive a governed workable brief from it."""

        project = self.repository.get_project(project_id)
        require_active_project(
            project, message=f"Cannot plan a {project.status.value} project"
        )
        self._reject_sensitive_payload({"text": request.text}, "text_brief")
        content = request.text.encode("utf-8")
        digest = sha256_bytes(content)
        stored = self.artifact_store.write_bytes(
            project_id, f"inputs/brief-source-{digest[:12]}.txt", content
        )
        artifact = self.repository.artifact_by_path(project_id, stored.relative_path)
        if artifact is None:
            artifact = self.repository.register_artifact(
                project_id,
                ArtifactCreate(
                    kind=ArtifactKind.ORIGINAL,
                    relative_path=stored.relative_path,
                    content_hash=stored.content_hash,
                    operation={
                        "type": "text_brief_source",
                        "size_bytes": stored.size_bytes,
                    },
                    immutable=True,
                ),
            )
        analysis = analyze_text(request)
        duration = analysis["duration_seconds"] or DEFAULT_DURATION_SECONDS
        brief_request = build_brief(artifact, analysis, project)
        brief = self.record_workable_brief(project_id, brief_request)
        shots: list[dict[str, Any]] = []
        scene_plan = None
        skipped_reason = None
        if request.create_plan:
            if self.repository.list_shots(project_id):
                skipped_reason = "project already has shots; plan not recreated"
            else:
                for spec in propose_shots(analysis, brief_request.beats):
                    shots.append(self.add_shot(project_id, ShotCreate(**spec)))
                scene_plan = self.record_scene_plan(
                    project_id, scene_plan_markdown(shots, duration)
                )
        proposed_slots = [
            {
                "shot_id": shot["id"],
                "stable_key": f"{shot['stable_key']}.visual",
                "role": "primary visual",
                "identity_requirement": "Original, non-protected subject",
                "fact_requirement": "Matches the planned shot requirement",
                "transformation_permission": "Composition and gentle animation",
                "approved_sources": [
                    "owned",
                    "official_api",
                    "manual_import",
                    "deterministic",
                    "generated",
                ],
            }
            for shot in shots
        ]
        recorded_slots: list[dict[str, Any]] = []
        if request.persist_slots:
            for spec in proposed_slots:
                recorded_slots.append(self.add_slot(project_id, SlotCreate(**spec)))
        production_contract = (
            self.record_production_contract(project_id) if request.create_plan else None
        )
        proposed_elements = propose_elements(request.text)
        recorded_elements: list[dict[str, Any]] = []
        if request.persist_elements:
            for spec in proposed_elements:
                recorded_elements.append(
                    self.repository.record_creative_element(
                        project_id, CreativeElementCreate(**spec)
                    )
                )
        return {
            "source_artifact": artifact,
            "analysis": {
                **analysis,
                "duration_seconds": str(duration),
            },
            "brief": brief,
            "shots": shots,
            "scene_plan": scene_plan,
            "plan_created": scene_plan is not None,
            "plan_skipped_reason": skipped_reason,
            "proposed_slots": proposed_slots,
            "slots": recorded_slots,
            "production_contract": production_contract,
            "proposed_elements": proposed_elements,
            "recorded_elements": recorded_elements,
        }

    def draft_script(
        self,
        project_id: str,
        *,
        duration_seconds: float | None = None,
        max_words: int | None = None,
        adapter: Any | None = None,
        model: str | None = None,
        script: str | None = None,
    ) -> dict[str, Any]:
        """Draft a short script from the brief; the human still approves it."""

        project = self.repository.get_project(project_id)
        require_active_project(
            project,
            message=f"Cannot draft a script for a {project.status.value} project",
        )
        brief = self.repository.latest_workable_brief(project_id)
        if not brief:
            raise IntegrityError("A current workable brief is required")
        package = brief["package"]
        seconds = float(
            duration_seconds
            if duration_seconds is not None
            else project.duration_seconds
        )
        words = int(
            max_words if max_words is not None else max(12, round(seconds * 2.4))
        )
        if seconds <= 0 or words < 5:
            raise IntegrityError(
                "Script duration must be positive and max_words at least 5"
            )
        supplied = next(
            (
                decision["value"]
                for decision in package["decisions"]
                if decision["field"] == "script.spoken_copy"
                and isinstance(decision["value"], str)
            ),
            None,
        )
        prompt = script_prompt(
            objective=package.get("objective", project.objective),
            audience=package.get("audience", project.audience),
            duration_seconds=seconds,
            max_words=words,
            supplied_copy=supplied,
        )
        if adapter is None and not (script or supplied or "").strip():
            raise ConfigurationError(
                "Supply script copy or a ScriptAdapter; no hosted model is called implicitly"
            )
        text = (
            (adapter or SuppliedScriptAdapter(script or supplied or ""))
            .draft(prompt=prompt, max_words=words)
            .strip()
        )
        if not text or len(text.split()) > words:
            raise IntegrityError(
                "Script proposal is empty or exceeds max_words; revise it rather than silently truncating"
            )
        content = text.encode("utf-8")
        digest = sha256_bytes(content)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"inputs/script-draft-{digest[:12]}.md",
            content=content,
            operation={
                "type": "script_draft",
                "model": model or getattr(adapter, "profile_key", "supplied-copy"),
                "duration_seconds": seconds,
                "max_words": words,
                "words": len(text.split()),
                "provider": "supplied-copy" if adapter is None else "injected-adapter",
            },
            dependencies=(
                DependencyRef(entity_type="workable_brief", entity_id=brief["id"]),
            ),
        )
        return {
            "text": text,
            "artifact": artifact,
            "words": len(text.split()),
            "max_words": words,
            "requires_approval": True,
        }

    def record_production_contract(self, project_id: str) -> dict[str, Any]:
        """Freeze the approved brief, shots and slots as the production contract."""

        brief = self.repository.latest_workable_brief(project_id)
        if not brief:
            raise IntegrityError("A production contract requires a workable brief")
        shots = self.repository.list_shots(project_id)
        slots = self.repository.list_slots(project_id)
        package = brief["package"]
        body = {
            "schema": "production-contract/v1",
            "project_id": project_id,
            "brief_id": brief["id"],
            "brief_content_hash": brief["content_hash"],
            "source_artifact_id": brief["source_artifact_id"],
            "source_content_hash": brief["source_content_hash"],
            "objective": package["objective"],
            "audience": package["audience"],
            "deliverable": package["deliverable"],
            "beats": package["beats"],
            "acceptance": package["acceptance"],
            "shots": shots,
            "slots": slots,
            "policies": {
                "sourcing_order": [
                    "owned",
                    "stock",
                    "manual_import",
                    "deterministic",
                    "generated",
                ],
                "generation_last": True,
                "two_pick_required": True,
                "stills_to_motion_minimum": "M1",
                "ab_required": True,
                "flow_ledger_required": True,
            },
        }
        content = canonical_json(body).encode("utf-8")
        content_hash = sha256_bytes(content)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"contracts/production-contract-{content_hash[:12]}.json",
            content=content,
            operation={
                "type": "production_contract",
                "brief_content_hash": brief["content_hash"],
                "shot_count": len(shots),
                "slot_count": len(slots),
            },
        )
        return {
            "artifact": artifact,
            "contract": body,
            "contract_hash": content_hash,
        }

    def current_production_contract(self, project_id: str) -> dict[str, Any] | None:
        contracts = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if artifact["relative_path"].startswith("contracts/production-contract-")
        ]
        if not contracts:
            return None
        artifact = max(contracts, key=lambda item: item["created_at"])
        return {
            "artifact": artifact,
            "contract": json.loads(self.read_artifact_text(artifact["id"])),
        }

    def require_production_contract(self, project_id: str) -> dict[str, Any] | None:
        """Paid work must match the frozen brief. No brief means no extra gate."""

        brief = self.repository.latest_workable_brief(project_id)
        if not brief:
            return None
        current = self.current_production_contract(project_id)
        if not current:
            raise IntegrityError(
                "A workable brief exists but no production contract was frozen. "
                "Run plan-from-text / record the production contract before spending."
            )
        if current["contract"]["brief_content_hash"] != brief["content_hash"]:
            raise IntegrityError(
                "The workable brief changed after the production contract was "
                "frozen; re-approve the brief and freeze a new contract"
            )
        return current

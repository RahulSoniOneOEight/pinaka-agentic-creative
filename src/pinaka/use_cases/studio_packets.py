from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..enums import (
    ApprovalStage,
    ArtifactKind,
    CapabilityStatus,
    ToolIntegrationMode,
)
from ..errors import (
    ApprovalRequiredError,
    BudgetExceededError,
    ConflictError,
    IntegrityError,
    NotFoundError,
)
from ..models import (
    CapabilityResult,
    DependencyRef,
    JobRequest,
    Money,
    StudioReadinessReport,
    StudioSpendApprovalCreate,
    StudioWorkPacketRequest,
    ToolOperationRequest,
)
from ..policy.guards import (
    require_active_project,
)
from ..references import (
    SUPPORTED_PROVIDERS,
    provider_reference_payload,
    reference_summary,
)
from ..utils import canonical_json, sha256_bytes, sha256_json, utc_now
from ._shared import (
    STUDIO_CAPABILITY_NAME,
    STUDIO_READINESS_TTL_SECONDS,
)


class StudioPacketsUseCases:
    @staticmethod
    def _studio_spend_scope_hash(
        project_id: str, request: StudioWorkPacketRequest
    ) -> str:
        payload = request.model_dump(mode="json")
        payload["spend_approval_id"] = None
        return sha256_json({"project_id": project_id, "work_packet": payload})

    def record_studio_readiness(self, report: StudioReadinessReport) -> dict[str, Any]:
        """Record one live Studio readiness probe for this session."""

        self._reject_sensitive_payload(
            report.model_dump(mode="json"), "studio_readiness"
        )
        self.repository.record_capability(
            CapabilityResult(
                name=STUDIO_CAPABILITY_NAME,
                status=report.status,
                detail=json.dumps(report.model_dump(mode="json"), sort_keys=True),
                checked_at=utc_now(),
            )
        )
        return self.studio_status()

    def studio_status(self) -> dict[str, Any]:
        """Report whether Studio has been verified recently enough to use."""

        latest = self.repository.latest_capability(STUDIO_CAPABILITY_NAME)
        if not latest:
            return {
                "ready": False,
                "stale": True,
                "age_seconds": None,
                "ttl_seconds": STUDIO_READINESS_TTL_SECONDS,
                "status": None,
                "checked_at": None,
                "report": None,
                "reason": "Studio readiness has never been checked in this home",
            }
        age = None
        try:
            stamp = datetime.fromisoformat(latest["checked_at"].replace("Z", "+00:00"))
            age = int((datetime.now(timezone.utc) - stamp).total_seconds())
        except (AttributeError, ValueError):
            stamp = None
        stale = age is None or age > STUDIO_READINESS_TTL_SECONDS
        report = json.loads(latest["detail"]) if latest["detail"] else None
        healthy = latest["status"] in {
            CapabilityStatus.WORKING.value,
            CapabilityStatus.CONFIGURED.value,
        }
        ready = healthy and not stale
        return {
            "ready": ready,
            "stale": stale,
            "age_seconds": age,
            "ttl_seconds": STUDIO_READINESS_TTL_SECONDS,
            "status": latest["status"],
            "checked_at": latest["checked_at"],
            "report": report,
            "reason": None
            if ready
            else (
                "The last Studio check is stale; run pinaka studio-check again"
                if stale
                else "Studio reported a non-ready status"
            ),
        }

    def approve_studio_spend(
        self, project_id: str, request: StudioSpendApprovalCreate
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        require_active_project(
            project,
            message=f"Cannot approve Studio spend for a {project.status.value} project",
        )
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "studio_spend_approval"
        )
        maximum = self._resolve_run_cap(project, request.work_packet.scope)
        if maximum.minor <= self._auto_run_cap_minor(maximum.currency):
            raise IntegrityError(
                "A run within the automatic limit does not need spend approval"
            )
        return self.repository.record_studio_spend_approval(
            project_id,
            scope_hash=self._studio_spend_scope_hash(project_id, request.work_packet),
            max_cost=maximum,
            approved_by=request.approved_by,
            reason=request.reason,
        )

    @staticmethod
    def _blocked_studio_decisions(
        brief: dict[str, Any],
        request: StudioWorkPacketRequest,
        shots: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        phases = {phase.value for phase in request.scope.phases}
        selected_shots = set(request.scope.shot_ids)
        selected_shots.update(
            shot["stable_key"] for shot in shots if shot["id"] in selected_shots
        )
        blocked: list[dict[str, Any]] = []
        for decision in brief["package"]["decisions"]:
            if decision["state"] != "blocked":
                continue
            affected = set(decision["blocked_work"])
            affects_phase = bool(affected & phases) or bool(affected & {"all", "*"})
            affects_prefixed_phase = any(
                item.split(":", 1)[0] in phases for item in affected
            )
            affects_shot = bool(affected & selected_shots)
            if affects_phase or affects_prefixed_phase or affects_shot:
                blocked.append(decision)
        return blocked

    def prepare_studio_work_packet(
        self, project_id: str, request: StudioWorkPacketRequest
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        require_active_project(
            project,
            message=f"Cannot authorize Studio work for a {project.status.value} project",
        )
        profile = self.repository.get_tool_profile(request.tool_profile_id)
        if profile["integration_mode"] not in {
            ToolIntegrationMode.MCP.value,
            ToolIntegrationMode.SPECIALIST_HANDOFF.value,
        }:
            raise IntegrityError(
                "Studio work requires an MCP or specialist-handoff tool profile"
            )
        operation_name = "openmontage.execute_work_packet"
        if operation_name not in profile["supported_operations"]:
            raise IntegrityError(
                f"Tool profile {profile['stable_key']} does not support {operation_name}"
            )
        readiness = self.studio_status()
        if not readiness["ready"]:
            raise ApprovalRequiredError(
                "Studio readiness must be verified before authorizing work: "
                f"{readiness['reason']}"
            )
        if request.required_approval_stage:
            approval = self.repository.active_approval(
                project_id, request.required_approval_stage.value
            )
            if not approval:
                raise ApprovalRequiredError(
                    f"Active {request.required_approval_stage.value} approval is required"
                )
        maximum_cost = self._resolve_run_cap(project, request.scope)
        auto_cap_minor = self._auto_run_cap_minor(maximum_cost.currency)
        approval_scope_hash = self._studio_spend_scope_hash(project_id, request)
        existing_for_request = self.repository.tool_operation_by_idempotency(
            project_id, request.tool_profile_id, request.idempotency_key
        )
        if maximum_cost.minor:
            available_minor = (
                project.max_budget.minor - project.spent.minor - project.reserved.minor
            )
            if not existing_for_request and maximum_cost.minor > available_minor:
                raise BudgetExceededError(
                    f"Insufficient budget: requested {maximum_cost.minor} minor units, "
                    f"available {available_minor}"
                )
            if maximum_cost.minor > auto_cap_minor:
                if not request.spend_approval_id:
                    raise ApprovalRequiredError(
                        "This Studio run exceeds the automatic per-run limit and "
                        "requires human spend approval"
                    )
                spend_approval = self.repository.get_studio_spend_approval(
                    request.spend_approval_id
                )
                if (
                    spend_approval["project_id"] != project_id
                    or spend_approval["scope_hash"] != approval_scope_hash
                    or spend_approval["max_cost_minor"] != maximum_cost.minor
                    or spend_approval["currency"] != maximum_cost.currency
                ):
                    raise ApprovalRequiredError(
                        "Studio spend approval does not match this exact work request"
                    )
                if spend_approval["consumed_operation_id"] and (
                    not existing_for_request
                    or existing_for_request["id"]
                    != spend_approval["consumed_operation_id"]
                ):
                    raise ConflictError(
                        "Studio spend approval has already been consumed"
                    )
            elif request.spend_approval_id:
                raise IntegrityError(
                    "Studio runs within the automatic limit do not use a spend approval"
                )
        elif request.spend_approval_id:
            raise IntegrityError("Zero-cost Studio runs do not use a spend approval")
        if existing_for_request:
            existing_scope_hash = existing_for_request["request"]["parameters"].get(
                "approval_scope_hash"
            )
            if existing_scope_hash != approval_scope_hash:
                raise ConflictError(
                    "Studio idempotency key was used for a different work request"
                )
            handoff = self.repository.specialist_handoff_for_operation(
                existing_for_request["id"]
            )
            packet_artifact = self.repository.get_artifact(
                handoff["work_packet_artifact_id"]
            )
            packet = json.loads(self.read_artifact_text(packet_artifact["id"]))
            budget_job = (
                self.repository.get_job(handoff["budget_job_id"])
                if handoff["budget_job_id"]
                else None
            )
            return {
                "handoff": handoff,
                "operation": existing_for_request,
                "work_packet_artifact": packet_artifact,
                "work_packet_hash": handoff["work_packet_hash"],
                "packet": packet,
                "budget_job": (
                    budget_job.model_dump(mode="json") if budget_job else None
                ),
            }
        brief = self.repository.latest_workable_brief(project_id)
        if not brief:
            raise IntegrityError("A current workable brief is required for Studio work")
        all_shots = self.repository.list_shots(project_id)
        shots_by_id = {shot["id"]: shot for shot in all_shots}
        if request.scope.shot_ids:
            missing_shots = set(request.scope.shot_ids) - set(shots_by_id)
            if missing_shots:
                raise NotFoundError(
                    f"Studio scope contains unknown shots: {sorted(missing_shots)}"
                )
            shots = [shots_by_id[shot_id] for shot_id in request.scope.shot_ids]
        else:
            shots = all_shots
        blockers = self._blocked_studio_decisions(brief, request, shots)
        if blockers:
            fields = ", ".join(decision["field"] for decision in blockers)
            raise ApprovalRequiredError(
                f"Studio scope is blocked by non-delegable decisions: {fields}"
            )
        phases = {phase.value for phase in request.scope.phases}
        scene_plan = self.repository.latest_scene_plan(project_id)
        if (
            phases & {"storyboard", "composition", "render", "review"}
            and not scene_plan
        ):
            raise IntegrityError(
                "Storyboard, composition, render and review work require a scene plan"
            )
        if "render" in phases and maximum_cost.minor:
            storyboard = self.repository.active_approval(
                project_id, ApprovalStage.ANIMATIC.value
            )
            if not storyboard:
                raise ApprovalRequiredError(
                    "Paid render work requires an approved storyboard/animatic first"
                )
        all_elements = self.repository.list_creative_elements(project_id)
        elements_by_id = {element["id"]: element for element in all_elements}
        if request.scope.element_ids:
            missing_elements = set(request.scope.element_ids) - set(elements_by_id)
            if missing_elements:
                raise NotFoundError(
                    "Studio scope contains unknown or superseded elements: "
                    f"{sorted(missing_elements)}"
                )
            elements = [
                elements_by_id[element_id] for element_id in request.scope.element_ids
            ]
        else:
            elements = all_elements
        selected_shot_ids = {shot["id"] for shot in shots}
        selected_element_ids = {element["id"] for element in elements}
        bindings = [
            binding
            for binding in self.repository.list_shot_element_bindings(project_id)
            if binding["shot_id"] in selected_shot_ids
            and binding["element_id"] in selected_element_ids
        ]
        references = []
        for reference in self.repository.list_shot_references(project_id):
            if reference["shot_id"] not in selected_shot_ids:
                continue
            artifact_id = reference["artifact_id"]
            if not artifact_id and reference["element_id"]:
                element = elements_by_id.get(reference["element_id"])
                artifact_id = element.get("source_artifact_id") if element else None
            if not artifact_id:
                references.append({**reference, "artifact": None})
                continue
            artifact = self.repository.get_artifact(artifact_id)
            references.append(
                {
                    **reference,
                    "artifact": {
                        "id": artifact["id"],
                        "relative_path": artifact["relative_path"],
                        "content_hash": artifact["content_hash"],
                    },
                }
            )
        reference_ids = [
            reference["artifact"]["id"]
            for reference in references
            if reference.get("artifact")
        ]
        slots = [
            slot
            for slot in self.repository.list_slots(project_id)
            if slot["shot_id"] in selected_shot_ids
        ]
        selected_slot_ids = {slot["id"] for slot in slots}
        slot_assets = [
            assignment
            for assignment in self.repository.list_slot_assets(project_id)
            if assignment["slot_id"] in selected_slot_ids
        ]
        if "render" in phases:
            gaps = []
            for slot in slots:
                roles = {
                    assignment["role"]
                    for assignment in slot_assets
                    if assignment["slot_id"] == slot["id"]
                }
                if not {"primary", "backup"} <= roles:
                    gaps.append(slot["stable_key"])
            if gaps:
                raise IntegrityError(
                    "Paid render requires a primary and backup asset for every slot: "
                    + ", ".join(sorted(gaps))
                )
        treatment_decisions = [
            decision
            for decision in self.repository.list_treatment_decisions(project_id)
            if decision["slot_id"] in selected_slot_ids
        ]
        audio_beds = self.repository.list_audio_beds(project_id)
        input_ids = [brief["source_artifact_id"], *request.input_artifact_ids]
        if scene_plan:
            input_ids.append(scene_plan["artifact_id"])
        input_ids.extend(
            element["source_artifact_id"]
            for element in elements
            if element["source_artifact_id"]
        )
        input_ids.extend(assignment["artifact_id"] for assignment in slot_assets)
        input_ids.extend(reference_ids)
        input_ids.extend(bed["artifact_id"] for bed in audio_beds)
        input_ids = list(dict.fromkeys(input_ids))
        input_artifacts = []
        for artifact_id in input_ids:
            artifact = self.repository.get_artifact(artifact_id)
            if artifact["project_id"] != project_id:
                raise IntegrityError(
                    f"Studio input artifact is not in the project: {artifact_id}"
                )
            input_artifacts.append(
                {
                    "id": artifact["id"],
                    "kind": artifact["kind"],
                    "relative_path": artifact["relative_path"],
                    "content_hash": artifact["content_hash"],
                }
            )
        packet = {
            "schema": "pinaka.openmontage.work-packet/v1",
            "project": {
                "id": project.id,
                "name": project.name,
                "objective": project.objective,
                "audience": project.audience,
                "output_format": project.output_format,
                "language": project.language,
                "duration_seconds": str(project.duration_seconds),
                "budget": {
                    "maximum": project.max_budget.model_dump(mode="json"),
                    "reserved": project.reserved.model_dump(mode="json"),
                    "spent": project.spent.model_dump(mode="json"),
                    "this_run_cap": maximum_cost.model_dump(mode="json"),
                },
            },
            "brief": brief,
            "scene_plan": scene_plan,
            "shots": shots,
            "elements": elements,
            "shot_element_bindings": bindings,
            "references": references,
            "reference_summary": reference_summary(references),
            "generated_motion": {
                "requested": request.request_generated_motion,
                "reason": request.generated_motion_reason,
                "providers": (
                    list(SUPPORTED_PROVIDERS)
                    if request.request_generated_motion
                    else []
                ),
            },
            "provider_reference_hints": (
                {
                    provider: provider_reference_payload(references, provider)
                    for provider in SUPPORTED_PROVIDERS
                }
                if request.request_generated_motion
                else {}
            ),
            "slots": slots,
            "slot_asset_assignments": slot_assets,
            "treatment_decisions": treatment_decisions,
            "audio_beds": audio_beds,
            "scope": request.scope.model_dump(mode="json"),
            "instructions": request.instructions,
            "input_artifacts": input_artifacts,
            "output_declaration": request.output_declaration,
            "controller_authority": {
                "system_of_record": "pinaka.CreativeController",
                "studio_review_is_advisory": True,
                "exact_hash_approval_required": True,
                "publication_authorized": False,
                "paid_generation_authorized": bool(maximum_cost.minor),
                "generated_motion_authorized": request.request_generated_motion,
                "max_credits": 0,
                "max_amount": str(maximum_cost.amount),
                "max_currency": maximum_cost.currency,
                "automatic_spend_limit": Money.from_minor(
                    auto_cap_minor, maximum_cost.currency
                ).model_dump(mode="json"),
                "spend_approval_id": request.spend_approval_id,
            },
        }
        packet_bytes = canonical_json(packet).encode("utf-8")
        packet_hash = sha256_bytes(packet_bytes)
        dependencies = [
            DependencyRef(entity_type="workable_brief", entity_id=brief["id"]),
            *(
                [DependencyRef(entity_type="scene_plan", entity_id=scene_plan["id"])]
                if scene_plan
                else []
            ),
            *(
                DependencyRef(entity_type="shot", entity_id=shot["id"])
                for shot in shots
            ),
            *(
                DependencyRef(entity_type="creative_element", entity_id=element["id"])
                for element in elements
            ),
            *(
                DependencyRef(entity_type="artifact", entity_id=artifact_id)
                for artifact_id in input_ids
            ),
        ]
        packet_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.CONTRACT,
            relative_path=(
                f"artifacts/openmontage/work-packets/{packet_hash[:12]}.json"
            ),
            content=packet_bytes,
            operation={
                "type": "openmontage_work_packet",
                "schema": packet["schema"],
            },
            dependencies=tuple(dependencies),
        )
        operation_request = ToolOperationRequest(
            tool_profile_id=request.tool_profile_id,
            operation=operation_name,
            idempotency_key=request.idempotency_key,
            parameters={
                "work_packet_artifact_id": packet_artifact["id"],
                "work_packet_hash": packet_hash,
                "scope": request.scope.model_dump(mode="json"),
                "approval_scope_hash": approval_scope_hash,
                "spend_approval_id": request.spend_approval_id,
            },
            input_artifact_ids=(packet_artifact["id"], *input_ids),
            output_declaration=request.output_declaration,
            limits={
                "paid": bool(maximum_cost.minor),
                "max_cost_minor": maximum_cost.minor,
                "currency": maximum_cost.currency,
                "max_credits": 0,
                "allow_publication": False,
            },
            required_approval_stage=request.required_approval_stage,
        )
        budget_job = None
        if maximum_cost.minor:
            budget_request = JobRequest(
                provider="openmontage",
                endpoint="studio:production",
                operation=operation_name,
                idempotency_key=f"studio:{request.idempotency_key}",
                estimated_cost=maximum_cost,
                parameters={
                    "work_packet_artifact_id": packet_artifact["id"],
                    "work_packet_hash": packet_hash,
                    "approval_scope_hash": approval_scope_hash,
                    "spend_approval_id": request.spend_approval_id,
                },
                input_refs=(packet_artifact["id"], *input_ids),
            )
            operation_payload = operation_request.model_dump(mode="json")
            budget_payload = budget_request.model_dump(mode="json")
            operation, handoff, budget_job = self.repository.reserve_studio_work(
                project_id,
                tool_request=operation_request,
                tool_request_hash=sha256_json(operation_payload),
                budget_request=budget_request,
                budget_request_hash=sha256_json(budget_payload),
                work_packet_artifact_id=packet_artifact["id"],
                work_packet_hash=packet_hash,
                input_artifact_ids=tuple(input_ids),
                spend_approval_id=request.spend_approval_id,
                approval_scope_hash=approval_scope_hash,
                requires_spend_approval=(maximum_cost.minor > auto_cap_minor),
            )
        else:
            operation = self.reserve_tool_operation(project_id, operation_request)
            handoff = self.repository.record_specialist_handoff(
                project_id,
                tool_profile_id=request.tool_profile_id,
                operation_id=operation["id"],
                work_packet_artifact_id=packet_artifact["id"],
                work_packet_hash=packet_hash,
                input_artifact_ids=tuple(input_ids),
            )
        return {
            "handoff": handoff,
            "operation": operation,
            "work_packet_artifact": packet_artifact,
            "work_packet_hash": packet_hash,
            "packet": packet,
            "budget_job": (budget_job.model_dump(mode="json") if budget_job else None),
        }

    def studio_work_packet(self, operation_id: str) -> dict[str, Any]:
        handoff = self.repository.specialist_handoff_for_operation(operation_id)
        packet = json.loads(self.read_artifact_text(handoff["work_packet_artifact_id"]))
        return {"handoff": handoff, "packet": packet}

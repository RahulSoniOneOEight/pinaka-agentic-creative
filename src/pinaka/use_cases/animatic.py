from __future__ import annotations

import json
from typing import Any

from ..adapters.animatic import DeterministicAnimaticAdapter
from ..animatic import build_timeline
from ..enums import (
    ApprovalStage,
    ArtifactKind,
    CapabilityStatus,
    ProjectStatus,
    ToolIntegrationMode,
)
from ..errors import (
    ApprovalRequiredError,
    IntegrityError,
)
from ..models import (
    DependencyRef,
    ToolOperationRequest,
    ToolProfileCreate,
)
from ..utils import canonical_json


class AnimaticUseCases:
    def _animatic_operation(
        self,
        tool_profile_id: str,
        operation: str,
        deliverable: dict[str, Any],
        timeline: dict[str, Any],
        suffix: str,
        output_declaration: dict[str, Any],
    ) -> ToolOperationRequest:
        return ToolOperationRequest(
            tool_profile_id=tool_profile_id,
            operation=operation,
            idempotency_key=(
                f"animatic-{suffix}-{deliverable['stable_key']}-"
                f"{timeline['timeline_hash'][:12]}"
            ),
            parameters={"timeline": timeline},
            output_declaration=output_declaration,
            limits={"paid": False, "max_cost_minor": 0},
            required_approval_stage=ApprovalStage.SOURCING,
        )

    def _pilot_timeline_context(
        self, project_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        deliverables = self.repository.list_deliverables(project_id)
        if len(deliverables) != 1:
            raise IntegrityError(
                "Pilot animatic production requires exactly one deliverable"
            )
        deliverable = deliverables[0].model_dump(mode="json")
        segments = [
            segment
            for segment in self.repository.list_segments(project_id)
            if segment["deliverable_id"] == deliverable["id"]
        ]
        if not segments:
            raise IntegrityError("Pilot animatic production requires planned segments")

        bundles = {
            bundle["kind"]: bundle
            for bundle in self.repository.current_grounding_bundles(project_id)
        }
        missing = {"brand", "claims", "sources"} - set(bundles)
        if missing:
            raise IntegrityError(
                "Animatic production requires current grounding: "
                + ", ".join(sorted(missing))
            )

        try:
            timeline = build_timeline(
                project_id=project_id,
                deliverable=deliverable,
                segments=segments,
                brand_values=bundles["brand"]["values"],
                claims_values=bundles["claims"]["values"],
            )
        except ValueError as error:
            raise IntegrityError(f"Animatic timeline is invalid: {error}") from error
        return deliverable, segments, bundles, timeline

    def _latest_animatic_bundle(
        self, project_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        manifests = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if artifact["kind"] == ArtifactKind.PREVIEW.value
            and artifact["relative_path"].startswith("previews/animatic-manifest-")
        ]
        if not manifests:
            return None
        manifest_artifact = max(manifests, key=lambda item: item["created_at"])
        manifest = json.loads(self.read_artifact_text(manifest_artifact["id"]))
        artifacts = {
            name: self.repository.get_artifact(reference["id"])
            for name, reference in manifest["artifacts"].items()
        }
        return manifest_artifact, manifest, artifacts

    def produce_animatic(self, project_id: str) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status not in {
            ProjectStatus.SOURCING,
            ProjectStatus.ANIMATIC_READY,
        }:
            raise ApprovalRequiredError(
                "Animatic production requires an approved sourcing state"
            )
        if (
            project.status == ProjectStatus.SOURCING
            and not self.repository.active_approval(
                project_id, ApprovalStage.SOURCING.value
            )
        ):
            raise ApprovalRequiredError(
                "An active sourcing approval is required before animatic production"
            )

        deliverable, segments, bundles, timeline = self._pilot_timeline_context(
            project_id
        )
        key = timeline["timeline_hash"][:12]
        manifest_path = f"previews/animatic-manifest-{key}.json"
        existing_manifest_artifact = self.repository.artifact_by_path(
            project_id, manifest_path
        )
        if existing_manifest_artifact:
            manifest = json.loads(
                self.read_artifact_text(existing_manifest_artifact["id"])
            )
            artifacts = {
                name: self.repository.get_artifact(reference["id"])
                for name, reference in manifest["artifacts"].items()
            }
            if project.status == ProjectStatus.SOURCING:
                project = self.transition_project(
                    project_id,
                    ProjectStatus.ANIMATIC_READY,
                    reason="Deterministic animatic bundle already rendered",
                )
            return {
                "project": project.model_dump(mode="json"),
                "timeline": timeline,
                "artifacts": artifacts,
                "manifest_artifact": existing_manifest_artifact,
                "resumed": True,
                "next_action": self.next_action(project_id),
            }

        paths = {
            "timeline": f"artifacts/timeline-{key}.json",
            "animatic": f"designs/animatic-{key}.html",
            "captions": f"artifacts/captions-{key}.srt",
        }
        grounding_dependencies = tuple(
            DependencyRef(
                entity_type="grounding_bundle",
                entity_id=bundle["id"],
                entity_hash=bundle["content_hash"],
            )
            for bundle in bundles.values()
        )
        plan_dependencies = (
            DependencyRef(entity_type="deliverable", entity_id=deliverable["id"]),
            *(
                DependencyRef(entity_type="segment", entity_id=segment["id"])
                for segment in segments
            ),
        )

        tool = self.register_tool_profile(
            ToolProfileCreate(
                stable_key="deterministic-animatic",
                name="Deterministic animatic renderer",
                integration_mode=ToolIntegrationMode.NATIVE,
                executable_or_endpoint="python:pinaka.adapters.animatic",
                supported_operations=(
                    "render_timeline",
                    "render_animatic",
                    "render_captions",
                ),
                limits={"paid": False, "max_cost_minor": 0},
                capability_status=CapabilityStatus.WORKING,
            )
        )
        adapter = DeterministicAnimaticAdapter()
        timeline_operation = self._animatic_operation(
            tool["id"],
            "render_timeline",
            deliverable,
            timeline,
            "timeline",
            {"media_type": "application/json", "schema": "pinaka.animatic.v1"},
        )
        _, timeline_receipt, timeline_artifact = self.run_tool_once(
            project_id,
            timeline_operation,
            adapter,
            artifact_path=paths["timeline"],
            dependencies=(*plan_dependencies, *grounding_dependencies),
        )
        if timeline_artifact is None:
            raise IntegrityError(
                "Timeline rendering did not produce an artifact: "
                f"{timeline_receipt['status']}"
            )
        timeline_dependency = DependencyRef(
            entity_type="artifact",
            entity_id=timeline_artifact["id"],
            entity_hash=timeline_artifact["content_hash"],
        )

        animatic_operation = self._animatic_operation(
            tool["id"],
            "render_animatic",
            deliverable,
            timeline,
            "html",
            {"media_type": "text/html", "width": 1080, "height": 1920},
        )
        _, animatic_receipt, animatic_artifact = self.run_tool_once(
            project_id,
            animatic_operation,
            adapter,
            artifact_path=paths["animatic"],
            artifact_kind=ArtifactKind.DESIGN,
            dependencies=(timeline_dependency,),
        )
        if animatic_artifact is None:
            raise IntegrityError(
                "Animatic rendering did not produce an artifact: "
                f"{animatic_receipt['status']}"
            )

        captions_operation = self._animatic_operation(
            tool["id"],
            "render_captions",
            deliverable,
            timeline,
            "captions",
            {"media_type": "application/x-subrip", "language": "en"},
        )
        _, captions_receipt, captions_artifact = self.run_tool_once(
            project_id,
            captions_operation,
            adapter,
            artifact_path=paths["captions"],
            dependencies=(timeline_dependency,),
        )
        if captions_artifact is None:
            raise IntegrityError(
                "Caption rendering did not produce an artifact: "
                f"{captions_receipt['status']}"
            )

        manifest = {
            "schema_version": 1,
            "project_id": project_id,
            "bundle_key": key,
            "timeline_hash": timeline["timeline_hash"],
            "renderer_version": timeline["renderer_version"],
            "deliverable_id": deliverable["id"],
            "artifacts": {
                name: {
                    "id": artifact["id"],
                    "relative_path": artifact["relative_path"],
                    "content_hash": artifact["content_hash"],
                }
                for name, artifact in {
                    "timeline": timeline_artifact,
                    "animatic": animatic_artifact,
                    "captions": captions_artifact,
                }.items()
            },
        }
        manifest_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.PREVIEW,
            relative_path=manifest_path,
            content=canonical_json(manifest).encode("utf-8"),
            operation={
                "type": "animatic_manifest",
                "timeline_hash": timeline["timeline_hash"],
                "renderer_version": timeline["renderer_version"],
            },
            dependencies=(
                timeline_dependency,
                DependencyRef(
                    entity_type="artifact",
                    entity_id=animatic_artifact["id"],
                    entity_hash=animatic_artifact["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=captions_artifact["id"],
                    entity_hash=captions_artifact["content_hash"],
                ),
            ),
        )

        if project.status == ProjectStatus.SOURCING:
            project = self.transition_project(
                project_id,
                ProjectStatus.ANIMATIC_READY,
                reason="Deterministic animatic, timeline and draft captions produced",
            )
        return {
            "project": project.model_dump(mode="json"),
            "timeline": timeline,
            "artifacts": {
                "timeline": timeline_artifact,
                "animatic": animatic_artifact,
                "captions": captions_artifact,
            },
            "manifest_artifact": manifest_artifact,
            "receipts": {
                "timeline": timeline_receipt,
                "animatic": animatic_receipt,
                "captions": captions_receipt,
            },
            "resumed": False,
            "next_action": self.next_action(project_id),
        }

from __future__ import annotations

from typing import Any

from ..assets import (
    AssetCriteria,
)
from ..enums import (
    SlotAssetRole,
)
from ..models import (
    CreativeElementCreate,
    ShotElementBindingCreate,
    ShotReferenceCreate,
    SlotCreate,
)
from ..policy.guards import (
    require_active_project,
)
from ._shared import (
    _slot_accepts_element,
)


class ElementsUseCases:
    def record_creative_element(
        self, project_id: str, request: CreativeElementCreate
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        require_active_project(
            project,
            message=f"Cannot change creative elements for a {project.status.value} project",
        )
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "creative_element"
        )
        return self.repository.record_creative_element(project_id, request)

    def list_creative_elements(
        self, project_id: str, *, current_only: bool = True
    ) -> list[dict[str, Any]]:
        return self.repository.list_creative_elements(
            project_id, current_only=current_only
        )

    def bind_element_to_shot(
        self, project_id: str, request: ShotElementBindingCreate
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        require_active_project(
            project,
            message=f"Cannot bind creative elements for a {project.status.value} project",
        )
        return self.repository.bind_element_to_shot(project_id, request)

    def bind_shot_reference(
        self, project_id: str, request: ShotReferenceCreate
    ) -> dict[str, Any]:
        """Attach a governed reference image to a shot role."""

        project = self.repository.get_project(project_id)
        require_active_project(
            project,
            message=f"Cannot bind references for a {project.status.value} project",
        )
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "shot_reference"
        )
        return self.repository.upsert_shot_reference(project_id, request)

    def shot_references(
        self, project_id: str, *, shot_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self.repository.list_shot_references(project_id, shot_id=shot_id)

    def bind_elements_to_slots(self, project_id: str) -> dict[str, Any]:
        """Assign element artwork to matching slots so compositions pick it up."""

        project = self.repository.get_project(project_id)
        require_active_project(
            project, message=f"Cannot bind artwork for a {project.status.value} project"
        )
        elements = {
            element["id"]: element
            for element in self.repository.list_creative_elements(project_id)
        }
        slots = {slot["id"]: slot for slot in self.repository.list_slots(project_id)}
        bindings = self.repository.list_shot_element_bindings(project_id)
        assigned: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for binding in bindings:
            element = elements.get(binding["element_id"])
            if not element:
                continue
            artifact_id = element.get("source_artifact_id")
            if not artifact_id:
                skipped.append(
                    {
                        "element": element["stable_key"],
                        "reason": "element has no artwork artifact",
                    }
                )
                continue
            for slot in slots.values():
                if slot["shot_id"] != binding["shot_id"]:
                    continue
                if not _slot_accepts_element(slot["role"], element["kind"]):
                    continue
                existing = self.repository.slot_asset_by_role(
                    slot["id"], SlotAssetRole.PRIMARY.value
                )
                if existing:
                    skipped.append(
                        {
                            "element": element["stable_key"],
                            "slot": slot["stable_key"],
                            "reason": "slot already has a primary asset",
                        }
                    )
                    continue
                self.assign_slot_asset(
                    project_id,
                    slot["id"],
                    artifact_id=artifact_id,
                    role=SlotAssetRole.PRIMARY,
                    criteria=AssetCriteria(),
                    decided_by="controller:element-binding",
                )
                assigned.append(
                    {
                        "element": element["stable_key"],
                        "slot": slot["stable_key"],
                        "role": slot["role"],
                        "artifact_id": artifact_id,
                    }
                )
        return {
            "project_id": project_id,
            "assigned": assigned,
            "skipped": skipped,
            "assigned_count": len(assigned),
        }

    def derive_element_slots(self, project_id: str) -> dict[str, Any]:
        """Create the visual slots an element needs on every bound shot."""

        project = self.repository.get_project(project_id)
        require_active_project(
            project, message=f"Cannot derive slots for a {project.status.value} project"
        )
        elements = {
            element["id"]: element
            for element in self.repository.list_creative_elements(project_id)
        }
        shots = {shot["id"]: shot for shot in self.repository.list_shots(project_id)}
        existing = {
            slot["stable_key"] for slot in self.repository.list_slots(project_id)
        }
        created: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for binding in self.repository.list_shot_element_bindings(project_id):
            element = elements.get(binding["element_id"])
            shot = shots.get(binding["shot_id"])
            if not element or not shot:
                continue
            kind = element["kind"].lower()
            if kind not in {"character", "prop", "location", "world"}:
                skipped.append(
                    {"element": element["stable_key"], "reason": "not a visual element"}
                )
                continue
            role = "subject" if kind in {"character", "prop"} else "background"
            stable_key = f"element.{element['stable_key']}.{shot['stable_key']}"
            if stable_key in existing:
                skipped.append(
                    {"element": element["stable_key"], "reason": "slot already exists"}
                )
                continue
            specification = element.get("specification") or {}
            ceiling = str(specification.get("treatment_ceiling", "ai_created"))
            slot = self.add_slot(
                project_id,
                SlotCreate(
                    shot_id=shot["id"],
                    stable_key=stable_key,
                    role=role,
                    identity_requirement=(
                        f"Keep {element['name']} consistent with its reference"
                    ),
                    fact_requirement=(f"Preserve the approved {kind} specification"),
                    transformation_permission=(
                        "Crop, grade and animate within the approved treatment ceiling"
                    ),
                    approved_sources=(
                        "owned",
                        "official_api",
                        "manual_import",
                        "deterministic",
                        "generated",
                    ),
                    treatment_ceiling=ceiling,
                ),
            )
            existing.add(stable_key)
            created.append(
                {
                    "slot": slot,
                    "element": element["stable_key"],
                    "shot": shot["stable_key"],
                }
            )
        return {
            "project_id": project_id,
            "created": created,
            "created_count": len(created),
            "skipped": skipped,
        }

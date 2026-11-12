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

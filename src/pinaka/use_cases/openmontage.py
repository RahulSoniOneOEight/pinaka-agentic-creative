from __future__ import annotations

import json
from typing import Any

from ..contracts.openmontage import (
    CapabilityDiscoveryCreate,
    CapabilityRoute,
)
from ..enums import ArtifactKind
from ..errors import ApprovalRequiredError, NotFoundError
from ..utils import canonical_json, sha256_json


class OpenMontageUseCases:
    """Record a Studio capability snapshot and route a shot to Studio or fallback."""

    def record_capability_discovery(
        self, project_id: str, request: CapabilityDiscoveryCreate
    ) -> dict[str, Any]:
        self.repository.get_project(project_id)
        body = {
            "schema": "openmontage-capabilities/v1",
            "project_id": project_id,
            **request.model_dump(mode="json"),
        }
        content = canonical_json(body).encode("utf-8")
        digest = sha256_json(body)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.REFERENCE,
            relative_path=f"studio/capabilities-{digest[:12]}.json",
            content=content,
            operation={
                "type": "openmontage_capabilities",
                "account_ready": request.account_ready,
                "credits": request.credits,
                "capabilities": len(request.capabilities),
                "source": request.source,
                "execution_owner": "external",
                "studio_role": "none",
            },
        )
        return {"artifact": artifact, "discovery": body}

    def current_capability_discovery(self, project_id: str) -> dict[str, Any] | None:
        candidates = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if (artifact.get("operation") or {}).get("type")
            == "openmontage_capabilities"
        ]
        if not candidates:
            return None
        artifact = candidates[-1]
        path = self.artifact_store.resolve(project_id, artifact["relative_path"])
        return {
            "artifact": artifact,
            "discovery": json.loads(path.read_text(encoding="utf-8")),
        }

    def route_capability(self, project_id: str, capability_id: str) -> CapabilityRoute:
        current = self.current_capability_discovery(project_id)
        if not current:
            raise ApprovalRequiredError(
                "No OpenMontage capability snapshot is recorded"
            )
        discovery = current["discovery"]
        capability = next(
            (
                item
                for item in discovery["capabilities"]
                if item["capability_id"] == capability_id
            ),
            None,
        )
        if not capability:
            raise NotFoundError(f"OpenMontage capability not found: {capability_id}")
        ready = bool(discovery["account_ready"] and capability["available"])
        if ready:
            return CapabilityRoute(
                capability_id=capability_id,
                ready=True,
                action="studio",
                fallback=capability["fallback"],
                reason="Studio is ready and the capability is available",
            )
        reasons = []
        if not discovery["account_ready"]:
            reasons.append("Studio account is not ready")
        if not capability["available"]:
            reasons.append("capability is marked unavailable")
        return CapabilityRoute(
            capability_id=capability_id,
            ready=False,
            action="fallback",
            fallback=capability["fallback"],
            reason="; ".join(reasons) or "fallback selected",
        )

    def require_capability_ready(
        self, project_id: str, capability_id: str
    ) -> CapabilityRoute:
        route = self.route_capability(project_id, capability_id)
        if not route.ready:
            raise ApprovalRequiredError(
                f"OpenMontage capability {capability_id} is not ready: {route.reason}"
            )
        return route

from __future__ import annotations

import json
from typing import Any

from ..contracts.production_plan import ProductionPlanCreate
from ..enums import ArtifactKind
from ..errors import ApprovalRequiredError, ConflictError, IntegrityError
from ..utils import canonical_json, sha256_json


class ProductionPlanUseCases:
    """Freeze and verify one executable production plan."""

    def record_production_plan(
        self, project_id: str, request: ProductionPlanCreate
    ) -> dict[str, Any]:
        self.repository.get_project(project_id)
        shots = self.repository.list_shots(project_id)
        if not shots:
            raise IntegrityError("A production plan requires a shot plan")
        shot_keys = {shot["stable_key"] for shot in shots}
        directed = {shot.shot_key for shot in request.shot_directions}
        unknown = directed - shot_keys
        if unknown:
            raise ConflictError(
                "Production plan references unknown shots: "
                + ", ".join(sorted(unknown))
            )
        missing = shot_keys - directed
        if missing:
            raise ConflictError(
                "Production plan is missing shot directions: "
                + ", ".join(sorted(missing))
            )
        body = {
            "schema": "production-plan/v1",
            "project_id": project_id,
            **request.model_dump(mode="json"),
        }
        content = canonical_json(body).encode("utf-8")
        digest = sha256_json(body)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.CONTRACT,
            relative_path=f"production-plans/plan-{digest[:12]}.json",
            content=content,
            operation={
                "type": "production_plan",
                "schema": "production-plan/v1",
                "production_format": request.production_format,
                "visual_style": request.visual_style,
                "delivery_format": request.delivery_format,
                "production_method": request.production_method,
                "shot_count": len(request.shot_directions),
                "beat_count": len(request.beats),
                "asset_count": len(request.asset_manifest),
                "duration_seconds": request.rough_cut.duration_seconds,
                "execution_owner": "local",
                "studio_role": "none",
            },
        )
        return {
            "artifact": artifact,
            "plan": body,
            "status": self.production_plan_status(project_id),
        }

    def current_execution_plan(self, project_id: str) -> dict[str, Any] | None:
        candidates = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if (artifact.get("operation") or {}).get("type") == "production_plan"
        ]
        if not candidates:
            return None
        artifact = candidates[-1]
        path = self.artifact_store.resolve(project_id, artifact["relative_path"])
        return {
            "artifact": artifact,
            "plan": json.loads(path.read_text(encoding="utf-8")),
        }

    def production_plan_status(self, project_id: str) -> dict[str, Any]:
        current = self.current_execution_plan(project_id)
        if not current:
            return {
                "project_id": project_id,
                "ready": False,
                "issues": ["no production plan recorded"],
                "warnings": [],
                "plan": None,
            }
        plan = current["plan"]
        shots = self.repository.list_shots(project_id)
        shot_keys = {shot["stable_key"] for shot in shots}
        direction_keys = {
            direction["shot_key"] for direction in plan.get("shot_directions", ())
        }
        issues: list[str] = []
        warnings: list[str] = []
        missing_directions = shot_keys - direction_keys
        extra_directions = direction_keys - shot_keys
        if missing_directions:
            issues.append(
                "missing shot directions: " + ", ".join(sorted(missing_directions))
            )
        if extra_directions:
            issues.append(
                "unknown shot directions: " + ", ".join(sorted(extra_directions))
            )
        missing_assets = [
            asset["asset_id"]
            for asset in plan.get("asset_manifest", ())
            if asset.get("approval_status") == "missing"
        ]
        if missing_assets:
            issues.append("assets marked missing: " + ", ".join(missing_assets))
        planned_assets = [
            asset["asset_id"]
            for asset in plan.get("asset_manifest", ())
            if asset.get("approval_status") == "planned"
        ]
        if planned_assets:
            warnings.append("assets still to source: " + ", ".join(planned_assets))
        rough = plan.get("rough_cut", {})
        total_direction_seconds = sum(
            float(direction["duration_seconds"])
            for direction in plan.get("shot_directions", ())
        )
        rough_seconds = float(rough.get("duration_seconds", 0))
        if abs(total_direction_seconds - rough_seconds) > 0.5:
            issues.append(
                "rough-cut duration does not match shot durations: "
                f"{rough_seconds} vs {total_direction_seconds}"
            )
        if plan.get("brief", {}).get("missing_inputs"):
            warnings.append(
                "brief has missing inputs: "
                + ", ".join(plan["brief"]["missing_inputs"])
            )
        return {
            "project_id": project_id,
            "ready": not issues,
            "issues": issues,
            "warnings": warnings,
            "plan": plan,
            "artifact": current["artifact"],
            "shot_count": len(direction_keys),
            "beat_count": len(plan.get("beats", ())),
            "asset_count": len(plan.get("asset_manifest", ())),
            "duration_seconds": rough_seconds,
        }

    def require_production_plan_ready(self, project_id: str) -> dict[str, Any]:
        status = self.production_plan_status(project_id)
        if not status["ready"]:
            raise ApprovalRequiredError(
                "Production plan is not ready: " + "; ".join(status["issues"])
            )
        return status

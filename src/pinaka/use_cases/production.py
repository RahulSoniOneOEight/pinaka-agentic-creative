from __future__ import annotations

from typing import Any

from ..contracts.planning import ScriptApproval, ScriptCreate
from ..enums import ArtifactKind
from ..errors import ConflictError, IntegrityError
from ..models import SlotCreate
from ..production import (
    PRESETS,
    STYLES,
    LayerPlan,
    ProductionPlan,
    ReuseDecisionCreate,
    ShotProductionCreate,
    style,
    style_query_plan,
)
from ..utils import canonical_json, sha256_bytes


class ProductionUseCases:
    def record_shot_production(
        self, project_id: str, request: ShotProductionCreate
    ) -> dict[str, Any]:
        plan = self.current_production_plan(project_id)
        if plan and request.preset_id != plan.preset_id:
            raise ConflictError(
                f"Shot preset {request.preset_id!r} does not match production "
                f"preset {plan.preset_id!r}"
            )
        return self.repository.upsert_shot_production(project_id, request)

    def shot_productions(self, project_id: str) -> list[dict[str, Any]]:
        return self.repository.list_shot_productions(project_id)

    def require_shot_productions(self, project_id: str) -> list[dict[str, Any]]:
        shots = self.repository.list_shots(project_id)
        productions = {
            item["shot_id"]: item
            for item in self.repository.list_shot_productions(project_id)
        }
        missing = [
            shot["stable_key"] for shot in shots if shot["id"] not in productions
        ]
        if missing:
            raise IntegrityError(
                "Every shot needs a production contract; missing: " + ", ".join(missing)
            )
        for production in productions.values():
            if not production["layers"]:
                raise IntegrityError(f"Shot {production['shot_id']} has no layer plan")
            roles = {layer["role"] for layer in production["layers"]}
            if "text" in roles:
                raise IntegrityError("Text cannot be a picture layer")
            if "character" in roles and "background" not in roles:
                raise IntegrityError("A character shot needs a background layer")
            if production["preset_id"] not in PRESETS:
                raise IntegrityError(f"Unknown preset on shot {production['shot_id']}")
            if production["style_id"] not in STYLES:
                raise IntegrityError(f"Unknown style on shot {production['shot_id']}")
            if not (production.get("fallback") or "").strip():
                raise IntegrityError(f"Shot {production['shot_id']} has no fallback")
            for item in production.get("exact_items", ()):
                source = (production.get("asset_source") or {}).get(item)
                if not source or source in {"generated", "stock_photo"}:
                    raise IntegrityError(
                        f"Exact item {item!r} on {production['shot_id']} has no "
                        "approved source"
                    )
        return productions

    def current_production_plan(self, project_id: str) -> ProductionPlan | None:
        contracts = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if artifact["relative_path"].startswith("contracts/production-contract-")
        ]
        if not contracts:
            return None
        artifact = max(contracts, key=lambda item: item["created_at"])
        body = self.read_artifact_text(artifact["id"])
        import json

        data = json.loads(body)
        raw = data.get("production") or {}
        if not raw:
            return None
        return ProductionPlan.model_validate(raw)

    def record_script(self, project_id: str, request: ScriptCreate) -> dict[str, Any]:
        content_hash = sha256_bytes(request.text.encode("utf-8"))
        return self.repository.record_script(
            project_id, request, content_hash=content_hash
        )

    def approve_script(
        self, project_id: str, script_id: str, request: ScriptApproval
    ) -> dict[str, Any]:
        return self.repository.approve_script(
            project_id, script_id, approved_by=request.approved_by
        )

    def current_script(self, project_id: str) -> dict[str, Any] | None:
        return self.repository.current_script(project_id)

    def require_approved_script(self, project_id: str) -> dict[str, Any]:
        return self.repository.require_approved_script(project_id)

    def write_style_query_plan(self, project_id: str, slot_id: str) -> dict[str, Any]:
        slot = self.repository.get_slot(slot_id)
        if slot["project_id"] != project_id:
            raise ConflictError("Slot belongs to a different project")
        style_id = slot.get("style_id")
        if not style_id:
            raise IntegrityError("Slot has no visual style")
        plan = style_query_plan(style_id)
        content = canonical_json(
            {"project_id": project_id, "slot_id": slot_id, **plan}
        ).encode("utf-8")
        digest = sha256_bytes(content)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"plans/style-query-{slot['stable_key']}-{digest[:12]}.json",
            content=content,
            operation={"type": "style_query_plan", "style_id": style_id},
        )
        return {"plan": plan, "artifact": artifact, "plan_hash": digest}

    def plan_production(self, project_id: str) -> dict[str, Any]:
        """Create a shot production contract and background slots for every shot."""

        creative = self.require_creative_plan(project_id)
        plan = creative["plan"]
        shots = self.repository.list_shots(project_id)
        if not shots:
            raise IntegrityError("A production plan requires shots")
        slots = {
            slot["stable_key"]: slot for slot in self.repository.list_slots(project_id)
        }
        created_slots = []
        productions = []
        for shot in shots:
            style_id = plan.production.style_map.get(
                shot["stable_key"], plan.production.style_id
            )
            style_def = style(style_id)
            background_key = f"{shot['stable_key']}.background"
            background = slots.get(background_key)
            if not background:
                background = self.add_slot(
                    project_id,
                    SlotCreate(
                        shot_id=shot["id"],
                        stable_key=background_key,
                        role="background",
                        identity_requirement="Environment consistent with the style",
                        fact_requirement="Matches the planned setting",
                        transformation_permission="Camera move and grade only",
                        approved_sources=(
                            "owned",
                            "official_api",
                            "manual_import",
                            "deterministic",
                        ),
                        treatment_ceiling="match",
                        exactness="generic",
                        style_id=style_id,
                        media_requirement="still",
                    ),
                )
                created_slots.append(background)
                slots[background_key] = background
            primary = slots.get(f"{shot['stable_key']}.primary_visual")
            if not primary:
                raise IntegrityError(
                    f"Missing primary visual slot for {shot['stable_key']}"
                )
            requirements = shot.get("requirements") or {}
            action = (requirements.get("subject") or {}).get("action", "bob")
            media = primary.get("media_requirement") or "cutout"
            method = (
                "cutout_rig"
                if style_id in {"anime", "flat-2d-cartoon", "comic-watercolor"}
                else "stock_clip"
                if style_id in {"cinematic-live-action", "handheld-creator"}
                else "composite"
                if style_id == "studio-product"
                else "kinetic_type"
                if style_id == "graphic-led"
                else "image_to_video"
            )
            layers = [
                LayerPlan(
                    role="background",
                    slot_id=background["id"],
                    media="still",
                    z=0,
                ),
                LayerPlan(
                    role="character",
                    element_id=None,
                    slot_id=primary["id"],
                    media=media,
                    z=10,
                    motion={
                        "level": "M1",
                        "action": action,
                        "parts": ["body", "head", "wing"],
                    },
                ),
                LayerPlan(role="effect", media="procedural", z=20),
            ]
            request = ShotProductionCreate(
                shot_id=shot["id"],
                appears=(
                    f"{shot['stable_key']}: {shot['narrative']} ({style_def.name})"
                ),
                preset_id=plan.production.preset_id,
                style_id=style_id,
                method=method,
                fallback=style_def.fallback[0],
                asset_source={
                    "background": "stock",
                    "character": "owned",
                    "effect": "procedural",
                },
                exact_items=plan.production.exact_items,
                layers=tuple(layers),
                motion={
                    "level": "M1",
                    "action": action,
                    "parts": ["body", "head", "wing"],
                },
                qa=style_def.continuity,
            )
            productions.append(self.record_shot_production(project_id, request))
            for slot in (background, primary):
                if slot.get("style_id"):
                    self.write_style_query_plan(project_id, slot["id"])
        return {
            "project_id": project_id,
            "preset_id": plan.production.preset_id,
            "style_id": plan.production.style_id,
            "productions": productions,
            "created_slots": created_slots,
        }

    def record_reuse_decision(
        self, project_id: str, request: ReuseDecisionCreate
    ) -> dict[str, Any]:
        body = {
            "schema": "reuse-decision/v1",
            "project_id": project_id,
            **request.model_dump(mode="json"),
        }
        content = canonical_json(body).encode("utf-8")
        digest = sha256_bytes(content)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"decisions/reuse-{digest[:12]}.json",
            content=content,
            operation={
                "type": "reuse_decision",
                "items": len(request.items),
                "approved_by": request.approved_by,
            },
        )
        return {"artifact": artifact, "decision": body, "decision_hash": digest}

    def require_reuse_decision(self, project_id: str) -> dict[str, Any]:
        decisions = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if (artifact.get("operation") or {}).get("type") == "reuse_decision"
        ]
        if not decisions:
            raise IntegrityError(
                "A build/adapt/reuse/reject decision is required before production"
            )
        return max(decisions, key=lambda item: item["created_at"])

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

    def generate_production_plan(self, project_id: str) -> dict[str, Any]:
        """Turn the current brief/shot plan into the executable plan contract."""

        project = self.repository.get_project(project_id)
        shots = self.repository.list_shots(project_id)
        if not shots:
            raise IntegrityError("Generate a shot plan before the production plan")
        productions = {
            item["shot_id"]: item
            for item in self.repository.list_shot_productions(project_id)
        }
        elements = self.repository.list_creative_elements(project_id)
        slots = {slot["id"]: slot for slot in self.repository.list_slots(project_id)}
        assignments: dict[str, list[str]] = {}
        for assignment in self.repository.list_slot_assets(project_id):
            slot = slots.get(assignment["slot_id"])
            if slot:
                assignments.setdefault(slot["shot_id"], []).append(
                    assignment["artifact_id"]
                )
        effects = self.current_effects_plan(project_id)
        cues_by_shot: dict[str, list[dict[str, Any]]] = {}
        for cue in effects["plan"].get("cues", ()) if effects else ():
            cues_by_shot.setdefault(str(cue["shot_key"]), []).append(cue)

        mode = project.mode.value
        if mode == "product":
            production_format = "product_showcase"
        elif mode == "entertainment":
            production_format = "sing_along"
        else:
            production_format = "cinematic_story"
        delivery_format = {
            "9:16": "vertical_reel",
            "16:9": "widescreen_ad",
            "1:1": "square_social",
        }.get(project.output_format, "episode")
        style_map = {
            "anime": "anime",
            "flat-2d-cartoon": "flat_2d_cartoon",
            "comic-watercolor": "watercolor",
            "stylized-3d": "stylized_3d",
            "cinematic-live-action": "cinematic_live_action",
            "studio-product": "studio_product",
            "graphic-led": "motion_graphics",
        }
        style_id = next(
            (
                item.get("style_id")
                for item in productions.values()
                if item.get("style_id")
            ),
            "flat-2d-cartoon",
        )
        visual_style = style_map.get(str(style_id), "mixed")
        method_map = {
            "layered_2d": "layered_2d_animation",
            "image_to_video": "image_to_video",
            "stock": "licensed_stock",
            "live": "real_footage",
            "screen_capture": "screen_capture",
        }
        production_method = "layered_2d_animation"
        for production in productions.values():
            mapped = method_map.get(str(production.get("method")))
            if mapped:
                production_method = mapped
                break

        asset_manifest: list[dict[str, Any]] = []
        seen_assets: set[str] = set()
        shot_assets: dict[str, list[str]] = {}
        for shot in shots:
            ids = assignments.get(shot["id"], [])
            if not ids:
                ids = [f"asset-{shot['stable_key']}"]
            shot_assets[shot["id"]] = ids
            for asset_id in ids:
                if asset_id in seen_assets:
                    continue
                seen_assets.add(asset_id)
                asset_manifest.append(
                    {
                        "asset_id": asset_id,
                        "kind": "image",
                        "description": f"visual for {shot['stable_key']}",
                        "source": "owned"
                        if not asset_id.startswith("asset-")
                        else "planned",
                        "method": production_method,
                        "approval_status": "owned"
                        if not asset_id.startswith("asset-")
                        else "planned",
                        "fallback": "owned still",
                        "reuse": "single shot",
                    }
                )

        beats = []
        timings = []
        shot_directions = []
        cursor = 0.0
        for index, shot in enumerate(shots, start=1):
            duration = float(shot["duration_seconds"])
            requirements = shot.get("requirements") or {}
            subject = requirements.get("subject") or {}
            motion = requirements.get("motion") or {}
            camera = requirements.get("camera") or motion.get("camera") or "hold"
            action = str(subject.get("action") or motion.get("action") or "hold")
            shot_key = shot["stable_key"]
            shot_cues = cues_by_shot.get(shot_key, [])
            beats.append(
                {
                    "beat_id": f"beat-{index:02d}",
                    "purpose": shot.get("narrative") or shot.get("beat") or shot_key,
                    "viewer_need": "see",
                    "script_line": shot.get("narrative") or shot_key,
                    "visual_action": action,
                    "duration_seconds": duration,
                    "shot_keys": [shot_key],
                }
            )
            timings.append(
                {
                    "beat_id": f"beat-{index:02d}",
                    "start_seconds": cursor,
                    "end_seconds": cursor + duration,
                }
            )
            shot_directions.append(
                {
                    "shot_key": shot_key,
                    "purpose": shot.get("beat") or shot_key,
                    "timing_cue": f"{cursor:.2f}s-{cursor + duration:.2f}s",
                    "duration_seconds": duration,
                    "composition": {
                        "framing": "medium",
                        "angle": "eye level",
                        "subject_placement": "centre",
                        "focal_point": action,
                        "camera_movement": str(camera),
                        "caption_room": "lower third",
                    },
                    "performance": {
                        "action_start": action,
                        "action_end": "hold",
                        "pause_or_reaction": "brief hold",
                        "eyeline": "camera",
                    },
                    "lighting": {
                        "direction": "soft key",
                        "softness": "soft",
                        "contrast": "low",
                        "palette": ["warm", "green"],
                        "change": "none",
                    },
                    "graphics": {
                        "effects": [str(cue["kind"]) for cue in shot_cues] or ["none"],
                        "trigger": "beat start" if shot_cues else "none",
                        "duration_seconds": (
                            max(float(cue["duration_seconds"]) for cue in shot_cues)
                            if shot_cues
                            else 0
                        ),
                        "intensity": "subtle" if shot_cues else "none",
                        "placement": "around the subject" if shot_cues else "none",
                        "editable": True,
                    },
                    "audio": {
                        "narration": shot.get("narrative") or shot_key,
                        "music": "primary bed",
                        "ambience": "room tone",
                        "sfx": [],
                        "silence": None,
                        "carry_over": None,
                    },
                    "asset_ids": shot_assets[shot["id"]],
                    "method": production_method,
                    "fallback": "owned still",
                    "acceptance_checks": ["subject visible", "caption readable"],
                    "reviewer_check": "Watch the beat against the approved shot",
                }
            )
            cursor += duration

        payload = {
            "production_format": production_format,
            "visual_style": visual_style,
            "delivery_format": delivery_format,
            "production_method": production_method,
            "brief": {
                "objective": project.objective,
                "audience": project.audience,
                "language": project.language,
                "required_content": [item["name"] for item in elements]
                or ["story beats"],
                "constraints": [
                    f"{project.max_budget.amount} {project.max_budget.currency}"
                ],
                "assumptions": [],
                "missing_inputs": [],
            },
            "treatment": {
                "premise": project.objective,
                "emotional_progression": ["setup", "development", "resolve"],
                "visual_language": f"{visual_style} with {style_id}",
                "pacing": "one beat per shot",
                "sound": "narration, music bed and ambience",
            },
            "beats": beats,
            "shot_directions": shot_directions,
            "asset_manifest": asset_manifest,
            "continuity_notes": [
                f"keep {item['stable_key']} consistent across shots"
                for item in elements
            ]
            or ["keep character identity and palette consistent"],
            "rough_cut": {
                "duration_seconds": cursor,
                "beat_timings": timings,
                "audio_reference": "narration-v1",
                "caption_notes": "phrase-level captions in the lower third",
                "holds": ["hold the final frame"],
            },
        }
        return self.record_production_plan(
            project_id, ProductionPlanCreate.model_validate(payload)
        )

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
        for binding in plan.get("data_bindings", ()):
            if not binding.get("locked"):
                issues.append(f"data binding is not locked: {binding.get('key')}")
            if not binding.get("evidence"):
                issues.append(f"data binding has no evidence: {binding.get('key')}")
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

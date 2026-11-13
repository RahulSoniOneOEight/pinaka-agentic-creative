from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import StrictModel

ProductionFormat = Literal[
    "cinematic_story",
    "sing_along",
    "narrative_brand_film",
    "product_showcase",
    "creator_ad",
    "launch_promo",
    "explainer",
    "tutorial",
    "documentary",
    "other",
]
VisualStyle = Literal[
    "anime",
    "flat_2d_cartoon",
    "watercolor",
    "stylized_3d",
    "cinematic_live_action",
    "studio_product",
    "motion_graphics",
    "mixed",
    "other",
]
DeliveryFormat = Literal[
    "vertical_reel",
    "widescreen_ad",
    "square_social",
    "episode",
    "other",
]
ProductionMethod = Literal[
    "real_footage",
    "licensed_stock",
    "layered_2d_animation",
    "image_to_video",
    "3d_render",
    "screen_capture",
    "compositing",
    "generated_image",
    "other",
]
ViewerNeed = Literal["see", "hear", "understand", "feel"]
AssetApproval = Literal["approved", "owned", "planned", "missing"]
BeatStatus = Literal["planned", "delivered", "blocked"]


class BriefPlan(StrictModel):
    objective: str = Field(min_length=1, max_length=2000)
    audience: str = Field(min_length=1, max_length=500)
    language: str = Field(min_length=2, max_length=20)
    required_content: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    missing_inputs: tuple[str, ...] = ()


class TreatmentPlan(StrictModel):
    premise: str = Field(min_length=1, max_length=2000)
    emotional_progression: tuple[str, ...] = Field(min_length=1)
    visual_language: str = Field(min_length=1, max_length=2000)
    pacing: str = Field(min_length=1, max_length=1000)
    sound: str = Field(min_length=1, max_length=1000)


class BeatPlan(StrictModel):
    beat_id: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=1000)
    viewer_need: ViewerNeed
    script_line: str = Field(min_length=1, max_length=2000)
    visual_action: str = Field(min_length=1, max_length=2000)
    duration_seconds: float = Field(gt=0, le=3600)
    shot_keys: tuple[str, ...] = Field(min_length=1)


class CompositionPlan(StrictModel):
    framing: str = Field(min_length=1, max_length=500)
    angle: str = Field(min_length=1, max_length=500)
    subject_placement: str = Field(min_length=1, max_length=500)
    focal_point: str = Field(min_length=1, max_length=500)
    camera_movement: str = Field(min_length=1, max_length=500)
    caption_room: str = Field(min_length=1, max_length=500)


class PerformancePlan(StrictModel):
    action_start: str = Field(min_length=1, max_length=1000)
    action_end: str = Field(min_length=1, max_length=1000)
    pause_or_reaction: str = Field(min_length=1, max_length=1000)
    eyeline: str = Field(min_length=1, max_length=500)


class LightingPlan(StrictModel):
    direction: str = Field(min_length=1, max_length=500)
    softness: str = Field(min_length=1, max_length=500)
    contrast: str = Field(min_length=1, max_length=500)
    palette: tuple[str, ...] = Field(min_length=1)
    change: str = Field(min_length=1, max_length=1000)


class GraphicsPlan(StrictModel):
    effects: tuple[str, ...] = ()
    trigger: str = Field(min_length=1, max_length=1000)
    duration_seconds: float = Field(ge=0, le=3600)
    intensity: str = Field(min_length=1, max_length=500)
    placement: str = Field(min_length=1, max_length=500)
    editable: bool = True


class AudioPlan(StrictModel):
    dialogue: str | None = Field(default=None, max_length=2000)
    narration: str | None = Field(default=None, max_length=4000)
    music: str | None = Field(default=None, max_length=1000)
    ambience: str | None = Field(default=None, max_length=1000)
    sfx: tuple[str, ...] = ()
    silence: str | None = Field(default=None, max_length=1000)
    carry_over: str | None = Field(default=None, max_length=1000)


class AssetRoute(StrictModel):
    asset_id: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)
    source: str = Field(min_length=1, max_length=1000)
    method: ProductionMethod
    approval_status: AssetApproval
    fallback: str = Field(min_length=1, max_length=1000)
    reuse: str = Field(min_length=1, max_length=1000)


class ShotDirection(StrictModel):
    shot_key: str = Field(min_length=1, max_length=80)
    purpose: str = Field(min_length=1, max_length=1000)
    timing_cue: str = Field(min_length=1, max_length=1000)
    duration_seconds: float = Field(gt=0, le=3600)
    composition: CompositionPlan
    performance: PerformancePlan
    lighting: LightingPlan
    graphics: GraphicsPlan
    audio: AudioPlan
    asset_ids: tuple[str, ...] = Field(min_length=1)
    method: ProductionMethod
    fallback: str = Field(min_length=1, max_length=1000)
    acceptance_checks: tuple[str, ...] = Field(min_length=1)
    reviewer_check: str = Field(min_length=1, max_length=1000)


class BeatTiming(StrictModel):
    beat_id: str = Field(min_length=1, max_length=120)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)


class RoughCutPlan(StrictModel):
    duration_seconds: float = Field(gt=0, le=3600)
    beat_timings: tuple[BeatTiming, ...] = Field(min_length=1)
    audio_reference: str = Field(min_length=1, max_length=1000)
    caption_notes: str = Field(min_length=1, max_length=2000)
    holds: tuple[str, ...] = ()


class ProductionPlanCreate(StrictModel):
    production_format: ProductionFormat
    visual_style: VisualStyle
    delivery_format: DeliveryFormat
    production_method: ProductionMethod
    brief: BriefPlan
    treatment: TreatmentPlan
    beats: tuple[BeatPlan, ...] = Field(min_length=1)
    shot_directions: tuple[ShotDirection, ...] = Field(min_length=1)
    asset_manifest: tuple[AssetRoute, ...] = Field(min_length=1)
    continuity_notes: tuple[str, ...] = Field(min_length=1)
    rough_cut: RoughCutPlan

    @model_validator(mode="after")
    def plan_is_linked_and_complete(self) -> ProductionPlanCreate:
        beat_ids = [beat.beat_id for beat in self.beats]
        shot_keys = [shot.shot_key for shot in self.shot_directions]
        asset_ids = [asset.asset_id for asset in self.asset_manifest]
        if len(beat_ids) != len(set(beat_ids)):
            raise ValueError("Beat ids must be unique")
        if len(shot_keys) != len(set(shot_keys)):
            raise ValueError("Shot keys must be unique")
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("Asset ids must be unique")
        known_shots = set(shot_keys)
        for beat in self.beats:
            unknown = set(beat.shot_keys) - known_shots
            if unknown:
                raise ValueError(
                    f"Beat {beat.beat_id} references unknown shots: "
                    + ", ".join(sorted(unknown))
                )
        known_assets = set(asset_ids)
        for shot in self.shot_directions:
            unknown = set(shot.asset_ids) - known_assets
            if unknown:
                raise ValueError(
                    f"Shot {shot.shot_key} references unknown assets: "
                    + ", ".join(sorted(unknown))
                )
        beat_set = set(beat_ids)
        timing_ids = [timing.beat_id for timing in self.rough_cut.beat_timings]
        if len(timing_ids) != len(set(timing_ids)):
            raise ValueError("Rough-cut beat timings must be unique")
        unknown_timings = set(timing_ids) - beat_set
        if unknown_timings:
            raise ValueError(
                "Rough cut references unknown beats: "
                + ", ".join(sorted(unknown_timings))
            )
        missing_timings = beat_set - set(timing_ids)
        if missing_timings:
            raise ValueError(
                "Rough cut is missing beats: " + ", ".join(sorted(missing_timings))
            )
        for timing in self.rough_cut.beat_timings:
            if timing.end_seconds <= timing.start_seconds:
                raise ValueError(f"Beat {timing.beat_id} has a non-positive duration")
        return self

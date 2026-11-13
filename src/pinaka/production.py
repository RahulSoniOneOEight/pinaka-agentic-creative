from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .models import StrictModel

LayerRole = Literal["background", "character", "prop", "effect"]
LayerMedia = Literal[
    "still", "cutout", "motion_clip", "model", "procedural", "brand_kit"
]
ShotMethod = Literal[
    "still_camera",
    "cutout_rig",
    "stock_clip",
    "footage_edit",
    "image_to_video",
    "3d_render",
    "screen_capture",
    "kinetic_type",
    "composite",
]


class ProductionPreset(StrictModel):
    id: str
    name: str
    purpose: str
    output: str
    narrative_shape: str
    suggested_recipe: str
    typical_styles: tuple[str, ...]
    exact_items: tuple[str, ...] = ()
    required_inputs: tuple[str, ...] = ()


class VisualStyle(StrictModel):
    id: str
    name: str
    sources: str
    methods: str
    motion_levels: tuple[str, ...]
    required_layers: tuple[str, ...]
    query_providers: tuple[str, ...]
    query_terms: tuple[str, ...]
    asset_classes: tuple[str, ...]
    continuity: tuple[str, ...]
    fallback: tuple[str, ...]


class LayerPlan(StrictModel):
    role: LayerRole
    element_id: str | None = Field(default=None, max_length=120)
    slot_id: str | None = Field(default=None, max_length=120)
    media: LayerMedia
    z: int = Field(ge=0, le=1000)
    actions: tuple[str, ...] = ()
    motion: dict[str, Any] = Field(default_factory=dict)
    rights: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[str, ...] = ()


class ShotProductionCreate(StrictModel):
    shot_id: str = Field(min_length=1, max_length=120)
    appears: str = Field(min_length=1, max_length=2000)
    preset_id: str = Field(min_length=1, max_length=100)
    style_id: str = Field(min_length=1, max_length=100)
    method: ShotMethod
    fallback: str = Field(min_length=1, max_length=1000)
    asset_source: dict[str, str] = Field(default_factory=dict)
    exact_items: tuple[str, ...] = ()
    layers: tuple[LayerPlan, ...] = Field(min_length=1)
    motion: dict[str, Any] = Field(default_factory=dict)
    qa: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_layers(self) -> ShotProductionCreate:
        if PRESETS.get(self.preset_id) is None:
            raise ValueError(f"Unknown production preset: {self.preset_id}")
        style = STYLES.get(self.style_id)
        if style is None:
            raise ValueError(f"Unknown visual style: {self.style_id}")
        z_values = [layer.z for layer in self.layers]
        if len(z_values) != len(set(z_values)):
            raise ValueError("Layer z values must be unique")
        roles = {layer.role for layer in self.layers}
        if "character" in roles:
            if "background" not in roles:
                raise ValueError("A character shot needs a background layer")
            if "character" not in roles:
                raise ValueError("A character shot needs a character layer")
        for layer in self.layers:
            if layer.role == "character" and layer.media not in {
                "cutout",
                "motion_clip",
                "model",
            }:
                raise ValueError("A character layer needs a cutout, clip or model")
            if layer.role == "background" and layer.media not in {
                "still",
                "motion_clip",
                "model",
                "procedural",
            }:
                raise ValueError("A background layer needs a still, clip or model")
        missing = set(style.required_layers) - roles
        if missing:
            raise ValueError(
                f"Style {self.style_id} requires layers: {', '.join(sorted(missing))}"
            )
        return self


class ProductionPlan(StrictModel):
    preset_id: str
    style_id: str
    style_map: dict[str, str] = Field(default_factory=dict)
    exact_items: tuple[str, ...] = ()
    shot_method_policy: tuple[str, ...] = ()

    @model_validator(mode="after")
    def known_preset_and_style(self) -> ProductionPlan:
        if PRESETS.get(self.preset_id) is None:
            raise ValueError(f"Unknown production preset: {self.preset_id}")
        if STYLES.get(self.style_id) is None:
            raise ValueError(f"Unknown visual style: {self.style_id}")
        for style_id in self.style_map.values():
            if STYLES.get(style_id) is None:
                raise ValueError(f"Unknown shot style: {style_id}")
        return self


PRESETS: dict[str, ProductionPreset] = {
    "cinematic-story": ProductionPreset(
        id="cinematic-story",
        name="Cinematic story",
        purpose="Entertainment",
        output="Character-led scenes with dialogue, establishing shots, reactions and action.",
        narrative_shape="establish -> inciting action -> reaction -> resolution",
        suggested_recipe="animated-story",
        typical_styles=(
            "anime",
            "flat-2d-cartoon",
            "stylized-3d",
            "cinematic-live-action",
        ),
        exact_items=("character_identity", "dialogue"),
        required_inputs=("character_sheet", "script", "style_references"),
    ),
    "storybook-motion-comic": ProductionPreset(
        id="storybook-motion-comic",
        name="Storybook or motion comic",
        purpose="Entertainment",
        output="Narrated illustrated scenes with panel transitions, camera moves and selective animation.",
        narrative_shape="panel/page sequence with narration",
        suggested_recipe="animated-story",
        typical_styles=("comic-watercolor", "flat-2d-cartoon"),
        exact_items=("panel_text", "illustration_identity"),
        required_inputs=("illustrations", "lettering", "narration"),
    ),
    "comedy-skit": ProductionPreset(
        id="comedy-skit",
        name="Comedy skit",
        purpose="Entertainment and social",
        output="A fast setup, character reaction and punchline.",
        narrative_shape="setup -> reaction -> punchline",
        suggested_recipe="comedy-skit",
        typical_styles=("cinematic-live-action", "flat-2d-cartoon", "anime"),
        exact_items=("character_identity", "punchline"),
        required_inputs=("presenter_or_character", "script", "location"),
    ),
    "product-showcase": ProductionPreset(
        id="product-showcase",
        name="Product showcase",
        purpose="Ads",
        output="Product close-ups, feature callouts and a branded end card.",
        narrative_shape="hero -> features -> end card",
        suggested_recipe="offer-short",
        typical_styles=("studio-product", "anime", "stylized-3d", "graphic-led"),
        exact_items=("logo", "packaging", "price", "claims", "cta"),
        required_inputs=("approved_pack_shots", "product_facts", "brand_kit"),
    ),
    "problem-solution-ad": ProductionPreset(
        id="problem-solution-ad",
        name="Problem-solution ad",
        purpose="Ads",
        output="Pain point, product demonstration, benefit and call to action.",
        narrative_shape="pain -> product -> proof -> cta",
        suggested_recipe="offer-short",
        typical_styles=("cinematic-live-action", "studio-product", "graphic-led"),
        exact_items=("product", "claim", "price", "cta"),
        required_inputs=("problem_statement", "product_proof", "approved_claims"),
    ),
    "creator-style-ad": ProductionPreset(
        id="creator-style-ad",
        name="Creator-style ad",
        purpose="Ads and social",
        output="Direct-to-camera hook, product-in-use shots and conversational delivery.",
        narrative_shape="hook -> product in use -> recommendation -> cta",
        suggested_recipe="offer-short",
        typical_styles=("handheld-creator", "cinematic-live-action"),
        exact_items=("presenter_identity", "product", "claims", "cta"),
        required_inputs=("presenter_recording", "product_in_use", "captions"),
    ),
    "offer-launch-promo": ProductionPreset(
        id="offer-launch-promo",
        name="Offer or launch promo",
        purpose="Ads and social",
        output="Reveal with bold typography, date or price and a clear next action.",
        narrative_shape="reveal -> offer -> urgency -> cta",
        suggested_recipe="offer-short",
        typical_styles=("graphic-led", "studio-product"),
        exact_items=("logo", "price", "date", "offer_terms", "cta"),
        required_inputs=("approved_offer", "dates", "product_assets"),
    ),
    "trend-led-reel": ProductionPreset(
        id="trend-led-reel",
        name="Trend-led reel",
        purpose="Social promotion",
        output="Strong visual opening, beat-synced cuts, short overlays and a payoff.",
        narrative_shape="visual hook -> beat cuts -> payoff",
        suggested_recipe="seamless-loop",
        typical_styles=("handheld-creator", "graphic-led", "cinematic-live-action"),
        exact_items=("brand", "captions"),
        required_inputs=("trend_reference", "footage", "music_rights"),
    ),
    "behind-the-scenes-reel": ProductionPreset(
        id="behind-the-scenes-reel",
        name="Behind-the-scenes reel",
        purpose="Social promotion",
        output="How a product is made, packed, served or used.",
        narrative_shape="process step -> step -> people/quality -> cta",
        suggested_recipe="captured-tutorial",
        typical_styles=("handheld-creator", "cinematic-live-action"),
        exact_items=("logo", "staff_consent", "process_facts"),
        required_inputs=("real_footage", "interviews", "captions"),
    ),
    "tips-list-reel": ProductionPreset(
        id="tips-list-reel",
        name="Tips or list reel",
        purpose="Social and education",
        output="Useful promise, then a short sequence of tips or examples with readable overlays.",
        narrative_shape="promise -> tip 1..N -> recap",
        suggested_recipe="trivia-list",
        typical_styles=("graphic-led", "handheld-creator", "cinematic-live-action"),
        exact_items=("tips", "numbers", "claims"),
        required_inputs=("approved_copy", "examples", "captions"),
    ),
    "demo-tutorial": ProductionPreset(
        id="demo-tutorial",
        name="Demo or tutorial",
        purpose="Product education",
        output="Task walkthrough with screen recordings, product footage or animation.",
        narrative_shape="task -> step 1..N -> result",
        suggested_recipe="captured-tutorial",
        typical_styles=("graphic-led", "stylized-3d", "cinematic-live-action"),
        exact_items=("ui_labels", "steps", "product_shape"),
        required_inputs=("screen_recordings", "product_footage", "steps"),
    ),
    "motion-graphics-explainer": ProductionPreset(
        id="motion-graphics-explainer",
        name="Motion-graphics explainer",
        purpose="Education and information",
        output="Narration, icons, diagrams and charts explain a concept or process.",
        narrative_shape="question -> diagram/process -> takeaway",
        suggested_recipe="explainer-short",
        typical_styles=("graphic-led",),
        exact_items=("data", "labels", "claims"),
        required_inputs=("brand_kit", "icons", "charts", "narration"),
    ),
    "mini-documentary": ProductionPreset(
        id="mini-documentary",
        name="Mini-documentary or customer story",
        purpose="Information and brand",
        output="Narrative from interviews, narration, B-roll and supporting graphics.",
        narrative_shape="character/context -> conflict/process -> resolution",
        suggested_recipe="news-commentary",
        typical_styles=("cinematic-live-action",),
        exact_items=("quotes", "names", "dates", "consent"),
        required_inputs=("interviews", "b_roll", "archive_rights"),
    ),
}


STYLES: dict[str, VisualStyle] = {
    "anime": VisualStyle(
        id="anime",
        name="Japanese anime",
        sources="Approved character sheets, original illustrations, reusable backgrounds, pose references.",
        methods="Keyframes from the same references; held poses; layered camera; image-to-video for action.",
        motion_levels=("M1", "M2", "M3"),
        required_layers=("background", "character"),
        query_providers=("owned", "commissioned", "illustration_archive"),
        query_terms=(
            "character sheet",
            "anime background",
            "pose reference",
            "illustration",
        ),
        asset_classes=("illustration", "character_sheet", "cutout", "background"),
        continuity=("face", "hair", "costume", "prop", "palette", "line_weight"),
        fallback=("request character sheet", "switch flat-2d-cartoon", "hold pose"),
    ),
    "flat-2d-cartoon": VisualStyle(
        id="flat-2d-cartoon",
        name="Flat 2D cartoon",
        sources="Reusable rigs, expressions, mouth shapes, props, backgrounds.",
        methods="Rig poses, gestures, lip sync; cycles for repeated actions.",
        motion_levels=("M1", "M2"),
        required_layers=("background", "character"),
        query_providers=("owned", "commissioned", "rig_library"),
        query_terms=(
            "character rig",
            "expression sheet",
            "mouth shapes",
            "2d background",
        ),
        asset_classes=("rig", "cutout", "illustration", "background"),
        continuity=("rig_version", "expression", "prop_scale"),
        fallback=("use static cutout", "switch comic-watercolor"),
    ),
    "comic-watercolor": VisualStyle(
        id="comic-watercolor",
        name="Comic book or watercolor storybook",
        sources="Original or licensed illustrations, layered panels, paper textures, lettering.",
        methods="Separate foreground/background; camera moves; panel transitions; selective animation.",
        motion_levels=("M1", "M2"),
        required_layers=("background", "character"),
        query_providers=("owned", "commissioned", "illustration_archive"),
        query_terms=(
            "storybook illustration",
            "watercolor background",
            "comic panel",
            "paper texture",
        ),
        asset_classes=("illustration", "panel", "texture", "background"),
        continuity=("panel_order", "paper_texture", "palette", "lettering"),
        fallback=("static panel with narration", "switch flat-2d-cartoon"),
    ),
    "stylized-3d": VisualStyle(
        id="stylized-3d",
        name="Stylized 3D",
        sources="Licensed or original models, rigs, materials, sets.",
        methods="Stage/light scenes; animate characters or objects; render camera passes.",
        motion_levels=("M2", "M3", "M4", "M5"),
        required_layers=("background", "character"),
        query_providers=("owned", "licensed_model", "3d_marketplace"),
        query_terms=("character model", "3d set", "material", "rig"),
        asset_classes=("model", "rig", "material", "3d_set"),
        continuity=("model_version", "material", "scale", "lighting"),
        fallback=("verified model", "hold shot", "switch studio-product"),
    ),
    "cinematic-live-action": VisualStyle(
        id="cinematic-live-action",
        name="Cinematic live action",
        sources="Client footage, commissioned footage, licensed stock.",
        methods="Match footage to shot list; edit for continuity; colour-grade; stock for support.",
        motion_levels=("M2",),
        required_layers=("background", "character"),
        query_providers=("owned", "licensed_stock", "commissioned"),
        query_terms=("cinematic footage", "process footage", "location", "atmosphere"),
        asset_classes=("footage", "still", "archive"),
        continuity=("wardrobe", "location", "grade"),
        fallback=("licensed stock", "reduce shot"),
    ),
    "handheld-creator": VisualStyle(
        id="handheld-creator",
        name="Handheld creator style",
        sources="Creator recordings, phone footage, product-in-use clips, screen captures.",
        methods="Spoken hook/demo; natural cuts; captions; vertical framing.",
        motion_levels=("M2",),
        required_layers=("background", "character"),
        query_providers=("owned", "creator_library"),
        query_terms=(
            "creator recording",
            "phone footage",
            "product in use",
            "screen capture",
        ),
        asset_classes=("footage", "screen_capture", "presenter"),
        continuity=("presenter_identity", "product", "framing", "consent"),
        fallback=("new take", "scripted spokesperson"),
    ),
    "studio-product": VisualStyle(
        id="studio-product",
        name="Studio product",
        sources="Approved pack shots, catalog photos, product footage, transparent cutouts, verified 3D models.",
        methods="Composite real product assets; controlled camera; feature callouts; branded end card.",
        motion_levels=("M1", "M2"),
        required_layers=("background", "character"),
        query_providers=("owned", "approved_product_assets"),
        query_terms=(
            "pack shot front",
            "product close-up",
            "product footage",
            "3d model",
        ),
        asset_classes=("pack_shot", "cutout", "product_footage", "model"),
        continuity=("logo", "packaging", "label", "price", "colour", "shape"),
        fallback=("request more views", "verified model", "hold shot"),
    ),
    "graphic-led": VisualStyle(
        id="graphic-led",
        name="Graphic-led animation",
        sources="Brand kit, licensed icons, approved copy, structured data, charts, screen recordings.",
        methods="Editable typography, diagrams, callouts; time to narration/music; exact text layers.",
        motion_levels=("M1", "M2"),
        required_layers=("background", "character"),
        query_providers=("brand_kit", "icon_library", "data"),
        query_terms=("icon set", "chart", "diagram", "brand typography"),
        asset_classes=("brand_kit", "icon", "chart", "screen_capture"),
        continuity=("brand_tokens", "exact_numbers", "labels", "claims"),
        fallback=("simplify chart", "approved copy"),
    ),
}


def preset(preset_id: str) -> ProductionPreset:
    return PRESETS[preset_id]


def style(style_id: str) -> VisualStyle:
    return STYLES[style_id]


def infer_preset(*, content_family: str, subject: str) -> str:
    if content_family in {"rhyme", "story"}:
        return "storybook-motion-comic"
    if content_family == "comedy":
        return "comedy-skit"
    if content_family in {"ads", "product"}:
        return "product-showcase"
    if content_family in {"explainer", "tutorial"}:
        return "motion-graphics-explainer"
    return "cinematic-story"


def infer_style(*, content_family: str, subject: str) -> str:
    if content_family in {"rhyme", "story"} and subject == "kids":
        return "flat-2d-cartoon"
    if content_family in {"rhyme", "story"}:
        return "comic-watercolor"
    if content_family == "comedy":
        return "cinematic-live-action"
    if content_family in {"ads", "product"}:
        return "studio-product"
    if content_family in {"explainer", "tutorial"}:
        return "graphic-led"
    return "cinematic-live-action"


def style_query_plan(style_id: str) -> dict[str, Any]:
    style_def = style(style_id)
    return {
        "style_id": style_id,
        "providers": list(style_def.query_providers),
        "terms": list(style_def.query_terms),
        "asset_classes": list(style_def.asset_classes),
        "reject_asset_classes": [
            item
            for item in ("photo", "stock_photo", "unverified_model")
            if item not in style_def.asset_classes
        ],
    }


def style_fit(
    style_id: str, *, asset_class: str, media_type: str
) -> tuple[int, list[str]]:
    style_def = style(style_id)
    reasons: list[str] = []
    score = 0
    if asset_class in style_def.asset_classes:
        score += 60
        reasons.append("asset class accepted")
    else:
        reasons.append(f"asset class {asset_class!r} not accepted by {style_id}")
    if media_type in {"image", "video", "audio"}:
        score += 20
        reasons.append("media type supported")
    else:
        reasons.append(f"unsupported media type {media_type!r}")
    if style_id in {"anime", "flat-2d-cartoon", "comic-watercolor"} and asset_class in {
        "photo",
        "stock_photo",
    }:
        score = 0
        reasons.append("photo cannot stand in for an illustrated character")
    if style_id == "studio-product" and asset_class in {"generated", "stock_photo"}:
        score = 0
        reasons.append("product identity must be owned/approved")
    return score, reasons


def required_layer_map(style_id: str) -> dict[str, str]:
    style_def = style(style_id)
    mapping = {role: role for role in style_def.required_layers}
    mapping.setdefault("prop", "prop")
    mapping.setdefault("effect", "effect")
    return mapping


class ReuseItem(StrictModel):
    component: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=200)
    decision: Literal["build", "adapt", "reuse", "reject"]
    license: str = Field(min_length=1, max_length=200)
    version: str | None = Field(default=None, max_length=100)


class ReuseDecisionCreate(StrictModel):
    approved_by: str = Field(min_length=1, max_length=200)
    items: tuple[ReuseItem, ...] = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=2000)

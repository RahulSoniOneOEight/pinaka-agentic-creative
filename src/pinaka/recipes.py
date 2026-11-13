from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .creative_rules import (
    Ceiling,
    CreativeProfile,
    Exactness,
    RuleDecision,
    evaluate_profile,
)
from .models import StrictModel
from .production import ProductionPlan
from .utils import sha256_json

GUIDE_VERSION = "0.1"

ProductionLane = Literal[
    "template_data",
    "stills_led",
    "stock_led",
    "footage_led",
    "narrated_visual",
    "real_capture",
    "character_story",
    "loop",
]
EffectsLevel = Literal["none", "light", "standard", "hero_moment"]
EndingType = Literal["cta", "takeaway", "reveal", "resolution", "next_episode", "loop"]

CEILING_ORDER: tuple[Ceiling, ...] = (
    "as_is",
    "match",
    "effects",
    "ai_enhance",
    "composite",
    "ai_created",
)


class Recipe(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,79}$")
    version: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    product_roles: tuple[str, ...] = ()
    forms: tuple[str, ...] = ()
    structures: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    tones: tuple[str, ...] = ()
    outcomes: tuple[str, ...] = ()
    duration_min_seconds: float | None = Field(default=None, ge=0)
    duration_max_seconds: float | None = Field(default=None, ge=0)
    default_lane: ProductionLane
    default_ceiling: Ceiling
    default_effects: EffectsLevel
    default_ending: EndingType
    beats: tuple[str, ...] = Field(min_length=1)
    grounding: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()


class RecipeSelection(StrictModel):
    recipe: Recipe
    score: int
    matched_dimensions: tuple[str, ...]
    runner_up: str | None = None
    runner_up_score: int | None = None
    rationale: str


class SlotPolicy(StrictModel):
    role: str = Field(min_length=1, max_length=100)
    exactness: Exactness
    grounding: tuple[str, ...] = ()
    allowed_sources: tuple[str, ...] = ()
    treatment_ceiling: Ceiling
    prohibited_operations: tuple[str, ...] = ()
    attribution_disclosure: tuple[str, ...] = ()
    fallback: str = Field(min_length=1, max_length=500)


class CreativePlan(StrictModel):
    schema_version: str = "creative-plan/v1"
    guide_version: str = GUIDE_VERSION
    profile_hash: str
    profile: CreativeProfile
    decision: RuleDecision
    recipe_selection: RecipeSelection
    slots: tuple[SlotPolicy, ...]
    production: ProductionPlan
    alternatives: tuple[str, ...] = ()


RECIPES: tuple[Recipe, ...] = (
    Recipe(
        id="offer-short",
        version="1.0",
        name="Offer Short",
        description="Hero product, direct promotional short with exact offer and CTA.",
        product_roles=("hero",),
        forms=("direct",),
        structures=("linear",),
        subjects=("food", "lifestyle", "tech"),
        tones=("urgent", "friendly", "neutral"),
        outcomes=("sell",),
        duration_min_seconds=5,
        duration_max_seconds=20,
        default_lane="stills_led",
        default_ceiling="composite",
        default_effects="standard",
        default_ending="cta",
        beats=("product_hook", "offer", "cta"),
        grounding=("product_facts", "brand_pack"),
        checks=(
            "identity",
            "variant",
            "price",
            "currency",
            "offer_dates",
            "cta",
            "legibility",
        ),
    ),
    Recipe(
        id="benefits-short",
        version="1.0",
        name="Benefits Short",
        description="Product benefit short with proof and an optional CTA.",
        product_roles=("hero", "cameo"),
        forms=("direct", "explainer"),
        structures=("linear",),
        subjects=("tech", "lifestyle", "money"),
        tones=("friendly", "neutral", "serious"),
        outcomes=("build_trust", "sell", "inform"),
        duration_min_seconds=15,
        duration_max_seconds=45,
        default_lane="stills_led",
        default_ceiling="composite",
        default_effects="standard",
        default_ending="cta",
        beats=("problem", "benefit", "proof", "cta"),
        grounding=("product_facts", "claim_evidence", "brand_pack"),
        checks=("benefit_evidence", "demonstration_accuracy"),
    ),
    Recipe(
        id="explainer-short",
        version="1.0",
        name="Explainer Short",
        description="Source-bound concise explainer with a takeaway.",
        product_roles=("none", "cameo"),
        forms=("explainer",),
        structures=("linear",),
        subjects=("science", "money", "health", "tech", "history"),
        tones=("neutral", "serious", "friendly"),
        outcomes=("inform", "build_trust"),
        duration_min_seconds=10,
        duration_max_seconds=90,
        default_lane="narrated_visual",
        default_ceiling="ai_enhance",
        default_effects="light",
        default_ending="takeaway",
        beats=("question", "concept", "example", "takeaway"),
        grounding=("claim_evidence",),
        checks=("figures", "claims", "citations", "diagram_meaning"),
    ),
    Recipe(
        id="long-explainer",
        version="1.0",
        name="Long Explainer",
        description="Chaptered long-form explainer with evidence and a recap.",
        product_roles=("none", "cameo"),
        forms=("explainer",),
        structures=("chapters", "linear"),
        subjects=("science", "history", "money", "health"),
        tones=("serious", "neutral"),
        outcomes=("inform", "bring_viewers_back", "build_trust"),
        duration_min_seconds=480,
        default_lane="narrated_visual",
        default_ceiling="ai_enhance",
        default_effects="light",
        default_ending="next_episode",
        beats=("hook", "chapters", "examples", "recap", "next_instalment"),
        grounding=("claim_evidence", "source_context"),
        checks=(
            "depth",
            "repetition",
            "chapter_logic",
            "evidence",
            "generated_seconds",
        ),
    ),
    Recipe(
        id="captured-tutorial",
        version="1.0",
        name="Captured Tutorial",
        description="Real captured task with ordered steps and verification.",
        product_roles=("none", "cameo", "hero"),
        forms=("tutorial",),
        structures=("linear",),
        subjects=("tech", "food", "lifestyle"),
        tones=("friendly", "neutral"),
        outcomes=("teach",),
        duration_min_seconds=60,
        default_lane="real_capture",
        default_ceiling="match",
        default_effects="standard",
        default_ending="resolution",
        beats=("result", "starting_state", "ordered_steps", "verification"),
        grounding=("tutorial_steps",),
        checks=(
            "step_order",
            "visibility",
            "reproducibility",
            "authentic_capture",
        ),
    ),
    Recipe(
        id="news-commentary",
        version="1.0",
        name="News Commentary",
        description="Attributed source material followed by clearly separated analysis.",
        product_roles=("none",),
        forms=("commentary",),
        structures=("linear",),
        subjects=("news", "history"),
        tones=("serious", "neutral"),
        outcomes=("build_trust", "inform"),
        duration_min_seconds=60,
        default_lane="footage_led",
        default_ceiling="match",
        default_effects="light",
        default_ending="takeaway",
        beats=("context", "sourced_material", "analysis", "conclusion"),
        grounding=("source_context", "claim_evidence"),
        checks=(
            "source",
            "date",
            "context",
            "authenticity",
            "quotation_boundaries",
            "fact_opinion_separation",
        ),
    ),
    Recipe(
        id="comedy-skit",
        version="1.0",
        name="Comedy Skit",
        description="Character-driven comedy with setup, escalation and payoff.",
        product_roles=("none", "cameo"),
        forms=("comedy",),
        structures=("linear",),
        subjects=("fiction", "lifestyle", "sports_pop_culture"),
        tones=("humorous", "dramatic"),
        outcomes=("entertain", "drive_engagement"),
        duration_min_seconds=15,
        duration_max_seconds=90,
        default_lane="character_story",
        default_ceiling="ai_created",
        default_effects="standard",
        default_ending="resolution",
        beats=("setup", "escalation", "punchline"),
        grounding=("story_bible",),
        checks=(
            "timing",
            "originality",
            "character_continuity",
            "likeness_ip",
            "realism_disclosure",
        ),
    ),
    Recipe(
        id="animated-story",
        version="1.0",
        name="Animated Story",
        description="Character-led story with continuity and emotional resolution.",
        product_roles=("none",),
        forms=("story",),
        structures=("linear",),
        subjects=("fiction", "lifestyle"),
        tones=("emotional", "dramatic", "calm"),
        outcomes=("evoke_emotion", "entertain"),
        duration_min_seconds=30,
        default_lane="character_story",
        default_ceiling="ai_created",
        default_effects="standard",
        default_ending="resolution",
        beats=("character_hook", "conflict", "resolution"),
        grounding=("story_bible",),
        checks=(
            "character_appearance",
            "props",
            "voice",
            "location",
            "scene_continuity",
        ),
    ),
    Recipe(
        id="trivia-list",
        version="1.0",
        name="Trivia List",
        description="List-structured facts with a reveal or question ending.",
        product_roles=("none", "cameo"),
        forms=("explainer", "comedy"),
        structures=("list",),
        subjects=("sports_pop_culture", "science", "history", "lifestyle"),
        tones=("humorous", "friendly", "neutral"),
        outcomes=("drive_engagement", "inform", "entertain"),
        duration_min_seconds=15,
        duration_max_seconds=90,
        default_lane="narrated_visual",
        default_ceiling="ai_enhance",
        default_effects="standard",
        default_ending="reveal",
        beats=("hook", "items", "reveal"),
        grounding=("claim_evidence", "platform_policy"),
        checks=(
            "facts",
            "source_dates",
            "identifiable_elements",
            "rights",
            "episode_variation",
        ),
    ),
    Recipe(
        id="seamless-loop",
        version="1.0",
        name="Seamless Loop",
        description="Short visual loop whose ending reconnects to the opening.",
        product_roles=("none",),
        forms=("direct", "explainer", "comedy", "story"),
        structures=("loop",),
        subjects=("lifestyle", "tech", "science"),
        tones=("calm", "friendly", "humorous"),
        outcomes=("drive_engagement", "bring_viewers_back"),
        duration_min_seconds=5,
        duration_max_seconds=20,
        default_lane="loop",
        default_ceiling="ai_enhance",
        default_effects="light",
        default_ending="loop",
        beats=("opening_state", "visual_audio_change", "return_to_opening"),
        grounding=(),
        checks=("play_twice", "visible_seam", "audible_seam"),
    ),
    Recipe(
        id="history-mystery",
        version="1.0",
        name="History Mystery",
        description="Long-form history mystery with evidence, uncertainty and a reveal.",
        product_roles=("none",),
        forms=("story", "explainer"),
        structures=("linear", "chapters"),
        subjects=("history",),
        tones=("dramatic", "serious"),
        outcomes=("bring_viewers_back", "inform", "build_trust"),
        duration_min_seconds=300,
        default_lane="narrated_visual",
        default_ceiling="ai_enhance",
        default_effects="standard",
        default_ending="reveal",
        beats=(
            "mystery",
            "evidence",
            "competing_explanations",
            "reveal",
            "remaining_uncertainty",
        ),
        grounding=("source_context", "claim_evidence"),
        checks=(
            "archive_sources",
            "uncertainty",
            "reconstruction_labels",
        ),
    ),
)


def _duration(profile: CreativeProfile) -> float:
    return float(profile.deliverable.duration_seconds)


def _score(recipe: Recipe, profile: CreativeProfile) -> tuple[int, list[str]]:
    score = 0
    matched: list[str] = []
    if recipe.product_roles and profile.product_role in recipe.product_roles:
        score += 3
        matched.append("product_role")
    if recipe.forms and profile.form in recipe.forms:
        score += 4
        matched.append("form")
    if recipe.structures and set(profile.structures) & set(recipe.structures):
        score += 3
        matched.append("structure")
    if recipe.subjects and set(profile.subjects) & set(recipe.subjects):
        score += 3
        matched.append("subject")
    if recipe.tones and set(profile.tones) & set(recipe.tones):
        score += 1
        matched.append("tone")
    if recipe.outcomes and {profile.primary_outcome, *profile.secondary_outcomes} & set(
        recipe.outcomes
    ):
        score += 2
        matched.append("outcome")
    duration = _duration(profile)
    if (
        recipe.duration_min_seconds is not None
        and duration < recipe.duration_min_seconds
    ):
        score -= 2
    if (
        recipe.duration_max_seconds is not None
        and duration > recipe.duration_max_seconds
    ):
        score -= 2
    if (
        (
            recipe.duration_min_seconds is not None
            or recipe.duration_max_seconds is not None
        )
        and not (
            recipe.duration_min_seconds is not None
            and duration < recipe.duration_min_seconds
        )
        and not (
            recipe.duration_max_seconds is not None
            and duration > recipe.duration_max_seconds
        )
    ):
        score += 2
        matched.append("duration")
    return score, matched


def select_recipe(profile: CreativeProfile) -> RecipeSelection:
    scored = [(recipe, *_score(recipe, profile)) for recipe in RECIPES]
    scored.sort(key=lambda item: (-item[1], item[0].id))
    best, score, matched = scored[0]
    runner_up, runner_score, _ = scored[1] if len(scored) > 1 else (None, None, [])
    rationale = (
        f"Selected {best.name} ({best.id} v{best.version}) with score {score}; "
        f"matched {', '.join(matched) if matched else 'no explicit selectors'}."
    )
    return RecipeSelection(
        recipe=best,
        score=score,
        matched_dimensions=tuple(matched),
        runner_up=runner_up.id if runner_up else None,
        runner_up_score=runner_score,
        rationale=rationale,
    )


def _slot(
    role: str,
    exactness: Exactness,
    ceiling: Ceiling,
    *,
    grounding: tuple[str, ...] = (),
    sources: tuple[str, ...] = (),
    prohibited: tuple[str, ...] = (),
    disclosures: tuple[str, ...] = (),
    fallback: str,
) -> SlotPolicy:
    return SlotPolicy(
        role=role,
        exactness=exactness,
        grounding=grounding,
        allowed_sources=sources,
        treatment_ceiling=ceiling,
        prohibited_operations=prohibited,
        attribution_disclosure=disclosures,
        fallback=fallback,
    )


def _min_ceiling(*values: Ceiling) -> Ceiling:
    return min(values, key=CEILING_ORDER.index)


def derive_slots(
    profile: CreativeProfile,
    decision: RuleDecision,
    recipe: Recipe,
) -> tuple[SlotPolicy, ...]:
    ceiling = _min_ceiling(decision.treatment_ceiling, recipe.default_ceiling)
    grounding = set(decision.required_grounding)
    disclosures = set(decision.disclosures)
    slots: list[SlotPolicy] = []

    slots.append(
        _slot(
            "hook",
            "exact" if decision.exactness == "exact" else "faithful",
            _min_ceiling(ceiling, "effects"),
            grounding=tuple(
                item for item in ("claim_evidence", "brand_pack") if item in grounding
            ),
            sources=("deterministic",),
            prohibited=("generated_headline_text", "unsupported_claim"),
            disclosures=tuple(sorted(disclosures)),
            fallback="Rewrite within approved claims and re-review.",
        )
    )

    if profile.product_role != "none":
        slots.append(
            _slot(
                "product_reference",
                "exact",
                _min_ceiling(ceiling, "match"),
                grounding=("product_facts",),
                sources=("owned", "approved_product_reference"),
                prohibited=(
                    "generated_product_identity",
                    "generated_logo",
                    "generated_label",
                ),
                disclosures=("product_identity_exact",),
                fallback="Request a replacement exact product asset.",
            )
        )

    # Generation-last cascade: owned, stock/archive, manual import and
    # deterministic composition are searched before paid generation is even
    # eligible. "generated" is last and still needs an exhaustion record.
    visual_cascade = ["owned", "official_api", "manual_import", "deterministic"]
    if profile.product_role == "none" and ceiling in {
        "ai_enhance",
        "composite",
        "ai_created",
    }:
        visual_cascade.append("generated")

    slots.append(
        _slot(
            "primary_visual",
            "generic" if profile.product_role == "none" else "exact",
            ceiling,
            grounding=tuple(
                item
                for item in ("claim_evidence", "source_context")
                if item in grounding
            ),
            sources=tuple(visual_cascade),
            prohibited=("unsourced_claim", "editorial_in_commercial"),
            disclosures=tuple(sorted(disclosures)),
            fallback="Deterministic composition from owned assets.",
        )
    )

    if "claim_evidence" in grounding:
        slots.append(
            _slot(
                "claim_or_proof",
                "exact",
                _min_ceiling(ceiling, "effects"),
                grounding=("claim_evidence",),
                sources=("deterministic", "approved_source"),
                prohibited=("unsourced_claim", "altered_quotation"),
                disclosures=("source_context",),
                fallback="Remove the claim or replace it with an approved claim.",
            )
        )

    if profile.form in {"explainer", "commentary", "tutorial"}:
        slots.append(
            _slot(
                "diagram_or_demonstration",
                "faithful" if profile.form != "tutorial" else "exact",
                _min_ceiling(ceiling, "effects"),
                grounding=tuple(
                    item
                    for item in ("claim_evidence", "tutorial_steps", "source_context")
                    if item in grounding
                ),
                sources=("deterministic", "real_capture", "approved_source"),
                prohibited=("fabricated_data", "fabricated_step"),
                disclosures=("source_context",) if "news" in profile.subjects else (),
                fallback="Simplify or recapture; do not invent missing steps or data.",
            )
        )

    slots.append(
        _slot(
            "cta",
            "exact"
            if (
                profile.usage.cta_required
                or "sell" in {profile.primary_outcome, *profile.secondary_outcomes}
            )
            else "faithful",
            _min_ceiling(ceiling, "effects"),
            grounding=tuple(
                item for item in ("product_facts", "brand_pack") if item in grounding
            ),
            sources=("deterministic",),
            prohibited=("generated_cta_text", "unsupported_offer"),
            disclosures=tuple(sorted(disclosures)),
            fallback="Use the approved CTA or omit the commercial action.",
        )
    )

    slots.append(
        _slot(
            "captions",
            "exact",
            "effects",
            sources=("deterministic",),
            prohibited=("generated_exact_text", "unreviewed_caption"),
            disclosures=("captions_required",),
            fallback="Manual correction against the approved script.",
        )
    )

    slots.append(
        _slot(
            "narration",
            "faithful",
            "as_is",
            grounding=tuple(item for item in ("claim_evidence",) if item in grounding),
            sources=("local_tts", "hosted_voice"),
            prohibited=("voice_clone_without_consent", "unsupported_claim"),
            disclosures=("synthetic_voice",),
            fallback="Captions-only or approved human recording.",
        )
    )

    slots.append(
        _slot(
            "music",
            "generic",
            "as_is",
            sources=("owned", "licensed"),
            prohibited=("unlicensed_music", "wmc_claim"),
            disclosures=("music_licence",),
            fallback="No music or approved owned track.",
        )
    )

    return tuple(slots)


def build_plan(profile: CreativeProfile) -> CreativePlan:
    decision = evaluate_profile(profile)
    selection = select_recipe(profile)
    slots = derive_slots(profile, decision, selection.recipe)
    return CreativePlan(
        profile_hash=sha256_json(profile.model_dump(mode="json")),
        profile=profile,
        decision=decision,
        recipe_selection=selection,
        slots=slots,
        production=ProductionPlan(
            preset_id=profile.production_preset_id,
            style_id=profile.visual_style_id,
            style_map=profile.style_map,
            exact_items=profile.exact_items,
        ),
        alternatives=(selection.runner_up or "No alternative recipe matched.",),
    )


def plan_payload(plan: CreativePlan) -> dict[str, Any]:
    return plan.model_dump(mode="json")

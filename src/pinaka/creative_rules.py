from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from .models import StrictModel
from .utils import sha256_json

GUIDE_VERSION = "0.1"

Orientation = Literal["vertical", "horizontal", "square", "custom"]
ProductRole = Literal["none", "cameo", "hero"]
CreativeForm = Literal[
    "direct", "explainer", "tutorial", "commentary", "comedy", "story"
]
Structure = Literal["linear", "list", "loop", "chapters"]
Subject = Literal[
    "lifestyle",
    "tech",
    "money",
    "health",
    "food",
    "science",
    "history",
    "news",
    "sports_pop_culture",
    "kids",
    "fiction",
]
Tone = Literal[
    "neutral",
    "serious",
    "friendly",
    "humorous",
    "dramatic",
    "emotional",
    "urgent",
    "calm",
]
Outcome = Literal[
    "inform",
    "teach",
    "entertain",
    "evoke_emotion",
    "drive_engagement",
    "bring_viewers_back",
    "sell",
    "build_trust",
]
TruthPolicy = Literal[
    "source_bound",
    "mixed_fact_opinion",
    "fictional",
    "satire",
    "reconstruction",
]
UseClass = Literal["commercial", "noncommercial", "editorial"]
QualityTier = Literal["test", "standard", "flagship"]
SeriesPattern = Literal["one_off", "series"]
ProductComplexity = Literal[
    "simple_pack",
    "variants_or_bundles",
    "needs_demonstration",
    "service_or_software",
]
AssetReadiness = Literal[
    "photos_and_footage",
    "photos_only",
    "low_quality_assets",
    "no_assets",
]
SourceMaterial = Literal[
    "own_footage_or_voice",
    "stock_or_archive",
    "idea_only",
    "third_party_clips",
]
ContentDepth = Literal["single_message", "multi_beat", "long_form"]
Risk = Literal["low", "medium", "high"]
Ceiling = Literal["as_is", "match", "effects", "ai_enhance", "composite", "ai_created"]
Exactness = Literal["exact", "faithful", "generic"]

CEILING_ORDER: tuple[Ceiling, ...] = (
    "as_is",
    "match",
    "effects",
    "ai_enhance",
    "composite",
    "ai_created",
)
RISK_ORDER: tuple[Risk, ...] = ("low", "medium", "high")

DEFAULT_CEILING_BY_FORM: dict[str, Ceiling] = {
    "direct": "composite",
    "explainer": "ai_enhance",
    "tutorial": "match",
    "commentary": "match",
    "comedy": "ai_created",
    "story": "ai_created",
}


class DeliverableProfile(StrictModel):
    output_kind: Literal["video", "still"]
    duration_seconds: Decimal = Field(gt=0, le=3600)
    orientation: Orientation
    aspect_ratio: str = Field(min_length=1, max_length=20)
    width_px: int = Field(gt=0, le=16384)
    height_px: int = Field(gt=0, le=16384)
    destination: str = Field(default="social", min_length=1, max_length=200)
    language: str = Field(default="en", min_length=2, max_length=20)


class UsageProfile(StrictModel):
    use_class: UseClass = "commercial"
    monetized: bool = False
    advertising: bool = False
    sponsored: bool = False
    modification_planned: bool = True
    attribution_can_be_delivered: bool = True
    cta_required: bool = False
    sponsor_disclosure_required: bool = False
    platforms: tuple[str, ...] = ()
    territories: tuple[str, ...] = ()


class AudienceProfile(StrictModel):
    description: str = Field(default="General audience", min_length=1, max_length=500)
    age_band: str | None = Field(default=None, max_length=50)
    caption_required: bool = True
    narration_required: bool = True
    accessibility_notes: tuple[str, ...] = ()


class CreativeProfile(StrictModel):
    schema_version: str = "creative-profile/v1"
    deliverable: DeliverableProfile
    product_role: ProductRole = "none"
    form: CreativeForm = "explainer"
    structures: tuple[Structure, ...] = Field(default=("linear",), min_length=1)
    subjects: tuple[Subject, ...] = Field(default=("lifestyle",), min_length=1)
    tones: tuple[Tone, ...] = Field(default=("neutral",), min_length=1)
    primary_outcome: Outcome = "inform"
    secondary_outcomes: tuple[Outcome, ...] = ()
    truth_policy: TruthPolicy = "source_bound"
    usage: UsageProfile = Field(default_factory=UsageProfile)
    audience: AudienceProfile = Field(default_factory=AudienceProfile)
    quality_tier: QualityTier = "standard"
    series_pattern: SeriesPattern = "one_off"
    product_complexity: ProductComplexity | None = None
    asset_readiness: AssetReadiness = "photos_and_footage"
    source_material: SourceMaterial = "own_footage_or_voice"
    content_depth: ContentDepth = "single_message"
    realistic_synthetic: bool = False
    heavy_generation: bool = False
    production_preset_id: str = "cinematic-story"
    visual_style_id: str = "cinematic-live-action"
    style_map: dict[str, str] = Field(default_factory=dict)
    exact_items: tuple[str, ...] = ()


class RuleMatch(StrictModel):
    rule_id: str
    title: str
    reason: str
    risk: Risk | None = None
    treatment_ceiling: Ceiling | None = None
    gates: tuple[str, ...] = ()
    grounding: tuple[str, ...] = ()
    disclosures: tuple[str, ...] = ()


class RuleDecision(StrictModel):
    schema_version: str = "creative-rule-decision/v1"
    guide_version: str = GUIDE_VERSION
    profile_hash: str
    matched_rules: tuple[RuleMatch, ...]
    risk: Risk
    treatment_ceiling: Ceiling
    required_gates: tuple[str, ...]
    required_grounding: tuple[str, ...]
    disclosures: tuple[str, ...]
    exactness: Exactness
    blocking: tuple[str, ...] = ()
    needs_human_input: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()


def _match(
    rule_id: str,
    title: str,
    reason: str,
    *,
    risk: Risk | None = None,
    ceiling: Ceiling | None = None,
    gates: tuple[str, ...] = (),
    grounding: tuple[str, ...] = (),
    disclosures: tuple[str, ...] = (),
) -> RuleMatch:
    return RuleMatch(
        rule_id=rule_id,
        title=title,
        reason=reason,
        risk=risk,
        treatment_ceiling=ceiling,
        gates=gates,
        grounding=grounding,
        disclosures=disclosures,
    )


def _outcomes(profile: CreativeProfile) -> set[str]:
    return {profile.primary_outcome, *profile.secondary_outcomes}


def _cr01(profile: CreativeProfile) -> RuleMatch | None:
    if profile.product_role != "none" and profile.asset_readiness in {
        "low_quality_assets",
        "no_assets",
    }:
        return _match(
            "CR-01",
            "Hero/cameo product with missing or low-quality assets",
            "A featured product has no acceptable exact asset; product shots require approval.",
            risk="medium",
            ceiling="match",
            gates=("product_asset_approval",),
            grounding=("product_facts",),
        )
    return None


def _cr02(profile: CreativeProfile) -> RuleMatch | None:
    if "sell" in _outcomes(profile):
        return _match(
            "CR-02",
            "Sell outcome with any form",
            "Product, price, offer, logo, CTA and required wording must remain exact components.",
            risk="medium",
            ceiling="composite",
            gates=("commercial_terms_review", "claim_review"),
            grounding=("product_facts", "brand_pack"),
            disclosures=("advertising_disclosure",),
        )
    return None


def _cr03(profile: CreativeProfile) -> RuleMatch | None:
    promotional = (
        profile.usage.advertising
        or profile.usage.sponsored
        or "sell" in _outcomes(profile)
    )
    if promotional and ("humorous" in profile.tones or "dramatic" in profile.tones):
        return _match(
            "CR-03",
            "Promotional work with humorous or dramatic tone",
            "Humour or tension belongs in setup, characters or edit, not product identity or unsupported claims.",
            risk="medium",
            gates=("brief_brand_fit",),
        )
    return None


def _cr04(profile: CreativeProfile) -> RuleMatch | None:
    if profile.form == "tutorial":
        return _match(
            "CR-04",
            "Tutorial with instructional steps",
            "Record the real task in order; recapture missing steps rather than fabricating them.",
            risk="medium",
            ceiling="match",
            gates=("reproducibility_review",),
            grounding=("tutorial_steps",),
        )
    return None


def _cr05(profile: CreativeProfile) -> RuleMatch | None:
    if profile.form == "commentary" or "news" in profile.subjects:
        return _match(
            "CR-05",
            "Commentary or news",
            "Authenticated, attributed material must be clearly separated from commentary.",
            risk="medium",
            ceiling="match",
            gates=("context_review", "rights_review"),
            grounding=("source_context", "claim_evidence"),
            disclosures=("source_context_label",),
        )
    return None


def _cr06(profile: CreativeProfile) -> RuleMatch | None:
    if "news" in profile.subjects and "humorous" in profile.tones:
        return _match(
            "CR-06",
            "News with comedy",
            "Authentic and stylised-satire segments must be distinct and clearly signalled.",
            risk="medium",
            gates=("satire_separation_review",),
            disclosures=("satire_label",),
        )
    return None


def _cr07(profile: CreativeProfile) -> RuleMatch | None:
    if set(profile.subjects) & {"money", "health"}:
        return _match(
            "CR-07",
            "Money or health",
            "Sourced explainer with approved claims, clear diagrams and designated sign-off.",
            risk="medium",
            ceiling="ai_enhance",
            gates=("claim_review", "designated_signoff"),
            grounding=("claim_evidence",),
        )
    return None


def _cr08(profile: CreativeProfile) -> RuleMatch | None:
    if "kids" in profile.subjects:
        return _match(
            "CR-08",
            "Kids",
            "Match language, pacing, imagery and sound to the intended age and platform.",
            risk="medium",
            gates=("age_platform_review",),
            grounding=("platform_policy",),
        )
    return None


def _cr09(profile: CreativeProfile) -> RuleMatch | None:
    if profile.form in {"comedy", "story"} and profile.realistic_synthetic:
        return _match(
            "CR-09",
            "Comedy/story with realistic synthetic visuals",
            "Every realistic synthetic shot needs treatment, disclosure and continuity review.",
            risk="medium",
            ceiling="ai_created",
            gates=("realism_disclosure_review", "story_continuity_review"),
            grounding=("story_bible",),
            disclosures=("synthetic_media_disclosure",),
        )
    return None


def _cr10(profile: CreativeProfile) -> RuleMatch | None:
    if "sports_pop_culture" in profile.subjects and profile.realistic_synthetic:
        return _match(
            "CR-10",
            "Sports/pop culture with generated visuals",
            "Inspect every identifiable likeness, character, logo and mark before use.",
            risk="high",
            ceiling="ai_created",
            gates=("rights_review", "likeness_review"),
            grounding=("claim_evidence", "platform_policy"),
            disclosures=("synthetic_media_disclosure",),
        )
    return None


def _cr11(profile: CreativeProfile) -> RuleMatch | None:
    if (
        profile.deliverable.duration_seconds >= Decimal(120)
        and profile.heavy_generation
    ):
        return _match(
            "CR-11",
            "Long-form with heavy generation",
            "Allocate generated motion only to selected beats and compare cost against the cap.",
            risk="medium",
            ceiling="ai_created",
            gates=("cost_deadline_review",),
        )
    return None


def _cr12(profile: CreativeProfile) -> RuleMatch | None:
    if (
        profile.deliverable.duration_seconds <= Decimal(60)
        and profile.content_depth == "long_form"
    ):
        return _match(
            "CR-12",
            "Short format with long-form depth",
            "Select one question, claim or takeaway; split additional ideas into separate shorts.",
            risk="medium",
            gates=("self_contained_review",),
        )
    return None


def _cr13(profile: CreativeProfile) -> RuleMatch | None:
    if (
        profile.asset_readiness == "no_assets"
        and profile.source_material == "idea_only"
    ):
        return _match(
            "CR-13",
            "Idea-only with missing assets",
            "Classify every missing slot before choosing source or treatment.",
            risk="medium",
            ceiling="ai_created",
            gates=("generation_eligibility_review",),
        )
    return None


def _cr14(profile: CreativeProfile) -> RuleMatch | None:
    urgent_emotional = "urgent" in profile.tones and (
        "evoke_emotion" in _outcomes(profile) or "emotional" in profile.tones
    )
    calm_sell = "calm" in profile.tones and "sell" in _outcomes(profile)
    if urgent_emotional or calm_sell:
        return _match(
            "CR-14",
            "Urgent + emotional, or calm + sell",
            "Give the feeling and the commercial action separate readable beats.",
            gates=("animatic_review",),
        )
    return None


def _cr15(profile: CreativeProfile) -> RuleMatch | None:
    if "list" in profile.structures and profile.series_pattern == "series":
        return _match(
            "CR-15",
            "List in a recurring series",
            "Reuse the recognizable structure while varying the episode's hooks, items and payoff.",
            gates=("series_novelty_review", "series_continuity_review"),
            grounding=("series_bible",),
        )
    return None


RULES = (
    _cr01,
    _cr02,
    _cr03,
    _cr04,
    _cr05,
    _cr06,
    _cr07,
    _cr08,
    _cr09,
    _cr10,
    _cr11,
    _cr12,
    _cr13,
    _cr14,
    _cr15,
)


def _max_risk(values: set[Risk]) -> Risk:
    return max(values, key=RISK_ORDER.index)


def _min_ceiling(values: set[Ceiling]) -> Ceiling:
    return min(values, key=CEILING_ORDER.index)


def evaluate_profile(profile: CreativeProfile) -> RuleDecision:
    matched = tuple(match for rule in RULES if (match := rule(profile)) is not None)
    outcomes = _outcomes(profile)
    subjects = set(profile.subjects)

    risks: set[Risk] = {"low"}
    ceilings: set[Ceiling] = {DEFAULT_CEILING_BY_FORM[profile.form]}
    gates: set[str] = {"brief_review"}
    grounding: set[str] = set()
    disclosures: set[str] = set()
    blocking: set[str] = set()
    human_input: set[str] = set()

    if profile.product_role != "none":
        risks.add("medium")
        gates.add("product_asset_approval")
        grounding.add("product_facts")
    if "sell" in outcomes or profile.usage.cta_required:
        gates.add("commercial_terms_review")
        grounding.add("product_facts")
    if profile.usage.advertising or profile.usage.sponsored:
        gates.add("sponsor_disclosure_review")
        grounding.add("brand_pack")
        grounding.add("platform_policy")
        disclosures.add("advertising_disclosure")
        if profile.usage.sponsored:
            disclosures.add("sponsor_disclosure")
    if profile.usage.modification_planned and profile.source_material in {
        "stock_or_archive",
        "third_party_clips",
    }:
        gates.add("rights_review")
    if profile.form == "tutorial":
        gates.add("reproducibility_review")
        grounding.add("tutorial_steps")
    if profile.form == "commentary" or subjects & {"news", "history"}:
        gates.add("context_review")
        grounding.add("source_context")
        risks.add("medium")
    if subjects & {"money", "health", "science", "news", "history"}:
        grounding.add("claim_evidence")
        risks.add("medium")
    if subjects & {"money", "health"}:
        risks.add("medium")
    if "kids" in subjects:
        gates.add("age_platform_review")
        grounding.add("platform_policy")
        risks.add("medium")
    if profile.truth_policy in {"fictional", "satire"} or profile.form in {
        "comedy",
        "story",
    }:
        grounding.add("story_bible")
    if profile.series_pattern == "series":
        gates.add("series_continuity_review")
        grounding.add("series_bible")
    if profile.realistic_synthetic:
        risks.add("high")
        gates.add("realism_disclosure_review")
        disclosures.add("synthetic_media_disclosure")
    if profile.truth_policy == "satire":
        disclosures.add("satire_label")
    if profile.truth_policy == "reconstruction":
        disclosures.add("reconstruction_label")
    if (
        profile.realistic_synthetic
        and profile.truth_policy in {"source_bound", "mixed_fact_opinion"}
        and ("news" in subjects or profile.form == "commentary")
    ):
        blocking.add("Realistic synthetic factual event footage is blocked")
    if profile.usage.use_class == "editorial" and (
        profile.usage.advertising or profile.usage.sponsored
    ):
        blocking.add("Editorial use cannot be used for advertising or sponsorship")
    if profile.form == "tutorial" and profile.source_material != "own_footage_or_voice":
        human_input.add(
            "Tutorial steps require real capture or approved demonstration material"
        )

    for match in matched:
        if match.risk:
            risks.add(match.risk)
        if match.treatment_ceiling:
            ceilings.add(match.treatment_ceiling)
        gates.update(match.gates)
        grounding.update(match.grounding)
        disclosures.update(match.disclosures)

    exactness: Exactness
    if (
        profile.product_role != "none"
        or "sell" in outcomes
        or profile.usage.cta_required
        or profile.usage.advertising
        or profile.usage.sponsored
    ):
        exactness = "exact"
    elif profile.form == "commentary" or subjects & {"news", "history"}:
        exactness = "faithful"
    else:
        exactness = "generic"

    return RuleDecision(
        profile_hash=sha256_json(profile.model_dump(mode="json")),
        matched_rules=matched,
        risk=_max_risk(risks),
        treatment_ceiling=_min_ceiling(ceilings),
        required_gates=tuple(sorted(gates)),
        required_grounding=tuple(sorted(grounding)),
        disclosures=tuple(sorted(disclosures)),
        exactness=exactness,
        blocking=tuple(sorted(blocking)),
        needs_human_input=tuple(sorted(human_input)),
        alternatives=(
            "Reduce the treatment ceiling or split the deliverable when constraints conflict.",
        )
        if blocking or human_input
        else (),
    )


def profile_payload(decision: RuleDecision) -> dict[str, Any]:
    return decision.model_dump(mode="json")

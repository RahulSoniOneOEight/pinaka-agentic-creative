from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from .creative_rules import (
    AudienceProfile,
    CreativeProfile,
    DeliverableProfile,
    UsageProfile,
)
from .enums import ApprovalStage, CreativeElementKind, DecisionProvenance, DecisionRisk
from .models import (
    BriefDecisionCreate,
    TextBriefRequest,
    WorkableBriefCreate,
)
from .production import infer_preset, infer_style

DEFAULT_DURATION_SECONDS = Decimal(10)
MAX_SHOTS = 6
SECONDS_PER_SHOT = Decimal("3.5")

_AUDIENCE_PATTERNS = (
    (r"\b(children|kids|toddlers?|preschool)\b", "Children"),
    (r"\b(teens?|teenagers?)\b", "Teenagers"),
    (r"\b(adults?|grown-ups?)\b", "Adults"),
    (r"\b(professionals?|executives?)\b", "Professionals"),
)

_FAMILY_PATTERNS = (
    (r"\brhyme\b", "rhyme"),
    (r"\b(explainer|tutorial|how[- ]to)\b", "explainer"),
    (r"\b(ad|advert|advertisement|promo|commercial)\b", "promo"),
    (r"\b(story|tale)\b", "story"),
)

_DESTINATIONS = ("youtube", "instagram", "tiktok", "reels", "shorts")

_CLAIM_PATTERNS = (
    r"[$â‚¹â‚¬Â£]\s?\d",
    r"\b\d+(?:\.\d+)?\s?%",
    r"\b(best|number one|#1|guaranteed|cures?|clinically proven)\b",
)

_PROPOSED_ELEMENTS = (
    ("duck", CreativeElementKind.CHARACTER, "Original duckling character"),
    ("star", CreativeElementKind.CHARACTER, "Original star character"),
    ("child", CreativeElementKind.CHARACTER, "Original child character"),
    ("cat", CreativeElementKind.CHARACTER, "Original cat character"),
    ("dog", CreativeElementKind.CHARACTER, "Original dog character"),
    ("pond", CreativeElementKind.LOCATION, "Pond environment"),
    ("sky", CreativeElementKind.LOCATION, "Sky environment"),
    ("room", CreativeElementKind.LOCATION, "Interior room"),
    ("beach", CreativeElementKind.LOCATION, "Beach environment"),
)

_VERTICAL = {"tiktok", "reels", "shorts"}


def _duration(text: str) -> Decimal | None:
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*[-\s]?\s*(seconds?|secs?|s)\b", text, flags=re.IGNORECASE
    )
    if match:
        return Decimal(match.group(1))
    minutes = re.search(
        r"(\d+(?:\.\d+)?)\s*[-\s]?\s*(minutes?|mins?)\b", text, flags=re.IGNORECASE
    )
    if minutes:
        return Decimal(minutes.group(1)) * 60
    return None


def _audience(text: str) -> str | None:
    for pattern, label in _AUDIENCE_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return label
    return None


def _age_range(text: str) -> str | None:
    match = re.search(
        r"age[sd]?\s*(\d+)\s*(?:-|to|â€“)\s*(\d+)", text, flags=re.IGNORECASE
    )
    if match:
        return f"{match.group(1)} to {match.group(2)}"
    single = re.search(r"(\d+)\s*(?:year|yr)s?[- ]old", text, flags=re.IGNORECASE)
    if single:
        return single.group(1)
    return None


def _family(text: str) -> str:
    for pattern, label in _FAMILY_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return label
    return "story"


def _destination(text: str) -> str | None:
    for name in _DESTINATIONS:
        if re.search(rf"\b{name}\b", text, flags=re.IGNORECASE):
            return name
    return None


def _spoken_copy(text: str) -> str | None:
    match = re.search(r"[\"â€œ]([^\"â€]{3,})[\"â€]", text)
    return match.group(1).strip() if match else None


def _has_unverified_claim(text: str) -> bool:
    return any(
        re.search(pattern, text, flags=re.IGNORECASE) for pattern in _CLAIM_PATTERNS
    )


def analyze_text(request: TextBriefRequest) -> dict[str, Any]:
    """Deterministically derive planning facts from one raw creative request."""

    text = request.text
    duration = request.duration_seconds or _duration(text)
    destination = request.destination or _destination(text)
    audience = request.audience or _audience(text)
    age_range = _age_range(text)
    family = _family(text)
    copy = _spoken_copy(text)
    vertical = destination in _VERTICAL if destination else True
    return {
        "duration_seconds": duration,
        "duration_supplied": duration is not None,
        "audience": audience,
        "age_range": age_range,
        "content_family": family,
        "destination": destination,
        "output_format": request.output_format or ("9:16" if vertical else "16:9"),
        "language": request.language or "en",
        "spoken_copy": copy,
        "claims_detected": _has_unverified_claim(text),
        "subject": "kids"
        if re.search(r"\b(children|kids|toddler)\b", text, flags=re.IGNORECASE)
        else "general",
    }


def _beats(analysis: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    duration = analysis["duration_seconds"]
    count = min(
        MAX_SHOTS,
        max(1, int(Decimal(duration) / SECONDS_PER_SHOT + Decimal("0.5"))),
    )
    step = duration / count
    beats = []
    for index in range(count):
        start = step * index
        end = duration if index == count - 1 else step * (index + 1)
        beats.append(
            {
                "id": f"beat-{index + 1:02d}",
                "start": str(start),
                "end": str(end),
                "purpose": "establish" if index == 0 else "develop",
            }
        )
    return tuple(beats)


def _decisions(
    analysis: dict[str, Any], brief_duration: Decimal
) -> tuple[BriefDecisionCreate, ...]:
    decisions: list[BriefDecisionCreate] = [
        BriefDecisionCreate(
            field="project.duration",
            value=str(brief_duration),
            state=DecisionProvenance.SUPPLIED
            if analysis["duration_supplied"]
            else DecisionProvenance.ASSUMED,
            basis=(
                "Duration stated in the request"
                if analysis["duration_supplied"]
                else "No duration supplied; a ten-second review master was assumed"
            ),
            risk=DecisionRisk.LOW,
            reversible=True,
            review_gate=ApprovalStage.BRIEF,
        ),
        BriefDecisionCreate(
            field="classification.content_family",
            value=analysis["content_family"],
            state=DecisionProvenance.DERIVED,
            basis="Derived from the wording of the request",
            risk=DecisionRisk.LOW,
            reversible=True,
        ),
        BriefDecisionCreate(
            field="visual.style",
            value="clean, high-contrast, editable composition",
            state=DecisionProvenance.ASSUMED,
            basis="Audience-appropriate default for a short review master",
            risk=DecisionRisk.LOW,
            reversible=True,
            alternatives_considered=("paper-cut animation", "flat vector"),
            review_gate=ApprovalStage.ANIMATIC,
        ),
        BriefDecisionCreate(
            field="audio.voice",
            value="warm synthetic adult narration",
            state=DecisionProvenance.ASSUMED,
            basis="No voice direction supplied",
            risk=DecisionRisk.LOW,
            reversible=True,
            review_gate=ApprovalStage.ANIMATIC,
        ),
    ]
    if analysis["audience"]:
        decisions.append(
            BriefDecisionCreate(
                field="project.audience",
                value=analysis["audience"]
                + (f" (ages {analysis['age_range']})" if analysis["age_range"] else ""),
                state=DecisionProvenance.DERIVED,
                basis="Derived from the audience wording in the request",
                risk=DecisionRisk.LOW,
                reversible=True,
            )
        )
    else:
        decisions.append(
            BriefDecisionCreate(
                field="project.audience",
                value="general audience",
                state=DecisionProvenance.ASSUMED,
                basis="No audience supplied",
                risk=DecisionRisk.LOW,
                reversible=True,
                review_gate=ApprovalStage.BRIEF,
            )
        )
    if analysis["spoken_copy"]:
        decisions.append(
            BriefDecisionCreate(
                field="script.spoken_copy",
                value=analysis["spoken_copy"],
                state=DecisionProvenance.SUPPLIED,
                basis="Exact quoted copy in the request",
                risk=DecisionRisk.LOW,
                reversible=True,
            )
        )
    if analysis["claims_detected"]:
        decisions.append(
            BriefDecisionCreate(
                field="facts.claims",
                value=None,
                state=DecisionProvenance.BLOCKED,
                basis=(
                    "The request contains a price, percentage or superlative that "
                    "needs approved grounding"
                ),
                risk=DecisionRisk.HIGH,
                reversible=False,
                blocked_work=("final_approval", "publication"),
            )
        )
    return tuple(decisions)


def build_brief(
    artifact: dict[str, Any],
    analysis: dict[str, Any],
    project: Any,
) -> WorkableBriefCreate:
    """Assemble a governed brief contract from the analysis."""

    duration = analysis["duration_seconds"] or DEFAULT_DURATION_SECONDS
    audience = analysis["audience"] or "general audience"
    beats = _beats({**analysis, "duration_seconds": duration})
    return WorkableBriefCreate(
        source_artifact_id=artifact["id"],
        source_content_hash=artifact["content_hash"],
        objective=project.objective,
        audience=audience,
        deliverable=(
            f"{duration.normalize():f}s {analysis['output_format']} "
            f"{analysis['content_family']} review master"
        ),
        destination=analysis["destination"],
        classification={
            "content_family": analysis["content_family"],
            "subject": analysis["subject"],
            "production_lane": "deterministic_composition",
        },
        treatment={
            "concept": analysis["spoken_copy"] or project.objective,
            "visual_style": "clean, high-contrast, editable composition",
            "world": "derived per shot",
        },
        beats=beats,
        audio_plan={"voice": "warm synthetic adult narration", "music": "optional"},
        acceptance={
            "duration_seconds": str(duration),
            "exact_copy_required": bool(analysis["spoken_copy"]),
            "publication_authorized": False,
        },
        decisions=_decisions(analysis, duration),
    )


def propose_shots(
    analysis: dict[str, Any], beats: tuple[dict[str, Any], ...]
) -> list[dict[str, Any]]:
    actions = ("swim", "bob", "count", "wave", "bob", "still")
    copy = analysis["spoken_copy"] or ""
    count = 1
    for word, value in (("five", 5), ("four", 4), ("three", 3), ("two", 2)):
        if word in copy.lower():
            count = value
            break
    shots = []
    for index, beat in enumerate(beats, start=1):
        start = Decimal(beat["start"])
        end = Decimal(beat["end"])
        if index == 1:
            purpose = "establish"
        elif index == len(beats):
            purpose = "resolve"
        else:
            purpose = "develop"
        shots.append(
            {
                "stable_key": f"shot-{index:02d}",
                "ordinal": index,
                "beat": purpose,
                "duration_seconds": end - start,
                "narrative": copy or f"{beat['purpose'].capitalize()} shot {index}",
                "requirements": {
                    "text": "none",
                    "start": beat["start"],
                    "end": beat["end"],
                    "subject": {
                        "action": actions[(index - 1) % len(actions)],
                        "count": count,
                    },
                    "motion": {
                        "level": "M1",
                        "action": actions[(index - 1) % len(actions)],
                        "parts": ["body", "head", "wing"],
                        "secondary": ["water_ripple"],
                        "camera": "push_in",
                    },
                },
            }
        )
    return shots


def propose_elements(text: str) -> list[dict[str, Any]]:
    lowered = text.lower()
    proposals = []
    for token, kind, name in _PROPOSED_ELEMENTS:
        if re.search(rf"\b{token}s?\b", lowered):
            proposals.append(
                {
                    "stable_key": f"{kind.value}.{token}",
                    "kind": kind,
                    "name": name,
                    "specification": {"origin": "original", "protected": False},
                    "continuity": {},
                }
            )
    return proposals


def scene_plan_markdown(shots: list[dict[str, Any]], duration: Decimal) -> str:
    total = Decimal(str(duration))
    lines = [
        "# Scene plan",
        "",
        f"Total duration: {total.normalize():f}s",
        f"Shots: {len(shots)}",
        "",
    ]
    for shot in shots:
        shot_seconds = Decimal(str(shot["duration_seconds"]))
        lines.append(f"## {shot['stable_key']} ({shot_seconds.normalize():f}s)")
        lines.append("")
        lines.append(f"- Beat: {shot['beat']}")
        lines.append(f"- Action: {shot['narrative']}")
        lines.append("- Text: none")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_profile_from_analysis(
    project: Any,
    analysis: dict[str, Any],
    request: TextBriefRequest,
) -> CreativeProfile:
    """Turn the text analysis into the governed classification profile."""

    duration = Decimal(str(analysis["duration_seconds"] or DEFAULT_DURATION_SECONDS))
    output_format = analysis["output_format"]
    vertical = output_format in {"9:16", "vertical"}
    family = analysis["content_family"]
    subject = analysis["subject"]
    form = (
        "story"
        if family in {"rhyme", "story"}
        else "comedy"
        if family == "comedy"
        else "tutorial"
        if family == "tutorial"
        else "direct"
        if family in {"ads", "product"}
        else "explainer"
    )
    subjects = (
        ("kids", "fiction")
        if subject == "kids" and family in {"rhyme", "story"}
        else ("kids",)
        if subject == "kids"
        else ("lifestyle",)
    )
    tone = ("friendly",) if subject == "kids" else ("neutral",)
    outcome = (
        "entertain"
        if family in {"rhyme", "story", "comedy"}
        else "sell"
        if family in {"ads", "product"}
        else "inform"
    )
    truth = "fictional" if family in {"rhyme", "story", "comedy"} else "source_bound"
    source_material = "idea_only"
    preset_id = request.production_preset_id or infer_preset(
        content_family=family, subject=subject
    )
    style_id = request.visual_style_id or infer_style(
        content_family=family, subject=subject
    )
    return CreativeProfile(
        deliverable=DeliverableProfile(
            output_kind="video",
            duration_seconds=duration,
            orientation="vertical" if vertical else "horizontal",
            aspect_ratio=output_format,
            width_px=1080 if vertical else 1920,
            height_px=1920 if vertical else 1080,
            destination=analysis["destination"] or "social",
            language=analysis["language"],
        ),
        product_role="none",
        form=form,
        structures=(
            ("loop",)
            if re.search(r"\b(loop|seamless|repeat)\b", request.text, re.IGNORECASE)
            else ("linear",)
        ),
        subjects=subjects,
        tones=tone,
        primary_outcome=outcome,
        truth_policy=truth,
        usage=UsageProfile(
            use_class="commercial",
            monetized=project.max_budget.minor > 0,
            modification_planned=True,
            attribution_can_be_delivered=True,
        ),
        audience=AudienceProfile(
            description=analysis["audience"] or project.audience,
            age_band=analysis["age_range"],
            caption_required=True,
            narration_required=True,
        ),
        quality_tier="standard",
        series_pattern="one_off",
        asset_readiness="no_assets",
        source_material=source_material,
        content_depth="multi_beat" if duration >= 8 else "single_message",
        realistic_synthetic=False,
        heavy_generation=False,
        production_preset_id=preset_id,
        visual_style_id=style_id,
        style_map=dict(request.style_map),
        exact_items=tuple(request.exact_items),
    )

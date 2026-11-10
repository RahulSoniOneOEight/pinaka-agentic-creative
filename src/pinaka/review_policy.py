from __future__ import annotations

from pydantic import Field

from .enums import ValueEnum
from .judge import VisualJudgement
from .models import StrictModel


class ReviewDecision(ValueEnum):
    AUTO_ACCEPT = "auto_accept"
    HUMAN_REVIEW = "human_review"
    REJECT = "reject"


class ReviewPolicy(StrictModel):
    """Thresholds that decide when automated judgement replaces a human look."""

    min_confidence: float = Field(default=0.8, ge=0, le=1)
    min_tone: float = Field(default=0.6, ge=0, le=1)
    min_brand: float = Field(default=0.6, ge=0, le=1)
    min_composition: float = Field(default=0.6, ge=0, le=1)
    min_legibility: float = Field(default=0.6, ge=0, le=1)
    min_safety: float = Field(default=0.7, ge=0, le=1)
    allow_auto_accept: bool = True
    require_human_when_high_risk: bool = True


class CampaignRisk(StrictModel):
    """Risk signals that override automatic acceptance."""

    monetized: bool = False
    advertising: bool = False
    has_recognizable_people: bool = False
    makes_factual_claims: bool = False

    @property
    def high_risk(self) -> bool:
        return any(
            (
                self.monetized,
                self.advertising,
                self.has_recognizable_people,
                self.makes_factual_claims,
            )
        )

    def describe(self) -> str:
        flags = [
            name
            for name, value in (
                ("monetized", self.monetized),
                ("advertising", self.advertising),
                ("recognizable people", self.has_recognizable_people),
                ("factual claims", self.makes_factual_claims),
            )
            if value
        ]
        return ", ".join(flags) or "low risk"


def decide_review(
    judgement: VisualJudgement,
    policy: ReviewPolicy,
    *,
    technical_ok: bool = True,
    risk: CampaignRisk | None = None,
) -> dict[str, object]:
    """Turn a visual judgement into accept, review or reject with explicit reasons."""

    reasons: list[str] = []

    blocking = [
        finding for finding in judgement.findings if finding.severity == "blocking"
    ]
    if blocking:
        for finding in blocking:
            reasons.append(f"blocking:{finding.category}:{finding.detail}")
        return {
            "decision": ReviewDecision.REJECT.value,
            "reasons": reasons,
            "automated": True,
        }

    if not technical_ok:
        reasons.append("technical checks failed")
    if judgement.verdict != "accept":
        reasons.append(f"judge verdict was {judgement.verdict}")
    if judgement.confidence < policy.min_confidence:
        reasons.append(
            f"confidence {judgement.confidence:.2f} below {policy.min_confidence:.2f}"
        )

    thresholds = {
        "tone": (judgement.scores.tone, policy.min_tone),
        "brand": (judgement.scores.brand, policy.min_brand),
        "composition": (judgement.scores.composition, policy.min_composition),
        "legibility": (judgement.scores.legibility, policy.min_legibility),
        "safety": (judgement.scores.safety, policy.min_safety),
    }
    for name, (score, minimum) in thresholds.items():
        if score < minimum:
            reasons.append(f"{name} {score:.2f} below {minimum:.2f}")

    warnings = [
        finding for finding in judgement.findings if finding.severity == "warning"
    ]
    for finding in warnings:
        reasons.append(f"warning:{finding.category}:{finding.detail}")

    if policy.require_human_when_high_risk and risk is not None and risk.high_risk:
        reasons.append(f"human review required for a {risk.describe()} campaign")

    if reasons:
        return {
            "decision": ReviewDecision.HUMAN_REVIEW.value,
            "reasons": reasons,
            "automated": True,
        }

    if not policy.allow_auto_accept:
        return {
            "decision": ReviewDecision.HUMAN_REVIEW.value,
            "reasons": ["automatic acceptance is disabled by policy"],
            "automated": True,
        }

    return {
        "decision": ReviewDecision.AUTO_ACCEPT.value,
        "reasons": ["all thresholds met with no blocking findings"],
        "automated": True,
    }

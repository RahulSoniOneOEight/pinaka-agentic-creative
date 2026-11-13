from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import StrictModel

QualityPurpose = Literal["production", "flow_fixture"]
QualityDimension = Literal[
    "character",
    "world",
    "story",
    "motion",
    "composition",
    "captions",
    "audio",
    "brand",
    "overall",
]
QUALITY_DIMENSIONS: tuple[str, ...] = (
    "character",
    "world",
    "story",
    "motion",
    "composition",
    "captions",
    "audio",
    "brand",
    "overall",
)
ReviewerKind = Literal["human", "model", "automated"]
DeliveryStatus = Literal["planned", "delivered", "blocked"]


class QualityContractCreate(StrictModel):
    """The project-specific definition of good, frozen before delivery."""

    purpose: QualityPurpose = "production"
    publication_allowed: bool = True
    human_review_required: bool = True
    review_required: bool = True
    hard_minimums: dict[str, str] = Field(default_factory=dict)
    must_have_beats: tuple[str, ...] = Field(min_length=1)
    review_dimensions: tuple[str, ...] = QUALITY_DIMENSIONS

    @model_validator(mode="after")
    def contract_is_consistent(self) -> QualityContractCreate:
        if len(self.must_have_beats) != len(set(self.must_have_beats)):
            raise ValueError("must_have_beats must be unique")
        if len(self.review_dimensions) != len(set(self.review_dimensions)):
            raise ValueError("review_dimensions must be unique")
        if self.purpose == "flow_fixture" and self.publication_allowed:
            raise ValueError("A flow fixture cannot allow publication")
        return self


class IntentBeatDelivery(StrictModel):
    beat_id: str = Field(min_length=1, max_length=120)
    status: DeliveryStatus = "planned"
    shot_keys: tuple[str, ...] = ()
    asset_ids: tuple[str, ...] = ()
    audio_evidence: str | None = Field(default=None, max_length=500)
    caption_evidence: str | None = Field(default=None, max_length=500)
    evidence: tuple[str, ...] = ()

    @model_validator(mode="after")
    def delivered_needs_evidence(self) -> IntentBeatDelivery:
        if self.status == "delivered" and not self.evidence:
            raise ValueError("A delivered intent beat requires evidence")
        return self


class IntentMatrixCreate(StrictModel):
    beats: tuple[IntentBeatDelivery, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def beat_ids_are_unique(self) -> IntentMatrixCreate:
        ids = [beat.beat_id for beat in self.beats]
        if len(ids) != len(set(ids)):
            raise ValueError("Intent beat ids must be unique")
        return self


class CreativeReviewCreate(StrictModel):
    artifact_id: str = Field(min_length=1, max_length=120)
    artifact_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer: str = Field(min_length=1, max_length=200)
    reviewer_kind: ReviewerKind
    scores: dict[str, int] = Field(default_factory=dict)
    notes: str | None = Field(default=None, max_length=4000)
    verdict: Literal["pass", "revise", "block"] = "revise"

    @model_validator(mode="after")
    def scores_are_complete_and_consistent(self) -> CreativeReviewCreate:
        missing = set(QUALITY_DIMENSIONS) - set(self.scores)
        extra = set(self.scores) - set(QUALITY_DIMENSIONS)
        if missing:
            raise ValueError(
                "Creative review is missing scores: " + ", ".join(sorted(missing))
            )
        if extra:
            raise ValueError(
                "Unknown creative review dimensions: " + ", ".join(sorted(extra))
            )
        for dimension, score in self.scores.items():
            if score < 1 or score > 5:
                raise ValueError(f"Score for {dimension} must be between 1 and 5")
        average = sum(self.scores.values()) / len(self.scores)
        if self.verdict == "pass" and any(score < 3 for score in self.scores.values()):
            raise ValueError("A passing review cannot have a score below 3")
        if self.verdict == "pass" and average < 4.0:
            raise ValueError("A passing review needs an average score of at least 4.0")
        return self

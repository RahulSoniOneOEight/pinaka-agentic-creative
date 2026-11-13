from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import StrictModel

CritiqueStage = Literal[
    "script",
    "shots",
    "style",
    "assets",
    "motion",
    "visual",
    "captions",
    "audio",
    "delivery",
]
CritiqueSeverity = Literal["blocker", "high", "medium", "low"]
CritiqueVerdict = Literal["pass", "revise", "block"]
ReviewerKind = Literal["automated", "model", "human"]
FindingStatus = Literal["open", "corrected", "accepted"]


class CritiqueFinding(StrictModel):
    finding_id: str | None = Field(default=None, max_length=60)
    dimension: str = Field(min_length=1, max_length=80)
    severity: CritiqueSeverity
    summary: str = Field(min_length=1, max_length=2000)
    evidence: tuple[str, ...] = ()
    metric: str | None = Field(default=None, max_length=200)
    threshold: str | None = Field(default=None, max_length=200)
    corrective_action: str | None = Field(default=None, max_length=2000)
    status: FindingStatus = "open"
    correction_evidence: tuple[str, ...] = ()


class StageCritiqueCreate(StrictModel):
    stage: CritiqueStage
    subject_type: Literal["project", "artifact", "script", "shot", "slot"]
    subject_id: str = Field(min_length=1, max_length=120)
    subject_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    reviewer: str = Field(min_length=1, max_length=200)
    reviewer_kind: ReviewerKind
    findings: tuple[CritiqueFinding, ...] = ()
    verdict: CritiqueVerdict
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def high_findings_need_actions(self) -> StageCritiqueCreate:
        open_high = [
            finding
            for finding in self.findings
            if finding.status == "open" and finding.severity in {"blocker", "high"}
        ]
        missing_action = [
            finding.summary for finding in open_high if not finding.corrective_action
        ]
        if missing_action:
            raise ValueError(
                "Every open blocker/high finding needs a corrective_action: "
                + "; ".join(missing_action)
            )
        if self.verdict == "pass" and open_high:
            raise ValueError("A critique with open blocker/high findings cannot pass")
        return self


class StageCritiqueRecord(StrictModel):
    id: str
    project_id: str
    stage: CritiqueStage
    subject_type: str
    subject_id: str
    subject_hash: str | None = None
    reviewer: str
    reviewer_kind: ReviewerKind
    verdict: CritiqueVerdict
    findings: tuple[CritiqueFinding, ...]
    notes: str | None = None
    created_at: str


class CorrectiveActionClose(StrictModel):
    closed_by: str = Field(min_length=1, max_length=200)
    evidence: tuple[str, ...] = Field(min_length=1)
    result: str = Field(min_length=1, max_length=2000)

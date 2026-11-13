from __future__ import annotations

import pytest
from pydantic import ValidationError

from pinaka.contracts.quality import (
    CreativeReviewCreate,
    IntentBeatDelivery,
    IntentMatrixCreate,
    QualityContractCreate,
)
from pinaka.enums import ArtifactKind
from pinaka.errors import ApprovalRequiredError, ConflictError

from .conftest import create_project


def _contract(*, fixture: bool = True, human: bool = False, review: bool = False):
    return QualityContractCreate(
        purpose="flow_fixture" if fixture else "production",
        publication_allowed=not fixture,
        human_review_required=human,
        review_required=review,
        hard_minimums={"resolution": "1080x1920", "fps": "24"},
        must_have_beats=("beat-a",),
    )


def _intent():
    return IntentMatrixCreate(
        beats=(
            IntentBeatDelivery(
                beat_id="beat-a",
                status="delivered",
                evidence=("shot:shot-01",),
            ),
        )
    )


def _review(artifact, *, human: bool):
    return CreativeReviewCreate(
        artifact_id=artifact["id"],
        artifact_hash=artifact["content_hash"],
        reviewer="qc",
        reviewer_kind="human" if human else "model",
        scores={
            key: 4
            for key in (
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
        },
        verdict="pass",
    )


def test_fixture_contract_needs_intent_but_not_review(controller):
    project = create_project(controller)
    artifact = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/final.mp4",
        content=b"final",
        operation={"type": "test_render"},
    )
    controller.record_quality_contract(project.id, _contract(fixture=True))
    with pytest.raises(ConflictError):
        controller.record_intent_matrix(
            project.id,
            IntentMatrixCreate(
                beats=(
                    IntentBeatDelivery(
                        beat_id="beat-a",
                        status="planned",
                    ),
                )
            ),
        )
    controller.record_intent_matrix(project.id, _intent())
    readiness = controller.require_quality_readiness(
        project.id, artifact_id=artifact["id"]
    )
    assert readiness["passed"] is True
    assert readiness["contract"]["publication_allowed"] is False


def test_production_contract_requires_human_review(controller):
    project = create_project(controller)
    artifact = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/final.mp4",
        content=b"final",
        operation={"type": "test_render"},
    )
    controller.record_quality_contract(
        project.id, _contract(fixture=False, human=True, review=True)
    )
    controller.record_intent_matrix(project.id, _intent())
    controller.record_creative_review(project.id, _review(artifact, human=False))
    with pytest.raises(ApprovalRequiredError, match="human reviewer"):
        controller.require_quality_readiness(project.id, artifact_id=artifact["id"])
    controller.record_creative_review(project.id, _review(artifact, human=True))
    readiness = controller.require_quality_readiness(
        project.id, artifact_id=artifact["id"]
    )
    assert readiness["passed"] is True


def test_fixture_cannot_allow_publication():
    with pytest.raises(ValidationError):
        QualityContractCreate(
            purpose="flow_fixture",
            publication_allowed=True,
            must_have_beats=("beat-a",),
        )

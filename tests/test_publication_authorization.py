from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pinaka.enums import ApprovalStage, ProjectStatus
from pinaka.errors import ConflictError
from pinaka.models import (
    ApprovalRequest,
    PublicationAuthorizationCreate,
)
from pinaka.publication import PublicationRequest, check_publication

from .conftest import advance_to_sourcing, record_final_candidate


def _finalised(controller):
    project, _, _ = advance_to_sourcing(controller)
    controller.transition_project(
        project.id, ProjectStatus.ANIMATIC_READY, reason="Animatic ready"
    )
    controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.ANIMATIC,
            approved_by="reviewer",
            scope={"animatic": "v1"},
        ),
    )
    controller.transition_project(
        project.id, ProjectStatus.FINAL_CANDIDATE, reason="Final rendered"
    )
    candidate = record_final_candidate(controller, project.id)
    controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.FINAL,
            approved_by="reviewer",
            scope={"candidate": "final-v1"},
            output_hash=candidate["content_hash"],
        ),
    )
    return project, candidate


def test_authorization_requires_a_final_approved_project(controller):
    project = advance_to_sourcing(controller)[0]
    with pytest.raises(ConflictError, match="final-approved"):
        controller.authorize_publication(
            project.id,
            PublicationAuthorizationCreate(
                channels=("instagram_reels",), authorized_by="producer"
            ),
        )


def test_authorization_binds_the_approved_hash(controller):
    project, candidate = _finalised(controller)
    record = controller.authorize_publication(
        project.id,
        PublicationAuthorizationCreate(
            channels=("instagram_reels", "youtube_shorts"),
            authorized_by="producer",
            reference="client-email-2026-11-12",
        ),
    )
    assert record["artifact_id"] == candidate["id"]
    assert record["artifact_hash"] == candidate["content_hash"]
    assert record["channels"] == ["instagram_reels", "youtube_shorts"]
    assert record["revoked_at"] is None
    assert any(
        event["event_type"] == "publication.authorized"
        for event in controller.repository.events(project.id)
    )


def test_stored_authorization_is_channel_scoped(controller):
    project, _ = _finalised(controller)
    controller.authorize_publication(
        project.id,
        PublicationAuthorizationCreate(
            channels=("instagram_reels",), authorized_by="producer"
        ),
    )
    allowed = controller.stored_publication_authorization(project.id, "instagram_reels")
    assert allowed.authorized is True
    assert allowed.authorized_by == "producer"
    denied = controller.stored_publication_authorization(project.id, "tiktok")
    assert denied.authorized is False


def test_expired_authorization_is_not_valid(controller):
    project, _ = _finalised(controller)
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    controller.authorize_publication(
        project.id,
        PublicationAuthorizationCreate(
            channels=("instagram_reels",),
            authorized_by="producer",
            expires_at=past,
        ),
    )
    assert (
        controller.stored_publication_authorization(
            project.id, "instagram_reels"
        ).authorized
        is False
    )


def test_reauthorizing_revokes_the_previous_record(controller):
    project, _ = _finalised(controller)
    first = controller.authorize_publication(
        project.id,
        PublicationAuthorizationCreate(
            channels=("instagram_reels",), authorized_by="producer"
        ),
    )
    second = controller.authorize_publication(
        project.id,
        PublicationAuthorizationCreate(channels=("tiktok",), authorized_by="producer"),
    )
    assert second["id"] != first["id"]
    assert (
        controller.stored_publication_authorization(
            project.id, "instagram_reels"
        ).authorized
        is False
    )
    assert (
        controller.stored_publication_authorization(project.id, "tiktok").authorized
        is True
    )


def test_publication_check_uses_the_stored_authorization(controller, monkeypatch):
    project, candidate = _finalised(controller)
    controller.authorize_publication(
        project.id,
        PublicationAuthorizationCreate(
            channels=("instagram_reels",), authorized_by="producer"
        ),
    )

    def fake_probe(_path):
        return {
            "duration_seconds": 10.0,
            "width_px": 1080,
            "height_px": 1920,
            "has_subtitles": True,
        }

    monkeypatch.setattr("pinaka.publication.probe_ffprobe", fake_probe)
    request = PublicationRequest(
        channel="instagram_reels",
        artifact_id=candidate["id"],
        title="Counting rhyme",
        description="Three little ducks count together.",
        disclosures=("Made for kids",),
    )
    authorization = controller.stored_publication_authorization(
        project.id, "instagram_reels"
    )
    plan = check_publication(controller, project.id, request, authorization)
    assert plan.authorization_valid is True
    assert plan.artifact_id == candidate["id"]


def test_publication_package_writes_a_manual_upload_bundle(
    controller, monkeypatch, tmp_path
):
    from pinaka.publication import build_dispatch_package

    project, candidate = _finalised(controller)
    controller.archive_project(project.id)
    controller.authorize_publication(
        project.id,
        PublicationAuthorizationCreate(
            channels=("instagram_reels",), authorized_by="producer"
        ),
    )

    def fake_probe(_path):
        return {
            "duration_seconds": 10.0,
            "width_px": 1080,
            "height_px": 1920,
            "has_subtitles": True,
        }

    monkeypatch.setattr("pinaka.publication.probe_ffprobe", fake_probe)
    request = PublicationRequest(
        channel="instagram_reels",
        artifact_id=candidate["id"],
        title="Counting rhyme",
        description="Three little ducks count together.",
        disclosures=("Made for kids",),
    )
    authorization = controller.stored_publication_authorization(
        project.id, "instagram_reels"
    )
    output = tmp_path / "delivery"
    package = build_dispatch_package(
        controller, project.id, request, authorization, output
    )
    assert package.status == "ready_for_manual_upload"
    assert package.publishing_performed is False
    assert package.artifact_hash == candidate["content_hash"]
    assert package.plan_hash
    assert package.files
    for item in package.files:
        assert (output / item.filename).is_file()
    assert any(item.role == "media" for item in package.files)


def test_publication_check_without_authorization_is_blocked(controller, monkeypatch):
    project, candidate = _finalised(controller)

    def fake_probe(_path):
        return {
            "duration_seconds": 10.0,
            "width_px": 1080,
            "height_px": 1920,
            "has_subtitles": True,
        }

    monkeypatch.setattr("pinaka.publication.probe_ffprobe", fake_probe)
    request = PublicationRequest(
        channel="instagram_reels",
        artifact_id=candidate["id"],
        title="Counting rhyme",
        description="x",
    )
    plan = check_publication(
        controller,
        project.id,
        request,
        controller.stored_publication_authorization(project.id, "instagram_reels"),
    )
    assert plan.authorization_valid is False
    assert plan.status != "ready"

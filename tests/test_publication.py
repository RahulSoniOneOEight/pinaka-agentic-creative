from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from pinaka.publication import (
    PublicationAuthorization,
    PublicationRequest,
    aspect_ratio,
    evaluate_publication,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
HASH = "f" * 64


def authorization(**overrides):
    payload = {
        "authorized": True,
        "authorized_by": "Pinaka reviewer",
        "authorized_at": NOW - timedelta(hours=1),
        "artifact_hash": HASH,
        "channels": ("instagram_reels",),
        "reference": "approval-2026-11-10",
    }
    payload.update(overrides)
    return PublicationAuthorization.model_validate(payload)


def request(**overrides):
    payload = {
        "channel": "instagram_reels",
        "artifact_id": "art_final",
        "title": "Your finance function, on demand",
        "description": "A 10-second Pinaka Intelligence Virtual CFO introduction.",
        "disclosures": ("advertising_disclosure",),
        "commercial": True,
    }
    payload.update(overrides)
    return PublicationRequest.model_validate(payload)


def evaluate(**overrides):
    values = {
        "project_id": "prj_test",
        "project_status": "archived",
        "request": request(),
        "authorization": authorization(),
        "artifact_hash": HASH,
        "final_approval_hash": HASH,
        "duration_seconds": 10.0,
        "width_px": 1080,
        "height_px": 1920,
        "has_embedded_captions": True,
        "caption_hash": None,
        "now": NOW,
    }
    values.update(overrides)
    return evaluate_publication(**values)


def test_complete_publication_plan_is_ready_but_not_published():
    plan = evaluate()
    assert plan.status == "ready"
    assert plan.authorization_valid is True
    assert plan.publishing_performed is False
    assert len(plan.manifest_hash) == 64


def test_publication_requires_a_separate_authorization():
    plan = evaluate(authorization=PublicationAuthorization())
    assert plan.status == "blocked"
    assert plan.authorization_valid is False
    assert any(check.rule_id == "P-AUTH-001" for check in plan.checks)


def test_publication_blocks_on_hash_mismatch():
    plan = evaluate(authorization=authorization(artifact_hash="e" * 64))
    assert plan.status == "blocked"
    assert any(
        check.rule_id == "P-AUTH-003" and check.status == "fail"
        for check in plan.checks
    )


def test_publication_blocks_when_channel_is_not_authorized():
    plan = evaluate(authorization=authorization(channels=("youtube_shorts",)))
    assert plan.status == "blocked"
    assert any(
        check.rule_id == "P-AUTH-004" and check.status == "fail"
        for check in plan.checks
    )


def test_publication_blocks_expired_authorization():
    plan = evaluate(authorization=authorization(expires_at=NOW - timedelta(minutes=1)))
    assert plan.status == "blocked"
    assert any(
        check.rule_id == "P-AUTH-005" and check.status == "fail"
        for check in plan.checks
    )


def test_publication_blocks_unsupported_aspect_and_duration():
    plan = evaluate(width_px=1920, height_px=1080, duration_seconds=120.0)
    statuses = {check.rule_id: check.status for check in plan.checks}
    assert statuses["P-ASPECT-001"] == "fail"
    assert statuses["P-DURATION-001"] == "fail"
    assert plan.status == "blocked"


def test_publication_blocks_missing_captions():
    plan = evaluate(has_embedded_captions=False, caption_hash=None)
    assert any(
        check.rule_id == "P-CAPTIONS-001" and check.status == "fail"
        for check in plan.checks
    )


def test_sidecar_captions_satisfy_the_channel():
    plan = evaluate(has_embedded_captions=False, caption_hash="a" * 64)
    assert plan.status == "ready"


def test_commercial_content_needs_disclosure():
    plan = evaluate(request=request(disclosures=()))
    assert any(
        check.rule_id == "P-DISCLOSURE-001" and check.status == "fail"
        for check in plan.checks
    )


def test_unarchived_project_is_blocked():
    plan = evaluate(project_status="final_approved")
    assert plan.status == "blocked"
    assert any(check.rule_id == "P-PROJECT-001" for check in plan.checks)


def test_aspect_ratio_reduction():
    assert aspect_ratio(1080, 1920) == "9:16"
    assert aspect_ratio(1080, 1080) == "1:1"
    assert aspect_ratio(1920, 1080) == "16:9"


def test_dispatch_package_writes_hashed_manual_upload_files(tmp_path):
    from pinaka.publication import write_dispatch_package
    from pinaka.utils import sha256_file

    media = tmp_path / "master.mp4"
    media.write_bytes(b"video-bytes")
    media_hash = sha256_file(media)
    captions = tmp_path / "captions.srt"
    captions.write_text("1\n00:00:00,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
    output = tmp_path / "dispatch"
    auth = authorization(artifact_hash=media_hash)

    package = write_dispatch_package(
        evaluate(
            authorization=auth,
            artifact_hash=media_hash,
            final_approval_hash=media_hash,
        ),
        request(),
        auth,
        media_path=media,
        caption_path=captions,
        output_dir=output,
    )

    assert package.status == "ready_for_manual_upload"
    assert package.publishing_performed is False
    assert package.artifact_hash == media_hash
    assert package.authorization_reference == "approval-2026-11-10"
    assert {item.role for item in package.files} == {"media", "captions"}
    assert (output / "media" / "master.mp4").read_bytes() == b"video-bytes"
    assert (output / "dispatch.json").is_file()
    manifest = json.loads((output / "dispatch.json").read_text(encoding="utf-8"))
    assert manifest["manifest_hash"] == package.manifest_hash
    assert manifest["publishing_performed"] is False


def test_dispatch_package_refuses_a_blocked_plan(tmp_path):
    import pytest

    from pinaka.errors import ConflictError
    from pinaka.publication import write_dispatch_package

    media = tmp_path / "master.mp4"
    media.write_bytes(b"video-bytes")
    with pytest.raises(ConflictError, match="not ready"):
        write_dispatch_package(
            evaluate(authorization=PublicationAuthorization()),
            request(),
            PublicationAuthorization(),
            media_path=media,
            output_dir=tmp_path / "dispatch",
        )


def test_dispatch_package_refuses_non_empty_output(tmp_path):
    import pytest

    from pinaka.publication import write_dispatch_package

    media = tmp_path / "master.mp4"
    media.write_bytes(b"video-bytes")
    output = tmp_path / "dispatch"
    output.mkdir()
    (output / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError):
        write_dispatch_package(
            evaluate(),
            request(),
            authorization(),
            media_path=media,
            output_dir=output,
        )
    assert (output / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_dispatch_package_rejects_media_hash_drift(tmp_path):
    import pytest

    from pinaka.errors import IntegrityError
    from pinaka.publication import write_dispatch_package

    media = tmp_path / "master.mp4"
    media.write_bytes(b"different-bytes")
    with pytest.raises(IntegrityError, match="does not match"):
        write_dispatch_package(
            evaluate(),
            request(),
            authorization(),
            media_path=media,
            output_dir=tmp_path / "dispatch",
        )

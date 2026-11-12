from __future__ import annotations

from decimal import Decimal

import pytest

from pinaka.adapters.fal_avatar import (
    OMNIHUMAN_MODEL,
    SYNC3_MODEL,
    FalAvatarAdapter,
    build_avatar_payload,
)
from pinaka.errors import IntegrityError
from pinaka.models import JobRequest, Money
from pinaka.operations import operation
from pinaka.policy.guards import require_likeness_consent


class FakeClient:
    def __init__(self) -> None:
        self.model = None
        self.payload = None

    def run(self, model: str, payload: dict) -> dict:
        self.model = model
        self.payload = payload
        return {
            "video": {"url": "https://cdn.example/avatar.mp4"},
            "duration": 5.0,
        }

    def download(self, url: str) -> bytes:
        return b"avatar-video"


def request(model: str = OMNIHUMAN_MODEL) -> JobRequest:
    return JobRequest(
        provider="fal",
        endpoint=model,
        operation="avatar.generate",
        idempotency_key="avatar-1",
        estimated_cost=Money(amount=Decimal("0.30"), currency="USD"),
        parameters={"num_outputs": 1, "likeness_consent": "release-2026-01"},
    )


def test_omnihuman_payload_matches_the_published_schema():
    payload = build_avatar_payload(
        OMNIHUMAN_MODEL,
        image_data=b"portrait",
        audio_data=b"speech",
        resolution="720p",
        turbo_mode=True,
        prompt="warm delivery",
    )

    assert payload["resolution"] == "720p"
    assert payload["turbo_mode"] is True
    assert payload["prompt"] == "warm delivery"
    assert payload["image_url"].startswith("data:image/png;base64,")
    assert payload["audio_url"].startswith("data:audio/wav;base64,")


def test_sync3_payload_matches_the_published_schema():
    payload = build_avatar_payload(
        SYNC3_MODEL,
        video_data=b"clip",
        audio_data=b"speech",
        sync_mode="bounce",
    )

    assert payload["sync_mode"] == "bounce"
    assert payload["enable_safety_checker"] is True
    assert payload["video_url"].startswith("data:video/mp4;base64,")


def test_avatar_payload_validates_inputs():
    with pytest.raises(ValueError, match="Unsupported avatar model"):
        build_avatar_payload("fal-ai/other", image_data=b"x", audio_data=b"y")
    with pytest.raises(ValueError, match="needs audio"):
        build_avatar_payload(OMNIHUMAN_MODEL, image_data=b"x")
    with pytest.raises(ValueError, match="portrait image"):
        build_avatar_payload(OMNIHUMAN_MODEL, audio_data=b"y")
    with pytest.raises(ValueError, match="resolution"):
        build_avatar_payload(
            OMNIHUMAN_MODEL, image_data=b"x", audio_data=b"y", resolution="4k"
        )


def test_adapter_submits_and_caches_the_video():
    client = FakeClient()
    adapter = FalAvatarAdapter(
        client,
        model=OMNIHUMAN_MODEL,
        source_bytes=b"portrait",
        audio_bytes=b"speech",
    )

    submission = adapter.submit(request())
    result = adapter.poll(submission.provider_job_id, request())

    assert client.model == OMNIHUMAN_MODEL
    assert submission.provider_job_id.startswith("fal_")
    assert result.output_bytes == b"avatar-video"
    assert result.actual_cost.minor == 30


def test_avatar_route_is_declared_and_guarded():
    definition = operation("avatar.generate")

    definition.validate("fal.avatar", model=OMNIHUMAN_MODEL)
    with pytest.raises(IntegrityError, match="not allowed"):
        definition.validate("fal.image", model=OMNIHUMAN_MODEL)
    with pytest.raises(IntegrityError, match="likeness-consent"):
        require_likeness_consent("  ")
    require_likeness_consent("release-2026-01")

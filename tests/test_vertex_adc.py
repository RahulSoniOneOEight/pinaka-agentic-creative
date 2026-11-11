from __future__ import annotations

import io
import wave
from decimal import Decimal

import pytest

from pinaka import pricing
from pinaka.adapters.google_vertex_image import estimate_cost_minor
from pinaka.adapters.tool import ToolExecutionResult
from pinaka.enums import ArtifactKind, CapabilityStatus, ToolIntegrationMode
from pinaka.errors import ApprovalRequiredError
from pinaka.models import ToolProfileCreate
from pinaka.utils import sha256_bytes
from pinaka.vertex import (
    PROVIDER_KEY,
    VertexTranscriptionAdapter,
    VertexTTSAdapter,
    estimate_transcription_cost_minor,
    estimate_tts_cost_minor,
    verify_models,
    vertex_provider_profile,
)

from .conftest import advance_to_sourcing


class _Inline:
    def __init__(self, data: bytes):
        self.data = data
        self.mime_type = "audio/wav"


class _Part:
    def __init__(self, data: bytes):
        self.inline_data = _Inline(data)


class _Content:
    def __init__(self, data: bytes):
        self.parts = [_Part(data)]


class _Candidate:
    def __init__(self, data: bytes):
        self.content = _Content(data)


class _Response:
    def __init__(self, data: bytes = b"", text: str = ""):
        self.candidates = [_Candidate(data)] if data else []
        self.text = text
        self.usage_metadata = None


class _Models:
    def __init__(self, available: set[str], client):
        self.available = available
        self.client = client

    def get(self, model: str):
        if model not in self.available:
            raise RuntimeError("model not found in region")
        return {"model": model}

    def generate_content(self, **_kwargs):
        if self.client.audio:
            return _Response(data=self.client.audio)
        return _Response(text=self.client.text)


class _Client:
    def __init__(self, *, audio: bytes = b"", text: str = "", available=None):
        self.audio = audio
        self.text = text
        self.models = _Models(available or set(), self)


def _pcm(seconds: float) -> bytes:
    return b"\x00\x00" * int(24_000 * seconds)


def test_vertex_profile_uses_adc_and_stays_disabled():
    profile = vertex_provider_profile()
    assert profile.credential_ref == "ADC"
    assert profile.enabled is False
    assert profile.provider == PROVIDER_KEY
    assert profile.limits["project"] == "spheric-host-505921-t5"
    assert profile.limits["location"] == "global"
    assert profile.terms["auth"] == "adc"


def test_controller_registers_the_vertex_profile(controller):
    profile = controller.register_vertex_profile()
    assert profile["credential_ref"] == "ADC"
    assert profile["enabled"] is False
    assert profile["limits"]["project"] == "spheric-host-505921-t5"
    with pytest.raises(ApprovalRequiredError):
        controller.register_vertex_profile(enabled=True)


def test_cost_estimates_scale_with_input():
    short = estimate_tts_cost_minor("hi")
    long = estimate_tts_cost_minor("one two three four five six seven eight " * 200)
    assert short >= 1
    assert long > short
    assert estimate_transcription_cost_minor(5) >= 1
    assert estimate_transcription_cost_minor(60) > estimate_transcription_cost_minor(5)
    assert estimate_cost_minor("gemini-3.1-flash-image", "1K") > 0


def test_verify_models_reports_availability(controller):
    client = _Client(available={"gemini-3.1-flash-image", "gemini-2.5-flash"})
    result = controller.verify_vertex_models(client=client)
    statuses = {item["name"]: item["status"] for item in result["models"]}
    assert statuses[f"{PROVIDER_KEY}:gemini-3.1-flash-image"] == "working"
    assert statuses[f"{PROVIDER_KEY}:gemini-2.5-flash-preview-tts"] == "unavailable"
    assert result["ready"] is False
    assert any(
        check["name"].startswith(PROVIDER_KEY)
        for check in (
            controller.repository.latest_capability(name)
            for name in [f"{PROVIDER_KEY}:gemini-3.1-flash-image"]
        )
    )


def test_verify_models_helper_marks_missing_models():
    results = verify_models(
        ("a-model", "b-model"), client=_Client(available={"a-model"})
    )
    assert [item.status for item in results] == [
        CapabilityStatus.WORKING,
        CapabilityStatus.UNAVAILABLE,
    ]


def test_tts_adapter_produces_a_wav():
    adapter = VertexTTSAdapter(client=_Client(audio=_pcm(0.5)))
    narration = adapter.synthesize("One, two, three little ducks.")
    assert narration.content[:4] == b"RIFF"
    assert narration.content_hash == sha256_bytes(narration.content)
    assert narration.duration_seconds == pytest.approx(0.5, abs=0.01)
    with wave.open(io.BytesIO(narration.content), "rb") as handle:
        assert handle.getframerate() == 24_000
    assert narration.voice == "Kore"


def test_transcription_adapter_returns_text():
    adapter = VertexTranscriptionAdapter(client=_Client(text="One, two, three."))
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp()) / "narration.wav"
    path.write_bytes(_pcm(0.25))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24_000)
        handle.writeframes(_pcm(0.25))
    result = adapter.transcribe(path)
    assert result.text == "One, two, three."
    assert result.duration_seconds == pytest.approx(0.25, abs=0.01)
    assert result.segments[0].text == "One, two, three."


class _FakeVertexAdapter:
    profile_key = "google-vertex-nano-banana"

    def execute(self, request):
        payload = b"\x89PNG\r\n\x1a\n" + b"edited"
        return ToolExecutionResult(
            output_bytes=payload,
            details={"stub": True},
            response_hash=sha256_bytes(payload),
        )


def test_enhance_still_uses_the_vertex_adapter(controller):
    project, _, _ = advance_to_sourcing(controller)
    source = controller.write_artifact(
        project.id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path="assets/sourced-frame.png",
        content=b"\x89PNG\r\n\x1a\nsource",
        operation={"type": "sourced_import"},
    )
    profile = controller.register_tool_profile(
        ToolProfileCreate(
            stable_key="google-vertex-nano-banana",
            name="Vertex image adapter",
            integration_mode=ToolIntegrationMode.NATIVE,
            executable_or_endpoint="python:vertex",
            supported_operations=("image.edit",),
        )
    )
    result = controller.enhance_still_with_vertex(
        project.id,
        tool_profile_id=profile["id"],
        source_artifact_id=source["id"],
        prompt="clean the background, keep the duckling identical",
        adapter=_FakeVertexAdapter(),
    )
    artifact = result["artifact"]
    assert artifact["parent_artifact_id"] == source["id"]
    assert artifact["kind"] == "derivative"
    assert controller.verify_artifact(artifact["id"])
    assert result["receipt"]["status"] == "succeeded"
    assert any(
        dependency["entity_type"] == "artifact"
        and dependency["entity_id"] == source["id"]
        for dependency in artifact["dependencies"]
    )


def test_enhance_still_refuses_an_original(controller):
    project, _, _ = advance_to_sourcing(controller)
    original = controller.import_original(
        project.id,
        _write_temp(b"immutable source", "source.bin"),
    )
    profile = controller.register_tool_profile(
        ToolProfileCreate(
            stable_key="google-vertex-nano-banana",
            name="Vertex image adapter",
            integration_mode=ToolIntegrationMode.NATIVE,
            executable_or_endpoint="python:vertex",
            supported_operations=("image.edit",),
        )
    )
    with pytest.raises(Exception, match="protected original"):
        controller.enhance_still_with_vertex(
            project.id,
            tool_profile_id=profile["id"],
            source_artifact_id=original["id"],
            prompt="edit",
            adapter=_FakeVertexAdapter(),
        )


def _write_temp(content: bytes, name: str):
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp()) / name
    path.write_bytes(content)
    return path


def test_fal_pricing_preview():
    assert pricing.estimate_fal_image_minor("fal-ai/flux/schnell") == 1
    assert pricing.estimate_fal_image_minor("fal-ai/sana") == 1
    ltx = pricing.estimate_fal_video_minor(
        "fal-ai/ltx-2-19b/distilled/image-to-video", seconds=5
    )
    assert ltx == 9
    preview = pricing.fal_budget_preview(images=100, clips=10)
    assert preview["estimated_total_minor"] == 100 + 90
    with pytest.raises(ValueError, match="Unknown fal image endpoint"):
        pricing.estimate_fal_image_minor("nope/model")
    with pytest.raises(ValueError, match="Unsupported resolution"):
        pricing.estimate_fal_video_minor(
            "minimax/h3-max-turbo/image-to-video", seconds=5, resolution="2160p"
        )


def test_budget_preview_fits_the_one_dollar_rule():
    preview = pricing.fal_budget_preview(images=20, clips=8, clip_seconds=5)
    assert preview["estimated_total_minor"] <= 100
    assert Decimal(preview["estimated_total_minor"]) / 100 <= Decimal("1.00")

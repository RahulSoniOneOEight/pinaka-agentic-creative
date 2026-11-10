from __future__ import annotations

from typing import ClassVar

import pytest

from pinaka.adapters.google_vertex_image import (
    GoogleVertexImageAdapter,
    estimate_cost_minor,
)
from pinaka.errors import ConfigurationError
from pinaka.models import ToolOperationRequest
from pinaka.utils import sha256_bytes

PNG = b"\x89PNG\r\n\x1a\n" + b"vertex-image"


class Usage:
    prompt_token_count = 12
    candidates_token_count = 1120
    total_token_count = 1132


class InlineData:
    data = PNG
    mime_type = "image/png"


class Part:
    inline_data = InlineData()


class Content:
    parts: ClassVar[list] = [Part()]


class Candidate:
    content = Content()


class Response:
    candidates: ClassVar[list] = [Candidate()]
    usage_metadata = Usage()


class Models:
    def __init__(self):
        self.calls = []

    def get(self, model):
        return object()

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return Response()


class FakeClient:
    def __init__(self):
        self.models = Models()


def request(**overrides):
    payload = {
        "tool_profile_id": "google-vertex-nano-banana",
        "operation": "image.generate",
        "idempotency_key": "vertex-image-test-1",
        "parameters": {
            "prompt": "ceramic espresso cup, dark walnut table, window light",
            "model": "gemini-nano-banana-2.1",
            "image_size": "1K",
            "aspect_ratio": "16:9",
        },
        "limits": {"max_cost_minor": 15},
    }
    payload.update(overrides)
    return ToolOperationRequest.model_validate(payload)


def test_cost_estimates_match_the_published_rates():
    assert estimate_cost_minor("gemini-nano-banana-2.1", "1K") == 5
    assert estimate_cost_minor("gemini-3.1-flash-image", "1K") == 8
    assert estimate_cost_minor("gemini-3-pro-image", "1K") == 15


def test_vertex_adapter_returns_image_and_usage():
    client = FakeClient()
    adapter = GoogleVertexImageAdapter(client=client)
    result = adapter.execute(request())

    assert result.output_bytes == PNG
    assert result.response_hash == sha256_bytes(PNG)
    assert result.details["model"] == "gemini-nano-banana-2.1"
    assert result.details["billing"] == "gcp_trial_credit"
    assert result.details["estimated_cost_minor"] == 5
    assert result.details["actual_cost_minor"] == 4
    assert result.details["candidate_token_count"] == 1120
    assert "espresso" not in str(result.details).casefold()
    assert client.models.calls[0]["model"] == "gemini-nano-banana-2.1"


def test_cost_ceiling_is_enforced_before_the_call():
    adapter = GoogleVertexImageAdapter(client=FakeClient())
    with pytest.raises(ValueError, match="at least 5"):
        adapter.execute(request(limits={"max_cost_minor": 4}))


def test_unknown_model_is_rejected():
    adapter = GoogleVertexImageAdapter(client=FakeClient())
    with pytest.raises(ValueError, match="Unsupported"):
        adapter.execute(
            request(
                parameters={
                    "prompt": "x",
                    "model": "not-a-model",
                    "image_size": "1K",
                    "aspect_ratio": "1:1",
                }
            )
        )


def test_auth_failure_is_mapped_to_a_clear_error():
    class BrokenModels(Models):
        def generate_content(self, model, contents, config):
            raise RuntimeError("Reauthentication is needed")

    class BrokenClient:
        models = BrokenModels()

    adapter = GoogleVertexImageAdapter(client=BrokenClient())
    with pytest.raises(
        ConfigurationError, match="gcloud auth application-default login"
    ):
        adapter.execute(request())


def test_missing_image_is_reported():
    class EmptyResponse:
        candidates: ClassVar[list] = []
        usage_metadata = Usage()

    class EmptyModels(Models):
        def generate_content(self, model, contents, config):
            return EmptyResponse()

    class EmptyClient:
        models = EmptyModels()

    adapter = GoogleVertexImageAdapter(client=EmptyClient())
    with pytest.raises(RuntimeError, match="no image"):
        adapter.execute(request())


def test_ensure_configured_checks_the_model():
    client = FakeClient()
    adapter = GoogleVertexImageAdapter(client=client)
    adapter.ensure_configured()


def test_project_and_location_can_come_from_environment(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "another-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    adapter = GoogleVertexImageAdapter(client=FakeClient())
    assert adapter.project == "another-project"
    assert adapter.location == "us-central1"

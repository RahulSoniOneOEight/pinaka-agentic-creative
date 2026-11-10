from __future__ import annotations

import base64
import json

import pytest

from pinaka.adapters.google_image import GeminiImageAdapter
from pinaka.errors import ConfigurationError
from pinaka.models import ToolOperationRequest
from pinaka.utils import sha256_bytes

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"


def request(**overrides):
    payload = {
        "tool_profile_id": "google-nano-banana-image",
        "operation": "image.generate",
        "idempotency_key": "nano-banana-test-1",
        "parameters": {
            "prompt": "A calm finance dashboard, no text",
            "aspect_ratio": "9:16",
            "billing_mode": "free_tier",
        },
        "limits": {"max_cost_minor": 0},
    }
    payload.update(overrides)
    return ToolOperationRequest.model_validate(payload)


def response(image=PNG):
    return json.dumps(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Here is the image."},
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": base64.b64encode(image).decode("ascii"),
                                }
                            },
                        ]
                    }
                }
            ]
        }
    ).encode()


def test_missing_key_is_rejected(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    adapter = GeminiImageAdapter(api_key="")
    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
        adapter.execute(request())


def test_paid_billing_is_refused():
    adapter = GeminiImageAdapter(api_key="test-key")
    with pytest.raises(ValueError, match="zero-cost"):
        adapter.execute(request(limits={"max_cost_minor": 1}))
    with pytest.raises(ValueError, match="paid billing"):
        adapter.execute(
            request(
                parameters={
                    "prompt": "x",
                    "billing_mode": "paid",
                }
            )
        )


def test_image_is_returned_without_logging_the_prompt():
    captured = {}

    def transport(url, headers, body):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = body
        return response()

    adapter = GeminiImageAdapter(api_key="test-key", transport=transport)
    result = adapter.execute(request())

    assert result.output_bytes == PNG
    assert result.response_hash == sha256_bytes(PNG)
    assert captured["headers"]["x-goog-api-key"] == "test-key"
    assert "test-key" not in json.dumps(captured["body"])
    assert "calm finance" not in json.dumps(result.details).casefold()
    assert result.details["prompt_sha256"]
    assert result.details["billing_mode"] == "free_tier"
    assert captured["body"]["generationConfig"]["imageConfig"]["aspectRatio"] == "9:16"


def test_non_image_payload_is_refused():
    payload = json.dumps(
        {"candidates": [{"content": {"parts": [{"text": "no image"}]}}]}
    ).encode()
    adapter = GeminiImageAdapter(api_key="test-key", transport=lambda *_: payload)
    with pytest.raises(RuntimeError, match="no image"):
        adapter.execute(request())


def test_invalid_base64_is_refused():
    payload = json.dumps(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": "not-base64!!!",
                                }
                            }
                        ]
                    }
                }
            ]
        }
    ).encode()
    adapter = GeminiImageAdapter(api_key="test-key", transport=lambda *_: payload)
    with pytest.raises(RuntimeError, match="invalid base64"):
        adapter.execute(request())


def test_api_key_in_parameters_is_refused():
    adapter = GeminiImageAdapter(api_key="test-key")
    with pytest.raises(ValueError, match="Credentials"):
        adapter.execute(
            request(
                parameters={
                    "prompt": "x",
                    "billing_mode": "free_tier",
                    "api_key": "leak",
                }
            )
        )


def test_key_file_is_supported(monkeypatch, tmp_path):
    key_file = tmp_path / "gemini-key"
    key_file.write_text("test-key-from-file", encoding="utf-8")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY_FILE", str(key_file))
    captured = {}

    def transport(url, headers, body):
        captured["headers"] = headers
        return response()

    adapter = GeminiImageAdapter(transport=transport)
    result = adapter.execute(request())
    assert result.output_bytes == PNG
    assert captured["headers"]["x-goog-api-key"] == "test-key-from-file"

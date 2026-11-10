from __future__ import annotations

import json

import pytest

from pinaka.adapters.comfyui import ComfyUIImageAdapter, validate_workflow
from pinaka.models import ToolOperationRequest
from pinaka.utils import sha256_bytes

PNG = b"\x89PNG\r\n\x1a\n" + b"comfy-image"


def request(**overrides):
    payload = {
        "tool_profile_id": "comfyui-local-image",
        "operation": "image.generate",
        "idempotency_key": "comfyui-test-1",
        "parameters": {
            "prompt": "minimal finance background, no text",
            "negative_prompt": "text, watermark",
            "width": 512,
            "height": 512,
            "steps": 4,
            "seed": 7,
        },
        "limits": {"max_cost_minor": 0},
    }
    payload.update(overrides)
    return ToolOperationRequest.model_validate(payload)


class FakeTransport:
    def __init__(self, history=None):
        self.calls = []
        self.history = history or {
            "p1": {
                "outputs": {
                    "9": {
                        "images": [
                            {"filename": "x.png", "subfolder": "", "type": "output"}
                        ]
                    }
                },
                "status": {"status_str": "success"},
            }
        }

    def __call__(self, method, url, payload):
        self.calls.append((method, url, payload))
        if url.endswith("/system_stats"):
            return json.dumps({"system": {"os": "win32"}}).encode()
        if url.endswith("/prompt"):
            return json.dumps({"prompt_id": "p1"}).encode()
        if "/history/" in url:
            return json.dumps(self.history).encode()
        if "/view?" in url:
            return PNG
        raise AssertionError(f"Unexpected URL: {url}")


def test_comfyui_adapter_returns_a_hashed_image():
    transport = FakeTransport()
    adapter = ComfyUIImageAdapter(transport=transport, poll_interval=0)
    result = adapter.execute(request())

    assert result.output_bytes == PNG
    assert result.response_hash == sha256_bytes(PNG)
    assert result.details["prompt_id"] == "p1"
    assert result.details["cost_minor"] == 0
    assert result.details["device"] == "cpu"
    assert "finance background" not in json.dumps(result.details).casefold()
    submitted = next(
        payload for method, url, payload in transport.calls if url.endswith("/prompt")
    )
    assert submitted["prompt"]["9"]["inputs"]["images"] == ["8", 0]
    assert submitted["prompt"]["4"]["inputs"]["ckpt_name"].endswith(".safetensors")


def test_remote_endpoint_is_rejected():
    with pytest.raises(ValueError, match="loopback"):
        ComfyUIImageAdapter(base_url="https://example.com")


def test_unknown_checkpoint_is_rejected():
    adapter = ComfyUIImageAdapter(transport=FakeTransport())
    with pytest.raises(ValueError, match="allowlisted"):
        adapter.execute(
            request(
                parameters={
                    "prompt": "x",
                    "checkpoint": "unknown.safetensors",
                    "width": 512,
                    "height": 512,
                    "steps": 4,
                    "seed": 1,
                }
            )
        )


def test_external_node_type_is_rejected():
    workflow = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "v1-5-pruned-emaonly-fp16.safetensors"},
        },
        "2": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
    }
    with pytest.raises(ValueError, match="not allowed"):
        validate_workflow(
            workflow,
            allowed_checkpoints={"v1-5-pruned-emaonly-fp16.safetensors"},
        )


def test_paid_limits_are_refused():
    adapter = ComfyUIImageAdapter(transport=FakeTransport())
    with pytest.raises(ValueError, match="zero-cost"):
        adapter.execute(request(limits={"max_cost_minor": 1}))


def test_timeout_is_reported():
    adapter = ComfyUIImageAdapter(
        transport=FakeTransport(history={}),
        poll_interval=0,
        timeout_seconds=0,
    )
    with pytest.raises(TimeoutError, match="timed out"):
        adapter.execute(request())


def test_ensure_available_checks_system_stats():
    adapter = ComfyUIImageAdapter(transport=FakeTransport())
    adapter.ensure_available()

from __future__ import annotations

import json

import pytest

from pinaka.errors import ConfigurationError
from pinaka.fal import FalClient, FalEditAdapter, FalVisionJudge

SUBMIT = {
    "request_id": "req-1",
    "status_url": "https://queue.fal.run/status/req-1",
    "response_url": "https://queue.fal.run/result/req-1",
}


def make_client(handler, **kwargs) -> FalClient:
    return FalClient(api_key="id:secret", transport=handler, **kwargs)


def test_key_is_required(monkeypatch, tmp_path):
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("FAL_KEY_FILE", raising=False)
    monkeypatch.setattr("pinaka.fal.DEFAULT_API_KEY_FILE", tmp_path / "missing")

    with pytest.raises(ConfigurationError, match="api key|API key"):
        FalClient(api_key="").run("fal-ai/test", {})


def test_multiline_key_file_is_rejected_without_echoing_it(tmp_path):
    import os

    key_file = tmp_path / "key"
    key_file.write_text("Key ID\nfal_secret_value\n", encoding="utf-8")
    client = FalClient(api_key="", transport=lambda *args: b"")
    os.environ["FAL_KEY_FILE"] = str(key_file)
    try:
        with pytest.raises(ConfigurationError, match="one line") as error:
            client.run("fal-ai/test", {})
    finally:
        os.environ.pop("FAL_KEY_FILE", None)
    assert "fal_secret_value" not in str(error.value)


def test_key_format_is_checked(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("shortkeywithnocolon", encoding="utf-8")
    client = FalClient(
        api_key="", transport=lambda *args: b"", base_url="https://queue.fal.run"
    )
    import os

    os.environ["FAL_KEY_FILE"] = str(key_file)
    try:
        with pytest.raises(ConfigurationError, match="id:secret"):
            client.run("fal-ai/test", {})
    finally:
        del os.environ["FAL_KEY_FILE"]


def test_single_token_fal_keys_are_accepted():
    client = FalClient(api_key="fal_sk_abc123", transport=lambda *args: b"{}")

    assert client._resolve_key() == "fal_sk_abc123"


def test_run_polls_until_completed_and_returns_the_result():
    calls: list[str] = []

    def handler(method, url, body, headers):
        calls.append(url)
        if body is not None and method == "POST":
            return json.dumps(SUBMIT).encode()
        if url.endswith("/status/req-1"):
            return json.dumps({"status": "COMPLETED"}).encode()
        if url.endswith("/result/req-1"):
            return json.dumps(
                {"images": [{"url": "https://cdn.example/out.png"}]}
            ).encode()
        raise AssertionError(url)

    client = make_client(handler)
    client.poll_interval = 0
    result = client.run("fal-ai/test", {"prompt": "x"})

    assert result["images"][0]["url"] == "https://cdn.example/out.png"
    assert len(calls) == 3


def test_run_reports_failed_jobs():
    def handler(method, url, body, headers):
        if method == "POST":
            return json.dumps(SUBMIT).encode()
        return json.dumps({"status": "FAILED"}).encode()

    client = make_client(handler)
    client.poll_interval = 0

    with pytest.raises(ConfigurationError, match="fal job failed"):
        client.run("fal-ai/test", {})


def test_inpaint_writes_a_result_with_provenance():
    def handler(method, url, body, headers):
        if method == "POST":
            return json.dumps(SUBMIT).encode()
        if "status" in url:
            return json.dumps({"status": "COMPLETED"}).encode()
        if "result" in url:
            return json.dumps(
                {"images": [{"url": "https://cdn.example/out.png"}]}
            ).encode()
        return b"image-bytes"

    adapter = FalEditAdapter(
        make_client(handler, poll_interval=0),
        allowed_models={"inpaint": ("fal-ai/fill",), "upscale": ("fal-ai/esrgan",)},
    )
    result = adapter.edit(b"source", prompt="remove the logo", mask=b"mask")

    assert result.content == b"image-bytes"
    assert result.operation["type"] == "fal_inpaint"
    assert result.operation["model"] == "fal-ai/fill"
    assert result.operation["provider"] == "fal"


def test_model_must_be_allowlisted():
    adapter = FalEditAdapter(
        make_client(lambda *args: b"{}"),
        allowed_models={"upscale": ("fal-ai/esrgan",)},
    )

    with pytest.raises(ValueError, match="not allowlisted"):
        adapter.upscale(b"image", model_name="fal-ai/other")


def test_ambiguous_allowlist_requires_an_explicit_model():
    adapter = FalEditAdapter(
        make_client(lambda *args: b"{}"),
        allowed_models={"upscale": ("a", "b")},
    )

    with pytest.raises(ConfigurationError, match="explicit upscale model"):
        adapter.upscale(b"image")


def test_vision_judge_parses_a_fal_response():
    payload = {
        "output": json.dumps(
            {
                "scores": {
                    "tone": 0.8,
                    "brand": 0.9,
                    "composition": 0.7,
                    "legibility": 0.9,
                    "safety": 1.0,
                },
                "findings": [],
                "confidence": 0.9,
                "verdict": "accept",
            }
        )
    }

    def handler(method, url, body, headers):
        if method == "POST":
            return json.dumps(SUBMIT).encode()
        if "status" in url:
            return json.dumps({"status": "COMPLETED"}).encode()
        return json.dumps(payload).encode()

    judge = FalVisionJudge(make_client(handler, poll_interval=0), model="fal-ai/vision")
    result = judge.judge(b"image", intent="explainer")

    assert result.verdict == "accept"
    assert result.model == "fal-ai/vision"

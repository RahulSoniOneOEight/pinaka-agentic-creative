from __future__ import annotations

import io
import json
import urllib.error

import pytest

from pinaka.errors import ConfigurationError
from pinaka.fal import FalClient

KEY = "fal_sk_" + "0123456789abcdef" * 2


class _Response:
    def __init__(self, status: int, body: bytes = b"[]") -> None:
        self.status = status
        self._body = body

    def read(self, size: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> _Response:  # noqa: PYI034 - Python 3.10 has no typing.Self
        return self

    def __exit__(self, *args) -> None:
        return None


def test_probe_reports_a_working_credential_from_a_transport():
    client = FalClient(api_key=KEY, transport=lambda *args: b"[]")

    result = client.probe_credentials()

    assert result["authenticated"] is True
    assert result["http_status"] == 200
    assert result["key_shape"] == "single-token"
    assert result["key_source"] == "argument"
    assert KEY not in json.dumps(result)


def test_probe_reports_a_refused_credential(monkeypatch):
    def refuse(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://api.fal.ai/v1/models",
            401,
            "Unauthorized",
            None,
            io.BytesIO(b'{"error":{"message":"Invalid API key"}}'),
        )

    monkeypatch.setattr("pinaka.fal.urllib.request.urlopen", refuse)

    result = FalClient(api_key=KEY).probe_credentials()

    assert result["authenticated"] is False
    assert result["http_status"] == 401
    assert "Invalid API key" in result["detail"]


def test_probe_redacts_credential_like_text(monkeypatch):
    body = json.dumps({"error": {"message": f"bad key {KEY}"}}).encode()

    def refuse(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://api.fal.ai/v1/models", 401, "Unauthorized", None, io.BytesIO(body)
        )

    monkeypatch.setattr("pinaka.fal.urllib.request.urlopen", refuse)

    result = FalClient(api_key=KEY).probe_credentials()

    assert KEY not in result["detail"]
    assert "[redacted]" in result["detail"]


def test_probe_reports_a_network_failure(monkeypatch):
    def refuse(*args, **kwargs):
        raise urllib.error.URLError("dns failure")

    monkeypatch.setattr("pinaka.fal.urllib.request.urlopen", refuse)

    result = FalClient(api_key=KEY).probe_credentials()

    assert result["authenticated"] is False
    assert result["http_status"] is None
    assert "network error" in result["detail"]


def test_probe_reads_the_configured_key_file(monkeypatch, tmp_path):
    key_file = tmp_path / "fal-api-key"
    key_file.write_text(KEY + "\n", encoding="utf-8")
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.setenv("FAL_KEY_FILE", str(key_file))
    monkeypatch.setattr(
        "pinaka.fal.urllib.request.urlopen", lambda *a, **k: _Response(200)
    )

    result = FalClient().probe_credentials()

    assert result["authenticated"] is True
    assert result["key_source"] == f"file:{key_file}"


def test_probe_requires_a_credential(monkeypatch, tmp_path):
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.setenv("FAL_KEY_FILE", str(tmp_path / "missing"))

    with pytest.raises(ConfigurationError, match="none is configured"):
        FalClient().probe_credentials()

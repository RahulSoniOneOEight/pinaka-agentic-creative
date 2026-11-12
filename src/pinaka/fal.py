from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ai_edit import EditResult
from .errors import ConfigurationError
from .judge import VisualFinding, VisualJudgement, VisualScores
from .policy.secrets import SENSITIVE_VALUE_PATTERNS
from .utils import sha256_bytes, sha256_json, utc_now

API_KEY_ENV = "FAL_KEY"
API_KEY_FILE_ENV = "FAL_KEY_FILE"
DEFAULT_API_KEY_FILE = Path.home() / ".config" / "opencode" / "secrets" / "fal-api-key"
DEFAULT_BASE_URL = "https://queue.fal.run"
# A public, zero-cost endpoint: 200 without a credential, 401 when the sent
# credential is invalid. It isolates authentication from route-level errors.
CREDENTIAL_PROBE_URL = "https://api.fal.ai/v1/models"

Transport = Callable[[str, str, bytes | None, dict[str, str]], bytes]


def _data_uri(content: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(content).decode("ascii")


class FalClient:
    """Minimal fal queue client: submit, poll, fetch the result."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 300,
        poll_interval: float = 1.0,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.transport = transport
        self.clock = clock

    def _resolve_key(self) -> str:
        return self._resolve_key_with_source()[0]

    def _resolve_key_with_source(self) -> tuple[str, str]:
        key = (self.api_key or "").strip()
        if key:
            source = "argument"
        else:
            key = (os.environ.get(API_KEY_ENV) or "").strip()
            source = f"env:{API_KEY_ENV}"
        if not key:
            configured = os.environ.get(API_KEY_FILE_ENV)
            path = Path(configured).expanduser() if configured else DEFAULT_API_KEY_FILE
            if path.is_file():
                key = path.read_text(encoding="utf-8").strip()
                source = f"file:{path}"
        if not key:
            raise ConfigurationError(
                f"fal requires an API key in {API_KEY_ENV} or a key file; none is configured"
            )
        # A pasted notes block leaves interior newlines/spaces. Reject it here so the
        # credential never reaches urllib, which puts the header value in its exception.
        if any(character.isspace() for character in key):
            raise ConfigurationError(
                "The stored fal key must be one line with no spaces; "
                "save the bare key, not a notes block"
            )
        if ":" not in key and not key.startswith("fal_"):
            raise ConfigurationError(
                "The stored fal key is neither an 'id:secret' key nor a 'fal_...' key"
            )
        return key, source

    @staticmethod
    def _key_shape(key: str) -> str:
        if ":" in key:
            return "id:secret"
        if key.startswith("fal_"):
            return "single-token"
        return "unrecognised"

    def probe_credentials(self, *, url: str = CREDENTIAL_PROBE_URL) -> dict[str, Any]:
        """Test the credential without submitting or charging provider work.

        Returns authentication evidence, never the secret. A 401 here means the
        credential itself was refused; it cannot be confused with a route or
        application-level "authentication required" response.
        """

        key, source = self._resolve_key_with_source()
        headers = {"Authorization": f"Key {key}"}
        result: dict[str, Any] = {
            "schema": "pinaka.fal.credential-probe/v1",
            "endpoint": url,
            "key_source": source,
            "key_shape": self._key_shape(key),
            "authenticated": False,
            "http_status": None,
            "detail": "",
        }
        if self.transport is not None:
            self.transport("GET", url, None, headers)
            result["authenticated"] = True
            result["http_status"] = 200
            return result
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result["authenticated"] = True
                result["http_status"] = response.status
        except urllib.error.HTTPError as error:
            result["http_status"] = error.code
            result["detail"] = _safe_probe_detail(error.read(2048))
        except urllib.error.URLError as error:
            result["detail"] = f"network error: {error.reason}"
        return result

    def _request(self, method: str, url: str, payload: Any | None = None) -> bytes:
        key = self._resolve_key()
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Key {key}",
            "Content-Type": "application/json",
        }
        if self.transport is not None:
            return self.transport(method, url, body, headers)
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()

    def run(self, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        submitted = json.loads(
            self._request("POST", f"{self.base_url}/{model}", payload).decode("utf-8")
        )
        status_url = submitted.get("status_url")
        response_url = submitted.get("response_url")
        request_id = submitted.get("request_id")
        if not status_url or not response_url:
            raise ConfigurationError("fal did not return queue URLs")
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            status = json.loads(self._request("GET", status_url).decode("utf-8")).get(
                "status"
            )
            if status == "COMPLETED":
                return json.loads(self._request("GET", response_url).decode("utf-8"))
            if status in {"FAILED", "ERROR"}:
                raise ConfigurationError(f"fal job failed: {request_id}")
            time.sleep(self.poll_interval)
        raise ConfigurationError(f"fal job timed out: {request_id}")

    def download(self, url: str) -> bytes:
        if self.transport is not None:
            return self.transport("GET", url, None, {})
        request = urllib.request.Request(url)
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()


def _first_output_url(payload: dict[str, Any]) -> str:
    images = payload.get("images") or payload.get("image")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict) and first.get("url"):
            return str(first["url"])
    if isinstance(images, dict) and images.get("url"):
        return str(images["url"])
    if payload.get("url"):
        return str(payload["url"])
    raise ConfigurationError("fal output contained no image URL")


def _safe_probe_detail(raw: bytes) -> str:
    """Provider error text with any credential-shaped token removed."""

    text = raw.decode("utf-8", "replace").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    message = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message", ""))
        else:
            message = str(error or payload.get("detail", ""))
    text = message or text
    for pattern in SENSITIVE_VALUE_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text[:240]


@dataclass(frozen=True)
class _FalAdapterBase:
    client: FalClient
    allowed_models: dict[str, tuple[str, ...]]

    def _model(self, kind: str, requested: str | None) -> str:
        allowed = self.allowed_models.get(kind, ())
        if requested is None:
            if len(allowed) != 1:
                raise ConfigurationError(
                    f"An explicit {kind} model is required; allowlist has {len(allowed)} entries"
                )
            return allowed[0]
        if requested not in allowed:
            raise ValueError(f"{kind} model is not allowlisted: {requested!r}")
        return requested


class FalEditAdapter(_FalAdapterBase):
    """Inpaint and generative upscale through fal, using the shared edit contract."""

    def edit(
        self,
        source: bytes,
        *,
        prompt: str,
        negative_prompt: str = "",
        mask: bytes | None = None,
        checkpoint: str | None = None,
        seed: int = 0,
        steps: int = 20,
        denoise: float = 0.55,
        model: str | None = None,
    ) -> EditResult:
        kind = "inpaint" if mask is not None else "edit"
        model = self._model(kind, model or checkpoint)
        payload: dict[str, Any] = {
            "prompt": prompt,
            "image_url": _data_uri(source, "image/png"),
            "seed": seed,
            "num_inference_steps": steps,
            "strength": denoise,
        }
        if mask is not None:
            payload["mask_url"] = _data_uri(mask, "image/png")
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        result = self.client.run(model, payload)
        content = self.client.download(_first_output_url(result))
        return EditResult(
            content=content,
            content_hash=sha256_bytes(content),
            workflow_hash=sha256_json(payload),
            operation={
                "type": f"fal_{kind}",
                "model": model,
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "seed": seed,
                "steps": steps,
                "denoise": denoise,
                "payload_hash": sha256_json(payload),
                "provider": "fal",
            },
        )

    def upscale(
        self,
        source: bytes,
        *,
        model_name: str | None = None,
        scale: int = 2,
    ) -> EditResult:
        model = self._model("upscale", model_name)
        payload: dict[str, Any] = {
            "image_url": _data_uri(source, "image/png"),
            "scale": scale,
        }
        result = self.client.run(model, payload)
        content = self.client.download(_first_output_url(result))
        return EditResult(
            content=content,
            content_hash=sha256_bytes(content),
            workflow_hash=sha256_json(payload),
            operation={
                "type": "fal_upscale",
                "model_name": model,
                "scale": scale,
                "payload_hash": sha256_json(payload),
                "provider": "fal",
            },
        )


class FalVisionJudge:
    """Visual judgement through a fal-hosted vision model."""

    def __init__(self, client: FalClient, *, model: str):
        self.client = client
        self.model = model

    def judge(
        self, image: bytes, *, intent: str, mime: str = "image/png"
    ) -> VisualJudgement:
        from .judge import RUBRIC

        payload = {
            "prompt": f"{RUBRIC}\n\nIntent: {intent}",
            "image_url": _data_uri(image, mime),
        }
        result = self.client.run(self.model, payload)
        text = (
            result.get("output")
            or result.get("text")
            or result.get("response")
            or json.dumps(result)
        )
        if not isinstance(text, str):
            text = json.dumps(text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise ConfigurationError(
                "fal vision model returned invalid JSON"
            ) from error
        return VisualJudgement(
            model=self.model,
            scores=VisualScores.model_validate(parsed["scores"]),
            findings=tuple(
                VisualFinding.model_validate(item)
                for item in parsed.get("findings", [])
            ),
            confidence=float(parsed["confidence"]),
            verdict=parsed["verdict"],
            image_hash=sha256_bytes(image),
            prompt_hash=sha256_json({"rubric": RUBRIC, "intent": intent}),
            response_hash=sha256_bytes(text.encode("utf-8")),
            judged_at=utc_now(),
        )

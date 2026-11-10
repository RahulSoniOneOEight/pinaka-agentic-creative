from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..errors import ConfigurationError
from ..models import ToolOperationRequest
from ..utils import sha256_bytes, sha256_json
from .tool import ToolExecutionResult

DEFAULT_MODEL = "gemini-2.5-flash-image"
DEFAULT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"
API_KEY_ENV = "GEMINI_API_KEY"
API_KEY_FILE_ENV = "GEMINI_API_KEY_FILE"
DEFAULT_KEY_FILE = Path.home() / ".config" / "opencode" / "secrets" / "gemini-api-key"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
SECRET_PATTERN = re.compile(r"(AIza[0-9A-Za-z_-]{20,})")


def _sanitize(message: str) -> str:
    return SECRET_PATTERN.sub("[redacted]", message)[:300]


class GeminiImageAdapter:
    """Zero-cost Nano Banana image adapter.

    The adapter refuses to run unless the operation declares ``max_cost_minor`` 0 and
    ``billing_mode`` ``free_tier``. It never logs the prompt, the API key, or the raw
    provider response.
    """

    profile_key = "google-nano-banana-image"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        endpoint: str = DEFAULT_ENDPOINT,
        transport: Callable[[str, dict[str, str], dict[str, Any]], bytes] | None = None,
        timeout: int = 120,
    ):
        self.api_key = api_key
        self.model = model or os.environ.get("GEMINI_IMAGE_MODEL") or DEFAULT_MODEL
        self.endpoint = endpoint.rstrip("/")
        self.transport = transport
        self.timeout = timeout

    def _key(self) -> str:
        key = (self.api_key or os.environ.get(API_KEY_ENV) or "").strip()
        if not key:
            configured = os.environ.get(API_KEY_FILE_ENV)
            path = Path(configured).expanduser() if configured else DEFAULT_KEY_FILE
            if path.is_file():
                key = path.read_text(encoding="utf-8").strip()
        if not key:
            raise ConfigurationError(
                f"Nano Banana requires {API_KEY_ENV} or a key file; no key is configured"
            )
        return key

    def ensure_configured(self) -> None:
        """Fail before a controller operation is reserved when no key exists."""

        self._key()

    def _post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> bytes:
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                payload = json.loads(error.read().decode("utf-8", errors="replace"))
                detail = str((payload.get("error") or {}).get("message") or "")
            except (ValueError, UnicodeDecodeError):
                detail = ""
            suffix = f": {_sanitize(detail)}" if detail else ""
            raise RuntimeError(
                f"Nano Banana request failed with HTTP {error.code}{suffix}"
            ) from None
        except urllib.error.URLError as error:
            raise RuntimeError(
                f"Nano Banana request failed: {_sanitize(str(error.reason))}"
            ) from None

    def _body(self, request: ToolOperationRequest) -> dict[str, Any]:
        parameters = request.parameters
        prompt = str(parameters.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("An image prompt is required")
        if len(prompt) > 8000:
            raise ValueError("The image prompt exceeds 8000 characters")
        if "api_key" in parameters or "key" in parameters:
            raise ValueError("Credentials must not be supplied in operation parameters")
        generation_config: dict[str, Any] = {"responseModalities": ["TEXT", "IMAGE"]}
        image_config: dict[str, Any] = {}
        aspect_ratio = parameters.get("aspect_ratio")
        if aspect_ratio:
            if not re.fullmatch(r"\d{1,2}:\d{1,2}", str(aspect_ratio)):
                raise ValueError("aspect_ratio must look like 9:16")
            image_config["aspectRatio"] = str(aspect_ratio)
        image_size = parameters.get("image_size")
        if image_size:
            if str(image_size) not in {"1K", "2K", "4K"}:
                raise ValueError("image_size must be 1K, 2K or 4K")
            image_config["imageSize"] = str(image_size)
        if image_config:
            generation_config["imageConfig"] = image_config
        return {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }

    def execute(self, request: ToolOperationRequest) -> ToolExecutionResult:
        if request.operation != "image.generate":
            raise ValueError(
                f"Nano Banana adapter does not support {request.operation!r}"
            )
        if request.limits.get("max_cost_minor") != 0:
            raise ValueError(
                "Nano Banana adapter is zero-cost only; set limits.max_cost_minor to 0"
            )
        if request.parameters.get("billing_mode", "free_tier") != "free_tier":
            raise ValueError(
                "Nano Banana adapter refuses paid billing; use billing_mode free_tier"
            )
        key = self._key()
        body = self._body(request)
        url = f"{self.endpoint}/models/{self.model}:generateContent"
        raw = (self.transport or self._post)(
            url,
            {"x-goog-api-key": key},
            body,
        )
        payload = json.loads(raw.decode("utf-8"))
        candidates = payload.get("candidates") or []
        if not candidates:
            raise RuntimeError("Nano Banana returned no candidates")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        image_part = next(
            (
                part
                for part in parts
                if part.get("inlineData") or part.get("inline_data")
            ),
            None,
        )
        if image_part is None:
            raise RuntimeError("Nano Banana returned no image data")
        inline = image_part.get("inlineData") or image_part.get("inline_data") or {}
        mime_type = str(inline.get("mimeType") or inline.get("mime_type") or "")
        if not mime_type.startswith("image/"):
            raise RuntimeError("Nano Banana returned a non-image payload")
        encoded = str(inline.get("data") or "")
        if not encoded:
            raise RuntimeError("Nano Banana returned an empty image payload")
        try:
            image = base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise RuntimeError(
                "Nano Banana returned invalid base64 image data"
            ) from error
        if not image or len(image) > MAX_IMAGE_BYTES:
            raise RuntimeError("Nano Banana image size is outside the allowed range")
        return ToolExecutionResult(
            output_bytes=image,
            details={
                "adapter": self.profile_key,
                "model": self.model,
                "mime_type": mime_type,
                "prompt_sha256": sha256_json(
                    {"prompt": body["contents"][0]["parts"][0]["text"]}
                ),
                "billing_mode": "free_tier",
                "image_bytes": len(image),
            },
            response_hash=sha256_bytes(image),
        )

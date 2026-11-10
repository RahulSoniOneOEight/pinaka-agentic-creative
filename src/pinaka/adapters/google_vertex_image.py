from __future__ import annotations

import math
import os
import re
from collections.abc import Callable
from typing import Any

from ..errors import ConfigurationError
from ..models import ToolOperationRequest
from ..utils import sha256_bytes, sha256_json
from .tool import ToolExecutionResult

DEFAULT_PROJECT = "spheric-host-505921-t5"
DEFAULT_LOCATION = "global"
DEFAULT_MODEL = "gemini-3.1-flash-image"
MODEL_COSTS_USD_PER_MILLION = {
    "gemini-3.1-flash-image": (0.50, 60.00),
    "gemini-3.1-flash-image-preview": (0.50, 60.00),
    "gemini-3.1-flash-lite-image": (0.25, 30.00),
    "gemini-3-pro-image": (2.00, 120.00),
    "gemini-nano-banana-2.1": (1.50, 30.00),
    "gemini-2.5-flash-image": (0.30, 30.00),
}
OUTPUT_TOKENS = {
    "gemini-3.1-flash-image": {"512": 747, "1K": 1120, "2K": 1680, "4K": 2520},
    "gemini-3.1-flash-image-preview": {"512": 747, "1K": 1120, "2K": 1680, "4K": 2520},
    "gemini-3.1-flash-lite-image": {"1K": 1120},
    "gemini-3-pro-image": {"1K": 1120, "2K": 1120, "4K": 2000},
    "gemini-nano-banana-2.1": {"1K": 1120, "2K": 1680, "4K": 3780},
    "gemini-2.5-flash-image": {"1K": 1290},
}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
ESTIMATED_INPUT_TOKENS = 100
ASPECT_PATTERN = re.compile(r"\d{1,2}:\d{1,2}")


def estimate_cost_minor(
    model: str, image_size: str = "1K", *, input_tokens: int = ESTIMATED_INPUT_TOKENS
) -> int:
    if model not in MODEL_COSTS_USD_PER_MILLION:
        raise ValueError(f"Unsupported Vertex image model: {model!r}")
    if image_size not in OUTPUT_TOKENS[model]:
        raise ValueError(f"Unsupported image_size {image_size!r} for {model}")
    input_rate, output_rate = MODEL_COSTS_USD_PER_MILLION[model]
    output_tokens = OUTPUT_TOKENS[model][image_size]
    cost = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
    return max(1, math.ceil(cost * 100) + 1)


class GoogleVertexImageAdapter:
    """Nano Banana through Vertex AI using Application Default Credentials.

    This route uses the Google Cloud project's finite trial credit, not the
    AI Studio free tier. The adapter requires an explicit per-call ceiling and
    never uses an API key.
    """

    profile_key = "google-vertex-nano-banana"

    def __init__(
        self,
        *,
        project: str | None = None,
        location: str | None = None,
        model: str = DEFAULT_MODEL,
        client: Any | None = None,
        client_factory: Callable[[], Any] | None = None,
    ):
        self.project = (
            project or os.environ.get("GOOGLE_CLOUD_PROJECT") or DEFAULT_PROJECT
        )
        self.location = (
            location or os.environ.get("GOOGLE_CLOUD_LOCATION") or DEFAULT_LOCATION
        )
        if model not in MODEL_COSTS_USD_PER_MILLION:
            raise ValueError(f"Unsupported Vertex image model: {model!r}")
        self.model = model
        self._client = client
        self.client_factory = client_factory

    def _client_instance(self) -> Any:
        if self._client is not None:
            return self._client
        if self.client_factory is not None:
            self._client = self.client_factory()
            return self._client
        try:
            from google import genai
        except ImportError as error:
            raise ConfigurationError(
                "google-genai is not installed; install the gcp-image extra"
            ) from error
        self._client = genai.Client(
            vertexai=True,
            project=self.project,
            location=self.location,
        )
        return self._client

    @staticmethod
    def _auth_error(error: Exception) -> ConfigurationError | None:
        text = str(error).casefold()
        if any(
            marker in text
            for marker in (
                "reauthentication",
                "refresherror",
                "application default credentials",
                "could not automatically determine credentials",
            )
        ):
            return ConfigurationError(
                "Google ADC is unavailable or expired; run "
                "gcloud auth application-default login"
            )
        return None

    def ensure_configured(self) -> None:
        client = self._client_instance()
        try:
            client.models.get(model=self.model)
        except Exception as error:
            auth = self._auth_error(error)
            if auth:
                raise auth from error
            raise ConfigurationError(
                f"Vertex image model is not available: {type(error).__name__}"
            ) from error

    def _parameters(self, request: ToolOperationRequest) -> dict[str, Any]:
        parameters = request.parameters
        prompt = str(parameters.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("An image prompt is required")
        if len(prompt) > 4000:
            raise ValueError("The image prompt exceeds 4000 characters")
        model = str(parameters.get("model") or self.model)
        if model not in MODEL_COSTS_USD_PER_MILLION:
            raise ValueError(f"Unsupported Vertex image model: {model!r}")
        image_size = str(parameters.get("image_size") or "1K").upper()
        if image_size == "512":
            image_size = "512"
        aspect_ratio = str(parameters.get("aspect_ratio") or "16:9")
        if not ASPECT_PATTERN.fullmatch(aspect_ratio):
            raise ValueError("aspect_ratio must look like 16:9")
        estimated = estimate_cost_minor(model, image_size)
        ceiling = int(request.limits.get("max_cost_minor", 0))
        if ceiling < estimated:
            raise ValueError(
                f"Vertex image call needs at least {estimated} minor units; "
                f"the operation ceiling is {ceiling}"
            )
        return {
            "prompt": prompt,
            "model": model,
            "image_size": image_size,
            "aspect_ratio": aspect_ratio,
            "estimated_cost_minor": estimated,
        }

    def execute(self, request: ToolOperationRequest) -> ToolExecutionResult:
        if request.operation != "image.generate":
            raise ValueError(
                f"Vertex image adapter does not support {request.operation!r}"
            )
        parameters = self._parameters(request)
        try:
            from google.genai import types
        except ImportError as error:
            raise ConfigurationError(
                "google-genai is not installed; install the gcp-image extra"
            ) from error
        config = types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio=parameters["aspect_ratio"],
                image_size=parameters["image_size"],
            ),
        )
        client = self._client_instance()
        try:
            response = client.models.generate_content(
                model=parameters["model"],
                contents=parameters["prompt"],
                config=config,
            )
        except Exception as error:
            auth = self._auth_error(error)
            if auth:
                raise auth from error
            raise RuntimeError(
                f"Vertex image request failed: {type(error).__name__}"
            ) from None
        image = None
        mime_type = "image/png"
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                inline = getattr(part, "inline_data", None)
                if inline is not None and getattr(inline, "data", None):
                    image = inline.data
                    mime_type = getattr(inline, "mime_type", None) or mime_type
                    break
            if image is not None:
                break
        if image is None:
            raise RuntimeError("Vertex returned no image data")
        if isinstance(image, str):
            import base64

            image = base64.b64decode(image)
        if not image.startswith((PNG_MAGIC, JPEG_MAGIC)):
            raise RuntimeError("Vertex returned a non-image payload")
        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        candidate_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        input_rate, output_rate = MODEL_COSTS_USD_PER_MILLION[parameters["model"]]
        actual_cost = (
            prompt_tokens * input_rate + candidate_tokens * output_rate
        ) / 1_000_000
        actual_minor = max(1, math.ceil(actual_cost * 100))
        return ToolExecutionResult(
            output_bytes=image,
            details={
                "adapter": self.profile_key,
                "project": self.project,
                "location": self.location,
                "model": parameters["model"],
                "image_size": parameters["image_size"],
                "aspect_ratio": parameters["aspect_ratio"],
                "mime_type": mime_type,
                "prompt_sha256": sha256_json({"prompt": parameters["prompt"]}),
                "estimated_cost_minor": parameters["estimated_cost_minor"],
                "actual_cost_minor": actual_minor,
                "prompt_token_count": prompt_tokens,
                "candidate_token_count": candidate_tokens,
                "total_token_count": int(getattr(usage, "total_token_count", 0) or 0),
                "image_bytes": len(image),
                "billing": "gcp_trial_credit",
            },
            response_hash=sha256_bytes(image),
        )

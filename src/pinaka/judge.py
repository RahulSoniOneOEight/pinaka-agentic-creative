from __future__ import annotations

import base64
import json
import os
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from .errors import ConfigurationError
from .models import StrictModel
from .utils import sha256_bytes, sha256_json, utc_now

API_KEY_ENV = "PINAKA_VISION_API_KEY"
API_KEY_FILE_ENV = "PINAKA_VISION_API_KEY_FILE"
DEFAULT_API_KEY_FILE = (
    Path.home() / ".config" / "opencode" / "secrets" / "vision-api-key"
)

RUBRIC = """You are a strict creative reviewer for a brand video.
Judge only what is visible in the image against the stated intent.
Return JSON with this shape:
{
  "scores": {"tone": 0-1, "brand": 0-1, "composition": 0-1,
             "legibility": 0-1, "safety": 0-1},
  "findings": [{"category": "tone|brand|composition|legibility|safety|other",
                "severity": "info|warning|blocking", "detail": "..."}],
  "confidence": 0-1,
  "verdict": "accept|revise|reject"
}
Be conservative: use "blocking" only for unsafe, misleading, unreadable or off-brand content.
Do not invent details that are not visible."""


class VisualScores(StrictModel):
    tone: float = Field(ge=0, le=1)
    brand: float = Field(ge=0, le=1)
    composition: float = Field(ge=0, le=1)
    legibility: float = Field(ge=0, le=1)
    safety: float = Field(ge=0, le=1)


class VisualFinding(StrictModel):
    category: Literal["tone", "brand", "composition", "legibility", "safety", "other"]
    severity: Literal["info", "warning", "blocking"]
    detail: str = Field(min_length=1, max_length=1000)


class VisualJudgement(StrictModel):
    model: str
    scores: VisualScores
    findings: tuple[VisualFinding, ...]
    confidence: float = Field(ge=0, le=1)
    verdict: Literal["accept", "revise", "reject"]
    image_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    prompt_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    response_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    judged_at: str


Transport = Callable[[str, dict[str, str], bytes], bytes]


class OpenAICompatibleJudge:
    """Visual judgement through an OpenAI-compatible chat completions endpoint."""

    profile_key = "vision-judge"

    def __init__(
        self,
        *,
        model: str,
        endpoint: str = "https://api.openai.com/v1/chat/completions",
        api_key: str | None = None,
        timeout: int = 120,
        transport: Transport | None = None,
    ):
        self.model = model
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout = timeout
        self.transport = transport

    def _resolve_key(self) -> str:
        key = (self.api_key or os.environ.get(API_KEY_ENV) or "").strip()
        if not key:
            configured = os.environ.get(API_KEY_FILE_ENV)
            path = Path(configured).expanduser() if configured else DEFAULT_API_KEY_FILE
            if path.is_file():
                key = path.read_text(encoding="utf-8").strip()
        if not key:
            raise ConfigurationError(
                f"A vision credential is required in {API_KEY_ENV} or a key file; "
                "none is configured"
            )
        return key

    def _post(self, headers: dict[str, str], body: bytes) -> bytes:
        if self.transport is not None:
            return self.transport(self.endpoint, headers, body)
        request = urllib.request.Request(
            self.endpoint, data=body, headers=headers, method="POST"
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()

    def judge(
        self, image: bytes, *, intent: str, mime: str = "image/png"
    ) -> VisualJudgement:
        key = self._resolve_key()
        prompt = f"{RUBRIC}\n\nIntent: {intent}"
        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    f"data:{mime};base64,"
                                    + base64.b64encode(image).decode("ascii")
                                )
                            },
                        },
                    ],
                }
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        raw = self._post(
            {"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            body,
        )
        text = _extract_content(raw)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise ConfigurationError("Vision judge returned invalid JSON") from error

        image_hash = sha256_bytes(image)
        judgement = VisualJudgement(
            model=self.model,
            scores=VisualScores.model_validate(parsed["scores"]),
            findings=tuple(
                VisualFinding.model_validate(item)
                for item in parsed.get("findings", [])
            ),
            confidence=float(parsed["confidence"]),
            verdict=parsed["verdict"],
            image_hash=image_hash,
            prompt_hash=sha256_json({"rubric": RUBRIC, "intent": intent}),
            response_hash=sha256_bytes(text.encode("utf-8")),
            judged_at=utc_now(),
        )
        return judgement


def _extract_content(raw: bytes) -> str:
    payload: Any = json.loads(raw.decode("utf-8"))
    if isinstance(payload, dict) and "choices" in payload:
        message = payload["choices"][0]["message"]["content"]
        if isinstance(message, list):
            return "".join(
                part.get("text", "") for part in message if isinstance(part, dict)
            )
        return str(message)
    if isinstance(payload, dict) and "output_text" in payload:
        return str(payload["output_text"])
    raise ConfigurationError("Vision response did not contain message content")

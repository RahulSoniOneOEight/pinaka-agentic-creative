"""fal avatar / lip-sync adapter.

Contracts are taken from the published queue OpenAPI schemas:

* ``fal-ai/bytedance/omnihuman/v1.5``: image + audio -> talking video.
* ``fal-ai/sync-lipsync/v3``: video + audio -> lip-synced video.

Both accept URLs. Local bytes are sent as data URIs under a size cap; a caller
that already has hosted URLs can pass them instead.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from ..adapters.base import PollResult, Submission
from ..enums import JobStatus
from ..errors import IntegrityError
from ..fal import FalClient
from ..models import JobRequest, Money
from ..policy.guards import require_one_take
from ..utils import sha256_bytes

OMNIHUMAN_MODEL = "fal-ai/bytedance/omnihuman/v1.5"
SYNC3_MODEL = "fal-ai/sync-lipsync/v3"
AVATAR_MODELS = (OMNIHUMAN_MODEL, SYNC3_MODEL)
OMNIHUMAN_SOURCE = "image"
SYNC3_SOURCE = "video"
MAX_DATA_URI_BYTES = 12 * 1024 * 1024


def _data_uri(content: bytes, mime: str) -> str:
    if len(content) > MAX_DATA_URI_BYTES:
        raise IntegrityError(
            "Avatar input is too large to inline; host it and pass a URL instead"
        )
    return f"data:{mime};base64," + base64.b64encode(content).decode("ascii")


def build_avatar_payload(
    model: str,
    *,
    image_data: bytes | None = None,
    video_data: bytes | None = None,
    audio_data: bytes | None = None,
    image_url: str | None = None,
    video_url: str | None = None,
    audio_url: str | None = None,
    image_mime: str = "image/png",
    video_mime: str = "video/mp4",
    audio_mime: str = "audio/wav",
    resolution: str = "720p",
    turbo_mode: bool = True,
    prompt: str | None = None,
    sync_mode: str = "cut_off",
) -> dict[str, Any]:
    """Build the exact request body for one supported avatar endpoint."""

    if model not in AVATAR_MODELS:
        raise ValueError(
            f"Unsupported avatar model {model!r}; expected one of {AVATAR_MODELS}"
        )
    if not audio_data and not audio_url:
        raise ValueError("Avatar generation needs audio")
    audio = audio_url or _data_uri(audio_data or b"", audio_mime)
    if model == OMNIHUMAN_MODEL:
        source = image_url or (
            _data_uri(image_data, image_mime) if image_data else None
        )
        if not source:
            raise ValueError("OmniHuman needs a portrait image")
        if resolution not in {"720p", "1080p"}:
            raise ValueError("resolution must be '720p' or '1080p'")
        payload: dict[str, Any] = {
            "image_url": source,
            "audio_url": audio,
            "resolution": resolution,
            "turbo_mode": bool(turbo_mode),
        }
        if prompt:
            payload["prompt"] = prompt
        return payload
    source = video_url or (_data_uri(video_data, video_mime) if video_data else None)
    if not source:
        raise ValueError("Sync 3 needs a source video")
    if sync_mode not in {"cut_off", "loop", "bounce", "silence", "remap"}:
        raise ValueError("sync_mode is not a documented Sync 3 mode")
    return {
        "video_url": source,
        "audio_url": audio,
        "sync_mode": sync_mode,
        "enable_safety_checker": True,
    }


def _first_video_url(payload: dict[str, Any]) -> str:
    video = payload.get("video")
    if isinstance(video, dict) and video.get("url"):
        return str(video["url"])
    if isinstance(video, str):
        return video
    for key in ("video_url", "url"):
        if payload.get(key):
            return str(payload[key])
    raise IntegrityError("Avatar response contained no video URL")


@dataclass
class FalAvatarAdapter:
    """Blocking fal avatar call inside the controller's job boundary."""

    client: FalClient
    model: str
    provider_name: str = "fal"
    source_bytes: bytes | None = None
    source_mime: str = "image/png"
    audio_bytes: bytes | None = None
    audio_mime: str = "audio/wav"
    resolution: str = "720p"
    turbo_mode: bool = True
    prompt: str | None = None
    sync_mode: str = "cut_off"
    _results: dict[str, PollResult] = field(default_factory=dict, init=False)

    def submit(self, request: JobRequest) -> Submission:
        require_one_take(request.parameters)
        if request.estimated_cost.currency != "USD":
            raise IntegrityError(
                "fal avatar estimates require USD; no implicit currency conversion"
            )
        if self.source_bytes is None:
            raise IntegrityError("Avatar generation needs a verified source artifact")
        if self.audio_bytes is None:
            raise IntegrityError("Avatar generation needs a verified audio artifact")
        if self.model == OMNIHUMAN_MODEL:
            payload = build_avatar_payload(
                self.model,
                image_data=self.source_bytes,
                image_mime=self.source_mime,
                audio_data=self.audio_bytes,
                audio_mime=self.audio_mime,
                resolution=self.resolution,
                turbo_mode=self.turbo_mode,
                prompt=self.prompt,
            )
        else:
            payload = build_avatar_payload(
                self.model,
                video_data=self.source_bytes,
                video_mime=self.source_mime,
                audio_data=self.audio_bytes,
                audio_mime=self.audio_mime,
                sync_mode=self.sync_mode,
            )
        result = self.client.run(self.model, payload)
        content = self.client.download(_first_video_url(result))
        provider_job_id = f"fal_{sha256_bytes(content)[:24]}"
        duration = result.get("duration")
        self._results[provider_job_id] = PollResult(
            status=JobStatus.SUCCEEDED,
            raw={
                "model": self.model,
                "reported_duration_seconds": duration,
                "cost_source": "planned_estimate",
                "simulated": False,
            },
            output_bytes=content,
            actual_cost=Money(
                amount=request.estimated_cost.amount,
                currency=request.estimated_cost.currency,
            ),
        )
        return Submission(
            provider_job_id=provider_job_id,
            raw={"model": self.model, "cost_source": "planned_estimate"},
        )

    def poll(self, provider_job_id: str, request: JobRequest) -> PollResult:
        try:
            return self._results[provider_job_id]
        except KeyError as error:
            raise ValueError(
                f"fal avatar result for {provider_job_id} is not available"
            ) from error

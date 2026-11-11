from __future__ import annotations

import math

# fal publishes per-model prices; these tables mirror the model pages reviewed in
# November 2026. Always confirm at https://fal.ai/models/<endpoint-id>/llms.txt or
# via api.fal.ai/v1/models/pricing before batching paid work.

FAL_IMAGE_USD_PER_MEGAPIXEL: dict[str, float] = {
    "fal-ai/sana": 0.001,
    "fal-ai/flux/schnell": 0.003,
    "fal-ai/z-image/turbo": 0.005,
    "fal-ai/flux-2/flash": 0.005,
    "fal-ai/flux/dev": 0.025,
}

FAL_IMAGE_USD_FLAT: dict[str, float] = {
    "fal-ai/qwen-image": 0.021,
}

FAL_VIDEO_USD_PER_SECOND: dict[str, dict[str, float]] = {
    "fal-ai/ltx-2-19b/distilled/image-to-video": {"720p": 0.018},
    "minimax/h3-max-turbo/image-to-video": {"480p": 0.0175, "768p": 0.028},
    "fal-ai/pixverse/v6/image-to-video": {"360p": 0.025, "540p": 0.035, "720p": 0.045},
    "fal-ai/pixverse/v5/image-to-video": {"360p": 0.030, "540p": 0.030, "720p": 0.040},
    "fal-ai/wan/v2.5/image-to-video": {"720p": 0.05},
    "bytedance/seedance-2.0/mini/image-to-video": {"480p": 0.068},
    "xai/grok-imagine-video/v1.5/image-to-video": {"480p": 0.08},
}

FAL_DRAFT_IMAGE_MODEL = "fal-ai/flux/schnell"
FAL_KEEPER_IMAGE_MODEL = "fal-ai/flux-2/flash"
FAL_CHEAPEST_VIDEO_MODEL = "fal-ai/ltx-2-19b/distilled/image-to-video"
FAL_PROMOTIONS: dict[str, str] = {
    "minimax/h3-max-turbo/image-to-video": "promo rate ends 2026-11-15",
}

MEGAPIXEL_1024 = (1024 * 1024) / 1_000_000


def _minor(amount: float) -> int:
    return max(1, math.ceil(amount * 100))


def estimate_fal_image_minor(
    endpoint: str, *, megapixels: float = MEGAPIXEL_1024
) -> int:
    if megapixels <= 0:
        raise ValueError("megapixels must be positive")
    if endpoint in FAL_IMAGE_USD_FLAT:
        return _minor(FAL_IMAGE_USD_FLAT[endpoint])
    rate = FAL_IMAGE_USD_PER_MEGAPIXEL.get(endpoint)
    if rate is None:
        raise ValueError(f"Unknown fal image endpoint: {endpoint}")
    return _minor(rate * megapixels)


def estimate_fal_video_minor(
    endpoint: str, *, seconds: float, resolution: str = "720p"
) -> int:
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    resolutions = FAL_VIDEO_USD_PER_SECOND.get(endpoint)
    if resolutions is None:
        raise ValueError(f"Unknown fal video endpoint: {endpoint}")
    rate = resolutions.get(resolution)
    if rate is None:
        raise ValueError(
            f"Unsupported resolution {resolution!r} for {endpoint}; "
            f"choose one of {sorted(resolutions)}"
        )
    return _minor(rate * seconds)


def fal_budget_preview(
    *, images: int = 0, clips: int = 0, clip_seconds: float = 5.0
) -> dict[str, object]:
    """Rough spend preview for a batch, using the cheapest suitable models."""

    image_minor = images * estimate_fal_image_minor(FAL_DRAFT_IMAGE_MODEL)
    clip_minor = clips * estimate_fal_video_minor(
        FAL_CHEAPEST_VIDEO_MODEL, seconds=clip_seconds
    )
    return {
        "images": images,
        "clips": clips,
        "clip_seconds": clip_seconds,
        "image_model": FAL_DRAFT_IMAGE_MODEL,
        "video_model": FAL_CHEAPEST_VIDEO_MODEL,
        "estimated_image_minor": image_minor,
        "estimated_video_minor": clip_minor,
        "estimated_total_minor": image_minor + clip_minor,
    }

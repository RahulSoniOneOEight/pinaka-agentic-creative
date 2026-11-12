from __future__ import annotations

import math
from dataclasses import dataclass

# Studio's own fal.video rate card, in USD per second by resolution.
# Re-verify at fal.ai/models/<endpoint>/llms.txt before a paid run.
RESOLUTIONS = ("360p", "480p", "540p", "720p", "768p", "1080p", "2k", "4k")


@dataclass(frozen=True)
class VideoModel:
    family: str
    modes: tuple[str, ...]
    max_seconds: int
    rates: dict[str, float]
    note: str = ""


STUDIO_VIDEO_MODELS: dict[str, VideoModel] = {
    "google/gemini-omni-flash/v1.2": VideoModel(
        family="google/gemini-omni-flash/v1.2",
        modes=("text-to-video", "first-frame", "reference-media"),
        max_seconds=15,
        rates={"360p": 0.027, "720p": 0.082, "1080p": 0.123, "4k": 0.246},
        note="native audio and references included",
    ),
    "minimax/h3": VideoModel(
        family="minimax/h3",
        modes=(
            "text-to-video",
            "first-frame",
            "first-and-last-frame",
            "reference-media",
            "video-to-video",
        ),
        max_seconds=15,
        rates={"480p": 0.05, "768p": 0.06, "2k": 0.13, "4k": 0.16},
        note="best quality per dollar for keyframe work",
    ),
    "lightricks/ltx-2.5/fast": VideoModel(
        family="lightricks/ltx-2.5/fast",
        modes=(
            "text-to-video",
            "first-frame",
            "first-and-last-frame",
            "audio-to-video",
        ),
        max_seconds=20,
        rates={"720p": 0.09},
        note="six to twenty seconds in one generation",
    ),
    "lightricks/ltx-2.5/pro": VideoModel(
        family="lightricks/ltx-2.5/pro",
        modes=(
            "text-to-video",
            "first-frame",
            "first-and-last-frame",
            "audio-to-video",
        ),
        max_seconds=20,
        rates={"720p": 0.12},
    ),
    "alibaba/wan-3.0": VideoModel(
        family="alibaba/wan-3.0",
        modes=("text-to-video", "first-frame"),
        max_seconds=30,
        rates={"480p": 0.05, "720p": 0.10},
    ),
    "kling/video-3.0": VideoModel(
        family="kling/video-3.0",
        modes=("text-to-video", "first-frame"),
        max_seconds=30,
        rates={"std": 0.126, "pro": 0.168},
        note="std/pro rather than resolution",
    ),
    "kling/video-4.0": VideoModel(
        family="kling/video-4.0",
        modes=("text-to-video", "first-frame"),
        max_seconds=30,
        rates={"720p": 0.157, "1080p": 0.235},
    ),
    "xai/grok-imagine/v1.5": VideoModel(
        family="xai/grok-imagine/v1.5",
        modes=("text-to-video", "first-frame"),
        max_seconds=15,
        rates={"480p": 0.08, "720p": 0.14},
    ),
    "flux-3-max": VideoModel(
        family="flux-3-max",
        modes=("text-to-video", "first-frame"),
        max_seconds=20,
        rates={"720p": 0.17, "1080p": 0.29},
    ),
    "bytedance/seedance-2.5": VideoModel(
        family="bytedance/seedance-2.5",
        modes=("text-to-video", "first-frame"),
        max_seconds=30,
        rates={},
        note="token-billed; estimate needs a token count, so it is not selectable here",
    ),
}

DEFAULT_MODEL = "minimax/h3"
DEFAULT_RESOLUTION = "768p"


def model(family: str) -> VideoModel:
    try:
        return STUDIO_VIDEO_MODELS[family]
    except KeyError as error:
        raise ValueError(
            f"Unknown Studio video model {family!r}; "
            f"choose one of {', '.join(sorted(STUDIO_VIDEO_MODELS))}"
        ) from error


def estimate_minor(family: str, *, seconds: float, resolution: str) -> int:
    spec = model(family)
    if not spec.rates:
        raise ValueError(
            f"{family} is billed by tokens; provide a token estimate instead"
        )
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    if seconds > spec.max_seconds:
        raise ValueError(f"{family} allows at most {spec.max_seconds}s per generation")
    rate = spec.rates.get(resolution.lower())
    if rate is None:
        raise ValueError(
            f"{family} does not offer {resolution!r}; "
            f"choose one of {', '.join(sorted(spec.rates))}"
        )
    return max(1, math.ceil(rate * seconds * 100))


def supports(family: str, mode: str) -> bool:
    return mode in model(family).modes


def plan_preview(
    *, family: str, seconds: float, resolution: str, count: int = 1
) -> dict[str, object]:
    per_shot = estimate_minor(family, seconds=seconds, resolution=resolution)
    return {
        "model": family,
        "resolution": resolution,
        "seconds_per_shot": seconds,
        "shots": count,
        "per_shot_minor": per_shot,
        "total_minor": per_shot * count,
        "max_seconds": model(family).max_seconds,
        "modes": list(model(family).modes),
    }


def affordable_takes(
    *,
    family: str,
    seconds: float,
    resolution: str,
    available_minor: int,
    max_takes: int = 3,
) -> dict[str, object]:
    """How many takes fit the remaining budget, without spending anything."""

    if max_takes < 1:
        raise ValueError("max_takes must be at least 1")
    per_take = estimate_minor(family, seconds=seconds, resolution=resolution)
    affordable = max(0, available_minor // per_take)
    takes = min(max_takes, affordable)
    return {
        "model": family,
        "resolution": resolution,
        "seconds": seconds,
        "per_take_minor": per_take,
        "available_minor": available_minor,
        "affordable_takes": takes,
        "planned_takes": takes,
        "total_minor": per_take * takes,
        "max_takes": max_takes,
    }

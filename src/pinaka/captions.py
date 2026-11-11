from __future__ import annotations

from decimal import Decimal
from typing import Any


def _timestamp(seconds: float) -> str:
    if seconds < 0:
        raise ValueError("Caption time cannot be negative")
    total_ms = round(seconds * 1000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build_srt(shots: list[dict[str, Any]]) -> str:
    """Deterministic SRT from the governed shot plan (no ASR required)."""

    if not shots:
        raise ValueError("Captions require at least one shot")
    blocks = []
    cursor = Decimal(0)
    for index, shot in enumerate(shots, start=1):
        duration = Decimal(str(shot["duration_seconds"]))
        start = cursor
        end = cursor + duration
        cursor = end
        text = str(shot.get("narrative") or shot.get("beat") or "").strip()
        if not text:
            continue
        blocks.append(
            f"{len(blocks) + 1}\n"
            f"{_timestamp(float(start))} --> {_timestamp(float(end))}\n"
            f"{text}\n"
        )
    if not blocks:
        raise ValueError("Captions require at least one non-empty cue")
    return "\n".join(blocks)

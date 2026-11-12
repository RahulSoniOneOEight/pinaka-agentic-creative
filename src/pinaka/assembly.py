from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Segment:
    index: int
    path: str
    duration: Decimal
    start: Decimal = Decimal(0)


@dataclass(frozen=True)
class AssemblyPlan:
    inputs: tuple[str, ...]
    filter_complex: str
    output_label: str
    total_seconds: Decimal


def build_plan(
    segments: list[Segment],
    *,
    transition: str = "cut",
    dissolve_seconds: Decimal = Decimal("0.4"),
    width: int = 1080,
    height: int = 1920,
    fps: int = 24,
) -> AssemblyPlan:
    """Build an FFmpeg filter graph that trims, scales, concatenates and joins shots."""

    if len(segments) < 1:
        raise ValueError("Assembly needs at least one segment")
    if transition not in {"cut", "dissolve"}:
        raise ValueError("transition must be 'cut' or 'dissolve'")
    if width <= 0 or height <= 0 or fps <= 0:
        raise ValueError("Assembly needs a positive width, height and fps")

    parts: list[str] = []
    for position, segment in enumerate(segments):
        if segment.duration <= 0:
            raise ValueError("Assembly segments need a positive duration")
        if segment.start < 0:
            raise ValueError("Assembly in-points cannot be negative")
        parts.append(
            f"[{position}:v]trim=start={segment.start}:duration={segment.duration},"
            f"setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            f"fps={fps},format=yuv420p[v{position}]"
        )

    if transition == "cut" or len(segments) == 1:
        joined = "".join(f"[v{position}]" for position in range(len(segments)))
        parts.append(f"{joined}concat=n={len(segments)}:v=1:a=0[outv]")
        total = sum((segment.duration for segment in segments), Decimal(0))
        return AssemblyPlan(
            inputs=tuple(segment.path for segment in segments),
            filter_complex=";".join(parts),
            output_label="[outv]",
            total_seconds=total,
        )

    if dissolve_seconds <= 0:
        raise ValueError("dissolve_seconds must be positive for a dissolve")
    label = "v0"
    elapsed = segments[0].duration
    for position in range(1, len(segments)):
        offset = elapsed - dissolve_seconds
        if offset <= 0:
            raise ValueError("dissolve is longer than the first shot")
        next_label = f"x{position}"
        parts.append(
            f"[{label}][v{position}]xfade=transition=fade:"
            f"duration={dissolve_seconds}:offset={offset}[{next_label}]"
        )
        label = next_label
        elapsed = elapsed + segments[position].duration - dissolve_seconds
    parts.append(f"[{label}]null[outv]")
    return AssemblyPlan(
        inputs=tuple(segment.path for segment in segments),
        filter_complex=";".join(parts),
        output_label="[outv]",
        total_seconds=elapsed,
    )

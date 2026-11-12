"""Beat-aware cut planning.

Pure planning only: beat extraction is a separate, optional audio pass. These
functions decide where a cut lands once beat times are known, so the policy is
testable without an audio stack.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BeatPlan:
    desired_cuts: tuple[float, ...]
    cuts: tuple[float, ...]
    beats: tuple[float, ...]
    max_shift_seconds: float
    snapped: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "desired_cuts": list(self.desired_cuts),
            "cuts": list(self.cuts),
            "max_shift_seconds": self.max_shift_seconds,
            "snapped": self.snapped,
            "unsnapped": len(self.cuts) - self.snapped,
        }


def _validate(values: tuple[float, ...], label: str) -> tuple[float, ...]:
    cleaned = tuple(round(float(value), 3) for value in values)
    if any(value < 0 for value in cleaned):
        raise ValueError(f"{label} cannot be negative")
    if list(cleaned) != sorted(cleaned):
        raise ValueError(f"{label} must be ascending")
    return cleaned


def snap_to_beats(
    desired_cuts: tuple[float, ...] | list[float],
    beats: tuple[float, ...] | list[float],
    *,
    tolerance_seconds: float = 0.08,
) -> BeatPlan:
    """Move each desired cut to the nearest beat when it is close enough."""

    if tolerance_seconds < 0:
        raise ValueError("tolerance_seconds cannot be negative")
    desired = _validate(tuple(desired_cuts), "desired_cuts")
    beat_times = _validate(tuple(beats), "beats")
    if not beat_times and desired:
        return BeatPlan(desired, desired, (), 0.0, 0)
    cuts: list[float] = []
    shifts: list[float] = []
    snapped = 0
    previous = 0.0
    for moment in desired:
        index = bisect.bisect_left(beat_times, moment)
        candidates = [
            beat_times[position]
            for position in (index - 1, index)
            if 0 <= position < len(beat_times)
        ]
        nearest = (
            min(candidates, key=lambda beat: abs(beat - moment))
            if candidates
            else moment
        )
        shift = abs(nearest - moment)
        if shift <= tolerance_seconds:
            cut = nearest
            snapped += 1
            shifts.append(shift)
        else:
            cut = moment
        cut = max(cut, previous + 0.001)
        cuts.append(round(cut, 3))
        previous = cut
    return BeatPlan(
        desired_cuts=desired,
        cuts=tuple(cuts),
        beats=beat_times,
        max_shift_seconds=round(max(shifts), 3) if shifts else 0.0,
        snapped=snapped,
    )


def plan_song_fit(
    *,
    track_duration: float,
    target_duration: float,
    beats: tuple[float, ...] | list[float],
    fade_seconds: float = 0.5,
    tolerance_seconds: float = 0.08,
) -> dict[str, Any]:
    """Choose an in/out pair on beats so a music bed ends on a real phrase."""

    if track_duration <= 0 or target_duration <= 0:
        raise ValueError("durations must be positive")
    if fade_seconds < 0:
        raise ValueError("fade_seconds cannot be negative")
    beat_times = _validate(tuple(beats), "beats")
    if track_duration >= target_duration:
        plan = snap_to_beats(
            [target_duration], beat_times, tolerance_seconds=tolerance_seconds
        )
        out_point = plan.cuts[0] if plan.cuts else target_duration
        return {
            "mode": "trim",
            "in_point": 0.0,
            "out_point": round(min(out_point, track_duration), 3),
            "fade_out_seconds": fade_seconds,
            "beat_snapped": plan.snapped == 1,
        }
    usable = [beat for beat in beat_times if beat < track_duration]
    out_point = usable[-1] if usable else track_duration
    return {
        "mode": "loop",
        "in_point": 0.0,
        "out_point": round(out_point, 3),
        "loop_length": round(track_duration, 3),
        "fade_out_seconds": fade_seconds,
        "beat_snapped": bool(usable),
    }

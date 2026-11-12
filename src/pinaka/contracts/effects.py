from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .common import StrictModel

EffectKind = Literal[
    "camera_push_in",
    "camera_pull_out",
    "camera_pan_left",
    "camera_pan_right",
    "parallax",
    "transition_fade",
    "transition_dissolve",
    "light_glow",
    "blur",
    "particles",
    "sfx",
    "music_bed",
    "flash",
]


class EffectCueCreate(StrictModel):
    """One planned effect, with its rights basis and timing."""

    shot_key: str = Field(min_length=1, max_length=100)
    kind: EffectKind
    start_seconds: Decimal = Field(ge=0)
    duration_seconds: Decimal = Field(gt=0, le=60)
    rights: str = Field(min_length=1, max_length=500)
    params: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class EffectsPlanCreate(StrictModel):
    """Camera, transition, light, particle and sound plan for a production."""

    cues: tuple[EffectCueCreate, ...] = Field(min_length=1)
    sound_design_notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def flash_rate_is_safe(self) -> EffectsPlanCreate:
        flashes = sorted(
            (
                cue.start_seconds,
                cue.duration_seconds,
            )
            for cue in self.cues
            if cue.kind == "flash"
        )
        for index, (start, _) in enumerate(flashes):
            window_end = start + Decimal(1)
            count = sum(
                1 for other_start, _ in flashes[index:] if other_start < window_end
            )
            if count > 3:
                raise ValueError(
                    "More than three flashes in one second is unsafe for kids"
                )
        return self

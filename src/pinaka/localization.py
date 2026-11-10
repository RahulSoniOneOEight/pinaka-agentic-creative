from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field

from .models import StrictModel
from .utils import sha256_json, utc_now

LOCALE_PROFILES: dict[str, dict[str, Any]] = {
    "en": {
        "label": "English",
        "cps": 20.0,
        "line_chars": 42,
        "lines": 2,
        "expansion": 1.0,
    },
    "hi": {
        "label": "Hindi",
        "cps": 15.0,
        "line_chars": 32,
        "lines": 2,
        "expansion": 1.1,
    },
    "de": {
        "label": "German",
        "cps": 17.0,
        "line_chars": 38,
        "lines": 2,
        "expansion": 1.3,
    },
    "fr": {
        "label": "French",
        "cps": 17.0,
        "line_chars": 38,
        "lines": 2,
        "expansion": 1.2,
    },
    "es": {
        "label": "Spanish",
        "cps": 17.0,
        "line_chars": 38,
        "lines": 2,
        "expansion": 1.2,
    },
    "ja": {
        "label": "Japanese",
        "cps": 12.0,
        "line_chars": 20,
        "lines": 2,
        "expansion": 0.8,
    },
    "ar": {
        "label": "Arabic",
        "cps": 15.0,
        "line_chars": 34,
        "lines": 2,
        "expansion": 0.9,
    },
}

TIMECODE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class CaptionCue(StrictModel):
    index: int = Field(ge=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str


class CaptionIssue(StrictModel):
    cue_index: int
    check: str
    detail: str


class LocaleProfile(StrictModel):
    locale: str
    label: str
    max_chars_per_second: float
    max_line_chars: int
    max_lines: int
    text_expansion: float


class TargetPlan(StrictModel):
    locale: str
    label: str
    status: Literal["ready", "review_timing"]
    estimated_cps: float
    cues_over_limit: tuple[int, ...] = ()
    note: str


class LocalizationPlan(StrictModel):
    schema_version: str = "localization-plan/v1"
    generated_at: str
    source_locale: str
    source_cues: int
    source_issues: tuple[CaptionIssue, ...] = ()
    status: Literal["ready_for_translation", "needs_source_fix"]
    targets: tuple[TargetPlan, ...] = ()
    manifest_hash: str


def _timecode_to_ms(value: str) -> int:
    match = TIMECODE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Invalid SRT timecode: {value!r}")
    hours, minutes, seconds, milliseconds = (int(part) for part in match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds


def parse_srt(text: str) -> tuple[CaptionCue, ...]:
    blocks = [block for block in text.replace("\r\n", "\n").strip().split("\n\n")]
    cues: list[CaptionCue] = []
    for position, block in enumerate(blocks, start=1):
        lines = [line for line in block.split("\n") if line.strip()]
        if len(lines) < 3:
            raise ValueError(f"Cue {position} is incomplete")
        index = int(lines[0].strip())
        start_text, separator, end_text = lines[1].partition("-->")
        if not separator:
            raise ValueError(f"Cue {position} has no timecode separator")
        cue = CaptionCue(
            index=index,
            start_ms=_timecode_to_ms(start_text),
            end_ms=_timecode_to_ms(end_text),
            text="\n".join(lines[2:]).strip(),
        )
        if cue.end_ms <= cue.start_ms:
            raise ValueError(f"Cue {position} has a non-positive duration")
        cues.append(cue)
    if not cues:
        raise ValueError("The SRT contains no cues")
    return tuple(cues)


def locale_profile(locale: str) -> LocaleProfile:
    try:
        payload = LOCALE_PROFILES[locale]
    except KeyError as error:
        raise ValueError(f"Unsupported locale: {locale!r}") from error
    return LocaleProfile(
        locale=locale,
        label=payload["label"],
        max_chars_per_second=payload["cps"],
        max_line_chars=payload["line_chars"],
        max_lines=payload["lines"],
        text_expansion=payload["expansion"],
    )


def caption_issues(
    cues: tuple[CaptionCue, ...], *, locale: str = "en"
) -> tuple[CaptionIssue, ...]:
    profile = locale_profile(locale)
    issues: list[CaptionIssue] = []
    for position, cue in enumerate(cues, start=1):
        duration_seconds = (cue.end_ms - cue.start_ms) / 1000
        lines = [line for line in cue.text.split("\n") if line.strip()]
        characters = len(re.sub(r"\s+", " ", cue.text).strip())
        if not cue.text.strip():
            issues.append(
                CaptionIssue(
                    cue_index=cue.index,
                    check="empty_text",
                    detail="The cue has no text.",
                )
            )
        if CONTROL_CHARS.search(cue.text):
            issues.append(
                CaptionIssue(
                    cue_index=cue.index,
                    check="control_chars",
                    detail="The cue contains control characters.",
                )
            )
        if len(lines) > profile.max_lines:
            issues.append(
                CaptionIssue(
                    cue_index=cue.index,
                    check="line_count",
                    detail=f"The cue has {len(lines)} lines; the limit is "
                    f"{profile.max_lines}.",
                )
            )
        for line in lines:
            if len(line) > profile.max_line_chars:
                issues.append(
                    CaptionIssue(
                        cue_index=cue.index,
                        check="line_length",
                        detail=f"A line has {len(line)} characters; the limit is "
                        f"{profile.max_line_chars}.",
                    )
                )
        if duration_seconds < 0.7:
            issues.append(
                CaptionIssue(
                    cue_index=cue.index,
                    check="duration",
                    detail="The cue is shorter than 0.7 seconds.",
                )
            )
        cps = characters / duration_seconds if duration_seconds else float("inf")
        if cps > profile.max_chars_per_second:
            issues.append(
                CaptionIssue(
                    cue_index=cue.index,
                    check="reading_speed",
                    detail=f"The cue runs at {cps:.1f} characters per second; the "
                    f"limit is {profile.max_chars_per_second:.1f}.",
                )
            )
        if position > 1:
            previous = cues[position - 2]
            if cue.start_ms < previous.end_ms:
                issues.append(
                    CaptionIssue(
                        cue_index=cue.index,
                        check="overlap",
                        detail="The cue overlaps the previous cue.",
                    )
                )
    return tuple(issues)


def plan_localization(
    source_srt: str,
    *,
    targets: tuple[str, ...] = ("hi", "de"),
    source_locale: str = "en",
) -> LocalizationPlan:
    cues = parse_srt(source_srt)
    source_issues = caption_issues(cues, locale=source_locale)
    target_plans: list[TargetPlan] = []
    for locale in targets:
        profile = locale_profile(locale)
        over_limit: list[int] = []
        worst_cps = 0.0
        for cue in cues:
            duration_seconds = (cue.end_ms - cue.start_ms) / 1000
            characters = len(re.sub(r"\s+", " ", cue.text).strip())
            estimated = characters * profile.text_expansion
            cps = estimated / duration_seconds if duration_seconds else float("inf")
            worst_cps = max(worst_cps, cps)
            if cps > profile.max_chars_per_second:
                over_limit.append(cue.index)
        target_plans.append(
            TargetPlan(
                locale=locale,
                label=profile.label,
                status="review_timing" if over_limit else "ready",
                estimated_cps=round(worst_cps, 2),
                cues_over_limit=tuple(over_limit),
                note=(
                    "Re-time or shorten after translation; the estimated text exceeds "
                    "the reading-speed limit."
                    if over_limit
                    else "Estimated translated text fits the reading-speed limit."
                ),
            )
        )
    body = {
        "generated_at": utc_now(),
        "source_locale": source_locale,
        "source_cues": len(cues),
        "source_issues": [issue.model_dump(mode="json") for issue in source_issues],
        "status": "needs_source_fix" if source_issues else "ready_for_translation",
        "targets": [target.model_dump(mode="json") for target in target_plans],
    }
    return LocalizationPlan(
        **body,
        manifest_hash=sha256_json(body),
    )

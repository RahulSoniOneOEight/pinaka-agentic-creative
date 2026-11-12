from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

DEFAULT_CPS = 20.0
DEFAULT_LINE_CHARS = 42
DEFAULT_LINES = 2


@dataclass(frozen=True)
class CaptionProfile:
    """Readability limits for one locale, sourced from LOCALE_PROFILES."""

    max_chars_per_second: float = DEFAULT_CPS
    line_chars: int = DEFAULT_LINE_CHARS
    max_lines: int = DEFAULT_LINES

    def __post_init__(self) -> None:
        if self.max_chars_per_second <= 0:
            raise ValueError("max_chars_per_second must be positive")
        if self.line_chars <= 0 or self.max_lines <= 0:
            raise ValueError("line_chars and max_lines must be positive")


def profile_for_locale(locale: str | None) -> CaptionProfile:
    """Use the locale profile the localisation layer already owns."""

    from .localization import LOCALE_PROFILES

    key = (locale or "en").lower().replace("_", "-").split("-")[0]
    data = LOCALE_PROFILES.get(key) or LOCALE_PROFILES["en"]
    return CaptionProfile(
        max_chars_per_second=float(data["cps"]),
        line_chars=int(data["line_chars"]),
        max_lines=int(data["lines"]),
    )


def _timestamp(seconds: float) -> str:
    if seconds < 0:
        raise ValueError("Caption time cannot be negative")
    total_ms = round(seconds * 1000)
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


def _wrap(text: str, limit: int) -> list[str]:
    """Greedy word wrap; a single over-long word is kept intact."""

    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if len(candidate) <= limit or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _split_by_lines(text: str, line_chars: int, max_lines: int) -> list[str]:
    """Split into cues that each wrap to at most ``max_lines`` lines."""

    cues: list[str] = []
    current: list[str] = []
    for word in text.split():
        trial = current + [word]
        if len(_wrap(" ".join(trial), line_chars)) <= max_lines or not current:
            current = trial
        else:
            cues.append(" ".join(current))
            current = [word]
    if current:
        cues.append(" ".join(current))
    return cues


def cues_for_shots(
    shots: list[dict[str, Any]],
    *,
    profile: CaptionProfile | None = None,
    locale: str | None = None,
) -> list[dict[str, Any]]:
    """Absolute-timed cues with wrapped lines, deterministic and total."""

    if not shots:
        raise ValueError("Captions require at least one shot")
    active = profile or profile_for_locale(locale)
    cues: list[dict[str, Any]] = []
    cursor = Decimal(0)
    for shot in shots:
        duration = Decimal(str(shot["duration_seconds"]))
        if duration <= 0:
            raise ValueError("Shot duration must be positive")
        text = _clean(shot.get("narrative") or shot.get("beat") or "")
        chunks = (
            _split_by_lines(text, active.line_chars, active.max_lines) if text else []
        )
        total_chars = sum(len(chunk) for chunk in chunks) or 1
        start = cursor
        for index, chunk in enumerate(chunks):
            if index == len(chunks) - 1:
                end = cursor + duration
            else:
                end = start + duration * Decimal(len(chunk)) / Decimal(total_chars)
            seconds = float(end - start)
            cues.append(
                {
                    "index": len(cues) + 1,
                    "start_seconds": round(float(start), 3),
                    "end_seconds": round(float(end), 3),
                    "duration_seconds": round(seconds, 3),
                    "text": chunk,
                    "lines": _wrap(chunk, active.line_chars),
                    "chars_per_second": round(
                        len(chunk) / seconds if seconds > 0 else float("inf"), 3
                    ),
                }
            )
            start = end
        cursor += duration
    if not cues:
        raise ValueError("Captions require at least one non-empty cue")
    return cues


def legibility_violations(
    cues: list[dict[str, Any]], profile: CaptionProfile
) -> list[dict[str, Any]]:
    """Every way a cue breaks the locale's reading rules.

    Splitting a cue cannot fix reading speed: the same words still occupy the
    same seconds. Over-cps cues are therefore reported, not silently rewritten.
    """

    violations = []
    for cue in cues:
        lines = cue["lines"]
        if len(lines) > profile.max_lines:
            violations.append(
                {
                    "cue": cue["index"],
                    "rule": "line_count",
                    "actual": len(lines),
                    "limit": profile.max_lines,
                }
            )
        if any(len(line) > profile.line_chars for line in lines):
            violations.append(
                {
                    "cue": cue["index"],
                    "rule": "line_length",
                    "actual": max(len(line) for line in lines),
                    "limit": profile.line_chars,
                }
            )
        if cue["chars_per_second"] > profile.max_chars_per_second:
            violations.append(
                {
                    "cue": cue["index"],
                    "rule": "chars_per_second",
                    "actual": cue["chars_per_second"],
                    "limit": profile.max_chars_per_second,
                }
            )
    return violations


def build_srt(
    shots: list[dict[str, Any]],
    *,
    profile: CaptionProfile | None = None,
    locale: str | None = None,
    strict: bool = False,
) -> str:
    """Deterministic SRT from the governed shot plan (no ASR required).

    Lines are wrapped to the locale's width and cue count. With ``strict``, an
    over-cps or over-wide cue raises instead of shipping an unreadable caption.
    """

    active = profile or profile_for_locale(locale)
    cues = cues_for_shots(shots, profile=active)
    violations = legibility_violations(cues, active)
    if strict and violations:
        first = violations[0]
        raise ValueError(
            f"Caption cue {first['cue']} breaks {first['rule']}: "
            f"{first['actual']} > {first['limit']}"
        )
    blocks = [
        f"{cue['index']}\n"
        f"{_timestamp(cue['start_seconds'])} --> {_timestamp(cue['end_seconds'])}\n"
        f"{chr(10).join(cue['lines'])}\n"
        for cue in cues
    ]
    return "\n".join(blocks)


def _words_from_payload(words: Any) -> list[tuple[float, float, str]]:
    """Accept dicts (JSON payload) or objects with the same fields."""

    normalized: list[tuple[float, float, str]] = []
    for word in words:
        if isinstance(word, dict):
            text = word.get("text")
            start = word.get("start_seconds")
            end = word.get("end_seconds")
        else:
            text = getattr(word, "text", None)
            start = getattr(word, "start_seconds", None)
            end = getattr(word, "end_seconds", None)
        cleaned = _clean(str(text or ""))
        if not cleaned or start is None or end is None:
            continue
        start_value = float(start)
        end_value = max(float(end), start_value)
        normalized.append((start_value, end_value, cleaned))
    normalized.sort(key=lambda item: (item[0], item[1]))
    return normalized


def cues_from_words(
    words: Any,
    *,
    profile: CaptionProfile | None = None,
    locale: str | None = None,
    max_gap_seconds: float = 0.6,
    max_cue_seconds: float = 6.0,
) -> list[dict[str, Any]]:
    """Group spoken words into readable cues from real word timings."""

    active = profile or profile_for_locale(locale)
    ordered = _words_from_payload(words)
    if not ordered:
        raise ValueError("Captions require at least one timed word")
    groups: list[list[tuple[float, float, str]]] = []
    current: list[tuple[float, float, str]] = []
    for word in ordered:
        if current:
            gap = word[0] - current[-1][1]
            candidate = [*current, word]
            text = " ".join(item[2] for item in candidate)
            over_lines = len(_wrap(text, active.line_chars)) > active.max_lines
            over_time = candidate[-1][1] - candidate[0][0] > max_cue_seconds
            if gap > max_gap_seconds or over_lines or over_time:
                groups.append(current)
                current = []
        current.append(word)
    if current:
        groups.append(current)
    cues: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        text = " ".join(item[2] for item in group)
        start = group[0][0]
        end = max(group[-1][1], start + 0.05)
        cues.append(
            {
                "index": index,
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "duration_seconds": round(end - start, 3),
                "text": text,
                "lines": _wrap(text, active.line_chars),
                "chars_per_second": round(len(text) / (end - start), 3),
            }
        )
    return cues


def build_srt_from_words(
    words: Any,
    *,
    profile: CaptionProfile | None = None,
    locale: str | None = None,
    max_gap_seconds: float = 0.6,
    max_cue_seconds: float = 6.0,
    strict: bool = False,
) -> str:
    """SRT from ASR word timings, wrapped to the same locale rules."""

    active = profile or profile_for_locale(locale)
    cues = cues_from_words(
        words,
        profile=active,
        max_gap_seconds=max_gap_seconds,
        max_cue_seconds=max_cue_seconds,
    )
    violations = legibility_violations(cues, active)
    if strict and violations:
        first = violations[0]
        raise ValueError(
            f"Caption cue {first['cue']} breaks {first['rule']}: "
            f"{first['actual']} > {first['limit']}"
        )
    return "\n".join(
        f"{cue['index']}\n"
        f"{_timestamp(cue['start_seconds'])} --> {_timestamp(cue['end_seconds'])}\n"
        f"{chr(10).join(cue['lines'])}\n"
        for cue in cues
    )


def parse_srt(text: str) -> list[dict[str, Any]]:
    """Parse an SRT so plan and speech captions can be compared."""

    cues: list[dict[str, Any]] = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        lines = [line for line in block.split("\n") if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start_text, end_text = [part.strip() for part in lines[1].split("-->")]
        try:
            start = _seconds(start_text)
            end = _seconds(end_text)
        except ValueError:
            continue
        body = " ".join(lines[2:]).strip()
        if not body:
            continue
        cues.append(
            {
                "index": len(cues) + 1,
                "start_seconds": start,
                "end_seconds": end,
                "duration_seconds": round(end - start, 3),
                "text": body,
                "chars_per_second": round(
                    len(body) / (end - start) if end > start else float("inf"), 3
                ),
            }
        )
    return cues


def _seconds(value: str) -> float:
    clock, _, millis = value.partition(",")
    hours, minutes, seconds = clock.split(":")
    return (
        int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis or 0) / 1000
    )


def _tokens(text: str) -> set[str]:
    cleaned = "".join(
        character.lower() if character.isalnum() else " " for character in text
    )
    return {token for token in cleaned.split() if token}


def caption_drift(
    plan_cues: list[dict[str, Any]],
    word_cues: list[dict[str, Any]],
    *,
    tolerance_seconds: float = 0.75,
    minimum_overlap: float = 0.8,
) -> dict[str, Any]:
    """Compare plan-timed captions with speech-timed captions.

    A cue passes when a spoken cue carries at least ``minimum_overlap`` of its
    tokens and starts within ``tolerance_seconds``.
    """

    mismatches: list[dict[str, Any]] = []
    offsets: list[float] = []
    for plan in plan_cues:
        plan_tokens = _tokens(plan["text"])
        if not plan_tokens:
            continue
        best = None
        best_score = 0.0
        for spoken in word_cues:
            overlap = len(plan_tokens & _tokens(spoken["text"])) / len(plan_tokens)
            if overlap > best_score:
                best_score = overlap
                best = spoken
        if best is None or best_score < minimum_overlap:
            mismatches.append(
                {
                    "cue": plan["index"],
                    "reason": "text",
                    "overlap": round(best_score, 3),
                }
            )
            continue
        offset = abs(float(plan["start_seconds"]) - float(best["start_seconds"]))
        offsets.append(offset)
        if offset > tolerance_seconds:
            mismatches.append(
                {
                    "cue": plan["index"],
                    "reason": "timing",
                    "offset_seconds": round(offset, 3),
                }
            )
    return {
        "passed": not mismatches,
        "max_offset_seconds": round(max(offsets), 3) if offsets else None,
        "mismatches": mismatches,
    }

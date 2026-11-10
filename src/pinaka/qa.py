from __future__ import annotations

import html
import re
from decimal import Decimal
from typing import Any

from .animatic import SPACING, TYPE_SCALE, decimal_seconds
from .models import QACheck, QAReport, QAStatus

MIN_TEXT_CONTRAST = 4.5


def _linear(channel: int) -> float:
    value = channel / 255
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    value = color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Expected a six-digit hexadecimal colour: {color!r}")
    red, green, blue = (int(value[index : index + 2], 16) for index in (0, 2, 4))
    return 0.2126 * _linear(red) + 0.7152 * _linear(green) + 0.0722 * _linear(blue)


def contrast_ratio(foreground: str, background: str) -> float:
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _pass(check: str, detail: str, evidence: dict[str, Any] | None = None) -> QACheck:
    return QACheck(
        check=check, status=QAStatus.PASS, detail=detail, evidence=evidence or {}
    )


def _fail(check: str, detail: str, evidence: dict[str, Any] | None = None) -> QACheck:
    return QACheck(
        check=check, status=QAStatus.FAIL, detail=detail, evidence=evidence or {}
    )


def overall_status(checks: list[QACheck]) -> QAStatus:
    statuses = {check.status for check in checks}
    if QAStatus.FAIL in statuses:
        return QAStatus.FAIL
    if QAStatus.WARN in statuses:
        return QAStatus.WARN
    return QAStatus.PASS


def _check_timeline(timeline: dict[str, Any]) -> list[QACheck]:
    checks: list[QACheck] = []
    total = decimal_seconds(timeline["total_duration_seconds"])
    expected = decimal_seconds(timeline["deliverable"]["duration_seconds"])
    if total == expected:
        checks.append(
            _pass(
                "timeline.duration",
                f"Timeline totals {total}s, matching the deliverable.",
                {"total_seconds": str(total), "deliverable_seconds": str(expected)},
            )
        )
    else:
        checks.append(
            _fail(
                "timeline.duration",
                f"Timeline totals {total}s but the deliverable requires {expected}s.",
                {"total_seconds": str(total), "deliverable_seconds": str(expected)},
            )
        )

    cursor = Decimal(0)
    issues: list[str] = []
    for segment in timeline["segments"]:
        start = decimal_seconds(segment["start_seconds"])
        end = decimal_seconds(segment["end_seconds"])
        if start != cursor:
            issues.append(
                f"{segment['stable_key']} starts at {start}s, expected {cursor}s"
            )
        if end <= start:
            issues.append(f"{segment['stable_key']} has a non-positive duration")
        cursor = end
    if cursor != total:
        issues.append(f"Last segment ends at {cursor}s, expected {total}s")
    if issues:
        checks.append(
            _fail(
                "timeline.sequence",
                "Timeline is not a contiguous ordered sequence.",
                {"issues": issues},
            )
        )
    else:
        checks.append(
            _pass(
                "timeline.sequence",
                "Segments are contiguous, ordered and end at the deliverable duration.",
            )
        )
    return checks


def _check_exact_text(timeline: dict[str, Any], html_text: str) -> QACheck:
    missing: list[dict[str, str]] = []
    for segment in timeline["segments"]:
        for fragment in segment["text_fragments"]:
            escaped = html.escape(fragment, quote=False)
            if fragment not in html_text and escaped not in html_text:
                missing.append({"segment": segment["stable_key"], "fragment": fragment})
    if missing:
        return _fail(
            "content.exact_text",
            "Rendered animatic is missing approved on-screen copy.",
            {"missing": missing},
        )
    return _pass(
        "content.exact_text",
        "Every approved on-screen fragment appears in the rendered animatic.",
        {
            "fragments_checked": sum(
                len(segment["text_fragments"]) for segment in timeline["segments"]
            )
        },
    )


def _check_prohibited_claims(timeline: dict[str, Any], html_text: str) -> QACheck:
    haystack = html_text.casefold()
    found = [
        claim
        for claim in timeline.get("prohibited_claims", [])
        if claim.casefold() in haystack
    ]
    if found:
        return _fail(
            "content.prohibited_claims",
            "Rendered animatic contains prohibited claims.",
            {"found": found},
        )
    return _pass(
        "content.prohibited_claims",
        "No prohibited claim is present in the rendered animatic.",
        {"checked": len(timeline.get("prohibited_claims", []))},
    )


def _check_brand_colors(timeline: dict[str, Any], html_text: str) -> QACheck:
    palette = timeline["brand"]["palette"]
    missing = [
        value for value in palette.values() if value.lower() not in html_text.lower()
    ]
    if missing:
        return _fail(
            "brand.colors",
            "The rendered animatic does not carry every approved brand colour.",
            {"missing": sorted(set(missing))},
        )
    return _pass(
        "brand.colors",
        "All approved brand colours are present as CSS custom properties.",
        {"colors": palette},
    )


def _check_typography(html_text: str) -> QACheck:
    missing = [name for name in TYPE_SCALE if f"--size-{name}:" not in html_text]
    if missing:
        return _fail(
            "typography.scale",
            "The rendered animatic is missing approved semantic type sizes.",
            {"missing": missing},
        )
    return _pass(
        "typography.scale",
        "All approved semantic type sizes are declared.",
        {"sizes": TYPE_SCALE},
    )


def _contrast_pairs(timeline: dict[str, Any]) -> list[tuple[str, str, str]]:
    palette = timeline["brand"]["palette"]
    return [
        ("text.default on bg.default", palette["ink"], palette["white"]),
        ("text.default on bg.subtle", palette["ink"], palette["soft"]),
        ("text.muted on bg.default", palette["muted"], palette["white"]),
        ("text.muted on bg.subtle", palette["muted"], palette["soft"]),
        ("action.primary on bg.default", palette["blue"], palette["white"]),
        ("action.primary.text on action.primary", palette["white"], palette["blue"]),
        ("text.inverse on bg.inverse", palette["white"], palette["ink"]),
    ]


def _check_contrast(timeline: dict[str, Any]) -> QACheck:
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for name, foreground, background in _contrast_pairs(timeline):
        ratio = round(contrast_ratio(foreground, background), 3)
        record = {
            "pair": name,
            "foreground": foreground,
            "background": background,
            "ratio": ratio,
            "threshold": MIN_TEXT_CONTRAST,
        }
        results.append(record)
        if ratio < MIN_TEXT_CONTRAST:
            failures.append(record)
    if failures:
        return _fail(
            "a11y.contrast",
            "One or more approved colour pairs do not meet WCAG AA text contrast.",
            {"failures": failures, "pairs": results},
        )
    return _pass(
        "a11y.contrast",
        "Approved colour pairs meet WCAG AA text contrast at 4.5:1 or better.",
        {"pairs": results},
    )


def _check_spacing(html_text: str) -> QACheck:
    matches = re.findall(r"--space-(\d+):\s*(\d+)px", html_text)
    if not matches:
        return _fail("spacing.grid", "No spacing tokens were found in the animatic.")
    off_grid = [
        {"token": name, "value": int(value)}
        for name, value in matches
        if int(value) % 4 != 0
    ]
    missing = [name for name in SPACING if f"--space-{name}:" not in html_text]
    if off_grid or missing:
        return _fail(
            "spacing.grid",
            "Spacing tokens are missing or off the 4px grid.",
            {"off_grid": off_grid, "missing": missing},
        )
    return _pass(
        "spacing.grid",
        "Every declared spacing token is a multiple of 4px.",
        {"tokens": {name: int(value) for name, value in matches}},
    )


def _timecode_to_ms(value: str) -> int:
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", value.strip())
    if not match:
        raise ValueError(f"Invalid SRT timecode: {value!r}")
    hours, minutes, seconds, milliseconds = (int(part) for part in match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds


def _check_captions(timeline: dict[str, Any], srt_text: str) -> QACheck:
    blocks = [block for block in srt_text.strip().split("\n\n") if block.strip()]
    expected_total = int(decimal_seconds(timeline["total_duration_seconds"]) * 1000)
    issues: list[str] = []
    cues: list[dict[str, Any]] = []
    cursor = 0
    for position, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        if len(lines) < 3:
            issues.append(f"Cue {position} is incomplete")
            continue
        try:
            index = int(lines[0])
            start_text, end_text = lines[1].split(" --> ")
            start = _timecode_to_ms(start_text)
            end = _timecode_to_ms(end_text)
        except (ValueError, IndexError) as error:
            issues.append(f"Cue {position} is invalid: {error}")
            continue
        text = "\n".join(lines[2:]).strip()
        if index != position:
            issues.append(f"Cue {position} has index {index}")
        if start != cursor:
            issues.append(f"Cue {position} starts at {start}ms, expected {cursor}ms")
        if end <= start:
            issues.append(f"Cue {position} has a non-positive duration")
        if not text:
            issues.append(f"Cue {position} has no text")
        cues.append({"index": index, "start_ms": start, "end_ms": end, "text": text})
        cursor = end
    if cursor != expected_total:
        issues.append(f"Captions end at {cursor}ms, expected {expected_total}ms")
    if issues:
        return _fail(
            "captions.coverage",
            "Caption timing does not cover the approved timeline contiguously.",
            {"issues": issues, "cues": cues},
        )
    return _pass(
        "captions.coverage",
        "Draft captions cover the full timeline contiguously.",
        {"cues": cues, "mode": timeline.get("caption_mode")},
    )


def run_animatic_qa(
    *,
    timeline: dict[str, Any],
    html_text: str,
    srt_text: str,
    artifacts_verified: bool,
    grounding_kinds: set[str],
) -> list[QACheck]:
    checks = _check_timeline(timeline)
    checks.append(_check_exact_text(timeline, html_text))
    checks.append(_check_prohibited_claims(timeline, html_text))
    checks.append(_check_brand_colors(timeline, html_text))
    checks.append(_check_typography(html_text))
    checks.append(_check_contrast(timeline))
    checks.append(_check_spacing(html_text))
    checks.append(_check_captions(timeline, srt_text))
    required_grounding = {"brand", "claims", "sources"}
    missing_grounding = sorted(required_grounding - grounding_kinds)
    if missing_grounding:
        checks.append(
            _fail(
                "rights.grounding",
                "Required brand, claim or source grounding is missing.",
                {"missing": missing_grounding},
            )
        )
    else:
        checks.append(
            _pass(
                "rights.grounding",
                "Brand, claim and source grounding bundles are current.",
                {"kinds": sorted(grounding_kinds)},
            )
        )
    if artifacts_verified:
        checks.append(
            _pass(
                "artifacts.hashes",
                "Timeline, HTML and caption artifact hashes verify.",
            )
        )
    else:
        checks.append(
            _fail(
                "artifacts.hashes",
                "One or more animatic artifacts failed hash verification.",
            )
        )
    return checks


def build_report(
    *, project_id: str, subject_artifact_id: str, checks: list[QACheck]
) -> QAReport:
    return QAReport(
        project_id=project_id,
        subject_artifact_id=subject_artifact_id,
        checks=tuple(checks),
        overall=overall_status(checks),
        notes=(
            "Deterministic baseline QA. Visual, brand and factual review remain human gates.",
            "Captions are drafts derived from approved segment narrative until narration exists.",
        ),
    )


def run_voiced_animatic_qa(
    *,
    mux_manifest: dict[str, Any],
    video_verified: bool,
    narration_verified: bool,
    expected_text_hash: str,
) -> list[QACheck]:
    checks: list[QACheck] = []
    metadata = mux_manifest.get("metadata") or {}
    loudness = mux_manifest.get("loudness") or {}
    duration = float(metadata.get("duration_seconds", 0))
    expected_duration = float(mux_manifest.get("duration_seconds", 0))
    if abs(duration - expected_duration) <= 0.05:
        checks.append(
            _pass(
                "render.duration",
                f"Muxed video is {duration:.3f}s, matching the approved timeline.",
                {"duration_seconds": duration, "expected": expected_duration},
            )
        )
    else:
        checks.append(
            _fail(
                "render.duration",
                f"Muxed video is {duration:.3f}s but {expected_duration:.3f}s was required.",
                {"duration_seconds": duration, "expected": expected_duration},
            )
        )

    video_expected = {
        "video_codec": "h264",
        "width_px": 1080,
        "height_px": 1920,
        "fps": 30.0,
    }
    video_observed = {key: metadata.get(key) for key in video_expected}
    if video_observed == video_expected:
        checks.append(
            _pass(
                "render.video",
                "H.264 stream is 1080x1920 at 30fps.",
                video_observed,
            )
        )
    else:
        checks.append(
            _fail(
                "render.video",
                "Encoded video stream does not match the approved profile.",
                {"observed": video_observed, "expected": video_expected},
            )
        )

    audio_expected = {
        "audio_codec": "aac",
        "audio_sample_rate": 48000,
        "audio_channels": "stereo",
    }
    audio_observed = {key: metadata.get(key) for key in audio_expected}
    if audio_observed == audio_expected:
        checks.append(
            _pass(
                "render.audio",
                "AAC narration stream is 48kHz stereo.",
                audio_observed,
            )
        )
    else:
        checks.append(
            _fail(
                "render.audio",
                "Encoded audio stream does not match the approved profile.",
                {"observed": audio_observed, "expected": audio_expected},
            )
        )

    try:
        integrated = float(loudness.get("input_i"))
        true_peak = float(loudness.get("input_tp"))
    except (TypeError, ValueError):
        integrated = true_peak = None
    if integrated is not None and abs(integrated + 16.0) <= 1.0:
        checks.append(
            _pass(
                "audio.loudness",
                f"Integrated loudness is {integrated:.1f} LUFS.",
                {"integrated_lufs": integrated, "target_lufs": -16.0},
            )
        )
    else:
        checks.append(
            _fail(
                "audio.loudness",
                "Integrated loudness is outside the -16 LUFS target tolerance.",
                {"integrated_lufs": integrated, "target_lufs": -16.0},
            )
        )
    if true_peak is not None and true_peak <= -1.0:
        checks.append(
            _pass(
                "audio.peak",
                f"True peak is {true_peak:.1f} dBTP.",
                {"true_peak_dbtp": true_peak, "maximum_dbtp": -1.0},
            )
        )
    else:
        checks.append(
            _fail(
                "audio.peak",
                "True peak exceeds the -1.0 dBTP ceiling.",
                {"true_peak_dbtp": true_peak, "maximum_dbtp": -1.0},
            )
        )

    narration_duration = float(mux_manifest.get("narration_duration_seconds", 0))
    if narration_duration <= expected_duration - 0.15:
        checks.append(
            _pass(
                "narration.fit",
                "Narration fits inside the video with a safe tail.",
                {
                    "narration_seconds": narration_duration,
                    "video_seconds": expected_duration,
                },
            )
        )
    else:
        checks.append(
            _fail(
                "narration.fit",
                "Narration leaves less than the required 0.15s tail.",
                {
                    "narration_seconds": narration_duration,
                    "video_seconds": expected_duration,
                },
            )
        )

    narration_hash = mux_manifest.get("narration_text_hash")
    if narration_hash == expected_text_hash:
        checks.append(
            _pass(
                "narration.script",
                "Narration text hash matches the approved voiceover.",
                {"text_hash": narration_hash},
            )
        )
    else:
        checks.append(
            _fail(
                "narration.script",
                "Narration text hash does not match the approved voiceover.",
                {"observed": narration_hash, "expected": expected_text_hash},
            )
        )

    if video_verified and narration_verified:
        checks.append(
            _pass(
                "artifacts.hashes",
                "Muxed video and narration artifact hashes verify.",
            )
        )
    else:
        checks.append(
            _fail(
                "artifacts.hashes",
                "One or more voiced-production artifacts failed hash verification.",
            )
        )
    return checks

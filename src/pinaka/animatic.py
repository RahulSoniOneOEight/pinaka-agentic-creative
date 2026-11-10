from __future__ import annotations

import html
import json
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import urlparse

from .utils import sha256_json

SPACING: dict[str, int] = {
    "1": 4,
    "2": 8,
    "3": 12,
    "4": 16,
    "5": 20,
    "6": 24,
    "8": 32,
    "10": 40,
    "12": 48,
    "16": 64,
    "24": 96,
}

RADII: dict[str, int] = {
    "xs": 4,
    "sm": 8,
    "md": 12,
    "lg": 16,
    "xl": 24,
    "2xl": 32,
}

TYPE_SCALE: dict[str, int] = {
    "meta": 29,
    "caption": 35,
    "body": 42,
    "label": 50,
    "subtitle": 60,
    "title": 72,
    "display": 103,
    "hero": 124,
}

PRIMITIVE_TYPE_SCALE: dict[str, int] = {
    "24": 24,
    "86": 86,
}

PALETTE_KEYS: tuple[str, ...] = (
    "ink",
    "muted",
    "line",
    "blue",
    "violet",
    "teal",
    "orange",
    "soft",
    "white",
)

ACCENTS: tuple[str, ...] = ("teal", "violet", "orange")

RENDERER_VERSION = 3


def decimal_seconds(value: Any) -> Decimal:
    return Decimal(str(value))


def format_seconds(value: Decimal) -> str:
    return format(value.normalize(), "f")


def srt_timecode(value: Decimal) -> str:
    total_ms = int((value * 1000).to_integral_value(rounding=ROUND_HALF_UP))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def css_percent(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def text_fragments(exact_text: str) -> list[str]:
    """Split approved copy into the smallest exact strings a renderer must show."""
    if " \u00b7 " in exact_text:
        return [part for part in exact_text.split(" \u00b7 ") if part]
    parts = [part for part in re.split(r"(?<=[.?!])\s+", exact_text) if part]
    return parts or [exact_text]


def _require_color(colors: dict[str, str], key: str) -> str:
    value = colors.get(key)
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError(f"Brand colour {key!r} is not a six-digit hexadecimal value")
    return value.upper()


def build_timeline(
    *,
    project_id: str,
    deliverable: dict[str, Any],
    segments: list[dict[str, Any]],
    brand_values: dict[str, Any],
    claims_values: dict[str, Any],
) -> dict[str, Any]:
    design = brand_values.get("design")
    client = brand_values.get("client")
    if not isinstance(design, dict) or not isinstance(client, dict):
        raise TypeError("Brand grounding must contain design and client values")

    colors = design.get("brand_colors")
    if not isinstance(colors, dict):
        raise TypeError("Brand grounding is missing brand_colors")
    palette = {key: _require_color(colors, key) for key in PALETTE_KEYS}

    duration = decimal_seconds(deliverable["duration_seconds"])
    ordered = sorted(segments, key=lambda item: item["ordinal"])
    cursor = Decimal(0)
    timeline_segments: list[dict[str, Any]] = []
    for segment in ordered:
        segment_duration = decimal_seconds(segment["duration_seconds"])
        start = cursor
        end = cursor + segment_duration
        constraints = segment.get("constraints") or {}
        exact_text = str(constraints.get("exact_text") or segment["narrative"])
        timeline_segments.append(
            {
                "ordinal": segment["ordinal"],
                "stable_key": segment["stable_key"],
                "purpose": segment["purpose"],
                "narrative": segment["narrative"],
                "start_seconds": format_seconds(start),
                "end_seconds": format_seconds(end),
                "duration_seconds": format_seconds(segment_duration),
                "exact_text": exact_text,
                "text_fragments": text_fragments(exact_text),
                "constraints": constraints,
            }
        )
        cursor = end

    if cursor != duration:
        raise ValueError(
            f"Segment duration {format_seconds(cursor)}s does not match "
            f"deliverable duration {format_seconds(duration)}s"
        )

    font_families = design.get("font_families") or []
    if not font_families:
        raise ValueError("Brand grounding is missing font_families")

    website = str(client.get("website") or "")
    timeline = {
        "schema_version": 1,
        "renderer_version": RENDERER_VERSION,
        "project_id": project_id,
        "deliverable": {
            "id": deliverable["id"],
            "stable_key": deliverable["stable_key"],
            "name": deliverable["name"],
            "duration_seconds": format_seconds(duration),
        },
        "canvas": {
            "width_px": design["width_px"],
            "height_px": design["height_px"],
            "frame_rate": str(design["frame_rate"]),
        },
        "brand": {
            "font_family": str(font_families[0]),
            "palette": palette,
            "client_name": str(client.get("name") or ""),
            "website": website,
            "cta_label": str(client.get("cta_label") or ""),
            "cta_url": str(client.get("cta_url") or ""),
            "service_claims": list(client.get("approved_service_claims") or []),
        },
        "voiceover": str(claims_values.get("voiceover") or ""),
        "prohibited_claims": list(claims_values.get("prohibited_claims") or []),
        "segments": timeline_segments,
        "total_duration_seconds": format_seconds(cursor),
        "caption_mode": "draft_from_approved_segment_narrative",
        "notes": [
            "Deterministic animatic source for the approved 10-second pilot.",
            "Captions are drafted from approved segment narrative until narration is synthesized.",
        ],
    }
    timeline["timeline_hash"] = sha256_json(timeline)
    return timeline


def _css(timeline: dict[str, Any]) -> str:
    palette = timeline["brand"]["palette"]
    font_family = timeline["brand"]["font_family"]
    duration = timeline["total_duration_seconds"]
    lines: list[str] = [
        ":root {",
        f'  --font-family: {font_family}, "Segoe UI", system-ui, -apple-system, sans-serif;',
        f"  --color-bg-default: {palette['white']};",
        f"  --color-bg-subtle: {palette['soft']};",
        f"  --color-bg-inverse: {palette['ink']};",
        f"  --color-text-default: {palette['ink']};",
        f"  --color-text-muted: {palette['muted']};",
        f"  --color-text-inverse: {palette['white']};",
        f"  --color-border-default: {palette['line']};",
        f"  --color-action-primary: {palette['blue']};",
        f"  --color-action-primary-text: {palette['white']};",
        f"  --color-accent-teal: {palette['teal']};",
        f"  --color-accent-violet: {palette['violet']};",
        f"  --color-accent-orange: {palette['orange']};",
        f"  --duration: {duration}s;",
    ]
    for name, value in SPACING.items():
        lines.append(f"  --space-{name}: {value}px;")
    for name, value in RADII.items():
        lines.append(f"  --radius-{name}: {value}px;")
    for name, value in TYPE_SCALE.items():
        lines.append(f"  --size-{name}: {value}px;")
    for name, value in PRIMITIVE_TYPE_SCALE.items():
        lines.append(f"  --size-{name}: {value}px;")
    lines.extend(
        [
            "}",
            "* { box-sizing: border-box; }",
            "html, body { margin: 0; padding: 0; background: var(--color-bg-inverse); }",
            "body {",
            "  min-height: 100vh;",
            "  display: flex;",
            "  align-items: flex-start;",
            "  justify-content: center;",
            "  font-family: var(--font-family);",
            "  color: var(--color-text-default);",
            "  -webkit-font-smoothing: antialiased;",
            "}",
            ".stage {",
            "  position: relative;",
            "  width: 1080px;",
            "  height: 1920px;",
            "  overflow: hidden;",
            "  background: var(--color-bg-default);",
            "}",
            ".scene {",
            "  position: absolute;",
            "  inset: 0;",
            "  display: flex;",
            "  flex-direction: column;",
            "  justify-content: center;",
            "  align-items: flex-start;",
            "  padding: var(--space-24);",
            "  opacity: 0;",
            "  animation-duration: var(--duration);",
            "  animation-timing-function: linear;",
            "  animation-fill-mode: both;",
            "}",
            ".scene-service { background: var(--color-bg-subtle); }",
            ".scene-cta {",
            "  background: var(--color-bg-inverse);",
            "  color: var(--color-text-inverse);",
            "  justify-content: center;",
            "  gap: var(--space-16);",
            "}",
            ".eyebrow {",
            "  margin: 0 0 var(--space-10) 0;",
            "  font-size: var(--size-24);",
            "  font-weight: 600;",
            "  color: var(--color-action-primary);",
            "  text-transform: uppercase;",
            "}",
            ".rule {",
            "  width: var(--space-24);",
            "  height: var(--space-2);",
            "  margin-bottom: var(--space-10);",
            "  border-radius: var(--radius-xs);",
            "  background: var(--color-action-primary);",
            "}",
            ".headline {",
            "  margin: 0;",
            "  max-width: 888px;",
            "  font-size: var(--size-display);",
            "  line-height: 1.02;",
            "  font-weight: 800;",
            "  letter-spacing: 0;",
            "}",
            ".headline-accent {",
            "  display: block;",
            "  margin-top: var(--space-2);",
            "  font-size: var(--size-86);",
            "  line-height: 1.05;",
            "  font-weight: 700;",
            "  color: var(--color-action-primary);",
            "}",
            ".capabilities {",
            "  list-style: none;",
            "  margin: 0;",
            "  padding: 0;",
            "  width: 888px;",
            "  max-width: 888px;",
            "  display: flex;",
            "  flex-direction: column;",
            "  gap: var(--space-8);",
            "}",
            ".capability {",
            "  display: flex;",
            "  align-items: center;",
            "  justify-content: space-between;",
            "  min-height: 128px;",
            "}",
            ".capability-left {",
            "  display: flex;",
            "  align-items: center;",
            "  gap: var(--space-6);",
            "}",
            ".capability-index {",
            "  width: var(--space-10);",
            "  font-size: var(--size-24);",
            "  font-weight: 600;",
            "  color: var(--color-text-muted);",
            "}",
            ".capability-label {",
            "  font-size: var(--size-title);",
            "  line-height: 1.05;",
            "  font-weight: 800;",
            "}",
            ".dot {",
            "  width: var(--space-4);",
            "  height: var(--space-4);",
            "  border-radius: var(--radius-xs);",
            "  flex: 0 0 auto;",
            "}",
            ".dot-teal { background: var(--color-accent-teal); }",
            ".dot-violet { background: var(--color-accent-violet); }",
            ".dot-orange { background: var(--color-accent-orange); }",
            ".scene-clarity { align-items: center; text-align: center; }",
            ".clarity {",
            "  margin: 0;",
            "  max-width: 888px;",
            "  font-size: var(--size-title);",
            "  line-height: 1.05;",
            "  font-weight: 800;",
            "}",
            ".support {",
            "  margin: var(--space-6) 0 0 0;",
            "  max-width: 720px;",
            "  font-size: var(--size-caption);",
            "  line-height: 1.4;",
            "  color: var(--color-text-muted);",
            "}",
            ".wordmark {",
            "  margin: 0;",
            "  font-size: var(--size-body);",
            "  font-weight: 800;",
            "}",
            ".wordmark span { font-weight: 400; opacity: 0.82; }",
            ".cta-block {",
            "  display: flex;",
            "  flex-direction: column;",
            "  align-items: flex-start;",
            "  gap: var(--space-6);",
            "}",
            ".cta {",
            "  display: inline-flex;",
            "  align-items: center;",
            "  justify-content: center;",
            "  min-height: 112px;",
            "  padding: 0 var(--space-12);",
            "  border-radius: var(--radius-md);",
            "  background: var(--color-action-primary);",
            "  color: var(--color-action-primary-text);",
            "  font-size: var(--size-body);",
            "  font-weight: 700;",
            "  text-decoration: none;",
            "}",
            ".site {",
            "  font-size: var(--size-caption);",
            "  color: var(--color-text-inverse);",
            "  opacity: 0.78;",
            "}",
            ".progress {",
            "  position: absolute;",
            "  left: 0;",
            "  bottom: 0;",
            "  z-index: 10;",
            "  height: var(--space-2);",
            "  background: var(--color-action-primary);",
            "  animation: progress var(--duration) linear forwards;",
            "}",
            "@keyframes progress { from { width: 0; } to { width: 100%; } }",
        ]
    )
    total = decimal_seconds(duration)
    for index, segment in enumerate(timeline["segments"], start=1):
        start = decimal_seconds(segment["start_seconds"])
        end = decimal_seconds(segment["end_seconds"])
        start_pct = (start / total * 100).quantize(Decimal("0.0001"))
        end_pct = (end / total * 100).quantize(Decimal("0.0001"))
        epsilon = Decimal("0.0001")
        if start == 0:
            lines.append(
                f"@keyframes scene-{index} {{ 0%, "
                f"{css_percent(end_pct - epsilon)}% {{ opacity: 1; }} "
                f"{css_percent(end_pct)}%, 100% {{ opacity: 0; }} }}"
            )
        elif end == total:
            lines.append(
                f"@keyframes scene-{index} {{ 0%, "
                f"{css_percent(start_pct - epsilon)}% {{ opacity: 0; }} "
                f"{css_percent(start_pct)}%, 100% {{ opacity: 1; }} }}"
            )
        else:
            lines.append(
                f"@keyframes scene-{index} {{ 0%, "
                f"{css_percent(start_pct - epsilon)}% {{ opacity: 0; }} "
                f"{css_percent(start_pct)}%, "
                f"{css_percent(end_pct - epsilon)}% {{ opacity: 1; }} "
                f"{css_percent(end_pct)}%, 100% {{ opacity: 0; }} }}"
            )
        lines.append(f".scene-{index} {{ animation-name: scene-{index}; }}")
    return "\n".join(lines)


def _scene_one(segment: dict[str, Any]) -> str:
    fragments = segment["text_fragments"]
    first = fragments[0] if fragments else segment["exact_text"]
    rest = fragments[1:]
    accent = (
        f'<span class="headline-accent">{html.escape(" ".join(rest))}</span>'
        if rest
        else ""
    )
    return (
        '<section class="scene scene-1">'
        '<div class="rule" aria-hidden="true"></div>'
        '<p class="eyebrow">PINAKA INTELLIGENCE / VIRTUAL CFO</p>'
        f'<h1 class="headline">{html.escape(first)}{accent}</h1>'
        "</section>"
    )


def _scene_two(segment: dict[str, Any]) -> str:
    labels = segment["text_fragments"]
    rows = []
    for index, label in enumerate(labels):
        accent = ACCENTS[index % len(ACCENTS)]
        rows.append(
            '<li class="capability">'
            '<span class="capability-left">'
            f'<span class="capability-index">{index + 1:02d}</span>'
            f'<span class="capability-label">{html.escape(label)}</span>'
            "</span>"
            f'<span class="dot dot-{accent}" aria-hidden="true"></span>'
            "</li>"
        )
    return (
        '<section class="scene scene-2 scene-service">'
        '<p class="eyebrow">A VIRTUAL CFO BRINGS</p>'
        f'<ul class="capabilities">{"".join(rows)}</ul>'
        "</section>"
    )


def _scene_three(segment: dict[str, Any]) -> str:
    support = segment["narrative"]
    if support.strip() == segment["exact_text"].strip():
        support_html = ""
    else:
        support_html = f'<p class="support">{html.escape(support)}</p>'
    return (
        '<section class="scene scene-3 scene-clarity">'
        '<div class="rule" aria-hidden="true"></div>'
        f'<h2 class="clarity">{html.escape(segment["exact_text"])}</h2>'
        f"{support_html}"
        "</section>"
    )


def _scene_four(segment: dict[str, Any], brand: dict[str, Any]) -> str:
    cta_label = segment["exact_text"]
    site = urlparse(brand.get("website") or "").netloc or brand.get("website") or ""
    cta_url = html.escape(str(brand.get("cta_url") or "#"), quote=True)
    return (
        '<section class="scene scene-4 scene-cta">'
        '<p class="wordmark">PINAKA <span>INTELLIGENCE</span></p>'
        '<div class="cta-block">'
        f'<a class="cta" href="{cta_url}">{html.escape(cta_label)}</a>'
        f'<span class="site">{html.escape(site)}</span>'
        "</div>"
        "</section>"
    )


def render_html(timeline: dict[str, Any]) -> bytes:
    segments = timeline["segments"]
    if len(segments) != 4:
        raise ValueError("The pilot animatic renderer expects exactly four segments")
    scenes = [
        _scene_one(segments[0]),
        _scene_two(segments[1]),
        _scene_three(segments[2]),
        _scene_four(segments[3], timeline["brand"]),
    ]
    description = " ".join(segment["narrative"] for segment in segments)
    document = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="pinaka:project-id" content="{html.escape(timeline["project_id"], quote=True)}">\n'
        f'<meta name="pinaka:timeline-hash" content="{timeline["timeline_hash"]}">\n'
        "<title>Pinaka Intelligence - Virtual CFO (10s animatic)</title>\n"
        f'<meta name="description" content="{html.escape(description, quote=True)}">\n'
        "<style>\n"
        f"{_css(timeline)}\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        f'<main class="stage" role="img" aria-label="{html.escape(description, quote=True)}">\n'
        f"{''.join(scenes)}\n"
        '<div class="progress" aria-hidden="true"></div>\n'
        "</main>\n"
        "<script>\n"
        "(() => {\n"
        '  const value = new URLSearchParams(window.location.search).get("t");\n'
        "  if (value === null) return;\n"
        "  const seconds = Number(value);\n"
        "  if (!Number.isFinite(seconds)) return;\n"
        "  for (const animation of document.getAnimations()) {\n"
        "    animation.currentTime = seconds * 1000;\n"
        "    animation.pause();\n"
        "  }\n"
        "})();\n"
        "</script>\n"
        "</body>\n"
        "</html>\n"
    )
    return document.encode("utf-8")


def render_srt(timeline: dict[str, Any]) -> bytes:
    lines: list[str] = []
    for index, segment in enumerate(timeline["segments"], start=1):
        lines.extend(
            [
                str(index),
                (
                    f"{srt_timecode(decimal_seconds(segment['start_seconds']))} --> "
                    f"{srt_timecode(decimal_seconds(segment['end_seconds']))}"
                ),
                segment["narrative"],
                "",
            ]
        )
    return ("\n".join(lines)).encode("utf-8")


def render_timeline(timeline: dict[str, Any]) -> bytes:
    return json.dumps(timeline, indent=2, sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    )

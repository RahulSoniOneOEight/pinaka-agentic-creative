from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

SCHEMA = "pinaka.hyperframes.composition/v1"
EXTERNAL_URL = re.compile(
    r"(?:src|href)\s*=\s*[\"']?https?://"
    r"|@import\s+(?:url\()?\s*[\"']?https?://"
    r"|url\(\s*[\"']?https?://",
    re.IGNORECASE,
)
SHOT_ATTR = re.compile(r'data-shot="([^"]+)"')


class CompositionError(ValueError):
    """Raised when a generated composition is not safe or complete."""


def _seconds(value: Any) -> Decimal:
    return Decimal(str(value))


def _pct(part: Decimal, total: Decimal) -> str:
    if total <= 0:
        return "0.0000"
    return f"{(part / total * 100):.4f}"


def _escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


BACKGROUND_ROLES = {"background", "world", "location", "environment"}
FULL_BLEED_ROLES = BACKGROUND_ROLES | {
    "visual",
    "primary visual",
    "hero visual",
    "hero image",
}
SUBJECT_ACTIONS = {"swim", "bob", "wave", "count", "enter", "still"}


def _layer_class(role: str) -> str:
    if role.lower() in FULL_BLEED_ROLES:
        return "layer background"
    return "layer subject"


def _layer_order(layer: dict[str, Any]) -> tuple[int, int]:
    """Canonical paint order: LayerPlan.z, then the legacy role fallback."""

    z = layer.get("z")
    if isinstance(z, int):
        return (z, 0)
    role = str(layer.get("role", "")).lower()
    if layer.get("kind") == "video" or role in FULL_BLEED_ROLES:
        return (0, 1)
    return (10, 1)


def _subject_plan(shot: dict[str, Any]) -> dict[str, Any]:
    """Resolve the per-shot subject behaviour from the shot requirements."""

    configured = (shot.get("requirements") or {}).get("subject") or {}
    action = str(configured.get("action", "")).lower()
    if action not in SUBJECT_ACTIONS:
        action = _infer_action(
            str(shot.get("narrative", "")) + " " + str(shot.get("beat", ""))
        )
    count = configured.get("count")
    if count is None:
        count = _infer_count(str(shot.get("narrative", "")))
    try:
        count = max(1, min(5, int(count)))
    except (TypeError, ValueError):
        count = 1
    scale = float(configured.get("scale", 0.62))
    scale = min(0.95, max(0.2, scale))
    path = configured.get("path")
    points: list[dict[str, float]] = []
    if isinstance(path, list):
        for point in path:
            try:
                points.append(
                    {
                        "t": min(1.0, max(0.0, float(point["t"]))),
                        "x": float(point["x"]),
                        "y": float(point["y"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    return {"action": action, "count": count, "scale": scale, "path": points}


def _infer_action(text: str) -> str:
    lowered = text.lower()
    if "quack" in lowered:
        return "bob"
    if "count" in lowered or "one, two" in lowered:
        return "count"
    if "swim" in lowered or "row" in lowered or "drift" in lowered:
        return "swim"
    if "wave" in lowered or "hello" in lowered:
        return "wave"
    if "enter" in lowered or "appear" in lowered:
        return "enter"
    return "bob"


def _infer_count(text: str) -> int:
    lowered = text.lower()
    for word, value in (
        ("five", 5),
        ("four", 4),
        ("three", 3),
        ("two", 2),
        ("one", 1),
    ):
        if word in lowered:
            return value
    return 1


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def _subject_keyframes(
    *,
    name: str,
    action: str,
    start: Decimal,
    duration: Decimal,
    total: Decimal,
    copy_index: int,
    count: int,
    path: list[dict[str, float]] | None = None,
) -> tuple[str, str]:
    """Return (keyframes, animation shorthand) for one subject copy."""

    s = _pct(start, total)
    e = _pct(start + duration, total)
    mid = _pct(start + duration / 2, total)
    if path:
        steps = ["  0%, " + s + "% { transform: translate(0%, 0%); }"]
        for point in path:
            offset = duration * Decimal(str(point["t"]))
            steps.append(
                f"  {_pct(start + offset, total)}% "
                f"{{ transform: translate({_num(point['x'])}%, "
                f"{_num(point['y'])}%); }}"
            )
        steps.append(f"  {e}%, 100% {{ transform: translate(0%, 0%); }}")
        frames = "@keyframes " + name + " {\n" + "\n".join(steps) + "\n}"
        return frames, f"animation: {name} {total}s linear infinite"
    if action == "swim":
        frames = (
            f"@keyframes {name} {{\n"
            f"  0%, {s}% {{ transform: translateX(-18%) translateY(0); }}\n"
            f"  {mid}% {{ transform: translateX(0%) translateY(-3%); }}\n"
            f"  {e}%, 100% {{ transform: translateX(18%) translateY(0); }}\n"
            f"}}"
        )
    elif action == "wave":
        frames = (
            f"@keyframes {name} {{\n"
            f"  0%, {s}% {{ transform: rotate(0deg); }}\n"
            f"  {mid}% {{ transform: rotate(-6deg); }}\n"
            f"  {e}%, 100% {{ transform: rotate(6deg); }}\n"
            f"}}"
        )
    elif action == "enter":
        frames = (
            f"@keyframes {name} {{\n"
            f"  0%, {s}% {{ transform: translateX(-70%); opacity: 0; }}\n"
            f"  {mid}% {{ transform: translateX(0%); opacity: 1; }}\n"
            f"  {e}%, 100% {{ transform: translateX(0%); opacity: 1; }}\n"
            f"}}"
        )
    elif action == "count":
        step = (duration / Decimal(max(count, 2))) * Decimal(copy_index)
        reveal = _pct(start + Decimal("0.05") + step, total)
        frames = (
            f"@keyframes {name} {{\n"
            f"  0%, {reveal}% {{ transform: scale(0.6); opacity: 0; }}\n"
            f"  {mid}% {{ transform: scale(1); opacity: 1; }}\n"
            f"  {e}%, 100% {{ transform: scale(1); opacity: 1; }}\n"
            f"}}"
        )
    elif action == "still":
        return "", ""
    else:  # bob
        frames = (
            f"@keyframes {name} {{\n"
            f"  0%, {s}% {{ transform: translateY(0); }}\n"
            f"  {mid}% {{ transform: translateY(-5%); }}\n"
            f"  {e}%, 100% {{ transform: translateY(0); }}\n"
            f"}}"
        )
    return frames, f"animation: {name} {total}s linear infinite"


def _js(value: Decimal | float) -> str:
    return f"{float(value):g}"


def _subject_timeline(
    *,
    selector: str,
    action: str,
    start: Decimal,
    duration: Decimal,
    path: list[dict[str, float]] | None,
    copy_index: int,
    count: int,
) -> list[str]:
    """GSAP timeline lines mirroring one subject's CSS action."""

    lines: list[str] = []
    s = _js(start)
    d = _js(duration)
    half = _js(duration / 2)
    if path:
        lines.append(f'  tl.set("{selector}", {{ xPercent: 0, yPercent: 0 }}, {s});')
        previous = Decimal(0)
        for point in path:
            point_time = max(previous, duration * Decimal(str(point["t"])))
            if point_time <= previous:
                continue
            lines.append(
                f'  tl.to("{selector}", {{ xPercent: {_js(point["x"])}, '
                f"yPercent: {_js(point['y'])}, duration: "
                f'{_js(point_time - previous)}, ease: "none" }}, '
                f"{_js(start + previous)});"
            )
            previous = point_time
        if previous < duration:
            lines.append(
                f'  tl.to("{selector}", {{ xPercent: 0, yPercent: 0, '
                f'duration: {_js(duration - previous)}, ease: "none" }}, '
                f"{_js(start + previous)});"
            )
        return lines
    if action == "swim":
        lines.append(
            f'  tl.fromTo("{selector}", {{ xPercent: -18 }}, '
            f'{{ xPercent: 18, duration: {d}, ease: "sine.inOut" }}, {s});'
        )
        lines.append(
            f'  tl.fromTo("{selector}", {{ yPercent: 0 }}, '
            f'{{ yPercent: -3, duration: {half}, ease: "sine.inOut", '
            f"yoyo: true, repeat: 1 }}, {s});"
        )
    elif action == "wave":
        lines.append(
            f'  tl.fromTo("{selector}", {{ rotation: 0 }}, '
            f'{{ rotation: -6, duration: {half}, ease: "sine.inOut" }}, {s});'
        )
        lines.append(
            f'  tl.to("{selector}", {{ rotation: 6, duration: {half}, '
            f'ease: "sine.inOut" }}, {_js(start + duration / 2)});'
        )
    elif action == "enter":
        lines.append(
            f'  tl.fromTo("{selector}", {{ xPercent: -70, opacity: 0 }}, '
            f"{{ xPercent: 0, opacity: 1, duration: {half}, "
            f'ease: "sine.out" }}, {s});'
        )
    elif action == "count":
        step = duration / Decimal(max(count, 2)) * Decimal(copy_index)
        reveal = start + Decimal("0.05") + step
        lines.append(
            f'  tl.fromTo("{selector}", {{ scale: 0.6, opacity: 0 }}, '
            f"{{ scale: 1, opacity: 1, duration: {half}, "
            f'ease: "sine.out" }}, {_js(reveal)});'
        )
    elif action == "bob":
        lines.append(
            f'  tl.fromTo("{selector}", {{ yPercent: 0 }}, '
            f'{{ yPercent: -5, duration: {half}, ease: "sine.inOut", '
            f"yoyo: true, repeat: 1 }}, {s});"
        )
    return lines


def build_composition(
    *,
    title: str,
    width: int,
    height: int,
    shots: list[dict[str, Any]],
    elements: list[dict[str, Any]] | None = None,
    layers: dict[str, list[dict[str, Any]]] | None = None,
    background: str = "#101418",
    foreground: str = "#f5f1e8",
    accent: str = "#f2b544",
    gsap_script: str = "",
    effects: list[dict[str, Any]] | None = None,
    target_duration: float | None = None,
) -> str:
    """Build one editable HTML/CSS composition from the governed shot plan."""

    if not shots:
        raise CompositionError("A composition needs at least one shot")
    if width <= 0 or height <= 0:
        raise CompositionError("Composition size must be positive")
    source_total = sum(
        (_seconds(shot["duration_seconds"]) for shot in shots), Decimal(0)
    )
    if source_total <= 0:
        raise CompositionError("Composition duration must be positive")
    total = (
        Decimal(str(target_duration)) if target_duration is not None else source_total
    )
    if total <= 0:
        raise CompositionError("Target duration must be positive")
    scale = total / source_total
    effects_by_shot: dict[str, list[dict[str, Any]]] = {}
    for cue in effects or ():
        effects_by_shot.setdefault(str(cue.get("shot_key")), []).append(cue)
    rendered_effects: dict[str, list[str]] = {}

    scenes = []
    keyframes = []
    effect_css: list[str] = []
    timeline = ['  tl.set(".scene", { opacity: 0 }, 0);']
    cursor = Decimal(0)
    for index, shot in enumerate(shots, start=1):
        duration = _seconds(shot["duration_seconds"]) * scale
        start, end = cursor, cursor + duration
        cursor = end
        shot_effects = effects_by_shot.get(str(shot["stable_key"]), [])
        kinds = [str(cue.get("kind")) for cue in shot_effects]
        if kinds:
            rendered_effects[str(shot["stable_key"])] = sorted(set(kinds))
        transition = next(
            (
                cue
                for cue in shot_effects
                if str(cue.get("kind")) in {"transition_fade", "transition_dissolve"}
            ),
            None,
        )
        fade = min(Decimal("0.6"), duration / Decimal(3))
        if transition is not None:
            fade = max(
                fade,
                min(
                    Decimal("1.0"),
                    Decimal(str(transition.get("duration_seconds", 0.5))),
                ),
            )
        half = fade / 2
        in_start = max(Decimal(0), start - half)
        in_end = min(total, start + half)
        out_start = max(Decimal(0), end - half)
        out_end = min(total, end + half)
        opening = (
            "  0% { opacity: 1; }\n"
            if start == 0
            else f"  0%, {_pct(in_start, total)}% {{ opacity: 0; }}\n"
        )
        keyframes.append(
            f"@keyframes scene-{index} {{\n"
            f"{opening}"
            f"  {_pct(in_end, total)}% {{ opacity: 1; }}\n"
            f"  {_pct(out_start, total)}% {{ opacity: 1; }}\n"
            f"  {_pct(out_end, total)}%, 100% {{ opacity: 0; }}\n"
            f"}}"
        )
        labels = "".join(
            f'<span class="element" data-element="{_escape(item["stable_key"])}"></span>'
            for item in (elements or ())
            if item.get("kind") in {"character", "prop", "location"}
        )
        layer_html = []
        for layer_number, layer in enumerate(
            sorted((layers or {}).get(shot["stable_key"], ()), key=_layer_order),
            start=1,
        ):
            role = str(layer.get("role", "visual"))
            track = layer.get("z")
            if not isinstance(track, int):
                track = 0 if role.lower() in FULL_BLEED_ROLES else 10
            track_attr = f' data-track-index="{track}"'
            if layer.get("kind") == "video":
                layer_html.append(
                    f'<video class="layer clip" id="layer-{index}-{layer_number}" '
                    f'data-role="{_escape(role)}"'
                    f'{track_attr} data-start="{start}" '
                    f'data-duration="{duration}" '
                    f'src="{layer["path"]}" muted playsinline '
                    f'preload="auto"></video>'
                )
                continue
            if role.lower() in FULL_BLEED_ROLES:
                layer_html.append(
                    f'<img class="{_layer_class(role)}" '
                    f'id="layer-{index}-{layer_number}" data-role="{_escape(role)}"'
                    f'{track_attr} alt="" '
                    f'src="data:{layer["mime"]};base64,{layer["data"]}">'
                )
                continue
            plan = _subject_plan(shot)
            for copy_index in range(plan["count"]):
                name = f"subj-{index}-{layer_number}-{copy_index}"
                frames, animation = _subject_keyframes(
                    name=name,
                    action=plan["action"],
                    start=start,
                    duration=duration,
                    total=total,
                    copy_index=copy_index,
                    count=plan["count"],
                    path=plan["path"],
                )
                if frames:
                    keyframes.append(frames)
                per_scale = plan["scale"] / (1 + 0.6 * (plan["count"] - 1))
                spread = (copy_index - (plan["count"] - 1) / 2) * (per_scale * 90.0)
                path_attr = (
                    f"data-path='{json.dumps(plan['path'])}' " if plan["path"] else ""
                )
                layer_html.append(
                    f'<img class="layer subject" id="subj-{index}-{layer_number}-{copy_index}" '
                    f'data-role="{_escape(role)}"'
                    f'{track_attr} data-action="{plan["action"]}" '
                    f'data-copy="{copy_index}" '
                    f'data-count="{plan["count"]}" {path_attr}'
                    f'style="max-width:{per_scale * 100:.0f}%;'
                    f"max-height:{per_scale * 100:.0f}%;"
                    f'left:{spread:.1f}%;{animation}" '
                    f'alt="" src="data:{layer["mime"]};base64,{layer["data"]}">'
                )
                timeline.extend(
                    _subject_timeline(
                        selector=f"#subj-{index}-{layer_number}-{copy_index}",
                        action=plan["action"],
                        start=start,
                        duration=duration,
                        path=plan["path"],
                        copy_index=copy_index,
                        count=plan["count"],
                    )
                )
        image_layers_html = "".join(layer_html)
        requirements = shot.get("requirements") or {}
        camera = str(requirements.get("camera") or "").lower()
        if not camera:
            camera = str((requirements.get("motion") or {}).get("camera") or "").lower()
        effect_camera = {
            "camera_push_in": "push_in",
            "camera_pull_out": "pull_out",
            "camera_pan_left": "pan_left",
            "camera_pan_right": "pan_right",
        }
        for cue in shot_effects:
            mapped = effect_camera.get(str(cue.get("kind")))
            if mapped:
                camera = mapped
        camera_animation = ""
        camera_moves = {
            "push_in": ("scale(1)", "scale(1.08)"),
            "pull_out": ("scale(1.08)", "scale(1)"),
            "pan_left": ("translateX(2%) scale(1.04)", "translateX(-2%) scale(1.04)"),
            "pan_right": ("translateX(-2%) scale(1.04)", "translateX(2%) scale(1.04)"),
            "parallax": ("translateY(0) scale(1.04)", "translateY(-1.5%) scale(1.04)"),
        }
        if camera in camera_moves:
            start_transform, end_transform = camera_moves[camera]
            keyframes.append(
                f"@keyframes camera-{index} {{\n"
                f"  from {{ transform: {start_transform}; }}\n"
                f"  to {{ transform: {end_transform}; }}\n"
                f"}}"
            )
            camera_animation = (
                f"animation: camera-{index} {duration}s ease-in-out infinite alternate"
            )
        if start == 0:
            timeline.append(f'  tl.set("#scene-{index}", {{ opacity: 1 }}, 0);')
        else:
            timeline.append(
                f'  tl.fromTo("#scene-{index}", {{ opacity: 0 }}, '
                f"{{ opacity: 1, duration: {_js(fade)} }}, {_js(in_start)});"
            )
        if end < total:
            timeline.append(
                f'  tl.to("#scene-{index}", {{ opacity: 0, '
                f"duration: {_js(fade)} }}, {_js(max(Decimal(0), end - half))});"
            )
        camera_gsap = {
            "push_in": ("{ scale: 1 }", '{ scale: 1.08, duration: DUR, ease: "none" }'),
            "pull_out": (
                "{ scale: 1.08 }",
                '{ scale: 1, duration: DUR, ease: "none" }',
            ),
            "pan_left": (
                "{ xPercent: 2, scale: 1.04 }",
                '{ xPercent: -2, scale: 1.04, duration: DUR, ease: "none" }',
            ),
            "pan_right": (
                "{ xPercent: -2, scale: 1.04 }",
                '{ xPercent: 2, scale: 1.04, duration: DUR, ease: "none" }',
            ),
            "parallax": (
                "{ yPercent: 0, scale: 1.04 }",
                '{ yPercent: -1.5, scale: 1.04, duration: DUR, ease: "none" }',
            ),
        }
        if camera in camera_gsap:
            start_state, end_state = camera_gsap[camera]
            timeline.append(
                f'  tl.fromTo("#camera-{index}", {start_state}, '
                f"{end_state.replace('DUR', _js(duration))}, {_js(start)});"
            )
        effect_html: list[str] = []
        for effect_index, cue in enumerate(shot_effects, start=1):
            kind = str(cue.get("kind"))
            if kind in {
                "camera_push_in",
                "camera_pull_out",
                "camera_pan_left",
                "camera_pan_right",
                "parallax",
                "sfx",
                "music_bed",
                "transition_fade",
                "transition_dissolve",
            }:
                continue
            name = f"fx-{index}-{effect_index}"
            cue_start = max(0.0, float(cue.get("start_seconds", 0)) - float(start))
            cue_duration = max(0.2, float(cue.get("duration_seconds", 1.0)))
            if kind == "light_glow":
                effect_css.append(
                    f"@keyframes {name} {{ from {{ opacity: .15; transform: scale(.94); }}"
                    f" to {{ opacity: .72; transform: scale(1.06); }} }}"
                )
                effect_html.append(
                    f'<div class="fx fx-glow" data-effect="light_glow" '
                    f'style="animation:{name} {cue_duration}s ease-in-out '
                    f'{cue_start}s infinite alternate"></div>'
                )
            elif kind == "blur":
                effect_css.append(
                    f"@keyframes {name} {{ from {{ opacity: .65; }}"
                    f" to {{ opacity: .2; }} }}"
                )
                effect_html.append(
                    f'<div class="fx fx-blur" data-effect="blur" '
                    f'style="animation:{name} {cue_duration}s ease-in-out '
                    f'{cue_start}s infinite alternate"></div>'
                )
            elif kind == "particles":
                dots = []
                for dot in range(8):
                    left = 12 + (dot * 11 + index * 7) % 76
                    top = 18 + (dot * 17 + index * 13) % 64
                    delay = round((dot % 4) * 0.35 + cue_start, 2)
                    dots.append(
                        f'<span style="left:{left}%;top:{top}%;'
                        f'animation-delay:{delay}s"></span>'
                    )
                effect_css.append(
                    f"@keyframes {name} {{ 0% {{ transform: translateY(12px);"
                    f" opacity: 0; }} 25% {{ opacity: .8; }}"
                    f" 100% {{ transform: translateY(-26px); opacity: 0; }} }}"
                )
                effect_html.append(
                    f'<div class="fx fx-particles" data-effect="particles" '
                    f'style="animation:{name} {cue_duration}s linear {cue_start}s infinite">'
                    f"{''.join(dots)}</div>"
                )
            elif kind == "flash":
                effect_css.append(
                    f"@keyframes {name} {{ 0%, 100% {{ opacity: 0; }}"
                    f" 8% {{ opacity: .85; }} 20% {{ opacity: 0; }} }}"
                )
                effect_html.append(
                    f'<div class="fx fx-flash" data-effect="flash" '
                    f'style="animation:{name} {cue_duration}s ease-out '
                    f'{cue_start}s 1"></div>'
                )
            elif kind in {"transition_fade", "transition_dissolve"}:
                effect_css.append(
                    f"@keyframes {name} {{ from {{ opacity: .35; }}"
                    f" to {{ opacity: 0; }} }}"
                )
                effect_html.append(
                    f'<div class="fx fx-transition" data-effect="{kind}" '
                    f'style="animation:{name} {cue_duration}s ease-out '
                    f'{cue_start}s 1 forwards"></div>'
                )
        effects_markup = "".join(effect_html)
        scene_timing = (
            ""
            if gsap_script
            else f'data-start="{start}" data-end="{end}" data-duration="{duration}"'
        )
        scenes.append(
            f'  <section id="scene-{index}" class="scene clip" '
            f'data-shot="{_escape(shot["stable_key"])}" '
            f'data-hf-scene="{_escape(shot["stable_key"])}" '
            f"data-hf-scene-no-swap "
            f"{scene_timing} "
            f'style="animation: scene-{index} {total}s linear infinite">\n'
            f'    <div id="camera-{index}" class="layers" style="{camera_animation}">{image_layers_html}</div>\n'
            f"    {effects_markup}\n"
            f'    <div class="elements" aria-hidden="true">{labels}</div>\n'
            f"  </section>"
        )

    gsap_class = ' class="gsap"' if gsap_script else ""
    gsap_head = f"<script>{gsap_script}</script>" if gsap_script else ""
    timeline_js = "\n".join(timeline)
    gsap_flag = str(bool(gsap_script)).lower()
    return f"""<!doctype html>
<html lang="en"{gsap_class}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="pinaka-schema" content="{SCHEMA}">
{gsap_head}
<title>{_escape(title)}</title>
<style>
:root {{
  --bg: {background};
  --fg: {foreground};
  --accent: {accent};
}}
html, body {{ margin: 0; padding: 0; background: var(--bg); overflow: hidden; }}
body {{ font-family: "Segoe UI", system-ui, sans-serif; color: var(--fg); }}
.stage {{
  position: absolute;
  width: {width}px;
  height: {height}px;
  overflow: hidden;
  transform-origin: top left;
}}
.scene {{
  position: absolute;
  inset: 0;
  opacity: 0;
  overflow: hidden;
}}
.content {{
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 6%;
  text-align: center;
  z-index: 2;
  text-shadow: 0 2px 18px rgba(0, 0, 0, 0.55);
}}
.content > div {{ max-width: 84%; }}
.layers {{
  position: absolute;
  inset: 0;
  z-index: 1;
}}
.layer {{
  position: absolute;
  inset: 0;
  margin: auto;
  object-fit: cover;
}}
.layer.background {{
  width: 100%;
  height: 100%;
  object-fit: cover;
}}
.layer.clip {{
  width: 100%;
  height: 100%;
  object-fit: cover;
}}
.layer.subject {{
  max-width: 64%;
  max-height: 52%;
  bottom: 14%;
  top: auto;
}}
.beat {{
  color: var(--accent);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-size: {max(14, round(height * 0.014))}px;
}}
.narrative {{ font-size: {max(20, round(height * 0.028))}px; line-height: 1.35; }}
.elements {{ display: none; }}
.fx {{
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 2;
}}
.fx-glow {{
  background: radial-gradient(circle at 50% 42%, rgba(255, 224, 160, 0.7), transparent 62%);
  mix-blend-mode: screen;
}}
.fx-blur {{
  inset: 28% 18%;
  border-radius: 50%;
  backdrop-filter: blur(7px);
}}
.fx-particles span {{
  position: absolute;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  opacity: 0;
}}
.fx-flash {{ background: #ffffff; }}
.fx-transition {{ background: #000000; }}
.gsap .scene, .gsap .layer.subject, .gsap .layers {{
  animation: none !important;
}}
{"".join(keyframes)}
{"".join(effect_css)}
</style>
</head>
<body>
<div class="stage" data-composition-id="pinaka-main"
     data-width="{width}" data-height="{height}" data-duration="{total}">
{chr(10).join(scenes)}
<span id="timeline-end" style="display:none"></span>
</div>
<script>
(function () {{
  var stage = document.querySelector(".stage");
  function fit() {{
    var scale = Math.min(window.innerWidth / {width}, window.innerHeight / {height});
    stage.style.transform = "scale(" + scale + ")";
    stage.style.left = ((window.innerWidth - {width} * scale) / 2) + "px";
    stage.style.top = ((window.innerHeight - {height} * scale) / 2) + "px";
  }}
  var gsapMode = {gsap_flag};
  var tl = null;
  if (gsapMode && window.gsap) {{
    tl = gsap.timeline({{ paused: true }});
{timeline_js}
    tl.set("#timeline-end", {{ opacity: 1 }}, {_js(total)});
    window.__timelines = window.__timelines || {{}};
    window.__timelines["pinaka-main"] = tl;
    window.__seek = function (seconds) {{
      tl.pause(Math.max(seconds, 0.001));
    }};
  }}
  var params = new URLSearchParams(window.location.search);
  var at = parseFloat(params.get("t") || "0");
  if (!isNaN(at)) {{
    if (gsapMode && tl) {{ tl.pause(at <= 0 ? 0.001 : at); }}
    document.querySelectorAll(".scene").forEach(function (el) {{
      el.querySelectorAll("video.clip").forEach(function (v) {{
        var start = parseFloat(v.dataset.start || "0");
        var local = Math.max(0, Math.min(at - start, v.duration || at));
        v.pause();
        if (Math.abs((v.currentTime || 0) - local) > 0.02) {{
          v.currentTime = local;
        }}
      }});
      if (gsapMode && tl) {{ return; }}
      el.style.animation = "none";
      var s = parseFloat(el.dataset.start || "0");
      var e = parseFloat(el.dataset.end || "0");
      var dur = Math.max(e - s, 0.0001);
      var p = Math.max(0, Math.min(1, (at - s) / dur));
      el.style.opacity = at >= s && at < e ? "1" : "0";
      el.querySelectorAll(".layer.subject").forEach(function (img) {{
        var action = img.dataset.action || "bob";
        var copy = parseInt(img.dataset.copy || "0", 10);
        var n = parseInt(img.dataset.count || "1", 10) || 1;
        var transform = "none";
        var opacity = 1;
        if (img.dataset.path) {{
          var pts = JSON.parse(img.dataset.path);
          pts.sort(function (a, b) {{ return a.t - b.t; }});
          var track = [{{ t: 0, x: 0, y: 0 }}].concat(pts).concat([
            {{ t: 1, x: 0, y: 0 }}
          ]);
          var x = 0;
          var y = 0;
          for (var i = 1; i < track.length; i++) {{
            if (p <= track[i].t) {{
              var a = track[i - 1];
              var b = track[i];
              var span = Math.max(b.t - a.t, 0.0001);
              var k = (p - a.t) / span;
              x = a.x + (b.x - a.x) * k;
              y = a.y + (b.y - a.y) * k;
              break;
            }}
            x = track[i].x;
            y = track[i].y;
          }}
          transform = "translate(" + x + "%, " + y + "%)";
        }} else if (action === "swim") {{
          transform = "translateX(" + (-18 + 36 * p) + "%) translateY(" +
            (-3 * Math.sin(Math.PI * p)) + "%)";
        }} else if (action === "bob") {{
          transform = "translateY(" + (-5 * Math.sin(Math.PI * p)) + "%)";
        }} else if (action === "wave") {{
          transform = "rotate(" + (-6 + 12 * p) + "deg)";
        }} else if (action === "enter") {{
          var q = Math.min(1, p * 2);
          transform = "translateX(" + (-70 * (1 - q)) + "%)";
          opacity = q;
        }} else if (action === "count") {{
          var reveal = 0.05 + copy / n;
          opacity = p >= reveal ? 1 : 0;
          transform = "scale(" + (opacity ? 1 : 0.7) + ")";
        }}
        img.style.animation = "none";
        img.style.transform = transform;
        img.style.opacity = opacity;
      }});
    }});
  }}
  document.querySelectorAll("video.clip").forEach(function (v) {{
    if (isNaN(at)) {{
      v.play().catch(function () {{}});
    }}
  }});
  window.addEventListener("resize", fit);
  fit();
}})();
</script>
</body>
</html>
"""


def validate_composition(html: str) -> dict[str, Any]:
    """Structural validation that a composition is editable and offline-safe."""

    problems: list[str] = []
    if not html.lstrip().lower().startswith("<!doctype html>"):
        problems.append("missing doctype")
    if f'name="pinaka-schema" content="{SCHEMA}"' not in html:
        problems.append("missing schema marker")
    if "<style>" not in html:
        problems.append("missing inline style block")
    if 'class="stage"' not in html:
        problems.append("missing stage container")
    if 'data-composition-id="pinaka-main"' not in html:
        problems.append("missing HyperFrames composition id")
    layer_count = html.count('<img class="layer') + html.count('<video class="layer')
    if layer_count and not re.search(r'data-track-index="\d+"', html):
        problems.append("missing HyperFrames track index")
    if not re.search(r'data-hf-scene="[^"]+"', html):
        problems.append("missing HyperFrames scene part")
    shots = SHOT_ATTR.findall(html)
    if not shots:
        problems.append("no shot scenes found")
    if len(shots) != len(set(shots)):
        problems.append("duplicate shot identifiers")
    if EXTERNAL_URL.search(html):
        problems.append("external resource reference (must render offline)")
    if "data-duration=" not in html:
        problems.append("missing duration marker")
    if 'class="narrative"' in html or 'class="beat"' in html:
        problems.append("baked text layer (captions must stay separate)")
    if "object-fit: contain" in html:
        problems.append("letterboxed layer (full-bleed required)")
    known_effects = {
        "light_glow",
        "blur",
        "particles",
        "flash",
        "transition_fade",
        "transition_dissolve",
    }
    effects = sorted(set(re.findall(r'data-effect="([^"]+)"', html)))
    unknown_effects = sorted(set(effects) - known_effects)
    if unknown_effects:
        problems.append("unknown effect markers: " + ", ".join(unknown_effects))
    return {
        "valid": not problems,
        "problems": problems,
        "shot_count": len(shots),
        "shots": shots,
        "layer_count": layer_count,
        "effects": effects,
        "bytes": len(html.encode("utf-8")),
    }


DURATION_ATTR = re.compile(r'data-duration="([0-9.]+)"')
SIZE_ATTR = re.compile(r'data-width="(\d+)"\s+data-height="(\d+)"')


def composition_size(html: str) -> tuple[int, int]:
    match = SIZE_ATTR.search(html)
    if not match:
        raise CompositionError("Composition has no data-width/data-height markers")
    return int(match.group(1)), int(match.group(2))


def composition_duration(html: str) -> Decimal:
    """Read the declared total duration from a composition."""

    match = DURATION_ATTR.search(html)
    if not match:
        raise CompositionError("Composition has no data-duration marker")
    value = Decimal(match.group(1))
    if value <= 0:
        raise CompositionError("Composition duration must be positive")
    return value


def composition_manifest(html: str) -> str:
    """Canonical manifest stored beside the composition source."""

    report = validate_composition(html)
    return json.dumps(
        {
            "schema": SCHEMA,
            "shot_count": report["shot_count"],
            "shots": report["shots"],
            "bytes": report["bytes"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )

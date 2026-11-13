from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

SCHEMA = "pinaka.hyperframes.composition/v1"
EXTERNAL_URL = re.compile(r"https?://", re.IGNORECASE)
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
) -> str:
    """Build one editable HTML/CSS composition from the governed shot plan."""

    if not shots:
        raise CompositionError("A composition needs at least one shot")
    if width <= 0 or height <= 0:
        raise CompositionError("Composition size must be positive")
    total = sum((_seconds(shot["duration_seconds"]) for shot in shots), Decimal(0))
    if total <= 0:
        raise CompositionError("Composition duration must be positive")

    scenes = []
    keyframes = []
    cursor = Decimal(0)
    for index, shot in enumerate(shots, start=1):
        duration = _seconds(shot["duration_seconds"])
        start, end = cursor, cursor + duration
        cursor = end
        fade_in = min(Decimal(6), duration / Decimal(4))
        fade_out = min(Decimal(6), duration / Decimal(4))
        from_pct = _pct(start + fade_in, total)
        to_pct = _pct(end - fade_out, total)
        opening = (
            "  0% { opacity: 1; }\n"
            if start == 0
            else f"  0%, {_pct(start, total)}% {{ opacity: 0; }}\n"
        )
        keyframes.append(
            f"@keyframes scene-{index} {{\n"
            f"{opening}"
            f"  {from_pct}% {{ opacity: 1; }}\n"
            f"  {to_pct}% {{ opacity: 1; }}\n"
            f"  {_pct(end, total)}%, 100% {{ opacity: 0; }}\n"
            f"}}"
        )
        labels = "".join(
            f'<span class="element" data-element="{_escape(item["stable_key"])}"></span>'
            for item in (elements or ())
            if item.get("kind") in {"character", "prop", "location"}
        )
        layer_html = []
        for layer in sorted(
            (layers or {}).get(shot["stable_key"], ()),
            key=_layer_order,
        ):
            role = str(layer.get("role", "visual"))
            track = layer.get("z")
            if not isinstance(track, int):
                track = 0 if role.lower() in FULL_BLEED_ROLES else 10
            track_attr = f' data-track-index="{track}"'
            if layer.get("kind") == "video":
                layer_html.append(
                    f'<video class="layer clip" data-role="{_escape(role)}"'
                    f'{track_attr} data-start="{start}" data-end="{end}" '
                    f'src="{layer["path"]}" muted playsinline '
                    f'preload="auto"></video>'
                )
                continue
            if role.lower() in FULL_BLEED_ROLES:
                layer_html.append(
                    f'<img class="{_layer_class(role)}" data-role="{_escape(role)}"'
                    f'{track_attr} alt="" '
                    f'src="data:{layer["mime"]};base64,{layer["data"]}">'
                )
                continue
            plan = _subject_plan(shot)
            for copy_index in range(plan["count"]):
                name = f"subj-{index}-{copy_index}"
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
                    f'<img class="layer subject" data-role="{_escape(role)}"'
                    f'{track_attr} data-action="{plan["action"]}" '
                    f'data-copy="{copy_index}" '
                    f'data-count="{plan["count"]}" {path_attr}'
                    f'style="max-width:{per_scale * 100:.0f}%;'
                    f"max-height:{per_scale * 100:.0f}%;"
                    f'left:{spread:.1f}%;{animation}" '
                    f'alt="" src="data:{layer["mime"]};base64,{layer["data"]}">'
                )
        image_layers_html = "".join(layer_html)
        camera = str((shot.get("requirements") or {}).get("camera", "")).lower()
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
        scenes.append(
            f'  <section class="scene" data-shot="{_escape(shot["stable_key"])}" '
            f'data-hf-scene="{_escape(shot["stable_key"])}" '
            f"data-hf-scene-no-swap "
            f'data-start="{start}" data-end="{end}" data-duration="{duration}" '
            f'style="animation: scene-{index} {total}s linear infinite">\n'
            f'    <div class="layers" style="{camera_animation}">{image_layers_html}</div>\n'
            f'    <div class="elements" aria-hidden="true">{labels}</div>\n'
            f"  </section>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="pinaka-schema" content="{SCHEMA}">
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
  max-width: 62%;
  max-height: 62%;
  bottom: 6%;
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
{"".join(keyframes)}
</style>
</head>
<body>
<div class="stage" data-composition-id="pinaka-main"
     data-width="{width}" data-height="{height}" data-duration="{total}">
{chr(10).join(scenes)}
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
  var params = new URLSearchParams(window.location.search);
  var at = parseFloat(params.get("t") || "0");
  if (!isNaN(at)) {{
    document.querySelectorAll(".scene").forEach(function (el) {{
      el.style.animation = "none";
      var s = parseFloat(el.dataset.start || "0");
      var e = parseFloat(el.dataset.end || "0");
      var dur = Math.max(e - s, 0.0001);
      var p = Math.max(0, Math.min(1, (at - s) / dur));
      el.style.opacity = at >= s && at < e ? "1" : "0";
      el.querySelectorAll("video.clip").forEach(function (v) {{
        var start = parseFloat(v.dataset.start || "0");
        var local = Math.max(0, Math.min(at - start, v.duration || at));
        v.pause();
        if (Math.abs((v.currentTime || 0) - local) > 0.02) {{
          v.currentTime = local;
        }}
      }});
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
    return {
        "valid": not problems,
        "problems": problems,
        "shot_count": len(shots),
        "shots": shots,
        "layer_count": layer_count,
        "bytes": len(html.encode("utf-8")),
    }


DURATION_ATTR = re.compile(r'data-duration="([0-9.]+)"')


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

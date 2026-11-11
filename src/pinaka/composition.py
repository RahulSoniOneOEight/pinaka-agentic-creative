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


def build_composition(
    *,
    title: str,
    width: int,
    height: int,
    shots: list[dict[str, Any]],
    elements: list[dict[str, Any]] | None = None,
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
            f'<span class="element" data-element="{_escape(item["stable_key"])}">'
            f"{_escape(item.get('name', item['stable_key']))}</span>"
            for item in (elements or ())
            if item.get("kind") in {"character", "prop", "location"}
        )
        scenes.append(
            f'  <section class="scene" data-shot="{_escape(shot["stable_key"])}" '
            f'data-start="{start}" data-end="{end}" data-duration="{duration}" '
            f'style="animation: scene-{index} {total}s linear infinite">\n'
            f'    <div class="content">\n'
            f'      <p class="beat">{_escape(str(shot.get("beat", "")))}</p>\n'
            f'      <p class="narrative">{_escape(str(shot.get("narrative", "")))}</p>\n'
            f'      <div class="elements">{labels}</div>\n'
            f"    </div>\n"
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
  display: grid;
  place-items: center;
  padding: 6%;
}}
.content {{ max-width: 84%; text-align: center; }}
.beat {{
  color: var(--accent);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-size: {max(14, round(height * 0.014))}px;
}}
.narrative {{ font-size: {max(20, round(height * 0.028))}px; line-height: 1.35; }}
.elements {{ display: flex; gap: 0.5rem; justify-content: center; flex-wrap: wrap; }}
.element {{
  border: 1px solid var(--accent);
  border-radius: 999px;
  padding: 0.15rem 0.6rem;
  font-size: {max(12, round(height * 0.013))}px;
}}
{"".join(keyframes)}
</style>
</head>
<body>
<div class="stage" data-duration="{total}">
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
    shots = SHOT_ATTR.findall(html)
    if not shots:
        problems.append("no shot scenes found")
    if len(shots) != len(set(shots)):
        problems.append("duplicate shot identifiers")
    if EXTERNAL_URL.search(html):
        problems.append("external resource reference (must render offline)")
    if "data-duration=" not in html:
        problems.append("missing duration marker")
    return {
        "valid": not problems,
        "problems": problems,
        "shot_count": len(shots),
        "shots": shots,
        "bytes": len(html.encode("utf-8")),
    }


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

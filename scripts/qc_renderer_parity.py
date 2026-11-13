"""Render the same 12 s / 1080x1920 three-layer composition through both renderers."""

from __future__ import annotations

import base64
import json
import shutil
import time
from pathlib import Path

import cv2

from pinaka.adapters.encode import FFmpegEncodeAdapter
from pinaka.adapters.frames_playwright import PlaywrightFrameCapture
from pinaka.adapters.hyperframes import (
    HyperFramesAdapter,
    frame_difference,
    video_duration,
)
from pinaka.composition import build_composition, composition_duration, composition_size

ROOT = Path(r"D:\pinaka-agentic-creative\workspace\renderer-parity-qc")
if ROOT.exists():
    shutil.rmtree(ROOT)
ROOT.mkdir(parents=True)

adapter = HyperFramesAdapter()
svg = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1920">'
    b'<rect width="1080" height="1920" fill="#6f8f5f"/>'
    b"</svg>"
)
duck = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="360" height="220">'
    b'<ellipse cx="180" cy="120" rx="150" ry="80" fill="#f2c230"/>'
    b'<circle cx="285" cy="85" r="48" fill="#f2c230"/>'
    b'<ellipse cx="110" cy="120" rx="80" ry="38" fill="#d9a520"/>'
    b"</svg>"
)
effect = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1920">'
    b'<circle cx="540" cy="900" r="420" fill="#ffffff" opacity="0.18"/>'
    b"</svg>"
)
html = build_composition(
    title="Renderer parity",
    width=1080,
    height=1920,
    shots=[
        {
            "stable_key": "shot-01",
            "beat": "establish",
            "duration_seconds": 12,
            "narrative": "A duck swims across the pond",
            "requirements": {
                "subject": {"action": "swim", "count": 1},
                "camera": "push_in",
            },
        }
    ],
    layers={
        "shot-01": [
            {
                "role": "background",
                "mime": "image/svg+xml",
                "data": base64.b64encode(svg).decode(),
                "z": 0,
            },
            {
                "role": "character",
                "mime": "image/svg+xml",
                "data": base64.b64encode(duck).decode(),
                "z": 10,
            },
            {
                "role": "effect",
                "mime": "image/svg+xml",
                "data": base64.b64encode(effect).decode(),
                "z": 20,
            },
        ]
    },
    gsap_script=adapter.gsap_source(),
)
duration = composition_duration(html)
size = composition_size(html)
geometry = html.encode("utf-8")
source = ROOT / "parity.html"
source.write_bytes(geometry)

frames = int(duration * 12)
capture = PlaywrightFrameCapture()
started = time.time()
captured = capture.capture(
    html_path=source, frame_count=frames, fps=12, width=size[0], height=size[1]
)
frames_dir = ROOT / "frames"
frames_dir.mkdir()
for frame in captured.frames:
    (frames_dir / f"frame-{frame.index:04d}.png").write_bytes(frame.content)
encoded = FFmpegEncodeAdapter().encode(
    frames_dir=frames_dir,
    frame_count=frames,
    fps=12,
    width=size[0],
    height=size[1],
)
legacy = ROOT / "legacy.mp4"
legacy.write_bytes(encoded.content)
legacy_seconds = round(time.time() - started, 1)

project = ROOT / "hyperframes"
project.mkdir()
(project / "index.html").write_bytes(geometry)
(project / "hyperframes.json").write_text(
    '{"paths":{"blocks":"compositions","components":"compositions/components",'
    '"assets":"assets"}}',
    encoding="utf-8",
)
lint = adapter.lint(project / "index.html")
started = time.time()
render = adapter.render(project, ROOT / "hyperframes.mp4", fps=12)
hyperframes_seconds = round(time.time() - started, 1)
hyperframes = ROOT / "hyperframes.mp4"


def probe(path: Path) -> dict[str, float]:
    capture = cv2.VideoCapture(str(path))
    data = {
        "duration_seconds": video_duration(path),
        "width": float(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": float(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": float(capture.get(cv2.CAP_PROP_FPS)),
        "frames": float(capture.get(cv2.CAP_PROP_FRAME_COUNT)),
    }
    capture.release()
    return data


report = {
    "composition": {
        "duration_seconds": float(duration),
        "width": size[0],
        "height": size[1],
        "frames_at_12fps": frames,
        "html_bytes": len(geometry),
    },
    "legacy": {**probe(legacy), "elapsed_seconds": legacy_seconds},
    "hyperframes": {
        **probe(hyperframes),
        "elapsed_seconds": hyperframes_seconds,
        "version": render["version"],
        "lint": {
            "ok": lint["ok"],
            "errors": lint["errorCount"],
            "warnings": lint["warningCount"],
        },
    },
    "parity": {
        "duration_delta": abs(
            probe(legacy)["duration_seconds"] - probe(hyperframes)["duration_seconds"]
        ),
        "frame_delta": abs(probe(legacy)["frames"] - probe(hyperframes)["frames"]),
        "frame_difference": frame_difference(legacy, hyperframes),
    },
}
(ROOT / "renderer-parity.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))

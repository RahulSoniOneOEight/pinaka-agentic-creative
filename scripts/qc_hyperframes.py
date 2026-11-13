"""Phase 0 HyperFrames QC: lint + deterministic 3-layer render (12 s, 1080x1920)."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import cv2

from pinaka.adapters.hyperframes import (
    PINNED_VERSION,
    HyperFramesAdapter,
    frame_difference,
    sampled_frame_hashes,
    video_duration,
)
from pinaka.motion_verify import verify_video

ROOT = Path(r"D:\pinaka-agentic-creative\workspace\hyperframes-qc")
if ROOT.exists():
    shutil.rmtree(ROOT)
ROOT.mkdir(parents=True)

adapter = HyperFramesAdapter()
project = ROOT / "project"
html = adapter.write_smoke_project(
    project, width=1080, height=1920, duration=12, fps=12
)
lint = adapter.lint(html)
started = time.time()
render = adapter.render(project, ROOT / "hyperframes-12s.mp4", fps=12)
elapsed = round(time.time() - started, 1)
output = ROOT / "hyperframes-12s.mp4"
second = ROOT / "hyperframes-12s-b.mp4"
adapter.render(project, second, fps=12)
determinism = frame_difference(output, second)
capture = cv2.VideoCapture(str(output))
width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
fps = float(capture.get(cv2.CAP_PROP_FPS))
frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
capture.release()
motion = verify_video(str(output), samples=12)
report = {
    "version": adapter.version(),
    "pinned": PINNED_VERSION,
    "integrity": adapter.lock_integrity(),
    "lint": lint,
    "render": {
        **render,
        "duration_seconds": video_duration(output),
        "width": width,
        "height": height,
        "fps": fps,
        "frames": frames,
        "elapsed_seconds": elapsed,
        "frame_hashes": sampled_frame_hashes(output, samples=4),
        "determinism": determinism,
    },
    "motion": {
        "verdict": motion["verdict"],
        "residual_ratio": motion["residual_ratio"],
        "components": motion["components"],
    },
}
(ROOT / "hyperframes-qc.json").write_text(
    json.dumps(report, indent=2), encoding="utf-8"
)
print(json.dumps(report, indent=2))

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import cv2

from ..errors import ConfigurationError

PINNED_VERSION = "0.9.41"
REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_DIR = REPO_ROOT / "tools" / "hyperframes"
LOCK_FILE = PACKAGE_DIR / "package-lock.json"
VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


class HyperFramesAdapter:
    """Pinned HyperFrames CLI wrapper: lint, render and frame hashing."""

    def __init__(self, package_dir: Path | None = None, timeout_seconds: int = 600):
        self.package_dir = Path(package_dir) if package_dir else PACKAGE_DIR
        self.timeout_seconds = timeout_seconds

    @property
    def module_path(self) -> Path:
        return (
            self.package_dir
            / "node_modules"
            / "hyperframes"
            / "bin"
            / "hyperframes.mjs"
        )

    def node(self) -> str | None:
        return shutil.which("node")

    def installed(self) -> bool:
        return self.node() is not None and self.module_path.is_file()

    def package_version(self) -> str | None:
        package = self.package_dir / "package.json"
        if not package.is_file():
            return None
        data = json.loads(package.read_text(encoding="utf-8"))
        value = str((data.get("dependencies") or {}).get("hyperframes") or "")
        return value or None

    def lock_integrity(self) -> str | None:
        if not LOCK_FILE.is_file():
            return None
        data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
        entry = (data.get("packages") or {}).get("node_modules/hyperframes") or {}
        return entry.get("integrity")

    def _run(
        self, args: list[str], *, cwd: Path | None = None
    ) -> subprocess.CompletedProcess:
        node = self.node()
        if node is None or not self.module_path.is_file():
            raise ConfigurationError(
                "HyperFrames is not installed; run npm ci in tools/hyperframes"
            )
        return subprocess.run(
            [node, str(self.module_path), *args],
            cwd=str(cwd or self.package_dir),
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )

    def version(self) -> str:
        result = self._run(["--version"])
        match = VERSION_RE.search((result.stdout or result.stderr).strip())
        if not match:
            raise ConfigurationError("Cannot read the HyperFrames version")
        return match.group(1)

    def lint(self, html_path: Path) -> dict[str, Any]:
        html_path = Path(html_path).resolve()
        if not html_path.is_file():
            raise FileNotFoundError(f"Composition not found: {html_path}")
        result = self._run(["lint", str(html_path), "--json"])
        payload = (result.stdout or "").strip()
        try:
            report = json.loads(payload)
        except json.JSONDecodeError as error:
            raise ConfigurationError(
                f"HyperFrames lint did not return JSON: {payload[:400]}"
            ) from error
        if result.returncode != 0 and report.get("ok") is not False:
            raise ConfigurationError(
                f"HyperFrames lint failed: {(result.stderr or payload)[:400]}"
            )
        return report

    def render(
        self,
        project_dir: Path,
        output: Path,
        *,
        composition: str = "index.html",
        fps: int = 12,
        quality: str = "draft",
        workers: int = 1,
    ) -> dict[str, Any]:
        project_dir = Path(project_dir).resolve()
        output = Path(output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        result = self._run(
            [
                "render",
                str(project_dir),
                "--composition",
                composition,
                "--output",
                str(output),
                "--fps",
                str(fps),
                "--quality",
                quality,
                "--workers",
                str(workers),
                "--no-wait",
                "--quiet",
            ],
            cwd=project_dir,
        )
        if result.returncode != 0 or not output.is_file():
            raise ConfigurationError(
                "HyperFrames render failed: "
                f"{(result.stderr or result.stdout or 'no output')[:600]}"
            )
        return {
            "output": str(output),
            "bytes": output.stat().st_size,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "fps": fps,
            "composition": composition,
            "version": self.version(),
            "command_hash": hashlib.sha256(
                f"{project_dir.name}:{composition}:{fps}:{quality}".encode()
            ).hexdigest(),
        }

    def gsap_source(self) -> str:
        vendor = (
            self.package_dir
            / "node_modules"
            / "hyperframes"
            / "dist"
            / "vendor"
            / "npm"
            / "gsap@3.15.0"
            / "dist"
            / "gsap.min.js"
        )
        if not vendor.is_file():
            raise ConfigurationError("HyperFrames did not ship the vendored GSAP build")
        return vendor.read_text(encoding="utf-8")

    def write_smoke_project(
        self,
        project_dir: Path,
        *,
        width: int,
        height: int,
        duration: float,
        fps: int,
    ) -> Path:
        """Write a three-layer offline composition (background/character/effect)."""

        project_dir = Path(project_dir)
        assets = project_dir / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        (assets / "gsap.min.js").write_text(self.gsap_source(), encoding="utf-8")
        html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
html, body {{ margin: 0; width: {width}px; height: {height}px; overflow: hidden;
  background: #101418; }}
#root {{ position: relative; width: {width}px; height: {height}px; }}
.layer {{ position: absolute; }}
#bg {{ inset: 0; width: 100%; height: 100%;
  background: linear-gradient(#87b06a, #5e7d9a); }}
#char {{ left: {width * 0.34:.1f}px; top: {height * 0.45:.1f}px;
  width: {width * 0.32:.1f}px; height: {height * 0.13:.1f}px;
  border-radius: 50%; background: #f2c230; }}
#wing {{ position: absolute; left: {width * 0.40:.1f}px;
  top: {height * 0.465:.1f}px; width: {width * 0.13:.1f}px;
  height: {height * 0.05:.1f}px; border-radius: 50%; background: #d9a520;
  transform-origin: 85% 50%; }}
#fx {{ inset: 0; opacity: 0;
  background: radial-gradient(circle at 50% 55%, rgba(255,255,255,.5), transparent 45%); }}
</style>
</head>
<body>
<div id="root" data-composition-id="pinaka-smoke" data-width="{width}"
     data-height="{height}" data-fps="{fps}" data-duration="{duration}">
  <div class="layer clip" id="bg" data-start="0" data-duration="{duration}"
       data-track-index="0"></div>
  <div class="layer clip" id="char" data-start="0" data-duration="{duration}"
       data-track-index="10" data-hf-scene="shot-01"
       data-hf-scene-no-swap></div>
  <div class="layer clip" id="wing" data-start="0" data-duration="{duration}"
       data-track-index="11"></div>
  <div class="layer clip" id="fx" data-start="0" data-duration="{duration}"
       data-track-index="20"></div>
</div>
<script src="assets/gsap.min.js"></script>
<script>
var tl = gsap.timeline({{ paused: true }});
tl.fromTo("#char", {{ x: -{width * 0.18:.1f}, y: 0 }},
  {{ x: {width * 0.18:.1f}, y: -{height * 0.02:.1f}, duration: {duration},
     ease: "sine.inOut" }}, 0);
tl.fromTo("#char", {{ scaleX: 1 }},
  {{ scaleX: 1.06, duration: {duration / 8}, ease: "sine.inOut",
     yoyo: true, repeat: 7 }}, 0);
tl.fromTo("#wing", {{ x: -{width * 0.18:.1f} }},
  {{ x: {width * 0.18:.1f}, duration: {duration}, ease: "sine.inOut" }}, 0);
tl.fromTo("#wing", {{ rotation: -22 }},
  {{ rotation: 22, duration: {duration / 24}, ease: "sine.inOut",
     yoyo: true, repeat: 23 }}, 0);
tl.fromTo("#fx", {{ opacity: 0 }},
  {{ opacity: 0.9, duration: {duration / 4}, ease: "sine.inOut",
     yoyo: true, repeat: 3 }}, 0);
tl.fromTo("#bg", {{ scale: 1 }},
  {{ scale: 1.06, duration: {duration}, ease: "none" }}, 0);
window.__timelines = window.__timelines || {{}};
window.__timelines["pinaka-smoke"] = tl;
</script>
</body>
</html>
"""
        (project_dir / "index.html").write_text(html, encoding="utf-8")
        (project_dir / "hyperframes.json").write_text(
            json.dumps(
                {
                    "paths": {
                        "blocks": "compositions",
                        "components": "compositions/components",
                        "assets": "assets",
                    }
                }
            ),
            encoding="utf-8",
        )
        return project_dir / "index.html"


def video_duration(path: Path) -> float:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ConfigurationError(f"Cannot open video: {path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    capture.release()
    if fps <= 0:
        raise ConfigurationError(f"Video has no frame rate: {path}")
    return frames / fps


def sampled_frame_hashes(path: Path, *, samples: int = 8) -> list[str]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ConfigurationError(f"Cannot open video: {path}")
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, total // max(1, samples))
    hashes: list[str] = []
    index = 0
    while len(hashes) < samples:
        ok, frame = capture.read()
        if not ok:
            break
        if index % step == 0:
            hashes.append(hashlib.sha256(frame.tobytes()).hexdigest())
        index += 1
    capture.release()
    return hashes


def frame_difference(path_a: Path, path_b: Path) -> dict[str, float]:
    """Per-frame mean absolute difference between two renders."""

    first = cv2.VideoCapture(str(path_a))
    second = cv2.VideoCapture(str(path_b))
    if not first.isOpened() or not second.isOpened():
        raise ConfigurationError("Cannot open one of the videos for comparison")
    differences: list[float] = []
    while True:
        ok_a, frame_a = first.read()
        ok_b, frame_b = second.read()
        if not ok_a or not ok_b:
            break
        if frame_a.shape != frame_b.shape:
            frame_b = cv2.resize(frame_b, (frame_a.shape[1], frame_a.shape[0]))
        differences.append(float(cv2.absdiff(frame_a, frame_b).mean()))
    first.release()
    second.release()
    if not differences:
        raise ConfigurationError("No frames compared")
    return {
        "frames": float(len(differences)),
        "max_mean": round(max(differences), 4),
        "mean": round(sum(differences) / len(differences), 4),
    }

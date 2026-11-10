from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigurationError
from .utils import sha256_bytes, sha256_json

SUPPORTED_OPERATIONS = ("crop", "scale", "pad")


@dataclass(frozen=True)
class CorrectionResult:
    content: bytes
    content_hash: str
    filter_graph: str
    command_hash: str
    encoder_version: str
    operations: tuple[dict[str, Any], ...]


def build_filter_graph(operations: list[dict[str, Any]]) -> str:
    """Translate declarative correction steps into one deterministic ffmpeg chain."""

    if not operations:
        raise ValueError("At least one correction operation is required")
    filters: list[str] = []
    for index, operation in enumerate(operations):
        kind = operation.get("type")
        if kind not in SUPPORTED_OPERATIONS:
            raise ValueError(
                f"Unsupported correction operation {kind!r}; "
                f"expected one of {SUPPORTED_OPERATIONS}"
            )
        try:
            width = int(operation["width"])
            height = int(operation["height"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                f"Operation {index} ({kind}) requires integer width and height"
            ) from error
        if width <= 0 or height <= 0:
            raise ValueError(f"Operation {index} ({kind}) requires positive dimensions")
        if kind == "crop":
            filters.append(
                f"crop={width}:{height}:{int(operation.get('x', 0))}:"
                f"{int(operation.get('y', 0))}"
            )
        elif kind == "scale":
            flags = str(operation.get("flags", "lanczos"))
            filters.append(f"scale={width}:{height}:flags={flags}")
        else:
            colour = str(operation.get("color", "black"))
            filters.append(
                f"pad={width}:{height}:{int(operation.get('x', 0))}:"
                f"{int(operation.get('y', 0))}:{colour}"
            )
    return ",".join(filters)


class FFmpegCorrectionAdapter:
    """Applies crop, scale and pad corrections to a still image."""

    profile_key = "ffmpeg-correction"

    def __init__(self, executable: str | None = None, timeout_seconds: int = 120):
        self.executable = executable or self.find_executable()
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def find_executable() -> str:
        from .adapters.encode import FFmpegEncodeAdapter

        return FFmpegEncodeAdapter.find_executable()

    def version(self) -> str:
        completed = subprocess.run(
            [self.executable, "-version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout:
            raise ConfigurationError("ffmpeg is not usable")
        return completed.stdout.splitlines()[0].strip()

    def correct(
        self,
        source: Path | str,
        operations: list[dict[str, Any]],
    ) -> CorrectionResult:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise ConfigurationError(f"Correction source not found: {source_path}")
        filter_graph = build_filter_graph(operations)
        argv = [
            self.executable,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source_path),
            "-vf",
            filter_graph,
            "-frames:v",
            "1",
        ]
        with tempfile.TemporaryDirectory(prefix="pinaka-correct-") as directory:
            target = Path(directory) / "corrected.png"
            completed = subprocess.run(
                [*argv, str(target)],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            if completed.returncode != 0 or not target.is_file():
                detail = (completed.stderr or "").strip().splitlines()
                raise ConfigurationError(
                    "Correction failed: "
                    + (detail[-1] if detail else "ffmpeg returned no output")
                )
            content = target.read_bytes()
        return CorrectionResult(
            content=content,
            content_hash=sha256_bytes(content),
            filter_graph=filter_graph,
            command_hash=sha256_json({"argv": argv, "type": "ffmpeg-correction"}),
            encoder_version=self.version(),
            operations=tuple(operations),
        )


class FFmpegCompositeAdapter:
    """Places one image over another with explicit geometry and opacity."""

    profile_key = "ffmpeg-composite"

    def __init__(self, executable: str | None = None, timeout_seconds: int = 120):
        self.executable = executable or FFmpegCorrectionAdapter.find_executable()
        self.timeout_seconds = timeout_seconds

    def compose(
        self,
        background: Path | str,
        overlay: Path | str,
        *,
        x: int = 0,
        y: int = 0,
        width: int | None = None,
        height: int | None = None,
        opacity: float = 1.0,
    ) -> CorrectionResult:
        background_path = Path(background).expanduser().resolve()
        overlay_path = Path(overlay).expanduser().resolve()
        for path in (background_path, overlay_path):
            if not path.is_file():
                raise ConfigurationError(f"Composite input not found: {path}")
        if not 0 < opacity <= 1:
            raise ValueError("opacity must be greater than 0 and at most 1")

        overlay_chain = []
        if width and height:
            overlay_chain.append(f"scale={width}:{height}:flags=lanczos")
        overlay_chain.append("format=rgba")
        if opacity < 1:
            overlay_chain.append(f"colorchannelmixer=aa={opacity}")
        filter_graph = (
            f"[1:v]{','.join(overlay_chain)}[ov];[0:v][ov]overlay={x}:{y}:format=auto"
        )
        argv = [
            self.executable,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(background_path),
            "-i",
            str(overlay_path),
            "-filter_complex",
            filter_graph,
            "-frames:v",
            "1",
        ]
        with tempfile.TemporaryDirectory(prefix="pinaka-compose-") as directory:
            target = Path(directory) / "composed.png"
            completed = subprocess.run(
                [*argv, str(target)],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            if completed.returncode != 0 or not target.is_file():
                detail = (completed.stderr or "").strip().splitlines()
                raise ConfigurationError(
                    "Composite failed: "
                    + (detail[-1] if detail else "ffmpeg returned no output")
                )
            content = target.read_bytes()
        return CorrectionResult(
            content=content,
            content_hash=sha256_bytes(content),
            filter_graph=filter_graph,
            command_hash=sha256_json({"argv": argv, "type": "ffmpeg-composite"}),
            encoder_version=FFmpegCorrectionAdapter(self.executable).version(),
            operations=(
                {
                    "type": "composite",
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "opacity": opacity,
                },
            ),
        )

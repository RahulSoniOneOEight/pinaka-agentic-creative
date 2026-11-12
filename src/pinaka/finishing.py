"""FFmpeg finishing operations: grade, key/composite and speed change.

Filter strings are built by pure functions so they can be tested without
invoking FFmpeg; the adapter only adds process execution and hashing.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigurationError
from .utils import sha256_bytes, sha256_json

COLOUR = re.compile(r"^(0x[0-9A-Fa-f]{6}|[A-Za-z]+)$")


@dataclass(frozen=True)
class FinishResult:
    content: bytes
    content_hash: str
    filter_graph: str
    command_hash: str
    encoder_version: str


def build_grade_filter(
    *,
    lut_filename: str | None = None,
    brightness: float = 0.0,
    saturation: float = 1.0,
    contrast: float = 1.0,
    gamma: float = 1.0,
) -> str:
    if lut_filename and ("/" in lut_filename or "\\" in lut_filename):
        raise ValueError("LUT filename must be a bare file name inside the work dir")
    if not -1.0 <= brightness <= 1.0:
        raise ValueError("brightness must be between -1 and 1")
    if saturation < 0 or contrast < 0 or gamma <= 0:
        raise ValueError("saturation, contrast and gamma must be positive")
    filters = []
    if lut_filename:
        filters.append(f"lut3d={lut_filename}")
    if (brightness, saturation, contrast, gamma) != (0.0, 1.0, 1.0, 1.0):
        filters.append(
            f"eq=brightness={brightness:g}:saturation={saturation:g}:"
            f"contrast={contrast:g}:gamma={gamma:g}"
        )
    if not filters:
        raise ValueError("A grade needs a LUT or at least one adjusted value")
    filters.append("format=yuv420p")
    return ",".join(filters)


def build_key_filter(
    *,
    key_color: str = "0x00FF00",
    similarity: float = 0.1,
    blend: float = 0.1,
    background_label: str = "0:v",
) -> str:
    if not COLOUR.match(key_color):
        raise ValueError("key_color must be 0xRRGGBB or a named colour")
    for label, value in (("similarity", similarity), ("blend", blend)):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{label} must be between 0 and 1")
    return (
        f"[1:v]chromakey={key_color}:{similarity:g}:{blend:g},"
        f"despill=type=green[fg];"
        f"[{background_label}][fg]overlay=shortest=1,format=yuv420p[outv]"
    )


def atempo_chain(speed: float) -> str:
    """FFmpeg's atempo accepts 0.5-2.0 per instance; chain for extremes."""

    if speed <= 0:
        raise ValueError("speed must be positive")
    factors: list[float] = []
    remaining = float(speed)
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ",".join(f"atempo={factor:g}" for factor in factors)


def build_speed_filters(speed: float) -> tuple[str, str]:
    if not 0.25 <= speed <= 4.0:
        raise ValueError("speed must be between 0.25 and 4")
    video = f"setpts=PTS/{speed:g}"
    audio = atempo_chain(speed)
    return video, audio


class FFmpegFinishAdapter:
    """Runs grade, key/composite and speed operations on a finished clip."""

    profile_key = "ffmpeg-finish"

    def __init__(self, executable: str | None = None, timeout_seconds: int = 900):
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

    def _run(
        self,
        *,
        inputs: list[Path],
        filter_graph: str,
        output: Path,
        cwd: Path | None = None,
        map_audio: bool = True,
    ) -> FinishResult:
        command = [self.executable, "-y", "-hide_banner", "-loglevel", "error"]
        for path in inputs:
            command.extend(["-i", str(path)])
        command.extend(["-filter_complex", filter_graph])
        if "[outv]" in filter_graph:
            command.extend(["-map", "[outv]"])
        else:
            command.extend(["-map", "0:v:0"])
        if map_audio:
            command.extend(["-map", "0:a:0?"])
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(output),
            ]
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
            cwd=str(cwd) if cwd else None,
        )
        if completed.returncode != 0 or not output.is_file():
            detail = (completed.stderr or "").strip().splitlines()
            raise ConfigurationError(
                "Finishing failed: " + (detail[-1] if detail else "ffmpeg failed")
            )
        content = output.read_bytes()
        return FinishResult(
            content=content,
            content_hash=sha256_bytes(content),
            filter_graph=filter_graph,
            command_hash=sha256_json({"argv": command}),
            encoder_version=self.version(),
        )

    def grade(
        self,
        source: Path | str,
        *,
        lut_path: Path | str | None = None,
        brightness: float = 0.0,
        saturation: float = 1.0,
        contrast: float = 1.0,
        gamma: float = 1.0,
    ) -> FinishResult:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise ConfigurationError(f"Grade source not found: {source_path}")
        with tempfile.TemporaryDirectory(prefix="pinaka-grade-") as temp:
            directory = Path(temp)
            lut_name = None
            if lut_path is not None:
                lut = Path(lut_path).expanduser().resolve()
                if not lut.is_file() or lut.suffix.lower() != ".cube":
                    raise ConfigurationError("A grade LUT must be a .cube file")
                lut_name = "grade.cube"
                shutil.copyfile(lut, directory / lut_name)
            filters = build_grade_filter(
                lut_filename=lut_name,
                brightness=brightness,
                saturation=saturation,
                contrast=contrast,
                gamma=gamma,
            )
            output = directory / "graded.mp4"
            return self._run(
                inputs=[source_path],
                filter_graph=f"[0:v]{filters}[outv]",
                output=output,
                cwd=directory,
            )

    def key_composite(
        self,
        *,
        background: Path | str,
        foreground: Path | str,
        key_color: str = "0x00FF00",
        similarity: float = 0.1,
        blend: float = 0.1,
    ) -> FinishResult:
        background_path = Path(background).expanduser().resolve()
        foreground_path = Path(foreground).expanduser().resolve()
        for path in (background_path, foreground_path):
            if not path.is_file():
                raise ConfigurationError(f"Key input not found: {path}")
        filters = build_key_filter(
            key_color=key_color, similarity=similarity, blend=blend
        )
        with tempfile.TemporaryDirectory(prefix="pinaka-key-") as temp:
            output = Path(temp) / "keyed.mp4"
            return self._run(
                inputs=[background_path, foreground_path],
                filter_graph=filters,
                output=output,
            )

    def change_speed(self, source: Path | str, *, speed: float) -> FinishResult:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise ConfigurationError(f"Speed source not found: {source_path}")
        video, audio = build_speed_filters(speed)
        filters = f"[0:v]{video}[outv];[0:a]{audio}[outa]"
        with tempfile.TemporaryDirectory(prefix="pinaka-speed-") as temp:
            output = Path(temp) / "speed.mp4"
            command = [
                self.executable,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source_path),
                "-filter_complex",
                filters,
                "-map",
                "[outv]",
                "-map",
                "[outa]",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(output),
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            if completed.returncode != 0 or not output.is_file():
                detail = (completed.stderr or "").strip().splitlines()
                raise ConfigurationError(
                    "Speed change failed: "
                    + (detail[-1] if detail else "ffmpeg failed")
                )
            content = output.read_bytes()
            return FinishResult(
                content=content,
                content_hash=sha256_bytes(content),
                filter_graph=filters,
                command_hash=sha256_json({"argv": command}),
                encoder_version=self.version(),
            )

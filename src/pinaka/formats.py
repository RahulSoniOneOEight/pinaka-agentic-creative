from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters.encode import FFmpegEncodeAdapter
from .utils import sha256_bytes, sha256_file, sha256_json


@dataclass(frozen=True)
class FormatSpec:
    key: str
    width_px: int
    height_px: int
    label: str


FORMAT_SPECS: tuple[FormatSpec, ...] = (
    FormatSpec("9x16", 1080, 1920, "9:16 vertical master"),
    FormatSpec("1x1", 1080, 1080, "1:1 square"),
    FormatSpec("16x9", 1920, 1080, "16:9 landscape"),
    FormatSpec("4x5", 1080, 1350, "4:5 portrait"),
)


@dataclass(frozen=True)
class DerivedFormat:
    key: str
    width_px: int
    height_px: int
    content: bytes
    content_hash: str
    metadata: dict[str, Any]
    command_hash: str


def format_output_name(source_stem: str, key: str) -> str:
    return f"{source_stem}-{key}.mp4"


def _probe(ffmpeg: str, path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
        shell=False,
    )
    text = completed.stderr
    duration_match = re.search(r"Duration: (\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)", text)
    video_match = None
    audio_match = None
    for line in text.splitlines():
        if "Video:" in line and "Stream" in line and video_match is None:
            video_match = re.search(
                r"Video: (\w+).*?(\d{2,5})x(\d{2,5}).*?([\d.]+) fps", line
            )
        if "Audio:" in line and "Stream" in line and audio_match is None:
            audio_match = re.search(r"Audio: (\w+).*?, (\d+) Hz, (\w+)", line)
    if duration_match is None or video_match is None:
        raise RuntimeError("FFmpeg could not read the derived format metadata")
    hours, minutes, seconds = duration_match.groups()
    codec, width, height, fps = video_match.groups()
    return {
        "duration_seconds": round(
            int(hours) * 3600 + int(minutes) * 60 + float(seconds), 3
        ),
        "video_codec": codec,
        "width_px": int(width),
        "height_px": int(height),
        "fps": float(fps),
        "has_audio": audio_match is not None,
        "audio_codec": audio_match.group(1) if audio_match else None,
        "audio_sample_rate": int(audio_match.group(2)) if audio_match else None,
        "audio_channels": audio_match.group(3) if audio_match else None,
    }


class FormatDerivationAdapter:
    """Local multi-format derivatives with deterministic padding and provenance."""

    profile_key = "ffmpeg-formats"

    def __init__(self, executable: str | None = None, timeout_seconds: int = 600):
        self.executable = executable or FFmpegEncodeAdapter.find_executable()
        self.timeout_seconds = timeout_seconds

    def derive(self, video_path: Path | str) -> list[DerivedFormat]:
        source = Path(video_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Video not found: {source}")
        source_hash = sha256_file(source)
        results: list[DerivedFormat] = []
        for spec in FORMAT_SPECS:
            if spec.key == "9x16":
                content = source.read_bytes()
                metadata = _probe(self.executable, source)
                command_hash = sha256_json(
                    {
                        "source_hash": source_hash,
                        "format": spec.key,
                        "mode": "copy",
                    }
                )
            else:
                with tempfile.TemporaryDirectory(prefix="pinaka-format-") as temp:
                    output = Path(temp) / format_output_name(source.stem, spec.key)
                    filter_graph = (
                        f"[0:v]scale={spec.width_px}:{spec.height_px}:"
                        "force_original_aspect_ratio=increase,"
                        f"crop={spec.width_px}:{spec.height_px},"
                        "boxblur=20:5[bg];"
                        f"[0:v]scale={spec.width_px}:{spec.height_px}:"
                        "force_original_aspect_ratio=decrease[fg];"
                        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
                    )
                    command = [
                        self.executable,
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-i",
                        str(source),
                        "-filter_complex",
                        filter_graph,
                        "-map",
                        "[v]",
                        "-map",
                        "0:a?",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "medium",
                        "-crf",
                        "18",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "copy",
                        "-movflags",
                        "+faststart",
                        str(output),
                    ]
                    completed = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=self.timeout_seconds,
                        check=False,
                        shell=False,
                    )
                    if completed.returncode != 0 or not output.is_file():
                        detail = (completed.stderr or completed.stdout or "").strip()[
                            :500
                        ]
                        raise RuntimeError(
                            f"Format {spec.key} failed with exit "
                            f"{completed.returncode}: {detail}"
                        )
                    content = output.read_bytes()
                    metadata = _probe(self.executable, output)
                    command_hash = sha256_json(
                        {
                            "source_hash": source_hash,
                            "format": spec.key,
                            "width_px": spec.width_px,
                            "height_px": spec.height_px,
                            "filter": filter_graph,
                            "codec": "libx264",
                            "crf": 18,
                            "background": "blurred_crop",
                        }
                    )
            if (
                metadata.get("width_px") != spec.width_px
                or metadata.get("height_px") != spec.height_px
            ):
                raise RuntimeError(
                    f"Derived format {spec.key} has unexpected dimensions"
                )
            results.append(
                DerivedFormat(
                    key=spec.key,
                    width_px=spec.width_px,
                    height_px=spec.height_px,
                    content=content,
                    content_hash=sha256_bytes(content),
                    metadata=metadata,
                    command_hash=command_hash,
                )
            )
        return results

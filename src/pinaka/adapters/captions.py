from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ConfigurationError
from ..utils import sha256_bytes, sha256_file, sha256_json
from .encode import FFmpegEncodeAdapter


@dataclass(frozen=True)
class CaptionedVideo:
    content: bytes
    content_hash: str
    metadata: dict[str, Any]
    command_hash: str
    encoder_version: str
    source_video_hash: str
    subtitle_hash: str


class CaptionMuxAdapter:
    """Muxes an SRT sidecar into an MP4 as a soft mov_text subtitle track."""

    profile_key = "ffmpeg-captions"

    def __init__(self, executable: str | None = None, timeout_seconds: int = 300):
        self.executable = executable or FFmpegEncodeAdapter.find_executable()
        self.timeout_seconds = timeout_seconds

    def version(self) -> str:
        completed = subprocess.run(
            [self.executable, "-version"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            shell=False,
        )
        output = (completed.stdout or completed.stderr).strip()
        if completed.returncode != 0 or not output:
            raise ConfigurationError("FFmpeg caption version probe failed")
        return output.splitlines()[0]

    def _probe(self, path: Path) -> dict[str, Any]:
        completed = subprocess.run(
            [self.executable, "-hide_banner", "-i", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            shell=False,
        )
        text = completed.stderr
        duration_match = re.search(r"Duration: (\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)", text)
        video_match = None
        audio_match = None
        subtitle_match = None
        for line in text.splitlines():
            if "Video:" in line and "Stream" in line and video_match is None:
                video_match = re.search(
                    r"Video: (\w+).*?(\d{2,5})x(\d{2,5}).*?([\d.]+) fps", line
                )
            if "Audio:" in line and "Stream" in line and audio_match is None:
                audio_match = re.search(r"Audio: (\w+).*?, (\d+) Hz, (\w+)", line)
            if "Subtitle:" in line and subtitle_match is None:
                subtitle_match = re.search(r"Subtitle: (\w+)", line)
        if duration_match is None or video_match is None:
            raise RuntimeError("FFmpeg could not read captioned video metadata")
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
            "audio_codec": audio_match.group(1) if audio_match else None,
            "audio_sample_rate": int(audio_match.group(2)) if audio_match else None,
            "audio_channels": audio_match.group(3) if audio_match else None,
            "subtitle_codec": subtitle_match.group(1) if subtitle_match else None,
            "has_subtitles": subtitle_match is not None,
            "size_bytes": path.stat().st_size,
        }

    def mux(
        self,
        *,
        video_path: Path,
        srt_path: Path,
        output_path: Path,
    ) -> CaptionedVideo:
        video_path = video_path.expanduser().resolve()
        srt_path = srt_path.expanduser().resolve()
        output_path = output_path.expanduser().resolve()
        if not video_path.is_file():
            raise FileNotFoundError(f"Video not found: {video_path}")
        if not srt_path.is_file():
            raise FileNotFoundError(f"SRT not found: {srt_path}")
        if output_path.exists():
            raise FileExistsError(f"Refusing to overwrite: {output_path}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        version = self.version()
        command = [
            self.executable,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-i",
            str(srt_path),
            "-map",
            "0",
            "-map",
            "1",
            "-c",
            "copy",
            "-c:s",
            "mov_text",
            "-metadata:s:s:0",
            "language=eng",
            "-disposition:s:0",
            "default",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
            shell=False,
        )
        if completed.returncode != 0 or not output_path.is_file():
            detail = (completed.stderr or completed.stdout or "").strip()[:500]
            raise RuntimeError(
                f"FFmpeg caption mux failed with exit {completed.returncode}: {detail}"
            )
        content = output_path.read_bytes()
        metadata = self._probe(output_path)
        if not metadata["has_subtitles"]:
            raise RuntimeError("Captioned output has no subtitle stream")
        source_video_hash = sha256_file(video_path)
        subtitle_hash = sha256_file(srt_path)
        command_hash = sha256_json(
            {
                "executable": self.executable,
                "version": version,
                "video_hash": source_video_hash,
                "subtitle_hash": subtitle_hash,
                "subtitle_codec": "mov_text",
            }
        )
        return CaptionedVideo(
            content=content,
            content_hash=sha256_bytes(content),
            metadata=metadata,
            command_hash=command_hash,
            encoder_version=version,
            source_video_hash=source_video_hash,
            subtitle_hash=subtitle_hash,
        )

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..errors import ConfigurationError
from ..utils import sha256_bytes, sha256_json
from .encode import FFmpegEncodeAdapter


@dataclass(frozen=True)
class MuxedVideo:
    content: bytes
    content_hash: str
    metadata: dict[str, Any]
    loudness: dict[str, Any]
    command_hash: str
    encoder_version: str


class MuxAdapter(Protocol):
    profile_key: str

    def mux(
        self,
        *,
        video_path: Path,
        audio_path: Path,
        duration_seconds: float,
    ) -> MuxedVideo: ...


class FFmpegMuxAdapter:
    """Two-pass loudness-normalized narration mux over the silent master."""

    profile_key = "ffmpeg-mux"

    def __init__(self, executable: str | None = None, timeout_seconds: int = 600):
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
            raise ConfigurationError("FFmpeg mux version probe failed")
        return output.splitlines()[0]

    def _loudness(self, path: Path) -> dict[str, Any]:
        completed = subprocess.run(
            [
                self.executable,
                "-hide_banner",
                "-i",
                str(path),
                "-af",
                "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            shell=False,
        )
        match = re.search(r"\{\s*\"input_i\".*?\}", completed.stderr, re.DOTALL)
        if not match:
            raise RuntimeError("FFmpeg loudness measurement returned no JSON")
        return json.loads(match.group(0))

    def loudness(self, path: Path) -> dict[str, Any]:
        """Public loudness measurement used by QA."""

        return self._loudness(Path(path))

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
        for line in text.splitlines():
            if "Video:" in line and "Stream" in line and video_match is None:
                video_match = re.search(
                    r"Video: (\w+).*?(\d{2,5})x(\d{2,5}).*?([\d.]+) fps", line
                )
            if "Audio:" in line and "Stream" in line and audio_match is None:
                audio_match = re.search(r"Audio: (\w+).*?, (\d+) Hz, (\w+)", line)
        if duration_match is None or video_match is None or audio_match is None:
            raise RuntimeError("FFmpeg could not read muxed video metadata")
        hours, minutes, seconds = duration_match.groups()
        codec, width, height, fps = video_match.groups()
        audio_codec, sample_rate, channels = audio_match.groups()
        return {
            "duration_seconds": round(
                int(hours) * 3600 + int(minutes) * 60 + float(seconds), 3
            ),
            "video_codec": codec,
            "width_px": int(width),
            "height_px": int(height),
            "fps": float(fps),
            "audio_codec": audio_codec,
            "audio_sample_rate": int(sample_rate),
            "audio_channels": channels,
            "size_bytes": path.stat().st_size,
        }

    def mux(
        self,
        *,
        video_path: Path,
        audio_path: Path,
        duration_seconds: float,
    ) -> MuxedVideo:
        if not video_path.is_file():
            raise FileNotFoundError(f"Silent video not found: {video_path}")
        if not audio_path.is_file():
            raise FileNotFoundError(f"Narration audio not found: {audio_path}")
        if duration_seconds <= 0:
            raise ValueError("Duration must be positive")
        version = self.version()
        measured = self._loudness(audio_path)
        filter_graph = (
            "[1:a]"
            "loudnorm=I=-16:TP=-1.5:LRA=11:"
            f"measured_I={measured['input_i']}:"
            f"measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:"
            f"measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']},"
            "adelay=200,"
            f"apad=whole_dur={duration_seconds},"
            f"atrim=0:{duration_seconds}"
            "[a]"
        )
        with tempfile.TemporaryDirectory(prefix="pinaka-mux-") as temp:
            output = Path(temp) / "voiced-master.mp4"
            command = [
                self.executable,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(video_path),
                "-i",
                str(audio_path),
                "-filter_complex",
                filter_graph,
                "-map",
                "0:v:0",
                "-map",
                "[a]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-t",
                f"{duration_seconds}",
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
                shell=False,
            )
            if completed.returncode != 0 or not output.is_file():
                detail = (completed.stderr or completed.stdout or "").strip()[:500]
                raise RuntimeError(
                    f"FFmpeg mux failed with exit {completed.returncode}: {detail}"
                )
            content = output.read_bytes()
            metadata = self._probe(output)
            output_loudness = self._loudness(output)
        command_hash = sha256_json(
            {
                "executable": self.executable,
                "version": version,
                "video_hash": sha256_bytes(video_path.read_bytes()),
                "audio_hash": sha256_bytes(audio_path.read_bytes()),
                "duration_seconds": duration_seconds,
                "filter_graph": filter_graph,
                "audio_codec": "aac",
                "audio_bitrate": "192k",
                "sample_rate": 48000,
            }
        )
        return MuxedVideo(
            content=content,
            content_hash=sha256_bytes(content),
            metadata=metadata,
            loudness=output_loudness,
            command_hash=command_hash,
            encoder_version=version,
        )

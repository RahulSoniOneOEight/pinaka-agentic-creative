from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..errors import ConfigurationError
from ..utils import sha256_bytes, sha256_json


@dataclass(frozen=True)
class EncodedVideo:
    content: bytes
    content_hash: str
    metadata: dict[str, Any]
    command_hash: str
    encoder_version: str


class VideoEncodeAdapter(Protocol):
    profile_key: str

    def encode(
        self,
        *,
        frames_dir: Path,
        frame_count: int,
        fps: int,
        width: int,
        height: int,
    ) -> EncodedVideo: ...


class FFmpegEncodeAdapter:
    """Local H.264 encoder backed by the approved imageio-ffmpeg binary."""

    profile_key = "ffmpeg-h264"

    def __init__(self, executable: str | None = None, timeout_seconds: int = 600):
        self.executable = executable or self.find_executable()
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def find_executable() -> str:
        try:
            import imageio_ffmpeg
        except ImportError as error:
            raise ConfigurationError(
                "imageio-ffmpeg is not installed; install the optional media dependency"
            ) from error
        executable = imageio_ffmpeg.get_ffmpeg_exe()
        if not Path(executable).is_file():
            raise ConfigurationError("imageio-ffmpeg did not provide a usable binary")
        return executable

    def version(self) -> str:
        try:
            completed = subprocess.run(
                [self.executable, "-version"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ConfigurationError(
                f"FFmpeg version probe failed: {type(error).__name__}"
            ) from error
        output = (completed.stdout or completed.stderr).strip()
        if completed.returncode != 0 or not output:
            raise ConfigurationError(
                f"FFmpeg version probe exited {completed.returncode}"
            )
        return output.splitlines()[0]

    def probe(self, path: Path) -> dict[str, Any]:
        completed = subprocess.run(
            [self.executable, "-hide_banner", "-i", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            shell=False,
        )
        text = completed.stderr
        duration_match = None
        video_match = None
        format_name = None
        for line in text.splitlines():
            if "Duration:" in line and duration_match is None:
                duration_match = re.search(
                    r"Duration: (\d{2}):(\d{2}):(\d{2}(?:\.\d+)?)", line
                )
            if "Video:" in line and "Stream" in line and video_match is None:
                video_match = re.search(
                    r"Video: (\w+).*?(\d{2,5})x(\d{2,5}).*?([\d.]+) fps", line
                )
            if line.startswith("Input #0") and format_name is None:
                match = re.search(r"Input #0, ([^,]+)", line)
                if match:
                    format_name = match.group(1).strip()
        if duration_match is None or video_match is None:
            raise RuntimeError("FFmpeg could not read encoded video metadata")
        hours, minutes, seconds = duration_match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        codec, parsed_width, parsed_height, parsed_fps = video_match.groups()
        return {
            "format": format_name or "mp4",
            "duration_seconds": round(duration, 3),
            "codec": codec,
            "width_px": int(parsed_width),
            "height_px": int(parsed_height),
            "fps": float(parsed_fps),
            "has_audio": any("Audio:" in line for line in text.splitlines()),
            "size_bytes": path.stat().st_size,
        }

    def encode(
        self,
        *,
        frames_dir: Path,
        frame_count: int,
        fps: int,
        width: int,
        height: int,
    ) -> EncodedVideo:
        if frame_count < 1:
            raise ValueError("Frame count must be positive")
        if fps < 1:
            raise ValueError("Frame rate must be positive")
        frames_dir = frames_dir.resolve()
        pattern = frames_dir / "frame-%04d.png"
        if not frames_dir.is_dir():
            raise FileNotFoundError(f"Frames directory not found: {frames_dir}")
        if not (frames_dir / "frame-0000.png").is_file():
            raise FileNotFoundError("Frame sequence does not start at frame-0000.png")
        version = self.version()
        with tempfile.TemporaryDirectory(prefix="pinaka-encode-") as temp:
            output = Path(temp) / "silent-master.mp4"
            command = [
                self.executable,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-framerate",
                str(fps),
                "-start_number",
                "0",
                "-i",
                str(pattern),
                "-frames:v",
                str(frame_count),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-colorspace",
                "bt709",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-an",
                "-movflags",
                "+faststart",
                "-metadata",
                "title=Pinaka Intelligence - Virtual CFO (silent animatic)",
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
                    f"FFmpeg encode failed with exit {completed.returncode}: {detail}"
                )
            content = output.read_bytes()
            metadata = self.probe(output)
        command_hash = sha256_json(
            {
                "executable": self.executable,
                "version": version,
                "frame_count": frame_count,
                "fps": fps,
                "width": width,
                "height": height,
                "codec": "libx264",
                "crf": 18,
                "pix_fmt": "yuv420p",
            }
        )
        return EncodedVideo(
            content=content,
            content_hash=sha256_bytes(content),
            metadata=metadata,
            command_hash=command_hash,
            encoder_version=version,
        )

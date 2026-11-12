from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..adapters.encode import FFmpegEncodeAdapter
from ..utils import sha256_bytes, sha256_json

STYLE = (
    "FontName=Segoe UI,FontSize=10,PrimaryColour=&H00FFFFFF,"
    "OutlineColour=&H00000000,BorderStyle=1,Outline=1,Shadow=0,MarginV=30"
)


@dataclass(frozen=True)
class BurnedVideo:
    content: bytes
    content_hash: str
    metadata: dict[str, Any]
    command_hash: str
    encoder_version: str
    subtitle_hash: str


class BurnedCaptionAdapter:
    """Burns the SRT into the picture. The file is re-encoded, unlike the soft mux."""

    profile_key = "ffmpeg-burned-captions"

    def __init__(self, executable: str | None = None, timeout_seconds: int = 600):
        self.executable = executable or FFmpegEncodeAdapter.find_executable()
        self.timeout_seconds = timeout_seconds

    def burn(
        self,
        *,
        video_path: Path,
        srt_path: Path,
        output_path: Path,
    ) -> BurnedVideo:
        video = Path(video_path).expanduser().resolve()
        srt = Path(srt_path).expanduser().resolve()
        output = Path(output_path).expanduser().resolve()
        for source in (video, srt):
            if not source.is_file():
                raise FileNotFoundError(f"Input not found: {source}")
        subtitle_hash = sha256_bytes(srt.read_bytes())
        output.parent.mkdir(parents=True, exist_ok=True)
        # Work in a temp directory with simple names so the subtitles filter
        # needs no Windows path escaping.
        with tempfile.TemporaryDirectory(prefix="pinaka-burn-") as temp:
            work = Path(temp)
            local_video = work / "input.mp4"
            local_srt = work / "captions.srt"
            local_out = work / "output.mp4"
            shutil.copyfile(video, local_video)
            shutil.copyfile(srt, local_srt)
            command = [
                self.executable,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "input.mp4",
                "-vf",
                f"subtitles=captions.srt:force_style='{STYLE}'",
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
                "160k",
                "-movflags",
                "+faststart",
                "output.mp4",
            ]
            completed = subprocess.run(
                command,
                cwd=work,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
            )
            if completed.returncode != 0 or not local_out.is_file():
                detail = (completed.stderr or completed.stdout or "").strip()[:500]
                raise RuntimeError(
                    f"Burned caption encode failed with exit "
                    f"{completed.returncode}: {detail}"
                )
            shutil.copyfile(local_out, output)
            metadata = FFmpegEncodeAdapter(self.executable).probe(local_out)
        content = output.read_bytes()
        return BurnedVideo(
            content=content,
            content_hash=sha256_bytes(content),
            metadata={**metadata, "burned_captions": True},
            command_hash=sha256_json(
                {
                    "filter": "subtitles",
                    "style": STYLE,
                    "subtitle_hash": subtitle_hash,
                    "video_sha256": sha256_bytes(video.read_bytes()),
                }
            ),
            encoder_version=FFmpegEncodeAdapter(self.executable).version(),
            subtitle_hash=subtitle_hash,
        )

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .errors import ConfigurationError


def find_ffprobe() -> str | None:
    configured = os.environ.get("PINAKA_FFPROBE")
    if configured and Path(configured).is_file():
        return configured
    try:
        import static_ffmpeg

        static_ffmpeg.add_paths()
    except (ImportError, OSError):
        pass
    return shutil.which("ffprobe")


def _fps(value: str | None) -> float | None:
    if not value:
        return None
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        try:
            return round(float(numerator) / float(denominator), 3)
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return round(float(value), 3)
    except ValueError:
        return None


def probe_ffprobe(path: Path | str) -> dict[str, Any]:
    executable = find_ffprobe()
    if not executable:
        raise ConfigurationError("ffprobe is not available")
    completed = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(Path(path).expanduser().resolve()),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
        shell=False,
    )
    if completed.returncode != 0 or not completed.stdout:
        raise RuntimeError("ffprobe could not read the video")
    payload = json.loads(completed.stdout)
    streams = payload.get("streams", [])
    video = next(
        (stream for stream in streams if stream.get("codec_type") == "video"), None
    )
    audio = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"), None
    )
    subtitle = next(
        (stream for stream in streams if stream.get("codec_type") == "subtitle"),
        None,
    )
    if video is None:
        raise RuntimeError("ffprobe found no video stream")
    return {
        "format": payload.get("format", {}).get("format_name"),
        "duration_seconds": round(
            float(payload.get("format", {}).get("duration", 0)), 3
        ),
        "video_codec": video.get("codec_name"),
        "width_px": video.get("width"),
        "height_px": video.get("height"),
        "fps": _fps(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        "pix_fmt": video.get("pix_fmt"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_sample_rate": int(audio["sample_rate"]) if audio else None,
        "audio_channels": audio.get("channels") if audio else None,
        "has_audio": audio is not None,
        "has_subtitles": subtitle is not None,
        "subtitle_codec": subtitle.get("codec_name") if subtitle else None,
    }


def probe_mediainfo(path: Path | str) -> dict[str, Any]:
    try:
        from pymediainfo import MediaInfo
    except ImportError as error:
        raise ConfigurationError("pymediainfo is not available") from error
    parsed = MediaInfo.parse(str(Path(path).expanduser().resolve()))
    tracks = parsed.tracks
    general = next((track for track in tracks if track.track_type == "General"), None)
    video = next((track for track in tracks if track.track_type == "Video"), None)
    audio = next((track for track in tracks if track.track_type == "Audio"), None)
    text = next((track for track in tracks if track.track_type == "Text"), None)
    if general is None or video is None:
        raise RuntimeError("MediaInfo found no general or video track")
    duration_ms = float(general.duration or 0)
    return {
        "duration_seconds": round(duration_ms / 1000, 3),
        "video_codec": video.codec_id or video.format,
        "width_px": video.width,
        "height_px": video.height,
        "fps": _fps(str(video.frame_rate)) if video.frame_rate else None,
        "audio_codec": (audio.codec_id or audio.format) if audio else None,
        "audio_sample_rate": audio.sampling_rate if audio else None,
        "audio_channels": audio.channel_s if audio else None,
        "has_audio": audio is not None,
        "has_subtitles": text is not None,
        "subtitle_codec": text.format if text else None,
    }

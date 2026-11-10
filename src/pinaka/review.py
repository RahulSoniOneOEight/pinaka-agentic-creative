from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from .adapters.encode import FFmpegEncodeAdapter
from .errors import ConfigurationError
from .media_probe import probe_ffprobe, probe_mediainfo
from .models import StrictModel
from .utils import sha256_file

HEX_PATTERN = r"^#[0-9A-Fa-f]{6}$"


class OCRCheck(StrictModel):
    time_seconds: Decimal = Field(ge=0)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    expected_text: str = Field(min_length=1)
    min_similarity: float = Field(default=0.85, ge=0, le=1)


class ColorCheck(StrictModel):
    time_seconds: Decimal = Field(ge=0)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    expected_hex: str = Field(pattern=HEX_PATTERN)
    tolerance: int = Field(default=45, ge=0, le=441)


class VideoReviewSpec(StrictModel):
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    duration_seconds: Decimal = Field(gt=0)
    fps: Decimal = Field(gt=0)
    video_codec: str = "h264"
    audio_codec: str = "aac"
    ocr: tuple[OCRCheck, ...] = ()
    colors: tuple[ColorCheck, ...] = ()
    expected_scene_cuts: tuple[Decimal, ...] = ()
    scene_threshold: Decimal = Field(default=Decimal("0.10"), ge=0, le=1)
    opencv_scene_threshold: Decimal = Field(default=Decimal("0.25"), ge=0, le=1)
    scene_detector: Literal["opencv", "ffmpeg"] = "opencv"
    black_min_duration: Decimal = Field(default=Decimal("0.10"), ge=0)
    freeze_min_duration: Decimal = Field(default=Decimal("1.0"), ge=0)
    silence_min_duration: Decimal = Field(default=Decimal("0.5"), ge=0)
    freeze_policy: Literal["warn", "ignore"] = "warn"


def _check(
    name: str,
    status: str,
    detail: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "check": name,
        "status": status,
        "detail": detail,
        "evidence": evidence or {},
    }


def _codec_is(value: Any, *aliases: str) -> bool:
    text = str(value).casefold()
    return any(text == alias or text.startswith(alias) for alias in aliases)


def _overall(checks: list[dict[str, Any]]) -> str:
    statuses = {check["status"] for check in checks}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def text_similarity(expected: str, observed: str) -> float:
    return SequenceMatcher(
        None, normalize_text(expected), normalize_text(observed)
    ).ratio()


def text_matches(expected: str, observed: str, minimum: float) -> bool:
    expected_tokens = normalize_text(expected).split()
    observed_tokens = set(normalize_text(observed).split())
    if expected_tokens and all(token in observed_tokens for token in expected_tokens):
        return True
    return text_similarity(expected, observed) >= minimum


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = value.lstrip("#")
    return tuple(int(raw[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def rgb_distance(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    return sum((a - b) ** 2 for a, b in zip(first, second, strict=True)) ** 0.5


def _run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        shell=False,
    )


def find_tesseract() -> str:
    import os

    candidates = [
        os.environ.get("PINAKA_TESSERACT"),
        shutil.which("tesseract"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise ConfigurationError(
        "Tesseract was not found. Set PINAKA_TESSERACT or install Tesseract OCR."
    )


def _probe(ffmpeg: str, path: Path) -> dict[str, Any]:
    completed = _run([ffmpeg, "-hide_banner", "-i", str(path)])
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
        raise RuntimeError("FFmpeg could not read video metadata")
    hours, minutes, seconds = duration_match.groups()
    codec, width, height, fps = video_match.groups()
    metadata = {
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
    return metadata


def _black_events(ffmpeg: str, path: Path, minimum: Decimal) -> list[dict[str, float]]:
    completed = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(path),
            "-vf",
            f"blackdetect=d={minimum}:pix_th=0.10",
            "-an",
            "-f",
            "null",
            "-",
        ]
    )
    starts = [
        float(match) for match in re.findall(r"black_start:([\d.]+)", completed.stderr)
    ]
    ends = [
        float(match) for match in re.findall(r"black_end:([\d.]+)", completed.stderr)
    ]
    durations = [
        float(match)
        for match in re.findall(r"black_duration:([\d.]+)", completed.stderr)
    ]
    return [
        {"start": start, "end": end, "duration": duration}
        for start, end, duration in zip(starts, ends, durations, strict=False)
    ]


def _freeze_events(ffmpeg: str, path: Path, minimum: Decimal) -> list[dict[str, float]]:
    completed = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(path),
            "-vf",
            f"freezedetect=n=-60dB:d={minimum}",
            "-an",
            "-f",
            "null",
            "-",
        ]
    )
    starts = [
        float(match)
        for match in re.findall(r"freeze_start: ([\d.]+)", completed.stderr)
    ]
    ends = [
        float(match) for match in re.findall(r"freeze_end: ([\d.]+)", completed.stderr)
    ]
    durations = [
        float(match)
        for match in re.findall(r"freeze_duration: ([\d.]+)", completed.stderr)
    ]
    return [
        {"start": start, "end": end, "duration": duration}
        for start, end, duration in zip(starts, ends, durations, strict=False)
    ]


def _silence_events(
    ffmpeg: str, path: Path, minimum: Decimal
) -> list[dict[str, float]]:
    completed = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            f"silencedetect=n=-50dB:d={minimum}",
            "-vn",
            "-f",
            "null",
            "-",
        ]
    )
    starts = [
        float(match)
        for match in re.findall(r"silence_start: ([\d.]+)", completed.stderr)
    ]
    ends = [
        float(match) for match in re.findall(r"silence_end: ([\d.]+)", completed.stderr)
    ]
    durations = [
        float(match)
        for match in re.findall(r"silence_duration: ([\d.]+)", completed.stderr)
    ]
    return [
        {
            "start": start,
            "end": ends[index] if index < len(ends) else None,
            "duration": durations[index] if index < len(durations) else None,
        }
        for index, start in enumerate(starts)
    ]


def _scene_cuts(ffmpeg: str, path: Path, threshold: Decimal) -> list[float]:
    completed = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(path),
            "-vf",
            f"select='gt(scene,{threshold})',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ]
    )
    return [
        float(match) for match in re.findall(r"pts_time:([\d.]+)", completed.stderr)
    ]


def _scene_cuts_opencv(path: Path, threshold: Decimal) -> list[float] | None:
    try:
        import cv2
    except ImportError:
        return None
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return None
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    previous = None
    index = 0
    changes: list[float] = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            small = cv2.resize(frame, (160, 90))
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            histogram = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
            cv2.normalize(histogram, histogram)
            if previous is not None:
                difference = cv2.compareHist(
                    previous, histogram, cv2.HISTCMP_BHATTACHARYYA
                )
                if difference > float(threshold):
                    changes.append(round(index / fps, 3))
            previous = histogram
            index += 1
    finally:
        capture.release()
    return changes


def _extract_crop(
    ffmpeg: str,
    video: Path,
    check: OCRCheck,
    output: Path,
) -> None:
    completed = _run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(check.time_seconds),
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            f"crop={check.width}:{check.height}:{check.x}:{check.y}",
            str(output),
        ]
    )
    if completed.returncode != 0 or not output.is_file():
        raise RuntimeError("FFmpeg could not extract the OCR crop")


def _ocr(tesseract: str, image: Path) -> str:
    completed = _run(
        [tesseract, str(image), "stdout", "--psm", "6"],
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError("Tesseract OCR failed")
    return completed.stdout or ""


def _frame_rgb(ffmpeg: str, video: Path, time_seconds: Decimal) -> bytes:
    completed = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(time_seconds),
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        timeout=120,
        check=False,
        shell=False,
    )
    if completed.returncode != 0 or not completed.stdout:
        raise RuntimeError("FFmpeg could not decode a review frame")
    return completed.stdout


def _sample_rgb(
    raw: bytes, width: int, height: int, x: int, y: int, radius: int = 2
) -> tuple[int, int, int]:
    if x >= width or y >= height:
        raise ValueError("Color sample lies outside the video frame")
    pixels: list[tuple[int, int, int]] = []
    for yy in range(max(0, y - radius), min(height, y + radius + 1)):
        for xx in range(max(0, x - radius), min(width, x + radius + 1)):
            offset = (yy * width + xx) * 3
            pixels.append(tuple(raw[offset : offset + 3]))  # type: ignore[arg-type]
    return tuple(
        sum(pixel[index] for pixel in pixels) // len(pixels) for index in range(3)
    )  # type: ignore[return-value]


def review_video(
    video_path: Path | str,
    spec: VideoReviewSpec,
    *,
    ffmpeg_exe: str | None = None,
    tesseract_exe: str | None = None,
) -> dict[str, Any]:
    path = Path(video_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Video not found: {path}")
    ffmpeg = ffmpeg_exe or FFmpegEncodeAdapter.find_executable()
    tesseract = tesseract_exe or (find_tesseract() if spec.ocr else None)
    checks: list[dict[str, Any]] = []
    metadata = _probe(ffmpeg, path)

    expected_meta = {
        "width_px": spec.width_px,
        "height_px": spec.height_px,
        "fps": float(spec.fps),
        "video_codec": spec.video_codec,
        "audio_codec": spec.audio_codec,
        "has_audio": True,
    }
    observed_meta = {key: metadata.get(key) for key in expected_meta}
    duration_ok = (
        abs(float(metadata["duration_seconds"]) - float(spec.duration_seconds)) <= 0.05
    )
    if observed_meta == expected_meta and duration_ok:
        checks.append(
            _check(
                "container.profile",
                "pass",
                "Container streams and duration match the review contract.",
                {"observed": metadata, "expected": expected_meta},
            )
        )
    else:
        checks.append(
            _check(
                "container.profile",
                "fail",
                "Container streams or duration do not match the review contract.",
                {
                    "observed": observed_meta,
                    "expected": expected_meta,
                    "duration_seconds": metadata["duration_seconds"],
                    "expected_duration": str(spec.duration_seconds),
                },
            )
        )

    for label, prober in (
        ("ffprobe", probe_ffprobe),
        ("mediainfo", probe_mediainfo),
    ):
        try:
            independent = prober(path)
        except (ConfigurationError, RuntimeError, OSError) as error:
            checks.append(
                _check(
                    f"container.{label}",
                    "warn",
                    f"{label} is unavailable or could not read the file.",
                    {"error": type(error).__name__},
                )
            )
            continue
        independent_ok = (
            abs(float(independent["duration_seconds"]) - float(spec.duration_seconds))
            <= 0.05
            and independent["width_px"] == spec.width_px
            and independent["height_px"] == spec.height_px
            and independent["fps"] is not None
            and abs(float(independent["fps"]) - float(spec.fps)) <= 0.01
            and _codec_is(independent["video_codec"], "h264", "avc", "avc1")
            and independent["has_audio"]
            and _codec_is(independent["audio_codec"], "aac", "mp4a")
        )
        checks.append(
            _check(
                f"container.{label}",
                "pass" if independent_ok else "fail",
                f"{label} independently confirms the container profile."
                if independent_ok
                else f"{label} disagrees with the review contract.",
                {"observed": independent},
            )
        )

    black = _black_events(ffmpeg, path, spec.black_min_duration)
    checks.append(
        _check(
            "black.frames",
            "fail" if black else "pass",
            "Black frame spans were detected." if black else "No black frame spans.",
            {"spans": black},
        )
    )

    freezes = _freeze_events(ffmpeg, path, spec.freeze_min_duration)
    if freezes and spec.freeze_policy == "warn":
        checks.append(
            _check(
                "freeze.scenes",
                "warn",
                "Static scene spans are expected for this typographic animatic.",
                {"spans": freezes},
            )
        )
    elif freezes:
        checks.append(
            _check(
                "freeze.scenes",
                "pass",
                "Static spans were ignored by policy.",
                {"spans": freezes},
            )
        )
    else:
        checks.append(_check("freeze.scenes", "pass", "No frozen spans."))

    silences = _silence_events(ffmpeg, path, spec.silence_min_duration)
    mid_silence = [
        span
        for span in silences
        if span["start"] > 0.5
        and (span["end"] is None or span["end"] < float(spec.duration_seconds) - 0.5)
    ]
    checks.append(
        _check(
            "audio.silence",
            "fail" if mid_silence else "pass",
            "Mid-program silence was detected."
            if mid_silence
            else "Silence is limited to the lead-in or tail.",
            {"spans": silences, "mid_program": mid_silence},
        )
    )

    detector_used = spec.scene_detector
    cuts: list[float] | None = None
    if spec.scene_detector == "opencv":
        cuts = _scene_cuts_opencv(path, spec.opencv_scene_threshold)
        if cuts is None:
            detector_used = "ffmpeg-fallback"
    if cuts is None:
        cuts = _scene_cuts(ffmpeg, path, spec.scene_threshold)
        if spec.scene_detector == "opencv":
            detector_used = "ffmpeg-fallback"
        else:
            detector_used = "ffmpeg"
    missing_cuts = [
        float(expected)
        for expected in spec.expected_scene_cuts
        if not any(abs(detected - float(expected)) <= 0.25 for detected in cuts)
    ]
    checks.append(
        _check(
            "scene.cuts",
            "warn" if missing_cuts else "pass",
            "Some expected cuts were not detected; OCR still confirms scene copy."
            if missing_cuts
            else "Expected scene cuts were detected.",
            {
                "detector": detector_used,
                "detected": cuts,
                "expected": [str(item) for item in spec.expected_scene_cuts],
                "missing": missing_cuts,
                "threshold": str(spec.scene_threshold),
                "opencv_threshold": str(spec.opencv_scene_threshold),
            },
        )
    )

    if spec.ocr:
        assert tesseract is not None
        with tempfile.TemporaryDirectory(prefix="pinaka-review-") as temp:
            for index, check in enumerate(spec.ocr, start=1):
                image = Path(temp) / f"ocr-{index}.png"
                _extract_crop(ffmpeg, path, check, image)
                observed_text = _ocr(tesseract, image)
                matched = text_matches(
                    check.expected_text, observed_text, check.min_similarity
                )
                checks.append(
                    _check(
                        f"text.ocr.{index}",
                        "pass" if matched else "fail",
                        f"On-screen text at {check.time_seconds}s "
                        + ("matches." if matched else "does not match."),
                        {
                            "time_seconds": str(check.time_seconds),
                            "crop": [
                                check.x,
                                check.y,
                                check.width,
                                check.height,
                            ],
                            "expected": check.expected_text,
                            "observed": observed_text.strip(),
                            "similarity": round(
                                text_similarity(check.expected_text, observed_text),
                                3,
                            ),
                        },
                    )
                )

    if spec.colors:
        frames: dict[str, bytes] = {}
        for index, check in enumerate(spec.colors, start=1):
            key = str(check.time_seconds)
            if key not in frames:
                frames[key] = _frame_rgb(ffmpeg, path, check.time_seconds)
            observed = _sample_rgb(
                frames[key], spec.width_px, spec.height_px, check.x, check.y
            )
            expected = _hex_to_rgb(check.expected_hex)
            distance = rgb_distance(observed, expected)
            passed = distance <= check.tolerance
            checks.append(
                _check(
                    f"color.brand.{index}",
                    "pass" if passed else "fail",
                    f"Sampled brand colour at {check.time_seconds}s "
                    + (
                        "matches within tolerance."
                        if passed
                        else "is outside tolerance."
                    ),
                    {
                        "time_seconds": str(check.time_seconds),
                        "point": [check.x, check.y],
                        "observed_hex": "#{:02X}{:02X}{:02X}".format(*observed),
                        "expected_hex": check.expected_hex.upper(),
                        "distance": round(distance, 2),
                        "tolerance": check.tolerance,
                    },
                )
            )

    ffmpeg_version = _run([ffmpeg, "-version"]).stdout.splitlines()
    tesseract_version = (
        _run([tesseract, "--version"]).stdout.splitlines() if tesseract else []
    )
    return {
        "schema_version": 1,
        "video": str(path),
        "sha256": sha256_file(path),
        "metadata": metadata,
        "tool_versions": {
            "ffmpeg": ffmpeg_version[0] if ffmpeg_version else "unknown",
            "tesseract": tesseract_version[0] if tesseract_version else None,
        },
        "checks": checks,
        "overall": _overall(checks),
    }


def load_spec(path: Path | str) -> VideoReviewSpec:
    return VideoReviewSpec.model_validate_json(Path(path).read_text(encoding="utf-8"))


def write_report(report: dict[str, Any], path: Path | str) -> Path:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return destination

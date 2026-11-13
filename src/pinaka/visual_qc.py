from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import cv2
import numpy as np

_SRT_TIME = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(?P<end>\d{2}:\d{2}:\d{2},\d{3})"
)


def _seconds(value: str) -> float:
    hours, minutes, rest = value.split(":")
    seconds, millis = rest.split(",")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000.0


def parse_srt(text: str) -> list[dict[str, Any]]:
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        match = _SRT_TIME.search(lines[1] if lines[0].strip().isdigit() else lines[0])
        if not match:
            continue
        body = lines[2:] if lines[0].strip().isdigit() else lines[1:]
        cues.append(
            {
                "start_seconds": _seconds(match.group("start")),
                "end_seconds": _seconds(match.group("end")),
                "text": " ".join(body).strip(),
                "lines": len(body),
            }
        )
    return cues


def find_dark_runs(
    luma: list[float], fps: float, *, threshold: float = 40.0, min_frames: int = 2
) -> list[dict[str, Any]]:
    runs = []
    start = None
    for index, value in enumerate(luma):
        if value < threshold:
            if start is None:
                start = index
        elif start is not None:
            if index - start >= min_frames:
                runs.append(
                    {
                        "start_seconds": round(start / fps, 3),
                        "end_seconds": round(index / fps, 3),
                        "frames": index - start,
                        "min_luma": round(min(luma[start:index]), 2),
                    }
                )
            start = None
    if start is not None and len(luma) - start >= min_frames:
        runs.append(
            {
                "start_seconds": round(start / fps, 3),
                "end_seconds": round(len(luma) / fps, 3),
                "frames": len(luma) - start,
                "min_luma": round(min(luma[start:]), 2),
            }
        )
    return runs


def analyze_video(path: Path | str) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"Video cannot be opened: {path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    luma: list[float] = []
    sharpness: list[float] = []
    top_bars: list[float] = []
    bottom_bars: list[float] = []
    dead_frames = 0
    sample: list[np.ndarray] = []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        luma.append(float(gray.mean()))
        sharpness.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
        if float(gray.std()) < 2.0 and float(gray.mean()) < 10.0:
            dead_frames += 1
        bar_height = max(1, int(height * 0.05))
        top_bars.append(float(gray[:bar_height, :].mean()))
        bottom_bars.append(float(gray[-bar_height:, :].mean()))
        if len(sample) < 24:
            sample.append(gray)
    capture.release()
    duration = frames / fps if fps else 0.0
    dark_runs = find_dark_runs(luma, fps) if fps else []
    top_ratio = (
        sum(1 for value in top_bars if value < 12.0) / len(top_bars)
        if top_bars
        else 0.0
    )
    bottom_ratio = (
        sum(1 for value in bottom_bars if value < 12.0) / len(bottom_bars)
        if bottom_bars
        else 0.0
    )
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "frames": frames,
        "duration_seconds": round(duration, 3),
        "luma_min": round(min(luma), 2) if luma else 0.0,
        "luma_max": round(max(luma), 2) if luma else 0.0,
        "luma_mean": round(float(np.mean(luma)), 2) if luma else 0.0,
        "sharpness": round(float(np.mean(sharpness)), 2) if sharpness else 0.0,
        "dark_runs": dark_runs,
        "dead_frames": dead_frames,
        "top_bar_ratio": round(top_ratio, 3),
        "bottom_bar_ratio": round(bottom_ratio, 3),
        "sampled_frames": len(sample),
    }


def analyze_image(path: Path | str) -> dict[str, Any]:
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"Image cannot be opened: {path}")
    has_alpha = image.ndim == 3 and image.shape[2] == 4
    gray = cv2.cvtColor(
        image[:, :, :3] if has_alpha else image,
        cv2.COLOR_BGR2GRAY,
    )
    if has_alpha:
        visible = image[:, :, 3] > 0
        values = gray[visible] if bool(visible.any()) else gray
        dark_ratio = float((values < 25).mean())
        unique = len(np.unique((values // 8).astype(np.uint8)))
    else:
        values = gray
        dark_ratio = float((gray < 25).mean())
        unique = len(np.unique((gray // 8).astype(np.uint8)))
    return {
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
        "channels": int(image.shape[2]) if image.ndim == 3 else 1,
        "has_alpha": has_alpha,
        "luma_mean": round(float(values.mean()), 2),
        "luma_std": round(float(values.std()), 2),
        "sharpness": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2),
        "dark_ratio": round(dark_ratio, 3),
        "unique_colors": unique,
    }


def analyze_asset(path: Path | str) -> dict[str, Any]:
    suffix = Path(path).suffix.lower()
    if suffix in {".mp4", ".webm", ".mov", ".mkv", ".avi"}:
        capture = cv2.VideoCapture(str(path))
        ok, frame = capture.read()
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        capture.release()
        if not ok:
            raise ValueError(f"Video asset cannot be read: {path}")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return {
            "width": width or int(frame.shape[1]),
            "height": height or int(frame.shape[0]),
            "channels": 3,
            "has_alpha": False,
            "luma_mean": round(float(gray.mean()), 2),
            "luma_std": round(float(gray.std()), 2),
            "sharpness": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2),
            "dark_ratio": round(float((gray < 25).mean()), 3),
            "unique_colors": len(np.unique((gray // 8).astype(np.uint8))),
        }
    return analyze_image(path)


def caption_metrics(
    srt_text: str, duration: float, *, tail_hold_seconds: float = 0.0
) -> dict[str, Any]:
    cues = parse_srt(srt_text)
    covered = 0.0
    for cue in cues:
        start = min(max(cue["start_seconds"], 0.0), duration)
        end = min(max(cue["end_seconds"], start), duration)
        covered += max(0.0, end - start)
    max_cue = 0.0
    max_cps = 0.0
    for index, cue in enumerate(cues):
        end = cue["end_seconds"]
        if index == len(cues) - 1 and tail_hold_seconds:
            end = max(cue["start_seconds"], end - tail_hold_seconds)
        cue_duration = max(end - cue["start_seconds"], 0.001)
        max_cue = max(max_cue, cue_duration)
        max_cps = max(max_cps, len(cue["text"]) / cue_duration)
    max_lines = max((cue["lines"] for cue in cues), default=0)
    return {
        "cue_count": len(cues),
        "covered_seconds": round(covered, 3),
        "coverage_ratio": round(covered / duration, 3) if duration > 0 else 0.0,
        "max_cue_seconds": round(max_cue, 3),
        "max_chars_per_second": round(max_cps, 2),
        "max_lines": max_lines,
    }

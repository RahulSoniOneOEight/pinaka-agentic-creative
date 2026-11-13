from __future__ import annotations

import itertools
from statistics import median
from typing import Any

import cv2
import numpy as np


def _gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        return frame
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def verify_frames(
    frames: list[np.ndarray],
    *,
    mask: np.ndarray | None = None,
    min_residual: float = 0.001,
    min_components: int = 2,
) -> dict[str, Any]:
    """Measure motion that remains after global camera compensation."""

    if len(frames) < 2:
        return {
            "verdict": "fail",
            "reason": "at least two frames are required",
            "residual_ratio": 0.0,
            "components": 0,
            "frames": len(frames),
        }
    residuals: list[float] = []
    components: list[int] = []
    centroids: list[tuple[float, float]] = []
    areas: list[float] = []
    for previous, current in itertools.pairwise(frames):
        prev = _gray(previous).astype(np.float32)
        cur = _gray(current).astype(np.float32)
        if prev.shape != cur.shape:
            cur = cv2.resize(cur, (prev.shape[1], prev.shape[0]))
        shift, _response = cv2.phaseCorrelate(prev, cur)
        warp = np.float32([[1, 0, shift[0]], [0, 1, shift[1]]])
        aligned = cv2.warpAffine(
            previous,
            warp,
            (previous.shape[1], previous.shape[0]),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        diff = cv2.absdiff(aligned, current)
        diff_gray = _gray(diff)
        if mask is not None:
            diff_gray = cv2.bitwise_and(diff_gray, diff_gray, mask=mask)
        threshold = cv2.threshold(diff_gray, 18, 255, cv2.THRESH_BINARY)[1]
        area = threshold.shape[0] * threshold.shape[1]
        residual = float(np.count_nonzero(threshold)) / float(area or 1)
        residuals.append(residual)
        count, _labels, stats, centroids_raw = cv2.connectedComponentsWithStats(
            threshold
        )
        moving = [
            index for index in range(1, count) if stats[index, cv2.CC_STAT_AREA] >= 12
        ]
        components.append(len(moving))
        if moving:
            largest = max(moving, key=lambda index: stats[index, cv2.CC_STAT_AREA])
            centroids.append(
                (
                    float(centroids_raw[largest][0]) / threshold.shape[1],
                    float(centroids_raw[largest][1]) / threshold.shape[0],
                )
            )
            areas.append(
                float(stats[largest, cv2.CC_STAT_AREA])
                / float(threshold.shape[0] * threshold.shape[1])
            )

    residual_ratio = float(sum(residuals) / len(residuals))
    max_components = max(components)
    passed = residual_ratio >= min_residual and max_components >= min_components
    return {
        "verdict": "pass" if passed else "fail",
        "reason": (
            "subject motion detected"
            if passed
            else "camera-only or insufficient independent motion"
        ),
        "residual_ratio": round(residual_ratio, 6),
        "components": max_components,
        "frames": len(frames),
        "per_frame_residual": [round(value, 6) for value in residuals],
        "per_frame_components": components,
        "centroid": (
            (
                round(median(item[0] for item in centroids), 4),
                round(median(item[1] for item in centroids), 4),
            )
            if centroids
            else None
        ),
        "motion_area_ratio": (round(median(areas), 6) if areas else 0.0),
    }


def verify_video(
    path: str,
    *,
    samples: int = 12,
    mask: np.ndarray | None = None,
    min_residual: float = 0.001,
    min_components: int = 2,
) -> dict[str, Any]:
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        return {
            "verdict": "fail",
            "reason": f"cannot open video: {path}",
            "residual_ratio": 0.0,
            "components": 0,
            "frames": 0,
        }
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, total // max(1, samples)) if total else 1
    frames: list[np.ndarray] = []
    index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if index % step == 0:
            frames.append(frame)
        index += 1
        if len(frames) >= samples:
            break
    capture.release()
    return verify_frames(
        frames,
        mask=mask,
        min_residual=min_residual,
        min_components=min_components,
    )


def verify_video_segment(
    path: str,
    *,
    start_seconds: float,
    duration_seconds: float,
    samples: int = 12,
    min_residual: float = 0.001,
    min_components: int = 2,
) -> dict[str, Any]:
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        return {
            "verdict": "fail",
            "reason": f"cannot open video: {path}",
            "residual_ratio": 0.0,
            "components": 0,
            "frames": 0,
        }
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 1.0)
    # Ignore the transition margins; measure the stable body of the shot.
    measure_start = start_seconds + duration_seconds * 0.1
    measure_end = start_seconds + duration_seconds * 0.9
    start_frame = max(0, int(measure_start * fps))
    end_frame = max(start_frame + 1, int(measure_end * fps))
    capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    frames: list[np.ndarray] = []
    step = max(1, (end_frame - start_frame) // max(1, samples))
    index = start_frame
    while index < end_frame and len(frames) < samples:
        ok, frame = capture.read()
        if not ok:
            break
        if (index - start_frame) % step == 0:
            frames.append(frame)
        index += 1
    capture.release()
    return verify_frames(
        frames,
        min_residual=min_residual,
        min_components=min_components,
    )

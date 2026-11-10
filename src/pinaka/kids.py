from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from .models import QACheck

FLASH_LIMIT_PER_SECOND = 3.0
LUMA_CHANGE_THRESHOLD = 0.10
KIDS_LOUDNESS_LUFS = -16.0
KIDS_TRUE_PEAK_DBTP = -2.0


def count_flashes(
    luminance: Sequence[float],
    *,
    threshold: float = LUMA_CHANGE_THRESHOLD,
) -> int:
    """Count luminance transitions large enough to read as a flash."""

    if not luminance:
        return 0
    flashes = 0
    previous = luminance[0]
    for value in luminance[1:]:
        if abs(value - previous) >= threshold:
            flashes += 1
        previous = value
    return flashes


def flash_rate_per_second(
    luminance: Sequence[float],
    fps: float,
    *,
    threshold: float = LUMA_CHANGE_THRESHOLD,
) -> float:
    if fps <= 0:
        raise ValueError("fps must be positive")
    if len(luminance) < 2:
        return 0.0
    duration = (len(luminance) - 1) / fps
    if duration <= 0:
        return 0.0
    return round(count_flashes(luminance, threshold=threshold) / duration, 3)


def check_flash_rate(
    luminance: Sequence[float],
    fps: float,
    *,
    limit: float = FLASH_LIMIT_PER_SECOND,
) -> QACheck:
    rate = flash_rate_per_second(luminance, fps)
    passed = rate <= limit
    return QACheck(
        check="kids.flash_rate",
        status="pass" if passed else "fail",
        detail=(
            f"Flash rate {rate}/s is within the {limit}/s limit."
            if passed
            else f"Flash rate {rate}/s exceeds the {limit}/s limit."
        ),
        evidence={
            "flash_rate_per_second": rate,
            "limit_per_second": limit,
            "frames_sampled": len(luminance),
            "fps": fps,
        },
    )


def normalize_loudness(measurement: dict[str, object]) -> dict[str, float | None]:
    """Accept either friendly keys or raw ffmpeg loudnorm fields."""

    integrated = None
    for key in ("integrated_lufs", "output_i", "input_i"):
        value = measurement.get(key)
        if value is not None:
            integrated = float(value)
            break

    peak = None
    for key in ("true_peak_dbtp", "output_tp", "input_tp"):
        value = measurement.get(key)
        if value is not None:
            peak = float(value)
            break

    return {"integrated_lufs": integrated, "true_peak_dbtp": peak}


def check_kids_loudness(
    loudness: dict[str, object],
    *,
    target_lufs: float = KIDS_LOUDNESS_LUFS,
    max_peak_dbtp: float = KIDS_TRUE_PEAK_DBTP,
    tolerance_lu: float = 1.0,
) -> QACheck:
    """Check the mix against the quieter kids profile."""

    normalized = normalize_loudness(loudness)
    measured = normalized["integrated_lufs"]
    peak = normalized["true_peak_dbtp"]
    if measured is None:
        return QACheck(
            check="kids.loudness",
            status="warn",
            detail="No integrated loudness measurement was supplied.",
            evidence={"target_lufs": target_lufs, "max_peak_dbtp": max_peak_dbtp},
        )

    measured_value = float(measured)
    peak_value = float(peak) if peak is not None else None
    problems: list[str] = []
    if measured_value > target_lufs + tolerance_lu:
        problems.append(
            f"Integrated loudness {measured_value} LUFS is above the "
            f"{target_lufs} LUFS kids target"
        )
    if peak_value is not None and peak_value > max_peak_dbtp:
        problems.append(
            f"True peak {peak_value} dBTP is above the {max_peak_dbtp} dBTP limit"
        )

    passed = not problems
    return QACheck(
        check="kids.loudness",
        status="pass" if passed else "fail",
        detail="Kids loudness profile satisfied." if passed else "; ".join(problems),
        evidence={
            "integrated_lufs": measured_value,
            "true_peak_dbtp": peak_value,
            "target_lufs": target_lufs,
            "max_peak_dbtp": max_peak_dbtp,
            "tolerance_lu": tolerance_lu,
        },
    )


def kids_safety_checks(
    *,
    luminance: Sequence[float] | None = None,
    fps: float | None = None,
    loudness: dict[str, object] | None = None,
) -> list[QACheck]:
    """Run the kids checks that have measurements available."""

    checks: list[QACheck] = []
    if luminance is not None and fps is not None:
        checks.append(check_flash_rate(luminance, fps))
    if loudness is not None:
        checks.append(check_kids_loudness(loudness))
    return checks


def luma_from_pcm(raw: bytes, frame_pixels: int) -> list[float]:
    """Convert one grayscale byte per pixel into a 0..1 mean per frame."""

    if frame_pixels <= 0:
        raise ValueError("frame_pixels must be positive")
    values: list[float] = []
    for start in range(0, len(raw), frame_pixels):
        chunk = raw[start : start + frame_pixels]
        if not chunk:
            break
        values.append(round(sum(chunk) / (255.0 * len(chunk)), 6))
    return values


def loudness_from_manifest(manifest: dict[str, object]) -> dict[str, object]:
    """Extract the loudness block a mux manifest may carry."""

    loudness = manifest.get("loudness") or {}
    if not isinstance(loudness, dict):
        return {}
    return {
        "integrated_lufs": loudness.get("integrated_lufs"),
        "true_peak_dbtp": loudness.get("true_peak_dbtp"),
    }


def peak_margin(peak_dbtp: float, limit: float = KIDS_TRUE_PEAK_DBTP) -> Decimal:
    return Decimal(str(round(limit - peak_dbtp, 2)))

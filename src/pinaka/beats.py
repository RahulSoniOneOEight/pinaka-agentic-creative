"""Beat-aware cut planning.

Pure planning only: beat extraction is a separate, optional audio pass. These
functions decide where a cut lands once beat times are known, so the policy is
testable without an audio stack.
"""

from __future__ import annotations

import bisect
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigurationError


@dataclass(frozen=True)
class BeatPlan:
    desired_cuts: tuple[float, ...]
    cuts: tuple[float, ...]
    beats: tuple[float, ...]
    max_shift_seconds: float
    snapped: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "desired_cuts": list(self.desired_cuts),
            "cuts": list(self.cuts),
            "max_shift_seconds": self.max_shift_seconds,
            "snapped": self.snapped,
            "unsnapped": len(self.cuts) - self.snapped,
        }


def _validate(values: tuple[float, ...], label: str) -> tuple[float, ...]:
    cleaned = tuple(round(float(value), 3) for value in values)
    if any(value < 0 for value in cleaned):
        raise ValueError(f"{label} cannot be negative")
    if list(cleaned) != sorted(cleaned):
        raise ValueError(f"{label} must be ascending")
    return cleaned


def snap_to_beats(
    desired_cuts: tuple[float, ...] | list[float],
    beats: tuple[float, ...] | list[float],
    *,
    tolerance_seconds: float = 0.08,
) -> BeatPlan:
    """Move each desired cut to the nearest beat when it is close enough."""

    if tolerance_seconds < 0:
        raise ValueError("tolerance_seconds cannot be negative")
    desired = _validate(tuple(desired_cuts), "desired_cuts")
    beat_times = _validate(tuple(beats), "beats")
    if not beat_times and desired:
        return BeatPlan(desired, desired, (), 0.0, 0)
    cuts: list[float] = []
    shifts: list[float] = []
    snapped = 0
    previous = 0.0
    for moment in desired:
        index = bisect.bisect_left(beat_times, moment)
        candidates = [
            beat_times[position]
            for position in (index - 1, index)
            if 0 <= position < len(beat_times)
        ]
        nearest = (
            min(candidates, key=lambda beat: abs(beat - moment))
            if candidates
            else moment
        )
        shift = abs(nearest - moment)
        if shift <= tolerance_seconds:
            cut = nearest
            snapped += 1
            shifts.append(shift)
        else:
            cut = moment
        cut = max(cut, previous + 0.001)
        cuts.append(round(cut, 3))
        previous = cut
    return BeatPlan(
        desired_cuts=desired,
        cuts=tuple(cuts),
        beats=beat_times,
        max_shift_seconds=round(max(shifts), 3) if shifts else 0.0,
        snapped=snapped,
    )


def plan_song_fit(
    *,
    track_duration: float,
    target_duration: float,
    beats: tuple[float, ...] | list[float],
    fade_seconds: float = 0.5,
    tolerance_seconds: float = 0.08,
) -> dict[str, Any]:
    """Choose an in/out pair on beats so a music bed ends on a real phrase."""

    if track_duration <= 0 or target_duration <= 0:
        raise ValueError("durations must be positive")
    if fade_seconds < 0:
        raise ValueError("fade_seconds cannot be negative")
    beat_times = _validate(tuple(beats), "beats")
    if track_duration >= target_duration:
        plan = snap_to_beats(
            [target_duration], beat_times, tolerance_seconds=tolerance_seconds
        )
        out_point = plan.cuts[0] if plan.cuts else target_duration
        return {
            "mode": "trim",
            "in_point": 0.0,
            "out_point": round(min(out_point, track_duration), 3),
            "fade_out_seconds": fade_seconds,
            "beat_snapped": plan.snapped == 1,
        }
    usable = [beat for beat in beat_times if beat < track_duration]
    out_point = usable[-1] if usable else track_duration
    return {
        "mode": "loop",
        "in_point": 0.0,
        "out_point": round(out_point, 3),
        "loop_length": round(track_duration, 3),
        "fade_out_seconds": fade_seconds,
        "beat_snapped": bool(usable),
    }


def estimate_beats(
    samples: Any,
    sample_rate: int,
    *,
    min_bpm: float = 60.0,
    max_bpm: float = 180.0,
) -> dict[str, Any]:
    """Estimate tempo and a beat grid from mono PCM with numpy only.

    Spectral-flux onsets, autocorrelation for the period, and a phase search for
    the grid. It is deliberately simple; complex mixes need an optional stronger
    tracker, but clear music edits are enough for cut-to-beat.
    """

    try:
        import numpy as np
    except ImportError as error:  # pragma: no cover - media extra is present in CI
        raise ConfigurationError(
            "Beat detection needs numpy; install the media extra"
        ) from error

    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sample_rate <= 0 or audio.size < sample_rate // 2:
        raise ValueError("Beat detection needs at least half a second of audio")
    frame = 1024
    hop = 512
    if audio.size < frame:
        audio = np.pad(audio, (0, frame - audio.size))
    frames = np.lib.stride_tricks.sliding_window_view(audio, frame)[::hop]
    window = np.hanning(frame).astype(np.float32)
    spectra = np.abs(np.fft.rfft(frames * window, axis=1))
    flux = np.maximum(0.0, np.diff(spectra, axis=0)).sum(axis=1)
    if flux.size < 4 or not np.any(flux):
        return {
            "bpm": None,
            "beats": [],
            "duration_seconds": round(audio.size / sample_rate, 3),
            "confidence": 0.0,
            "method": "spectral-flux/autocorrelation",
        }
    flux = flux - flux.mean()
    flux[flux < 0] = 0
    autocorrelation = np.correlate(flux, flux, mode="full")[flux.size - 1 :]
    min_lag = max(1, round((60.0 / max_bpm) * sample_rate / hop))
    max_lag = min(flux.size - 1, round((60.0 / min_bpm) * sample_rate / hop))
    if max_lag <= min_lag:
        raise ValueError("Audio is too short for the requested tempo range")
    window_scores = autocorrelation[min_lag : max_lag + 1]
    period = min_lag + int(np.argmax(window_scores))
    # Autocorrelation peaks at multiples of the period. Prefer the half-period
    # when it is nearly as strong, so 120 BPM is not reported as 60.
    for _ in range(2):
        halves = [half for half in {period // 2, (period + 1) // 2} if half >= min_lag]
        best_half = (
            max(halves, key=lambda half: autocorrelation[half]) if halves else None
        )
        if (
            best_half is not None
            and autocorrelation[best_half] >= 0.5 * autocorrelation[period]
        ):
            period = best_half
        else:
            break
    confidence = 0.0
    if autocorrelation[0] > 0:
        confidence = round(float(window_scores.max() / autocorrelation[0]), 3)
    threshold = float(flux.mean() + 0.5 * flux.std())
    peaks = [
        index + 1
        for index in range(1, flux.size - 1)
        if flux[index] > flux[index - 1]
        and flux[index] >= flux[index + 1]
        and flux[index] > threshold
    ]
    peak_times: list[float] = []
    for index in peaks:
        moment = round(float(index * hop / sample_rate), 3)
        if not peak_times or moment - peak_times[-1] > 0.12:
            peak_times.append(moment)
    if len(peak_times) >= 4:
        beats = peak_times
    else:
        phase_scores = [float(flux[offset::period].sum()) for offset in range(period)]
        offset = int(np.argmax(phase_scores))
        beat_frames = np.arange(offset, flux.size, period)
        beats = [round(float(index * hop / sample_rate), 3) for index in beat_frames]
    bpm = 60.0 * sample_rate / (period * hop)
    return {
        "bpm": round(float(bpm), 2),
        "beats": beats,
        "duration_seconds": round(audio.size / sample_rate, 3),
        "confidence": confidence,
        "method": "spectral-flux/autocorrelation",
    }


def detect_beats(
    audio_path: Path | str,
    *,
    executable: str | None = None,
    sample_rate: int = 22050,
) -> dict[str, Any]:
    """Decode locally with FFmpeg and return a beat grid. No network call."""

    import numpy as np

    path = Path(audio_path).expanduser().resolve()
    if not path.is_file():
        raise ConfigurationError(f"Audio not found: {path}")
    if executable is None:
        from .adapters.encode import FFmpegEncodeAdapter

        executable = FFmpegEncodeAdapter.find_executable()
    completed = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", "replace").strip()
        raise ConfigurationError(
            "Beat detection could not decode the audio: "
            + (detail.splitlines()[-1] if detail else "ffmpeg failed")
        )
    samples = np.frombuffer(completed.stdout, dtype="<f4")
    analysis = estimate_beats(samples, sample_rate)
    analysis["audio_path"] = str(path)
    analysis["sample_rate"] = sample_rate
    return analysis


def quantize_durations(
    durations: tuple[float, ...] | list[float],
    beats: tuple[float, ...] | list[float],
    *,
    tolerance_seconds: float = 0.08,
) -> dict[str, Any]:
    """Snap cumulative cut boundaries to beats, returning adjusted durations."""

    if tolerance_seconds < 0:
        raise ValueError("tolerance_seconds cannot be negative")
    desired = [round(float(value), 3) for value in durations]
    if any(value <= 0 for value in desired):
        raise ValueError("durations must be positive")
    beat_times = _validate(tuple(beats), "beats")
    adjusted: list[float] = []
    shifts: list[float] = []
    snapped = 0
    previous = 0.0
    for duration in desired:
        boundary = previous + duration
        if beat_times:
            index = bisect.bisect_left(beat_times, boundary)
            candidates = [
                beat_times[position]
                for position in (index - 1, index)
                if 0 <= position < len(beat_times)
            ]
            nearest = (
                min(candidates, key=lambda beat: abs(beat - boundary))
                if candidates
                else boundary
            )
            shift = abs(nearest - boundary)
            if shift <= tolerance_seconds + 1e-9 and nearest > previous + 0.05:
                boundary = nearest
                shifts.append(shift)
                snapped += 1
        boundary = max(previous + 0.05, boundary)
        adjusted.append(round(boundary - previous, 3))
        previous = boundary
    return {
        "durations": adjusted,
        "shifts": [round(shift, 3) for shift in shifts],
        "snapped": snapped,
        "max_shift_seconds": round(max(shifts), 3) if shifts else 0.0,
        "tolerance_seconds": tolerance_seconds,
    }

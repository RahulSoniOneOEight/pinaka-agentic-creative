from __future__ import annotations

import pytest

from pinaka.beats import (
    detect_beats,
    estimate_beats,
    plan_song_fit,
    quantize_durations,
    snap_to_beats,
)


def test_cuts_snap_to_the_nearest_beat_within_tolerance():
    plan = snap_to_beats([1.0, 2.5], [0.0, 1.05, 2.0, 2.9], tolerance_seconds=0.1)

    assert plan.cuts == (1.05, 2.5)
    assert plan.snapped == 1
    assert plan.max_shift_seconds == 0.05


def test_distant_cuts_are_left_alone():
    plan = snap_to_beats([1.0], [2.0], tolerance_seconds=0.1)

    assert plan.cuts == (1.0,)
    assert plan.snapped == 0


def test_snapped_cuts_stay_monotonic():
    plan = snap_to_beats([1.0, 1.02], [1.0, 1.0], tolerance_seconds=0.1)

    assert plan.cuts[0] < plan.cuts[1]


def test_beat_plan_needs_ascending_input():
    with pytest.raises(ValueError, match="ascending"):
        snap_to_beats([2.0, 1.0], [1.0, 2.0])


def test_song_fit_trims_a_long_track_on_a_beat():
    fit = plan_song_fit(
        track_duration=60.0,
        target_duration=10.0,
        beats=[9.8, 10.1, 10.4],
        tolerance_seconds=0.15,
    )

    assert fit["mode"] == "trim"
    assert fit["out_point"] == 10.1
    assert fit["beat_snapped"] is True


def test_song_fit_loops_a_short_track():
    fit = plan_song_fit(
        track_duration=6.0,
        target_duration=10.0,
        beats=[0.0, 2.0, 4.0, 5.9],
    )

    assert fit["mode"] == "loop"
    assert fit["in_point"] == 0.0
    assert fit["out_point"] == 5.9
    assert fit["loop_length"] == 6.0


def click_track(bpm: float = 120.0, seconds: float = 8.0, sample_rate: int = 22050):
    import numpy as np

    samples = np.zeros(int(sample_rate * seconds), dtype=np.float32)
    click = np.hanning(256).astype(np.float32) * 0.9
    period = 60.0 / bpm
    for index in range(int(seconds / period)):
        start = int(index * period * sample_rate)
        samples[start : start + click.size] += click
    return samples, sample_rate


def test_estimator_finds_tempo_and_phase_of_a_click_track():
    samples, sample_rate = click_track(120.0, 8.0)

    analysis = estimate_beats(samples, sample_rate)

    assert 115 <= analysis["bpm"] <= 125
    assert analysis["beats"]
    assert abs(analysis["beats"][0] - 0.5) < 0.1


def test_quantize_durations_snaps_cumulative_boundaries():
    quantized = quantize_durations(
        [1.0, 1.0, 1.0], [0.98, 2.02, 2.99], tolerance_seconds=0.05
    )

    assert quantized["snapped"] == 3
    assert quantized["durations"] == [0.98, 1.04, 0.97]
    assert quantized["max_shift_seconds"] == 0.04


def test_quantize_leaves_a_distant_cut_alone():
    quantized = quantize_durations([1.0], [1.5], tolerance_seconds=0.05)

    assert quantized["durations"] == [1.0]
    assert quantized["snapped"] == 0


def test_detect_beats_decodes_a_local_wav(tmp_path):
    import numpy as np

    samples, sample_rate = click_track(120.0, 4.0)
    peak = float(np.max(np.abs(samples))) or 1.0
    pcm = (samples / peak * 30000).astype("<i2")
    path = tmp_path / "click.wav"
    import wave

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())

    analysis = detect_beats(path)

    assert 110 <= analysis["bpm"] <= 130
    assert analysis["beats"]
    assert analysis["audio_path"].endswith("click.wav")

from __future__ import annotations

import pytest

from pinaka.beats import plan_song_fit, snap_to_beats


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

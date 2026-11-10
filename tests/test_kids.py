from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from pinaka.controller import CreativeController
from pinaka.errors import IntegrityError
from pinaka.kids import (
    check_flash_rate,
    check_kids_loudness,
    count_flashes,
    flash_rate_per_second,
    kids_safety_checks,
    luma_from_pcm,
    peak_margin,
)
from pinaka.models import PilotContract
from pinaka.publication import CHANNELS

CONTRACT = Path(__file__).parents[1] / "config" / "pilots" / "kids-rhyme-10s.json"


def test_count_flashes_detects_large_luminance_changes():
    steady = [0.2] * 10
    flashing = [0.1, 0.9] * 5

    assert count_flashes(steady) == 0
    assert count_flashes(flashing) == 9


def test_flash_rate_uses_frames_per_second():
    flashing = [0.1, 0.9] * 5

    rate = flash_rate_per_second(flashing, fps=10)

    assert rate == 10.0


def test_flash_rate_passes_below_the_limit():
    gentle = [0.50, 0.52, 0.50, 0.53, 0.51]

    check = check_flash_rate(gentle, fps=30)

    assert check.status == "pass"
    assert check.check == "kids.flash_rate"


def test_flash_rate_fails_above_the_limit():
    flashing = [0.1, 0.9] * 10

    check = check_flash_rate(flashing, fps=30)

    assert check.status == "fail"
    assert "exceeds" in check.detail


def test_kids_loudness_accepts_a_quiet_mix():
    check = check_kids_loudness({"integrated_lufs": -17.4, "true_peak_dbtp": -3.0})

    assert check.status == "pass"


def test_kids_loudness_fails_a_loud_mix():
    check = check_kids_loudness({"integrated_lufs": -12.0, "true_peak_dbtp": -0.5})

    assert check.status == "fail"
    assert "above" in check.detail


def test_kids_loudness_warns_without_a_measurement():
    check = check_kids_loudness({})

    assert check.status == "warn"


def test_kids_loudness_reads_ffmpeg_loudnorm_fields():
    check = check_kids_loudness({"output_i": "-16.6", "output_tp": "-4.7"})

    assert check.status == "pass"
    assert check.evidence["integrated_lufs"] == -16.6


def test_kids_loudness_fails_when_ffmpeg_measurement_is_too_loud():
    check = check_kids_loudness({"input_i": "-14.2", "input_tp": "-1.0"})

    assert check.status == "fail"


def test_kids_safety_checks_compose_available_measurements():
    checks = kids_safety_checks(
        luminance=[0.5] * 10, fps=30, loudness={"integrated_lufs": -18.0}
    )

    assert {item.check for item in checks} == {"kids.flash_rate", "kids.loudness"}


def test_luma_from_pcm_mean_per_frame():
    values = luma_from_pcm(bytes([0, 255, 0, 255]), frame_pixels=2)

    assert values == [0.5, 0.5]


def test_peak_margin_is_positive_when_within_limit():
    assert float(peak_margin(-3.0)) == 1.0


def test_youtube_kids_profile_is_made_for_kids():
    profile = CHANNELS["youtube_kids"]

    assert profile.made_for_kids is True
    assert profile.comments_enabled is False
    assert profile.personalized_ads is False
    assert profile.requires_captions is True


def test_other_channels_keep_adult_defaults():
    profile = CHANNELS["instagram_reels"]

    assert profile.made_for_kids is False
    assert profile.comments_enabled is True


def _mux_result(loudness: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(
        metadata={
            "duration_seconds": "5",
            "video_codec": "h264",
            "width_px": 1080,
            "height_px": 1920,
            "fps": 30.0,
            "audio_codec": "aac",
            "audio_sample_rate": 48000,
            "audio_channels": "stereo",
        },
        loudness=loudness,
    )


def test_mux_validation_accepts_a_quiet_mix():
    CreativeController._validate_mux_result(
        _mux_result({"input_i": "-16.4", "input_tp": "-4.0"}), Decimal(5)
    )


def test_mux_validation_rejects_loud_audio():
    with pytest.raises(IntegrityError, match="loudness"):
        CreativeController._validate_mux_result(
            _mux_result({"input_i": "-12.0", "input_tp": "-3.0"}), Decimal(5)
        )


def test_mux_validation_rejects_a_hot_peak():
    with pytest.raises(IntegrityError, match="true peak"):
        CreativeController._validate_mux_result(
            _mux_result({"input_i": "-16.0", "input_tp": "-0.5"}), Decimal(5)
        )


def test_kids_pilot_contract_validates():
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))

    contract = PilotContract.model_validate(payload)

    assert contract.project.duration_seconds == 10
    assert contract.acceptance_targets["made_for_kids"] is True
    assert contract.acceptance_targets["flash_rate_max_per_second"] == 3
    segments = contract.deliverable_plan.segments
    assert sum(segment.duration_seconds for segment in segments) == 10

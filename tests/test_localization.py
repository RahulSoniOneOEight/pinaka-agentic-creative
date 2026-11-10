from __future__ import annotations

from pathlib import Path

import pytest

from pinaka.localization import (
    CaptionCue,
    caption_issues,
    locale_profile,
    parse_srt,
    plan_localization,
)

CLEAN_SRT = """1
00:00:00,000 --> 00:00:02,000
Growing business.

2
00:00:02,000 --> 00:00:06,000
A virtual CFO brings forecasts.

3
00:00:06,000 --> 00:00:10,000
Start a conversation.
"""

PILOT_SRT = (
    Path(__file__).resolve().parents[1]
    / "workspace"
    / "deliverables"
    / "pinaka-virtual-cfo"
    / "packet-v2"
    / "captions.srt"
)


def test_parse_srt_preserves_timing_and_text():
    cues = parse_srt(CLEAN_SRT)
    assert len(cues) == 3
    assert cues[0].start_ms == 0
    assert cues[-1].end_ms == 10_000
    assert cues[1].text == "A virtual CFO brings forecasts."


def test_clean_srt_has_no_issues_and_is_ready_for_translation():
    assert caption_issues(parse_srt(CLEAN_SRT)) == ()
    plan = plan_localization(CLEAN_SRT, targets=("hi", "de"))
    assert plan.status == "ready_for_translation"
    assert {target.locale for target in plan.targets} == {"hi", "de"}
    assert all(target.status == "ready" for target in plan.targets)


def test_long_line_and_fast_reading_speed_are_flagged():
    cues = (
        CaptionCue(
            index=1,
            start_ms=0,
            end_ms=2000,
            text="Start a Conversation with Pinaka Intelligence today.",
        ),
    )
    checks = {issue.check for issue in caption_issues(cues)}
    assert "line_length" in checks
    assert "reading_speed" in checks


def test_overlapping_cues_are_flagged():
    cues = (
        CaptionCue(index=1, start_ms=0, end_ms=3000, text="One"),
        CaptionCue(index=2, start_ms=2000, end_ms=4000, text="Two"),
    )
    assert any(issue.check == "overlap" for issue in caption_issues(cues))


def test_unknown_locale_is_rejected():
    with pytest.raises(ValueError, match="Unsupported locale"):
        locale_profile("xx")


def test_pilot_captions_have_actionable_readability_findings():
    if not PILOT_SRT.is_file():
        pytest.skip("Pilot SRT is not present in this checkout")
    plan = plan_localization(PILOT_SRT.read_text(encoding="utf-8"))
    assert plan.source_cues == 4
    assert plan.status == "needs_source_fix"
    checks = {issue.check for issue in plan.source_issues}
    assert "line_length" in checks
    assert "reading_speed" in checks
    assert all(target.status == "review_timing" for target in plan.targets)

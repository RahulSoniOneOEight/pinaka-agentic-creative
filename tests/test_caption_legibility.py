from __future__ import annotations

import pytest

from pinaka.captions import (
    CaptionProfile,
    build_srt,
    cues_for_shots,
    legibility_violations,
    profile_for_locale,
)
from pinaka.models import TextBriefRequest
from tests.conftest import create_project


def shot(text: str, duration: float = 6.0) -> dict:
    return {"duration_seconds": duration, "narrative": text}


def test_lines_are_wrapped_to_the_locale_width():
    text = "One two three four five six seven eight nine ten eleven twelve"
    cues = cues_for_shots([shot(text, 6.0)])

    assert len(cues) == 1
    assert all(len(line) <= 42 for line in cues[0]["lines"])
    assert len(cues[0]["lines"]) <= 2


def test_long_text_is_split_into_display_sized_cues():
    text = " ".join(f"word{index}" for index in range(40))
    cues = cues_for_shots([shot(text, 12.0)])

    assert len(cues) > 1
    for cue in cues:
        assert len(cue["lines"]) <= 2
        assert all(len(line) <= 42 for line in cue["lines"])


def test_locale_profile_narrows_the_line_and_flags_dense_speech():
    hindi = profile_for_locale("hi")
    cues = cues_for_shots([shot("a" * 90, 1.0)], profile=hindi)
    violations = legibility_violations(cues, hindi)

    assert any(item["rule"] == "chars_per_second" for item in violations)
    assert hindi.line_chars == 32


def test_strict_build_refuses_an_unreadable_cue():
    with pytest.raises(ValueError, match="chars_per_second"):
        build_srt([shot("word " * 25, 1.0)], strict=True)


def test_short_text_keeps_the_plan_timing():
    srt = build_srt(
        [
            shot("Quack, quack, quack!", 4.0),
            shot("Waddle to the pond.", 3.0),
        ]
    )

    assert srt.startswith("1\n00:00:00,000 --> 00:00:04,000\n")
    assert "00:00:04,000 --> 00:00:07,000" in srt


def test_export_records_legibility_evidence(controller):
    project = create_project(controller, budget="10.00")
    controller.plan_from_text(
        project.id, TextBriefRequest(text="Make a 10 second rhyme for kids")
    )

    result = controller.export_captions(project.id)

    assert result["legibility"]["passed"] is True
    assert result["artifact"]["operation"]["legible"] is True
    assert result["artifact"]["operation"]["locale"] == "en"


def test_a_custom_profile_is_honoured():
    profile = CaptionProfile(max_chars_per_second=12.0, line_chars=20, max_lines=1)
    cues = cues_for_shots([shot("one two three four five six", 2.0)], profile=profile)
    violations = legibility_violations(cues, profile)

    assert any(item["rule"] == "chars_per_second" for item in violations)
    assert all(len(cue["lines"]) == 1 for cue in cues)

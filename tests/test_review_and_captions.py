import pytest
from pydantic import ValidationError

from pinaka.adapters.transcribe import compare_transcript
from pinaka.review import (
    ColorCheck,
    VideoReviewSpec,
    normalize_text,
    rgb_distance,
    text_matches,
    text_similarity,
)


def test_text_normalization_ignores_ocr_punctuation_and_case():
    assert normalize_text("  A VIRTUAL CFO BRINGS  ") == "a virtual cfo brings"
    assert normalize_text('" Forecasts .') == "forecasts"
    assert text_matches(
        "Start a Conversation",
        "Start a Conversation\n",
        0.85,
    )
    assert text_matches(
        "A VIRTUAL CFO BRINGS",
        "A VIRTUAL CFO BRINGS",
        0.85,
    )
    assert not text_matches(
        "Start a Conversation",
        "PINAKA INTELLIGENCE",
        0.85,
    )
    assert text_similarity("Growing business.", "Growing business.") == 1.0


def test_rgb_distance_is_zero_for_identical_colour():
    assert rgb_distance((57, 92, 255), (57, 92, 255)) == 0.0
    assert rgb_distance((0, 0, 0), (255, 255, 255)) > 400


def test_review_spec_rejects_invalid_brand_colour():
    with pytest.raises(ValidationError):
        ColorCheck(
            time_seconds="1",
            x=10,
            y=10,
            expected_hex="not-a-colour",
        )


def test_review_spec_parses_pilot_contract():
    spec = VideoReviewSpec.model_validate(
        {
            "width_px": 1080,
            "height_px": 1920,
            "duration_seconds": "10",
            "fps": "30",
            "expected_scene_cuts": ["2", "6", "8"],
            "ocr": [
                {
                    "time_seconds": "1",
                    "x": 0,
                    "y": 0,
                    "width": 1080,
                    "height": 1920,
                    "expected_text": "Growing business. Unclear cash flow?",
                }
            ],
            "colors": [
                {
                    "time_seconds": "3",
                    "x": 974,
                    "y": 835,
                    "expected_hex": "#18B7B7",
                    "tolerance": 30,
                }
            ],
        }
    )
    assert spec.video_codec == "h264"
    assert spec.audio_codec == "aac"
    assert len(spec.ocr) == 1
    assert len(spec.colors) == 1


def test_transcript_comparison_ignores_punctuation_and_case():
    expected = (
        "Growing business, unclear cash flow? A virtual CFO brings forecasts, "
        "budgets, and financial insight—so your next decision starts with clarity."
    )
    observed = (
        "growing business, unclear cash flow. A virtual CFO brings forecasts, "
        "budgets, and financial insight, so your next decision starts with clarity."
    )
    result = compare_transcript(expected, observed)
    assert result["matches"] is True
    assert result["missing_tokens"] == []
    assert result["similarity"] > 0.9


def test_transcript_comparison_flags_missing_word():
    result = compare_transcript(
        "Start a Conversation with Pinaka Intelligence",
        "Start a Conversation",
    )
    assert result["matches"] is False
    assert "pinaka" in result["missing_tokens"]

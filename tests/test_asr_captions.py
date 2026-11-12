from __future__ import annotations

from pinaka.adapters.transcribe import (
    TranscriptionResult,
    TranscriptSegment,
    TranscriptWord,
    transcription_payload,
)
from pinaka.captions import (
    build_srt_from_words,
    caption_drift,
    cues_from_words,
    parse_srt,
)


def words(*items: tuple[float, float, str]) -> list[dict]:
    return [
        {"start_seconds": start, "end_seconds": end, "text": text}
        for start, end, text in items
    ]


def test_word_cues_split_on_a_pause():
    cues = cues_from_words(
        words(
            (0.0, 0.4, "Quack"),
            (0.4, 0.8, "quack,"),
            (0.8, 1.2, "quack!"),
            (2.0, 2.4, "Waddle"),
            (2.4, 2.8, "to"),
            (2.8, 3.4, "the"),
        )
    )

    assert len(cues) == 2
    assert cues[0]["start_seconds"] == 0.0
    assert cues[0]["end_seconds"] == 1.2
    assert cues[1]["start_seconds"] == 2.0


def test_word_cues_respect_line_limits():
    cues = cues_from_words(
        words(*[(index * 0.3, index * 0.3 + 0.28, "word") for index in range(20)])
    )

    for cue in cues:
        assert len(cue["lines"]) <= 2
        assert all(len(line) <= 42 for line in cue["lines"])


def test_srt_from_words_keeps_speech_timing():
    srt = build_srt_from_words(words((0.0, 0.4, "Quack,"), (0.4, 1.0, "quack!")))

    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,000\n")
    assert "Quack, quack!" in srt


def test_plan_and_speech_captions_agree():
    plan = [
        {
            "index": 1,
            "start_seconds": 0.0,
            "end_seconds": 1.0,
            "text": "Quack, quack!",
        }
    ]
    spoken = cues_from_words(words((0.1, 0.5, "Quack,"), (0.5, 0.95, "quack!")))

    result = caption_drift(plan, spoken)

    assert result["passed"] is True
    assert result["max_offset_seconds"] == 0.1


def test_plan_and_speech_captions_report_text_and_timing_drift():
    plan = [
        {
            "index": 1,
            "start_seconds": 0.0,
            "end_seconds": 2.0,
            "text": "Waddle to the pond",
        }
    ]
    spoken = cues_from_words(words((2.5, 3.0, "something"), (3.0, 3.5, "else")))

    result = caption_drift(plan, spoken)

    assert result["passed"] is False
    assert result["mismatches"][0]["reason"] == "text"


def test_plan_srt_round_trips_into_cues():
    srt = (
        "1\n00:00:00,000 --> 00:00:01,500\nQuack, quack!\n\n"
        "2\n00:00:01,500 --> 00:00:03,000\nWaddle on.\n"
    )

    cues = parse_srt(srt)

    assert len(cues) == 2
    assert cues[0]["end_seconds"] == 1.5
    assert cues[1]["text"] == "Waddle on."
    assert cues[1]["duration_seconds"] == 1.5


def test_transcription_payload_carries_words():
    result = TranscriptionResult(
        text="Quack quack",
        segments=(
            TranscriptSegment(
                start_seconds=0.0,
                end_seconds=1.0,
                text="Quack quack",
                words=(
                    TranscriptWord(0.0, 0.4, "Quack"),
                    TranscriptWord(0.4, 1.0, "quack"),
                ),
            ),
        ),
        language="en",
        language_probability=0.99,
        duration_seconds=1.0,
        model_name="tiny.en",
        model_path=None,
        model_sha256=None,
        audio_path="narration.wav",
        audio_sha256="a" * 64,
        command_hash="b" * 64,
    )

    payload = transcription_payload(result)

    assert payload["segments"][0]["words"][0] == {
        "start_seconds": 0.0,
        "end_seconds": 0.4,
        "text": "Quack",
    }

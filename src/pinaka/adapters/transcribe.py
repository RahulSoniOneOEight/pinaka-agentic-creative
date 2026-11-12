from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ..errors import ConfigurationError
from ..utils import sha256_file, sha256_json


@dataclass(frozen=True)
class TranscriptWord:
    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True)
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str
    words: tuple[TranscriptWord, ...] = ()


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    segments: tuple[TranscriptSegment, ...]
    language: str
    language_probability: float
    duration_seconds: float
    model_name: str
    model_path: str | None
    model_sha256: str | None
    audio_path: str
    audio_sha256: str
    command_hash: str


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def compare_transcript(expected: str, observed: str) -> dict[str, Any]:
    expected_normalized = _normalize(expected)
    observed_normalized = _normalize(observed)
    expected_tokens = expected_normalized.split()
    observed_tokens = observed_normalized.split()
    missing = [token for token in expected_tokens if token not in observed_tokens]
    similarity = SequenceMatcher(None, expected_normalized, observed_normalized).ratio()
    return {
        "similarity": round(similarity, 4),
        "missing_tokens": missing,
        "matches": not missing,
    }


class WhisperTranscriber:
    """Local faster-whisper transcription with model and audio evidence."""

    profile_key = "faster-whisper"

    def __init__(
        self,
        *,
        model_size: str = "tiny.en",
        download_root: Path | None = None,
        device: str = "cpu",
        compute_type: str = "int8",
        beam_size: int = 5,
        word_timestamps: bool = False,
    ):
        self.model_size = model_size
        self.download_root = download_root
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.word_timestamps = word_timestamps
        self._model: Any = None

    def _engine(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as error:
                raise ConfigurationError(
                    "faster-whisper is not installed; install the optional asr dependency"
                ) from error
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                download_root=str(self.download_root) if self.download_root else None,
            )
        return self._model

    def _model_hash(self) -> tuple[str | None, str | None]:
        if self.download_root is None:
            return None, None
        candidates = sorted(self.download_root.glob("models--*/snapshots/*/model.bin"))
        for candidate in reversed(candidates):
            if candidate.is_file():
                return str(candidate), sha256_file(candidate)
        return None, None

    def transcribe(self, audio_path: Path | str) -> TranscriptionResult:
        path = Path(audio_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Audio not found: {path}")
        engine = self._engine()
        segments, info = engine.transcribe(
            str(path),
            beam_size=self.beam_size,
            language="en",
            vad_filter=True,
            word_timestamps=self.word_timestamps,
        )
        collected = tuple(
            TranscriptSegment(
                start_seconds=round(float(segment.start), 3),
                end_seconds=round(float(segment.end), 3),
                text=segment.text.strip(),
                words=tuple(
                    TranscriptWord(
                        start_seconds=round(float(word.start), 3),
                        end_seconds=round(float(word.end), 3),
                        text=str(word.word).strip(),
                    )
                    for word in (getattr(segment, "words", None) or ())
                    if str(getattr(word, "word", "")).strip()
                ),
            )
            for segment in segments
        )
        text = " ".join(segment.text for segment in collected if segment.text).strip()
        model_path, model_hash = self._model_hash()
        audio_hash = sha256_file(path)
        command_hash = sha256_json(
            {
                "model": self.model_size,
                "device": self.device,
                "compute_type": self.compute_type,
                "beam_size": self.beam_size,
                "language": "en",
                "vad_filter": True,
                "word_timestamps": self.word_timestamps,
                "audio_sha256": audio_hash,
            }
        )
        return TranscriptionResult(
            text=text,
            segments=collected,
            language=info.language,
            language_probability=round(float(info.language_probability), 4),
            duration_seconds=round(float(info.duration), 3),
            model_name=self.model_size,
            model_path=model_path,
            model_sha256=model_hash,
            audio_path=str(path),
            audio_sha256=audio_hash,
            command_hash=command_hash,
        )


def transcription_payload(
    result: TranscriptionResult, *, expected_text: str | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "text": result.text,
        "language": result.language,
        "language_probability": result.language_probability,
        "duration_seconds": result.duration_seconds,
        "segments": [
            {
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
                "text": segment.text,
                "words": [
                    {
                        "start_seconds": word.start_seconds,
                        "end_seconds": word.end_seconds,
                        "text": word.text,
                    }
                    for word in segment.words
                ],
            }
            for segment in result.segments
        ],
        "model_name": result.model_name,
        "model_path": result.model_path,
        "model_sha256": result.model_sha256,
        "audio_path": result.audio_path,
        "audio_sha256": result.audio_sha256,
        "command_hash": result.command_hash,
    }
    if expected_text is not None:
        payload["script_comparison"] = compare_transcript(expected_text, result.text)
    return payload


def write_transcript(payload: dict[str, Any], path: Path | str) -> Path:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return destination

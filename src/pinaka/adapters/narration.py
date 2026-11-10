from __future__ import annotations

import io
import os
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..errors import ConfigurationError
from ..utils import sha256_bytes, sha256_file, sha256_json


@dataclass(frozen=True)
class SynthesizedNarration:
    content: bytes
    content_hash: str
    text_hash: str
    voice: str
    speed: float
    sample_rate: int
    duration_seconds: float
    model_name: str
    model_sha256: str
    voices_sha256: str
    licence_note: str
    command_hash: str


class NarrationAdapter(Protocol):
    profile_key: str

    def synthesize(self, text: str) -> SynthesizedNarration: ...


class KokoroNarrationAdapter:
    """Local Kokoro-82M narration with explicit model and voice evidence."""

    profile_key = "kokoro-tts"
    model_name = "Kokoro-82M-v1.0"

    def __init__(
        self,
        *,
        model_path: Path,
        voices_path: Path,
        voice: str = "af_heart",
        speed: float = 1.0,
    ):
        self.model_path = model_path
        self.voices_path = voices_path
        self.voice = voice
        self.speed = speed
        self._kokoro: Any = None
        if not model_path.is_file():
            raise ConfigurationError(f"Kokoro model not found: {model_path}")
        if not voices_path.is_file():
            raise ConfigurationError(f"Kokoro voices not found: {voices_path}")

    @classmethod
    def from_default_paths(
        cls, *, voice: str = "af_heart", speed: float = 1.0
    ) -> KokoroNarrationAdapter:
        base = Path(
            os.environ.get(
                "PINAKA_KOKORO_DIR",
                str(Path.cwd() / "workspace" / "tools" / "kokoro"),
            )
        )
        return cls(
            model_path=base / "kokoro-v1.0.onnx",
            voices_path=base / "voices-v1.0.bin",
            voice=voice,
            speed=speed,
        )

    def _engine(self) -> Any:
        if self._kokoro is None:
            try:
                from kokoro_onnx import Kokoro
            except ImportError as error:
                raise ConfigurationError(
                    "kokoro-onnx is not installed; install the optional tts dependency"
                ) from error
            self._kokoro = Kokoro(str(self.model_path), str(self.voices_path))
        return self._kokoro

    def synthesize(self, text: str) -> SynthesizedNarration:
        if not text.strip():
            raise ValueError("Narration text must not be empty")
        try:
            import numpy as np
        except ImportError as error:
            raise ConfigurationError("numpy is required for local narration") from error
        engine = self._engine()
        samples, sample_rate = engine.create(
            text,
            voice=self.voice,
            speed=self.speed,
            lang="en-us",
        )
        duration = len(samples) / sample_rate
        if duration <= 0:
            raise RuntimeError("Kokoro returned empty narration audio")
        pcm = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(pcm.tobytes())
        content = buffer.getvalue()
        text_hash = sha256_bytes(text.encode("utf-8"))
        model_sha256 = sha256_file(self.model_path)
        voices_sha256 = sha256_file(self.voices_path)
        command_hash = sha256_json(
            {
                "model": self.model_name,
                "model_sha256": model_sha256,
                "voices_sha256": voices_sha256,
                "voice": self.voice,
                "speed": self.speed,
                "text_hash": text_hash,
                "sample_rate": sample_rate,
            }
        )
        return SynthesizedNarration(
            content=content,
            content_hash=sha256_bytes(content),
            text_hash=text_hash,
            voice=self.voice,
            speed=self.speed,
            sample_rate=sample_rate,
            duration_seconds=round(duration, 3),
            model_name=self.model_name,
            model_sha256=model_sha256,
            voices_sha256=voices_sha256,
            licence_note=(
                "Kokoro-82M v1.0 weights Apache-2.0; kokoro-onnx runtime MIT; "
                "voice is a synthetic speaker embedding; model card states training "
                "audio is permissive/non-copyrighted. Review the current model card "
                "and voice list before external distribution."
            ),
            command_hash=command_hash,
        )

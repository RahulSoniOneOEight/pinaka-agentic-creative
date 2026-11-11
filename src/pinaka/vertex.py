from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from .adapters.google_vertex_image import DEFAULT_LOCATION, DEFAULT_PROJECT
from .adapters.narration import SynthesizedNarration
from .adapters.transcribe import TranscriptionResult, TranscriptSegment
from .enums import CapabilityStatus, DataClassification
from .errors import ConfigurationError
from .models import CapabilityResult, ProviderModelProfileCreate
from .utils import sha256_bytes, sha256_file, sha256_json, utc_now

PROVIDER_KEY = "google-vertex"
CREDENTIAL_REF = "ADC"
TTS_MODEL = "gemini-2.5-flash-preview-tts"
TRANSCRIBE_MODEL = "gemini-2.5-flash"
VERIFIABLE_MODELS = (
    "gemini-3.1-flash-image",
    TTS_MODEL,
    TRANSCRIBE_MODEL,
)

# Policy rates used for *estimates*. Verify against the live Vertex price sheet
# before enabling a paid run; the receipt records the provider's usage instead.
TTS_INPUT_USD_PER_MILLION_TOKENS = 0.50
TTS_OUTPUT_USD_PER_MILLION_TOKENS = 10.00
CHARS_PER_TOKEN = 4
AUDIO_TOKENS_PER_SECOND = 25
TTS_SAMPLE_RATE = 24_000
TTS_CHANNELS = 1
TTS_SAMPLE_WIDTH = 2

TRANSCRIBE_USD_PER_SECOND = 0.0006

TRANSCRIBE_USD_PER_MILLION_TOKENS = 0.50
TRANSCRIBE_TOKENS_PER_SECOND = 32


def _minor(amount: float) -> int:
    return max(1, math.ceil(amount * 100))


def estimate_tts_cost_minor(text: str) -> int:
    """Estimate Gemini TTS cost for one spoken script."""

    if not text.strip():
        raise ValueError("TTS estimate requires non-empty text")
    input_tokens = math.ceil(len(text) / CHARS_PER_TOKEN)
    spoken_seconds = max(1.0, len(text) / 14.0)
    output_tokens = math.ceil(spoken_seconds * AUDIO_TOKENS_PER_SECOND)
    cost = (
        input_tokens * TTS_INPUT_USD_PER_MILLION_TOKENS
        + output_tokens * TTS_OUTPUT_USD_PER_MILLION_TOKENS
    ) / 1_000_000
    return _minor(cost)


def estimate_transcription_cost_minor(duration_seconds: float) -> int:
    """Estimate Gemini transcription cost for one audio file."""

    if duration_seconds <= 0:
        raise ValueError("Transcription estimate requires a positive duration")
    return _minor(duration_seconds * TRANSCRIBE_USD_PER_SECOND)


def vertex_provider_profile(
    *,
    project: str | None = None,
    location: str | None = None,
    enabled: bool = False,
) -> ProviderModelProfileCreate:
    """Provider profile that authenticates with Application Default Credentials."""

    resolved_project = project or DEFAULT_PROJECT
    resolved_location = location or DEFAULT_LOCATION
    return ProviderModelProfileCreate(
        stable_key=f"{PROVIDER_KEY}-adc",
        provider=PROVIDER_KEY,
        endpoint=f"https://{resolved_location}-aiplatform.googleapis.com",
        model=",".join(VERIFIABLE_MODELS),
        approved_roles=("image_generation", "narration", "transcription"),
        credential_ref=CREDENTIAL_REF,
        data_classification=DataClassification.INTERNAL,
        limits={
            "project": resolved_project,
            "location": resolved_location,
            "auth": "application_default_credentials",
            "paid": True,
        },
        terms={"billing": "gcp_project", "auth": "adc"},
        enabled=enabled,
    )


@dataclass
class VertexTTSAdapter:
    """Gemini text-to-speech through Vertex AI with ADC."""

    profile_key: str = "google-vertex-tts"
    model: str = TTS_MODEL
    voice: str = "Kore"
    client: Any | None = None
    project: str | None = None
    location: str | None = None

    def _client(self) -> Any:
        if self.client is not None:
            return self.client
        try:
            from google import genai
        except ImportError as error:
            raise ConfigurationError(
                "google-genai is not installed; install the gcp-image extra"
            ) from error
        self.client = genai.Client(
            vertexai=True,
            project=self.project or DEFAULT_PROJECT,
            location=self.location or DEFAULT_LOCATION,
        )
        return self.client

    def build_config(self) -> Any:
        from google.genai import types

        return types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.voice
                    )
                )
            ),
        )

    def synthesize(self, text: str) -> SynthesizedNarration:
        script = (text or "").strip()
        if not script:
            raise ValueError("Narration text is empty")
        client = self._client()
        try:
            response = client.models.generate_content(
                model=self.model, contents=script, config=self.build_config()
            )
        except Exception as error:  # noqa: BLE001 - provider SDK raises broadly
            raise RuntimeError(
                f"Vertex TTS request failed: {type(error).__name__}"
            ) from None
        pcm = _first_audio(response)
        content = _wrap_wav(pcm)
        duration = len(pcm) / (TTS_SAMPLE_RATE * TTS_CHANNELS * TTS_SAMPLE_WIDTH)
        usage = getattr(response, "usage_metadata", None)
        return SynthesizedNarration(
            content=content,
            content_hash=sha256_bytes(content),
            text_hash=sha256_json({"text": script}),
            voice=self.voice,
            speed=1.0,
            sample_rate=TTS_SAMPLE_RATE,
            duration_seconds=round(duration, 3),
            model_name=self.model,
            model_sha256=sha256_json({"model": self.model}),
            voices_sha256=sha256_json({"voice": self.voice}),
            licence_note="Vertex AI Gemini TTS; provider terms apply",
            command_hash=sha256_json(
                {
                    "provider": PROVIDER_KEY,
                    "model": self.model,
                    "voice": self.voice,
                    "text_hash": sha256_json({"text": script}),
                    "prompt_tokens": int(getattr(usage, "prompt_token_count", 0) or 0),
                }
            ),
        )


@dataclass
class VertexTranscriptionAdapter:
    """Gemini transcription through Vertex AI with ADC."""

    profile_key: str = "google-vertex-transcribe"
    model: str = TRANSCRIBE_MODEL
    client: Any | None = None
    project: str | None = None
    location: str | None = None

    def _client(self) -> Any:
        if self.client is not None:
            return self.client
        try:
            from google import genai
        except ImportError as error:
            raise ConfigurationError(
                "google-genai is not installed; install the gcp-image extra"
            ) from error
        self.client = genai.Client(
            vertexai=True,
            project=self.project or DEFAULT_PROJECT,
            location=self.location or DEFAULT_LOCATION,
        )
        return self.client

    def transcribe(self, audio_path: Path | str) -> TranscriptionResult:
        path = Path(audio_path).expanduser().resolve()
        if not path.is_file():
            raise ConfigurationError(f"Audio file not found: {path}")
        content = path.read_bytes()
        client = self._client()
        try:
            from google.genai import types

            part = types.Part.from_bytes(data=content, mime_type=_mime(path))
            response = client.models.generate_content(
                model=self.model,
                contents=[
                    part,
                    "Transcribe this audio exactly. Return the spoken words only.",
                ],
                config=types.GenerateContentConfig(temperature=0),
            )
        except Exception as error:  # noqa: BLE001 - provider SDK raises broadly
            raise RuntimeError(
                f"Vertex transcription failed: {type(error).__name__}"
            ) from None
        text = str(getattr(response, "text", "") or "").strip()
        if not text:
            raise RuntimeError("Vertex returned no transcript text")
        duration = _wav_seconds(content) or 0.0
        return TranscriptionResult(
            text=text,
            segments=(
                TranscriptSegment(start_seconds=0.0, end_seconds=duration, text=text),
            ),
            language="und",
            language_probability=0.0,
            duration_seconds=duration,
            model_name=self.model,
            model_path=None,
            model_sha256=None,
            audio_path=str(path),
            audio_sha256=sha256_file(path),
            command_hash=sha256_json(
                {
                    "provider": PROVIDER_KEY,
                    "model": self.model,
                    "audio_sha256": sha256_file(path),
                }
            ),
        )


def verify_models(
    models: tuple[str, ...] = VERIFIABLE_MODELS,
    *,
    client: Any | None = None,
    project: str | None = None,
    location: str | None = None,
) -> list[CapabilityResult]:
    """Free model availability check; consumes no tokens."""

    resolved_project = project or DEFAULT_PROJECT
    resolved_location = location or DEFAULT_LOCATION
    if client is None:
        try:
            from google import genai
        except ImportError:
            return [
                CapabilityResult(
                    name=f"{PROVIDER_KEY}:{model}",
                    status=CapabilityStatus.UNAVAILABLE,
                    detail="google-genai is not installed",
                    checked_at=utc_now(),
                )
                for model in models
            ]
        client = genai.Client(
            vertexai=True, project=resolved_project, location=resolved_location
        )
    results = []
    for model in models:
        try:
            client.models.get(model=model)
            status = CapabilityStatus.WORKING
            detail = f"available in {resolved_project}/{resolved_location}"
        except Exception as error:  # noqa: BLE001 - provider SDK raises broadly
            status = CapabilityStatus.UNAVAILABLE
            detail = f"{type(error).__name__}; check project, region and API enablement"
        results.append(
            CapabilityResult(
                name=f"{PROVIDER_KEY}:{model}",
                status=status,
                detail=detail,
                checked_at=utc_now(),
            )
        )
    return results


def _first_audio(response: Any) -> bytes:
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None)
            if data:
                if isinstance(data, str):
                    import base64

                    return base64.b64decode(data)
                return bytes(data)
    raise RuntimeError("Vertex TTS returned no audio data")


def _wrap_wav(pcm: bytes) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(TTS_CHANNELS)
        handle.setsampwidth(TTS_SAMPLE_WIDTH)
        handle.setframerate(TTS_SAMPLE_RATE)
        handle.writeframes(pcm)
    return buffer.getvalue()


def _wav_seconds(content: bytes) -> float | None:
    try:
        with wave.open(BytesIO(content), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate() or 1
            return round(frames / rate, 3)
    except (wave.Error, EOFError):
        return None


def _mime(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".wav": "audio/wav",
        ".mp3": "audio/mp3",
        ".m4a": "audio/mp4",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
    }.get(suffix, "audio/wav")

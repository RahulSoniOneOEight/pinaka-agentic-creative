from __future__ import annotations

from typing import Any

from ..adapters.burned_captions import BurnedCaptionAdapter
from ..adapters.captions import CaptionMuxAdapter
from ..captions import (
    build_srt,
    build_srt_from_words,
    cues_for_shots,
    cues_from_words,
    legibility_violations,
    profile_for_locale,
)
from ..enums import (
    ArtifactKind,
)
from ..errors import (
    ConflictError,
    IntegrityError,
)
from ..models import (
    DependencyRef,
)
from ..utils import sha256_bytes


class CaptionsUseCases:
    def export_captions(
        self,
        project_id: str,
        *,
        locale: str | None = None,
        source: str = "auto",
        narration_artifact_id: str | None = None,
        transcriber: Any | None = None,
        pad_to_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Write an SRT from narration (ASR) when it exists, else from the shot plan."""

        if source not in {"auto", "asr", "plan"}:
            raise IntegrityError("Caption source must be auto, asr or plan")
        project = self.repository.get_project(project_id)
        shots = self.repository.list_shots(project_id)
        if not shots:
            raise IntegrityError("Captions require a shot plan")
        active_locale = locale or project.language
        profile = profile_for_locale(active_locale)
        narration = None
        if narration_artifact_id:
            narration = self.repository.get_artifact(narration_artifact_id)
            if narration["project_id"] != project_id:
                raise ConflictError("Narration artifact belongs to another project")
        elif source in {"auto", "asr"}:
            narration = self._latest_narration(project_id)
        if source == "asr" and narration is None:
            raise IntegrityError("ASR captions require a narration artifact")

        source_used = "shot_plan"
        fallback_reason = None
        if narration is not None and source in {"auto", "asr"}:
            narration_path = self.artifact_store.resolve(
                project_id, narration["relative_path"]
            )
            engine = transcriber or self._default_transcriber()
            result = engine.transcribe(narration_path)
            words = self._words_from_transcription(result)
            if not words:
                raise IntegrityError("ASR produced no timed words")
            duration = getattr(result, "duration_seconds", None)
            cues = cues_from_words(
                words,
                profile=profile,
                locale=active_locale,
                pad_to_seconds=pad_to_seconds,
            )
            violations = legibility_violations(cues, profile)
            srt = build_srt_from_words(
                words,
                profile=profile,
                locale=active_locale,
                pad_to_seconds=pad_to_seconds,
            )
            operation = {
                "type": "captions",
                "cues": len(cues),
                "source": "asr",
                "locale": active_locale,
                "legible": not violations,
                "audio_artifact_id": narration["id"],
                "audio_hash": narration["content_hash"],
                "model": getattr(result, "model_name", None)
                or getattr(result, "get", lambda *_: None)("model_name"),
                "model_sha256": getattr(result, "model_sha256", None),
                "command_hash": getattr(result, "command_hash", None),
                "asr_duration_seconds": duration,
                "tail_hold_seconds": cues[-1].get("tail_hold_seconds", 0.0),
                "execution_owner": "local",
                "studio_role": "none",
            }
            dependencies = (
                DependencyRef(
                    entity_type="artifact",
                    entity_id=narration["id"],
                    entity_hash=narration["content_hash"],
                ),
                *(
                    DependencyRef(entity_type="shot", entity_id=shot["id"])
                    for shot in shots
                ),
            )
            source_used = "asr"
        else:
            cues = cues_for_shots(shots, profile=profile)
            violations = legibility_violations(cues, profile)
            srt = build_srt(shots, profile=profile)
            operation = {
                "type": "captions",
                "cues": len(cues),
                "source": "shot_plan",
                "locale": active_locale,
                "legible": not violations,
                "execution_owner": "local",
                "studio_role": "none",
            }
            dependencies = tuple(
                DependencyRef(entity_type="shot", entity_id=shot["id"])
                for shot in shots
            )
            if source == "auto" and narration is None:
                fallback_reason = "no narration artifact"
                operation["fallback_reason"] = fallback_reason

        content = srt.encode("utf-8")
        digest = sha256_bytes(content)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DERIVATIVE,
            relative_path=f"captions/{source_used}-{digest[:12]}.srt",
            content=content,
            operation=operation,
            dependencies=dependencies,
        )
        return {
            "artifact": artifact,
            "cues": len(cues),
            "srt": srt,
            "source": source_used,
            "fallback_reason": fallback_reason,
            "legibility": {"passed": not violations, "violations": violations},
        }

    def _latest_narration(self, project_id: str) -> dict[str, Any] | None:
        candidates = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if (artifact.get("operation") or {}).get("type")
            in {"kokoro_narration", "tts"}
        ]
        return candidates[-1] if candidates else None

    @staticmethod
    def _default_transcriber() -> Any:
        from ..adapters.transcribe import WhisperTranscriber

        return WhisperTranscriber(word_timestamps=True)

    @staticmethod
    def _words_from_transcription(result: Any) -> list[Any]:
        words: list[Any] = []
        segments = (
            result.get("segments", ())
            if isinstance(result, dict)
            else getattr(result, "segments", ())
        )
        for segment in segments:
            segment_words = (
                segment.get("words", ())
                if isinstance(segment, dict)
                else getattr(segment, "words", ())
            )
            words.extend(segment_words)
        if not words and isinstance(result, dict):
            words = list(result.get("words", ()))
        return words

    def burn_captions(
        self,
        project_id: str,
        *,
        video_artifact_id: str,
        captions_artifact_id: str,
        adapter: Any | None = None,
        mode: str = "soft",
    ) -> dict[str, Any]:
        """Attach the SRT as a soft subtitle track on the rendered video."""

        import tempfile
        from pathlib import Path as _Path

        video = self.repository.get_artifact(video_artifact_id)
        subtitles = self.repository.get_artifact(captions_artifact_id)
        for artifact in (video, subtitles):
            if artifact["project_id"] != project_id:
                raise ConflictError("Artifact belongs to a different project")
        if not subtitles["relative_path"].endswith(".srt"):
            raise IntegrityError("Caption artifact must be an SRT")
        if mode not in {"soft", "hard"}:
            raise IntegrityError("Caption mode must be 'soft' or 'hard'")
        video_path = self.artifact_store.resolve(project_id, video["relative_path"])
        srt_path = self.artifact_store.resolve(project_id, subtitles["relative_path"])
        with tempfile.TemporaryDirectory(prefix="pinaka-captions-") as temp:
            output = _Path(temp) / "captioned.mp4"
            if mode == "hard":
                result = (adapter or BurnedCaptionAdapter()).burn(
                    video_path=video_path, srt_path=srt_path, output_path=output
                )
            else:
                result = (adapter or CaptionMuxAdapter()).mux(
                    video_path=video_path, srt_path=srt_path, output_path=output
                )
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.RENDER,
            relative_path=f"renders/captioned-{result.content_hash[:12]}.mp4",
            content=result.content,
            operation={
                "type": "caption_burn",
                "mode": mode,
                "video_artifact_id": video["id"],
                "captions_artifact_id": subtitles["id"],
                "subtitle_hash": result.subtitle_hash,
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=video["id"],
                    entity_hash=video["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=subtitles["id"],
                    entity_hash=subtitles["content_hash"],
                ),
            ),
        )
        return {"video": artifact, "metadata": result.metadata}

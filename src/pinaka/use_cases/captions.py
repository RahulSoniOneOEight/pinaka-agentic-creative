from __future__ import annotations

from typing import Any

from ..adapters.burned_captions import BurnedCaptionAdapter
from ..adapters.captions import CaptionMuxAdapter
from ..captions import (
    build_srt,
    cues_for_shots,
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
        self, project_id: str, *, locale: str | None = None
    ) -> dict[str, Any]:
        """Write a deterministic SRT from the shot plan, wrapped to the locale profile."""

        project = self.repository.get_project(project_id)
        shots = self.repository.list_shots(project_id)
        if not shots:
            raise IntegrityError("Captions require a shot plan")
        active_locale = locale or project.language
        profile = profile_for_locale(active_locale)
        cues = cues_for_shots(shots, profile=profile)
        violations = legibility_violations(cues, profile)
        srt = build_srt(shots, profile=profile)
        content = srt.encode("utf-8")
        digest = sha256_bytes(content)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DERIVATIVE,
            relative_path=f"captions/captions-{digest[:12]}.srt",
            content=content,
            operation={
                "type": "captions",
                "cues": len(cues),
                "source": "shot_plan",
                "locale": active_locale,
                "legible": not violations,
            },
            dependencies=tuple(
                DependencyRef(entity_type="shot", entity_id=shot["id"])
                for shot in shots
            ),
        )
        return {
            "artifact": artifact,
            "cues": len(cues),
            "srt": srt,
            "legibility": {"passed": not violations, "violations": violations},
        }

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

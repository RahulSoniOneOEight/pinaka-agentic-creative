from __future__ import annotations

from typing import Any

from ..adapters.encode import FFmpegEncodeAdapter, VideoEncodeAdapter
from ..adapters.frames import ChromeFrameCapture, FrameCaptureAdapter
from ..adapters.frames_playwright import PlaywrightFrameCapture
from ..composition import (
    build_composition,
    composition_duration,
    validate_composition,
)
from ..enums import (
    ArtifactKind,
)
from ..errors import (
    ConflictError,
    IntegrityError,
    NotFoundError,
)
from ..models import (
    DependencyRef,
)
from ..policy.guards import (
    require_active_project,
)
from ..utils import sha256_bytes
from ._shared import (
    MAX_EMBEDDED_BYTES,
    MAX_LAYER_BYTES,
    VIDEO_MIME,
    _image_mime,
)


class CompositionUseCases:
    def _composition_layers(
        self, project_id: str, shots: list[dict[str, Any]]
    ) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        import base64

        slots = {slot["id"]: slot for slot in self.repository.list_slots(project_id)}
        shots_by_id = {shot["id"]: shot for shot in shots}
        layers: dict[str, list[dict[str, Any]]] = {}
        skipped: list[dict[str, Any]] = []
        total = 0
        for assignment in self.repository.list_slot_assets(project_id):
            slot = slots.get(assignment["slot_id"])
            if not slot:
                continue
            shot = shots_by_id.get(slot["shot_id"])
            if not shot:
                continue
            artifact = self.repository.get_artifact(assignment["artifact_id"])
            path = self.artifact_store.resolve(project_id, artifact["relative_path"])
            image_mime = _image_mime(path)
            if image_mime is None and path.suffix.lower() in VIDEO_MIME:
                if not path.is_file():
                    skipped.append(
                        {"artifact_id": artifact["id"], "reason": "file missing"}
                    )
                    continue
                layers.setdefault(shot["stable_key"], []).append(
                    {
                        "role": slot["role"] or assignment["role"],
                        "kind": "video",
                        "mime": VIDEO_MIME[path.suffix.lower()],
                        "path": path.as_uri(),
                    }
                )
                continue
            mime = image_mime
            if mime is None:
                skipped.append(
                    {
                        "artifact_id": artifact["id"],
                        "reason": f"unsupported media type {path.suffix!r}",
                    }
                )
                continue
            if not path.is_file():
                skipped.append(
                    {"artifact_id": artifact["id"], "reason": "file missing"}
                )
                continue
            data = path.read_bytes()
            if len(data) > MAX_LAYER_BYTES or total + len(data) > MAX_EMBEDDED_BYTES:
                skipped.append(
                    {"artifact_id": artifact["id"], "reason": "layer too large"}
                )
                continue
            total += len(data)
            layers.setdefault(shot["stable_key"], []).append(
                {
                    "role": slot["role"] or assignment["role"],
                    "mime": mime,
                    "data": base64.b64encode(data).decode("ascii"),
                }
            )
        return layers, skipped

    def author_composition(
        self,
        project_id: str,
        *,
        width: int = 1080,
        height: int = 1920,
        title: str | None = None,
        embed_layers: bool = True,
    ) -> dict[str, Any]:
        """Author one editable HyperFrames composition from the governed plan."""
        project = self.repository.get_project(project_id)
        require_active_project(
            project,
            message=f"Cannot author a composition for a {project.status.value} project",
        )
        brief = self.repository.latest_workable_brief(project_id)
        if not brief:
            raise IntegrityError("A current workable brief is required")
        shots = self.repository.list_shots(project_id)
        if not shots:
            raise IntegrityError("A composition requires at least one planned shot")
        elements = self.repository.list_creative_elements(project_id)
        layers, skipped = (
            self._composition_layers(project_id, shots) if embed_layers else ({}, [])
        )
        html = build_composition(
            title=title or project.name,
            width=width,
            height=height,
            shots=shots,
            elements=elements,
            layers=layers,
        )
        report = validate_composition(html)
        if not report["valid"]:
            raise IntegrityError(
                "Composition failed validation: " + ", ".join(report["problems"])
            )
        content = html.encode("utf-8")
        digest = sha256_bytes(content)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"scenes/composition-{digest[:12]}/index.html",
            content=content,
            operation={
                "type": "hyperframes_composition",
                "schema": report.get("schema", "hyperframes"),
                "shots": report["shot_count"],
                "width": width,
                "height": height,
            },
            dependencies=(
                DependencyRef(entity_type="workable_brief", entity_id=brief["id"]),
                *(
                    DependencyRef(entity_type="shot", entity_id=shot["id"])
                    for shot in shots
                ),
                *(
                    DependencyRef(
                        entity_type="creative_element", entity_id=element["id"]
                    )
                    for element in elements
                ),
            ),
        )
        return {
            "artifact": artifact,
            "validation": report,
            "content_hash": digest,
            "shot_count": report["shot_count"],
            "layers_embedded": report["layer_count"],
            "layers_skipped": skipped,
        }

    def render_composition(
        self,
        project_id: str,
        *,
        composition_artifact_id: str | None = None,
        fps: int = 24,
        width: int = 1080,
        height: int = 1920,
        max_frames: int = 600,
        capture: FrameCaptureAdapter | None = None,
        encoder: VideoEncodeAdapter | None = None,
    ) -> dict[str, Any]:
        """Render one authored composition to a silent H.264 preview."""

        import tempfile
        from pathlib import Path as _Path

        if fps < 1 or fps > 60:
            raise IntegrityError("fps must be between 1 and 60")
        artifact = (
            self.repository.get_artifact(composition_artifact_id)
            if composition_artifact_id
            else self._latest_composition(project_id)
        )
        if not artifact:
            raise NotFoundError("No composition has been authored for this project")
        if artifact["project_id"] != project_id:
            raise ConflictError("Composition belongs to a different project")
        html = self.read_artifact_text(artifact["id"])
        report = validate_composition(html)
        if not report["valid"]:
            raise IntegrityError(
                "Composition failed validation: " + ", ".join(report["problems"])
            )
        duration = composition_duration(html)
        frame_count = int((duration * fps).to_integral_value(rounding="ROUND_CEILING"))
        if frame_count < 1:
            raise IntegrityError("Composition duration produces no frames")
        if frame_count > max_frames:
            raise IntegrityError(
                f"Render would produce {frame_count} frames; limit is {max_frames}"
            )
        html_path = self.artifact_store.resolve(project_id, artifact["relative_path"])
        if capture is None:
            capture = (
                PlaywrightFrameCapture()
                if 'class="layer clip"' in html
                else ChromeFrameCapture()
            )
        encoder = encoder or FFmpegEncodeAdapter()
        captured = capture.capture(
            html_path=html_path,
            frame_count=frame_count,
            fps=fps,
            width=width,
            height=height,
        )
        with tempfile.TemporaryDirectory(prefix="pinaka-render-") as temp:
            frames_dir = _Path(temp)
            for frame in captured.frames:
                (frames_dir / f"frame-{frame.index:04d}.png").write_bytes(frame.content)
            encoded = encoder.encode(
                frames_dir=frames_dir,
                frame_count=frame_count,
                fps=fps,
                width=width,
                height=height,
            )
        duration_text = format(duration.normalize(), "f")
        digest = sha256_bytes(encoded.content)
        video = self.write_artifact(
            project_id,
            kind=ArtifactKind.RENDER,
            relative_path=f"renders/composition-{digest[:12]}.mp4",
            content=encoded.content,
            operation={
                "type": "composition_render",
                "composition_artifact_id": artifact["id"],
                "fps": fps,
                "frames": frame_count,
                "duration_seconds": duration_text,
                "width": width,
                "height": height,
                "encoder": encoder.profile_key,
                "command_hash": encoded.command_hash,
                "encoder_version": encoded.encoder_version,
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=artifact["id"],
                    entity_hash=artifact["content_hash"],
                ),
            ),
        )
        return {
            "video": video,
            "composition": artifact,
            "fps": fps,
            "frames": frame_count,
            "duration_seconds": duration_text,
            "metadata": encoded.metadata,
            "command_hash": encoded.command_hash,
        }

    def _latest_composition(self, project_id: str) -> dict[str, Any] | None:
        for artifact in reversed(self.repository.list_artifacts(project_id)):
            if artifact["kind"] == ArtifactKind.DESIGN.value and artifact[
                "relative_path"
            ].startswith("scenes/composition-"):
                return artifact
        return None

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from ..adapters.encode import FFmpegEncodeAdapter, VideoEncodeAdapter
from ..adapters.frames import ChromeFrameCapture, FrameCaptureAdapter
from ..adapters.frames_playwright import PlaywrightFrameCapture
from ..composition import (
    build_composition,
    composition_duration,
    composition_size,
    validate_composition,
)
from ..enums import (
    ArtifactKind,
)
from ..errors import (
    ConfigurationError,
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

LOCAL_SRC = re.compile(r'src="(file:///[^"]+)"')
from ._shared import (
    MAX_EMBEDDED_BYTES,
    MAX_LAYER_BYTES,
    VIDEO_MIME,
    _image_mime,
)


def _materialize_local_media(html: str, project_dir: Path) -> str:
    """Copy file:// layers into the HyperFrames project and use relative paths."""

    assets = project_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    for uri in set(LOCAL_SRC.findall(html)):
        parsed = urlparse(uri)
        source = Path(url2pathname(unquote(parsed.path)))
        if not source.is_file():
            raise IntegrityError(f"Layer file is missing: {source}")
        digest = sha256_bytes(source.read_bytes())[:12]
        target = assets / f"{digest}-{source.name}"
        if not target.is_file():
            shutil.copy2(source, target)
        html = html.replace(uri, f"assets/{target.name}")
    return html


class CompositionUseCases:
    def _composition_layers(
        self, project_id: str, shots: list[dict[str, Any]]
    ) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        import base64

        slots = {slot["id"]: slot for slot in self.repository.list_slots(project_id)}
        shots_by_id = {shot["id"]: shot for shot in shots}
        z_by_slot: dict[str, int] = {}
        for production in self.repository.list_shot_productions(project_id):
            for layer in production.get("layers", ()):
                slot_id = layer.get("slot_id")
                z = layer.get("z")
                if slot_id and isinstance(z, int):
                    z_by_slot[slot_id] = z
        layers: dict[str, list[dict[str, Any]]] = {}
        skipped: list[dict[str, Any]] = []
        total = 0
        for assignment in self.repository.list_slot_assets(project_id):
            # The master render uses the primary asset only; backups and A/B
            # variants are deliberate alternates, not extra layers.
            if assignment.get("role") != "primary":
                continue
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
                        "z": z_by_slot.get(slot["id"], 10),
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
                    "z": z_by_slot.get(slot["id"], 10),
                }
            )
        return layers, skipped

    @staticmethod
    def _gsap_source() -> str:
        """Inline GSAP when the pinned HyperFrames install ships it."""

        from ..adapters.hyperframes import HyperFramesAdapter

        adapter = HyperFramesAdapter()
        if not adapter.installed():
            return ""
        try:
            return adapter.gsap_source()
        except ConfigurationError:
            return ""

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
            gsap_script=self._gsap_source(),
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
        renderer: str | None = None,
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
        renderer_name = (
            (renderer or os.environ.get("PINAKA_RENDERER", "legacy")).strip().lower()
        )
        if renderer_name not in {"legacy", "hyperframes"}:
            raise IntegrityError(
                f"Unknown renderer {renderer_name!r}; use legacy or hyperframes"
            )
        if renderer_name == "hyperframes":
            from ..adapters.hyperframes import HyperFramesAdapter

            adapter = HyperFramesAdapter()
            if not adapter.installed():
                raise IntegrityError(
                    "PINAKA_RENDERER=hyperframes requires the pinned install: "
                    "run npm ci in tools/hyperframes"
                )
            declared = composition_size(html)
            if declared != (width, height):
                raise IntegrityError(
                    f"Composition is {declared[0]}x{declared[1]} but render asked "
                    f"for {width}x{height}"
                )
            temp = tempfile.mkdtemp(prefix="pinaka-hyperframes-")
            try:
                project_dir = _Path(temp) / "project"
                project_dir.mkdir()
                local_html = _materialize_local_media(html, project_dir)
                (project_dir / "index.html").write_text(local_html, encoding="utf-8")
                (project_dir / "hyperframes.json").write_text(
                    '{"paths":{"blocks":"compositions",'
                    '"components":"compositions/components","assets":"assets"}}',
                    encoding="utf-8",
                )
                lint = adapter.lint(project_dir / "index.html")
                if not lint.get("ok") or lint.get("errorCount"):
                    raise IntegrityError(
                        "HyperFrames lint rejected the composition: "
                        + "; ".join(
                            str(item.get("message"))
                            for item in lint.get("findings", ())
                        )
                    )
                result = adapter.render(
                    project_dir,
                    _Path(temp) / "render.mp4",
                    fps=fps,
                    quality="looks",
                )
                content = (_Path(temp) / "render.mp4").read_bytes()
            finally:
                shutil.rmtree(temp, ignore_errors=True)
            encoder_key = "hyperframes"
            command_hash = result["command_hash"]
            encoder_version = f"hyperframes/{result['version']}"
            metadata = {
                "renderer": "hyperframes",
                "version": result["version"],
                "lint": {
                    "errors": lint.get("errorCount", 0),
                    "warnings": lint.get("warningCount", 0),
                },
                "bytes": result["bytes"],
            }
        else:
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
                    (frames_dir / f"frame-{frame.index:04d}.png").write_bytes(
                        frame.content
                    )
                encoded = encoder.encode(
                    frames_dir=frames_dir,
                    frame_count=frame_count,
                    fps=fps,
                    width=width,
                    height=height,
                )
            content = encoded.content
            encoder_key = encoder.profile_key
            command_hash = encoded.command_hash
            encoder_version = encoded.encoder_version
            metadata = encoded.metadata
        duration_text = format(duration.normalize(), "f")
        digest = sha256_bytes(content)
        video = self.write_artifact(
            project_id,
            kind=ArtifactKind.RENDER,
            relative_path=f"renders/composition-{digest[:12]}.mp4",
            content=content,
            operation={
                "type": "composition_render",
                "composition_artifact_id": artifact["id"],
                "fps": fps,
                "frames": frame_count,
                "duration_seconds": duration_text,
                "width": width,
                "height": height,
                "renderer": encoder_key,
                "encoder": encoder_key,
                "command_hash": command_hash,
                "encoder_version": encoder_version,
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=artifact["id"],
                    entity_hash=artifact["content_hash"],
                ),
            ),
        )
        # A new animatic render invalidates any active animatic approval.
        self.repository.invalidate_active_approval(
            project_id,
            "animatic",
            reason=f"New animatic render: {video['id']}",
        )
        return {
            "video": video,
            "composition": artifact,
            "fps": fps,
            "frames": frame_count,
            "duration_seconds": duration_text,
            "metadata": metadata,
            "command_hash": command_hash,
            "renderer": encoder_key,
        }

    def _latest_composition(self, project_id: str) -> dict[str, Any] | None:
        for artifact in reversed(self.repository.list_artifacts(project_id)):
            if artifact["kind"] == ArtifactKind.DESIGN.value and artifact[
                "relative_path"
            ].startswith("scenes/composition-"):
                return artifact
        return None

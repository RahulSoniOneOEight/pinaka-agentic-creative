from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any

from ..adapters.encode import FFmpegEncodeAdapter, VideoEncodeAdapter
from ..adapters.frames import ChromeFrameCapture, FrameCaptureAdapter
from ..animatic import decimal_seconds
from ..enums import (
    ArtifactKind,
    CapabilityStatus,
    ProjectStatus,
    ToolIntegrationMode,
    ToolOperationStatus,
)
from ..errors import (
    ApprovalRequiredError,
    ConflictError,
    IntegrityError,
    NotFoundError,
)
from ..models import (
    DependencyRef,
    ExecutionReceiptCreate,
    ToolOperationRequest,
    ToolProfileCreate,
)
from ..utils import canonical_json, sha256_bytes, sha256_file


class FramesUseCases:
    def capture_animatic_frames(
        self,
        project_id: str,
        *,
        frame_count: int = 300,
        fps: int = 30,
        adapter: FrameCaptureAdapter | None = None,
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status not in {
            ProjectStatus.ANIMATIC_READY,
            ProjectStatus.ANIMATIC_APPROVED,
        }:
            raise ApprovalRequiredError(
                "Frame capture requires an animatic-ready project"
            )
        bundle = self._latest_animatic_bundle(project_id)
        if bundle:
            _, _, artifacts = bundle
        else:
            legacy_paths = {
                "timeline": "artifacts/timeline.json",
                "animatic": "designs/animatic.html",
                "captions": "artifacts/captions.srt",
            }
            artifacts = {
                name: self.repository.artifact_by_path(project_id, path)
                for name, path in legacy_paths.items()
            }
        missing = [name for name, artifact in artifacts.items() if artifact is None]
        if missing:
            raise NotFoundError(
                "Frame capture is missing artifacts: " + ", ".join(sorted(missing))
            )
        if not all(
            self.verify_artifact(artifact["id"]) for artifact in artifacts.values()
        ):
            raise IntegrityError("Animatic artifacts failed hash verification")
        timeline = json.loads(self.read_artifact_text(artifacts["timeline"]["id"]))
        html_artifact = artifacts["animatic"]
        width = int(timeline["canvas"]["width_px"])
        height = int(timeline["canvas"]["height_px"])
        total = decimal_seconds(timeline["total_duration_seconds"])
        requested = Decimal(frame_count) / Decimal(fps)
        if requested != total:
            raise IntegrityError(
                f"{frame_count} frames at {fps}fps cover {requested}s, "
                f"but the animatic is {total}s"
            )

        tool = self.register_tool_profile(
            ToolProfileCreate(
                stable_key="chrome-headless-capture",
                name="Chrome headless frame capture",
                integration_mode=ToolIntegrationMode.HEADLESS,
                executable_or_endpoint="chrome:--headless=new",
                supported_operations=("capture_frames",),
                limits={"paid": False},
                capability_status=CapabilityStatus.WORKING,
            )
        )
        request = ToolOperationRequest(
            tool_profile_id=tool["id"],
            operation="capture_frames",
            idempotency_key=(
                f"frames-{html_artifact['content_hash'][:12]}-{frame_count}-{fps}"
            ),
            parameters={
                "html_artifact_id": html_artifact["id"],
                "html_hash": html_artifact["content_hash"],
                "frame_count": frame_count,
                "fps": fps,
                "width": width,
                "height": height,
            },
            output_declaration={
                "media_type": "image/png-sequence",
                "frame_count": frame_count,
            },
            limits={"paid": False, "max_frames": frame_count},
        )
        operation = self.reserve_tool_operation(project_id, request)
        existing_receipt = self.repository.get_execution_receipt(operation["id"])
        if existing_receipt:
            output_ids = existing_receipt["output_artifact_ids"]
            if not output_ids:
                raise IntegrityError(
                    "Captured-frame receipt has no output manifest artifact"
                )
            manifest_artifact = self.repository.get_artifact(output_ids[0])
            manifest = json.loads(self.read_artifact_text(manifest_artifact["id"]))
            return {
                "project": project.model_dump(mode="json"),
                "frame_manifest": manifest,
                "manifest_artifact": manifest_artifact,
                "receipt": existing_receipt,
                "resumed": True,
            }
        if operation["status"] == ToolOperationStatus.OUTCOME_UNKNOWN.value:
            raise ConflictError(
                "Frame capture outcome is unknown and requires reconciliation"
            )
        operation = self.repository.mark_tool_operation_running(operation["id"])
        started = perf_counter()
        capture = adapter or ChromeFrameCapture()
        if capture.profile_key != tool["stable_key"]:
            raise IntegrityError(
                f"Capture adapter {capture.profile_key!r} does not match "
                f"tool profile {tool['stable_key']!r}"
            )
        html_path = self.artifact_store.resolve(
            project_id, html_artifact["relative_path"]
        )
        try:
            result = capture.capture(
                html_path=html_path,
                frame_count=frame_count,
                fps=fps,
                width=width,
                height=height,
            )
        except (OSError, RuntimeError, ValueError) as error:
            self.record_execution_receipt(
                operation["id"],
                ExecutionReceiptCreate(
                    status=ToolOperationStatus.FAILED,
                    elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                    details={"type": type(error).__name__, "redacted": True},
                ),
            )
            raise IntegrityError(
                f"Frame capture failed: {type(error).__name__}"
            ) from error

        stored_frames: list[dict[str, Any]] = []
        for frame in result.frames:
            relative_path = (
                f"renders/frames-{html_artifact['content_hash'][:12]}/"
                f"frame-{frame.index:04d}.png"
            )
            stored = self.artifact_store.write_bytes(
                project_id, relative_path, frame.content
            )
            if stored.content_hash != frame.content_hash:
                raise IntegrityError(
                    f"Stored frame hash mismatch: {stored.relative_path}"
                )
            stored_frames.append(
                {
                    "index": frame.index,
                    "time_seconds": frame.time_seconds,
                    "relative_path": stored.relative_path,
                    "content_hash": stored.content_hash,
                    "size_bytes": stored.size_bytes,
                }
            )

        manifest = {
            "schema_version": 1,
            "project_id": project_id,
            "html_artifact_id": html_artifact["id"],
            "html_hash": html_artifact["content_hash"],
            "timeline_hash": timeline["timeline_hash"],
            "frame_count": frame_count,
            "fps": fps,
            "duration_seconds": str(total),
            "width_px": width,
            "height_px": height,
            "browser": result.renderer_version,
            "command_hash": result.command_hash,
            "frames": stored_frames,
        }
        manifest_bytes = canonical_json(manifest).encode("utf-8")
        manifest_hash = sha256_bytes(manifest_bytes)
        manifest_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.PREVIEW,
            relative_path=f"renders/frame-manifest-{manifest_hash[:12]}.json",
            content=manifest_bytes,
            operation={
                "type": "frame_capture",
                "frame_count": frame_count,
                "fps": fps,
                "browser": result.renderer_version,
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=html_artifact["id"],
                    entity_hash=html_artifact["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=artifacts["timeline"]["id"],
                    entity_hash=artifacts["timeline"]["content_hash"],
                ),
            ),
        )
        receipt = self.record_execution_receipt(
            operation["id"],
            ExecutionReceiptCreate(
                status=ToolOperationStatus.SUCCEEDED,
                elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                output_artifact_ids=(manifest_artifact["id"],),
                response_hash=manifest_hash,
                details={
                    "frame_count": frame_count,
                    "fps": fps,
                    "browser": result.renderer_version,
                    "width_px": width,
                    "height_px": height,
                },
            ),
        )
        return {
            "project": project.model_dump(mode="json"),
            "frame_manifest": manifest,
            "manifest_artifact": manifest_artifact,
            "receipt": receipt,
            "resumed": False,
        }

    def encode_silent_animatic(
        self,
        project_id: str,
        *,
        adapter: VideoEncodeAdapter | None = None,
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status not in {
            ProjectStatus.ANIMATIC_READY,
            ProjectStatus.ANIMATIC_APPROVED,
        }:
            raise ApprovalRequiredError(
                "Silent encode requires an animatic-ready project"
            )
        manifests = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if artifact["kind"] == ArtifactKind.PREVIEW.value
            and artifact["relative_path"].startswith("renders/frame-manifest-")
        ]
        if not manifests:
            raise NotFoundError("No captured frame manifest exists for this project")
        frame_manifest_artifact = max(manifests, key=lambda item: item["created_at"])
        if not self.verify_artifact(frame_manifest_artifact["id"]):
            raise IntegrityError("Frame manifest failed hash verification")
        frame_manifest = json.loads(
            self.read_artifact_text(frame_manifest_artifact["id"])
        )
        if frame_manifest.get("project_id") != project_id:
            raise IntegrityError("Frame manifest belongs to another project")

        frame_dir: Path | None = None
        for frame in frame_manifest["frames"]:
            path = self.artifact_store.resolve(project_id, frame["relative_path"])
            if not path.is_file() or sha256_file(path) != frame["content_hash"]:
                raise IntegrityError(
                    f"Captured frame failed hash verification: {frame['relative_path']}"
                )
            if frame_dir is None:
                frame_dir = path.parent
            elif path.parent != frame_dir:
                raise IntegrityError("Captured frames are not in one directory")
        if frame_dir is None:
            raise IntegrityError("Frame manifest contains no frames")

        frame_count = int(frame_manifest["frame_count"])
        fps = int(frame_manifest["fps"])
        width = int(frame_manifest["width_px"])
        height = int(frame_manifest["height_px"])
        duration = decimal_seconds(frame_manifest["duration_seconds"])

        tool = self.register_tool_profile(
            ToolProfileCreate(
                stable_key="ffmpeg-h264",
                name="FFmpeg H.264 encoder",
                integration_mode=ToolIntegrationMode.NATIVE,
                executable_or_endpoint="python:imageio_ffmpeg",
                supported_operations=("encode_silent_master",),
                limits={"paid": False},
                capability_status=CapabilityStatus.WORKING,
            )
        )
        request = ToolOperationRequest(
            tool_profile_id=tool["id"],
            operation="encode_silent_master",
            idempotency_key=(
                f"encode-silent-{frame_manifest_artifact['content_hash'][:12]}"
            ),
            parameters={
                "frame_manifest_artifact_id": frame_manifest_artifact["id"],
                "frame_manifest_hash": frame_manifest_artifact["content_hash"],
                "frame_count": frame_count,
                "fps": fps,
                "width_px": width,
                "height_px": height,
            },
            output_declaration={
                "media_type": "video/mp4",
                "codec": "h264",
                "audio": False,
            },
            limits={"paid": False, "max_seconds": str(duration)},
        )
        operation = self.reserve_tool_operation(project_id, request)
        existing_receipt = self.repository.get_execution_receipt(operation["id"])
        if existing_receipt:
            output_ids = existing_receipt["output_artifact_ids"]
            if not output_ids:
                raise IntegrityError("Encode receipt has no output artifacts")
            video_artifact = self.repository.get_artifact(output_ids[0])
            encode_manifest_artifact = (
                self.repository.get_artifact(output_ids[1])
                if len(output_ids) > 1
                else None
            )
            return {
                "project": project.model_dump(mode="json"),
                "video_artifact": video_artifact,
                "encode_manifest_artifact": encode_manifest_artifact,
                "receipt": existing_receipt,
                "resumed": True,
            }
        if operation["status"] == ToolOperationStatus.OUTCOME_UNKNOWN.value:
            raise ConflictError("Encode outcome is unknown and requires reconciliation")
        operation = self.repository.mark_tool_operation_running(operation["id"])
        started = perf_counter()
        encoder = adapter or FFmpegEncodeAdapter()
        if encoder.profile_key != tool["stable_key"]:
            raise IntegrityError(
                f"Encode adapter {encoder.profile_key!r} does not match "
                f"tool profile {tool['stable_key']!r}"
            )
        try:
            result = encoder.encode(
                frames_dir=frame_dir,
                frame_count=frame_count,
                fps=fps,
                width=width,
                height=height,
            )
        except (OSError, RuntimeError, ValueError) as error:
            self.record_execution_receipt(
                operation["id"],
                ExecutionReceiptCreate(
                    status=ToolOperationStatus.FAILED,
                    elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                    details={"type": type(error).__name__, "redacted": True},
                ),
            )
            raise IntegrityError(
                f"Silent encode failed: {type(error).__name__}"
            ) from error

        metadata = result.metadata
        expected = {
            "width_px": width,
            "height_px": height,
            "fps": float(fps),
        }
        observed = {
            "width_px": metadata.get("width_px"),
            "height_px": metadata.get("height_px"),
            "fps": metadata.get("fps"),
        }
        if observed != expected:
            raise IntegrityError(
                f"Encoded stream dimensions/fps {observed} do not match {expected}"
            )
        if metadata.get("codec") != "h264" or metadata.get("has_audio"):
            raise IntegrityError("Encoded master is not a silent H.264 video")
        if abs(float(metadata.get("duration_seconds", 0)) - float(duration)) > 0.05:
            raise IntegrityError("Encoded duration does not match the frame timeline")
        if int(metadata.get("size_bytes", 0)) != len(result.content):
            raise IntegrityError("Encoded byte size does not match metadata")

        video_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.RENDER,
            relative_path=f"renders/animatic-silent-{result.content_hash[:12]}.mp4",
            content=result.content,
            operation={
                "type": "encode_silent_master",
                "codec": "h264",
                "fps": fps,
                "frame_count": frame_count,
                "encoder": result.encoder_version,
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=frame_manifest_artifact["id"],
                    entity_hash=frame_manifest_artifact["content_hash"],
                ),
            ),
        )
        encode_manifest = {
            "schema_version": 1,
            "project_id": project_id,
            "frame_manifest_artifact_id": frame_manifest_artifact["id"],
            "frame_manifest_hash": frame_manifest_artifact["content_hash"],
            "video_artifact_id": video_artifact["id"],
            "video_hash": video_artifact["content_hash"],
            "codec": "h264",
            "audio": False,
            "frame_count": frame_count,
            "fps": fps,
            "duration_seconds": str(duration),
            "width_px": width,
            "height_px": height,
            "metadata": metadata,
            "encoder_version": result.encoder_version,
            "command_hash": result.command_hash,
            "licence_note": (
                "FFmpeg GPL-enabled build with libx264. Internal production use; "
                "review distribution obligations before distributing the binary."
            ),
        }
        encode_manifest_bytes = canonical_json(encode_manifest).encode("utf-8")
        encode_manifest_hash = sha256_bytes(encode_manifest_bytes)
        encode_manifest_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.PREVIEW,
            relative_path=(f"renders/encode-manifest-{encode_manifest_hash[:12]}.json"),
            content=encode_manifest_bytes,
            operation={
                "type": "silent_encode_manifest",
                "codec": "h264",
                "audio": False,
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=video_artifact["id"],
                    entity_hash=video_artifact["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=frame_manifest_artifact["id"],
                    entity_hash=frame_manifest_artifact["content_hash"],
                ),
            ),
        )
        receipt = self.record_execution_receipt(
            operation["id"],
            ExecutionReceiptCreate(
                status=ToolOperationStatus.SUCCEEDED,
                elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                output_artifact_ids=(
                    video_artifact["id"],
                    encode_manifest_artifact["id"],
                ),
                response_hash=encode_manifest_hash,
                details={
                    "codec": "h264",
                    "audio": False,
                    "frame_count": frame_count,
                    "fps": fps,
                    "duration_seconds": str(duration),
                    "encoder": result.encoder_version,
                },
            ),
        )
        return {
            "project": project.model_dump(mode="json"),
            "video_artifact": video_artifact,
            "encode_manifest": encode_manifest,
            "encode_manifest_artifact": encode_manifest_artifact,
            "receipt": receipt,
            "resumed": False,
        }

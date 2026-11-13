from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from ..adapters.narration import KokoroNarrationAdapter, NarrationAdapter
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
from ..operations import operation as catalog_operation
from ..policy.guards import require_active_project
from ..utils import canonical_json, sha256_bytes, sha256_json


class NarrationUseCases:
    def _synthesize_script_narration(
        self,
        project_id: str,
        *,
        script: str,
        voice: str,
        speed: float,
        adapter: NarrationAdapter | None,
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        require_active_project(
            project, message="Cannot add narration to a closed project"
        )
        text = script.strip()
        if not text:
            raise IntegrityError("Narration script is empty")
        narrator = adapter or KokoroNarrationAdapter.from_default_paths(
            voice=voice, speed=speed
        )
        profile = self.register_tool_profile(
            ToolProfileCreate(
                stable_key=narrator.profile_key,
                name="Local narration",
                integration_mode=ToolIntegrationMode.NATIVE,
                executable_or_endpoint="local:kokoro",
                supported_operations=("speech.synthesize",),
                limits={"paid": False},
            )
        )
        request = ToolOperationRequest(
            tool_profile_id=profile["id"],
            operation="speech.synthesize",
            idempotency_key="script-tts-"
            + sha256_json(
                {
                    "text": text,
                    "voice": voice,
                    "speed": speed,
                    "adapter": narrator.profile_key,
                }
            )[:32],
            parameters={"text": text, "voice": voice, "speed": speed},
            limits={"paid": False},
        )
        reserved = self.reserve_tool_operation(project_id, request)
        previous = self.repository.get_execution_receipt(reserved["id"])
        if previous:
            if previous["status"] != ToolOperationStatus.SUCCEEDED.value:
                raise ConflictError(
                    "Narration attempt failed; reconcile before another take"
                )
            artifact = self.repository.get_artifact(previous["output_artifact_ids"][0])
            if not self.verify_artifact(artifact["id"]):
                raise IntegrityError("Saved narration failed hash verification")
            return {
                "artifact": artifact,
                "receipt": previous,
                "resumed": True,
                "reservation": None,
            }
        self.repository.mark_tool_operation_running(reserved["id"])
        started = perf_counter()
        try:
            result = narrator.synthesize(text)
            if sha256_bytes(result.content) != result.content_hash:
                raise IntegrityError("Narration content hash mismatch")
        except (OSError, RuntimeError, ValueError, IntegrityError) as error:
            self.record_execution_receipt(
                reserved["id"],
                ExecutionReceiptCreate(
                    status=ToolOperationStatus.FAILED,
                    elapsed_ms=int((perf_counter() - started) * 1000),
                    details={"type": type(error).__name__, "redacted": True},
                ),
            )
            raise
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.AUDIO,
            relative_path=f"audio/narration-{result.content_hash[:12]}.wav",
            content=result.content,
            operation={
                "type": "tts",
                "provider": "kokoro",
                "model": result.model_name,
                "voice": result.voice,
                "duration_seconds": result.duration_seconds,
                "chars": len(text),
                "text_hash": sha256_bytes(text.encode()),
                "sample_rate": result.sample_rate,
                "paid": False,
                "tool_operation_id": reserved["id"],
                "requires_approval": True,
            },
        )
        receipt = self.record_execution_receipt(
            reserved["id"],
            ExecutionReceiptCreate(
                status=ToolOperationStatus.SUCCEEDED,
                elapsed_ms=int((perf_counter() - started) * 1000),
                output_artifact_ids=(artifact["id"],),
                response_hash=artifact["content_hash"],
                details={"paid": False},
            ),
        )
        return {
            "artifact": artifact,
            "receipt": receipt,
            "duration_seconds": result.duration_seconds,
            "reservation": None,
            "resumed": False,
        }

    def synthesize_narration(
        self,
        project_id: str,
        *,
        voice: str = "af_heart",
        speed: float = 1.0,
        adapter: NarrationAdapter | None = None,
        script: str | None = None,
        route: str = "kokoro",
    ) -> dict[str, Any]:
        catalog_operation("speech.synthesize").validate(route)
        if script is not None:
            return self._synthesize_script_narration(
                project_id, script=script, voice=voice, speed=speed, adapter=adapter
            )
        project = self.repository.get_project(project_id)
        if project.status not in {
            ProjectStatus.ANIMATIC_READY,
            ProjectStatus.ANIMATIC_APPROVED,
        }:
            raise ApprovalRequiredError("Narration requires an animatic-ready project")
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
                "Narration is missing animatic artifacts: " + ", ".join(sorted(missing))
            )
        timeline = json.loads(self.read_artifact_text(artifacts["timeline"]["id"]))
        text = str(timeline.get("voiceover") or "").strip()
        if not text:
            raise IntegrityError("The approved timeline has no voiceover text")
        total = decimal_seconds(timeline["total_duration_seconds"])
        text_hash = sha256_bytes(text.encode("utf-8"))
        tool = self.register_tool_profile(
            ToolProfileCreate(
                stable_key="kokoro-tts",
                name="Kokoro local narration",
                integration_mode=ToolIntegrationMode.NATIVE,
                executable_or_endpoint="python:kokoro_onnx",
                supported_operations=("synthesize_narration",),
                limits={"paid": False},
                capability_status=CapabilityStatus.WORKING,
            )
        )
        request = ToolOperationRequest(
            tool_profile_id=tool["id"],
            operation="synthesize_narration",
            idempotency_key=f"narration-{text_hash[:12]}-{voice}-{speed}",
            parameters={
                "text": text,
                "text_hash": text_hash,
                "voice": voice,
                "speed": speed,
            },
            output_declaration={"media_type": "audio/wav", "language": "en"},
            limits={"paid": False, "max_seconds": str(total)},
        )
        operation = self.reserve_tool_operation(project_id, request)
        existing_receipt = self.repository.get_execution_receipt(operation["id"])
        if existing_receipt:
            output_ids = existing_receipt["output_artifact_ids"]
            if not output_ids:
                raise IntegrityError("Narration receipt has no output artifacts")
            wav_artifact = self.repository.get_artifact(output_ids[0])
            manifest_artifact = (
                self.repository.get_artifact(output_ids[1])
                if len(output_ids) > 1
                else None
            )
            return {
                "project": project.model_dump(mode="json"),
                "narration_artifact": wav_artifact,
                "narration_manifest_artifact": manifest_artifact,
                "receipt": existing_receipt,
                "resumed": True,
            }
        if operation["status"] == ToolOperationStatus.OUTCOME_UNKNOWN.value:
            raise ConflictError(
                "Narration outcome is unknown and requires reconciliation"
            )
        operation = self.repository.mark_tool_operation_running(operation["id"])
        started = perf_counter()
        narrator = adapter or KokoroNarrationAdapter.from_default_paths(
            voice=voice, speed=speed
        )
        if narrator.profile_key != tool["stable_key"]:
            raise IntegrityError(
                f"Narration adapter {narrator.profile_key!r} does not match "
                f"tool profile {tool['stable_key']!r}"
            )
        try:
            result = narrator.synthesize(text)
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
                f"Narration synthesis failed: {type(error).__name__}"
            ) from error
        if result.duration_seconds > float(total) - 0.15:
            raise IntegrityError(
                f"Narration is {result.duration_seconds:.3f}s, leaving less than "
                f"the required 0.15s tail in a {total}s video"
            )
        wav_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.AUDIO,
            relative_path=f"audio/narration-{result.content_hash[:12]}.wav",
            content=result.content,
            operation={
                "type": "kokoro_narration",
                "voice": result.voice,
                "speed": result.speed,
                "model": result.model_name,
                "sample_rate": result.sample_rate,
                "execution_owner": "local",
                "studio_role": "none",
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=artifacts["timeline"]["id"],
                    entity_hash=artifacts["timeline"]["content_hash"],
                ),
            ),
        )
        narration_manifest = {
            "schema_version": 1,
            "project_id": project_id,
            "text": text,
            "text_hash": result.text_hash,
            "voice": result.voice,
            "speed": result.speed,
            "sample_rate": result.sample_rate,
            "duration_seconds": result.duration_seconds,
            "model_name": result.model_name,
            "model_sha256": result.model_sha256,
            "voices_sha256": result.voices_sha256,
            "licence_note": result.licence_note,
            "command_hash": result.command_hash,
            "narration_artifact_id": wav_artifact["id"],
            "narration_hash": wav_artifact["content_hash"],
        }
        manifest_bytes = canonical_json(narration_manifest).encode("utf-8")
        manifest_hash = sha256_bytes(manifest_bytes)
        manifest_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.PREVIEW,
            relative_path=(f"audio/narration-manifest-{manifest_hash[:12]}.json"),
            content=manifest_bytes,
            operation={
                "type": "narration_manifest",
                "voice": result.voice,
                "speed": result.speed,
                "model": result.model_name,
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=wav_artifact["id"],
                    entity_hash=wav_artifact["content_hash"],
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
                output_artifact_ids=(wav_artifact["id"], manifest_artifact["id"]),
                response_hash=manifest_hash,
                details={
                    "voice": result.voice,
                    "speed": result.speed,
                    "duration_seconds": result.duration_seconds,
                    "sample_rate": result.sample_rate,
                    "model": result.model_name,
                    "paid": False,
                },
            ),
        )
        return {
            "project": project.model_dump(mode="json"),
            "narration_artifact": wav_artifact,
            "narration_manifest": narration_manifest,
            "narration_manifest_artifact": manifest_artifact,
            "receipt": receipt,
            "resumed": False,
        }

from __future__ import annotations

import json
from decimal import Decimal
from time import perf_counter
from typing import Any

from ..adapters.mux import FFmpegMuxAdapter, MuxAdapter
from ..animatic import decimal_seconds
from ..assets import (
    AssetCriteria,
    evaluate_treatment,
    verdict_binds_asset,
)
from ..enums import (
    ArtifactKind,
    CapabilityStatus,
    ProjectStatus,
    SlotAssetRole,
    ToolIntegrationMode,
    ToolOperationStatus,
)
from ..errors import (
    ApprovalRequiredError,
    ConflictError,
    IntegrityError,
    NotFoundError,
    RightsEligibilityError,
)
from ..models import (
    AssetRightsRecord,
    AssetUseContext,
    AudioCueRequest,
    DependencyRef,
    ExecutionReceiptCreate,
    ToolOperationRequest,
    ToolProfileCreate,
)
from ..policy.audio import require_audio_rights
from ..policy.guards import (
    require_active_project,
)
from ..utils import canonical_json, sha256_bytes, sha256_json
from ._shared import (
    BED_GAIN_DB,
    MAX_AUDIO_CUES,
)


class AudioUseCases:
    def assign_audio_bed(
        self,
        project_id: str,
        *,
        artifact_id: str,
        role: SlotAssetRole,
        criteria: AssetCriteria,
        decided_by: str,
        context: AssetUseContext | None = None,
    ) -> dict[str, Any]:
        """Bind a sourced track as the primary or backup music bed."""

        require_active_project(
            self.repository.get_project(project_id),
            message="Cannot assign music on a closed project",
        )
        context = self._enforce_context(project_id, context)
        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")
        rights = require_audio_rights(artifact)
        decision = evaluate_treatment(
            slot_id=f"audio:{role.value}",
            criteria=criteria,
            context=context,
            decided_by=decided_by,
            rights=rights,
            artifact_id=artifact_id,
        )
        if not verdict_binds_asset(decision.verdict):
            raise RightsEligibilityError(
                f"Verdict {decision.verdict.value} cannot be assigned as a music bed: "
                + "; ".join(decision.reasons)
            )
        bed = self.repository.upsert_audio_bed(
            project_id,
            artifact_id,
            role.value,
            verdict=decision.verdict.value,
            decision=decision.model_dump(mode="json"),
        )
        return {"bed": bed, "verdict": decision.verdict.value, "role": role.value}

    def audio_coverage(self, project_id: str) -> dict[str, Any]:
        """Report whether a primary and a backup music bed exist."""

        beds = {bed["role"]: bed for bed in self.repository.list_audio_beds(project_id)}
        problems: list[str] = []
        if "primary" not in beds:
            problems.append("missing primary")
        if "backup" not in beds:
            problems.append("missing backup")
        cues = self.repository.list_audio_cues(project_id)
        return {
            "project_id": project_id,
            "primary_artifact_id": beds.get("primary", {}).get("artifact_id"),
            "backup_artifact_id": beds.get("backup", {}).get("artifact_id"),
            "cue_count": len(cues),
            "cue_roles": sorted({cue["role"] for cue in cues}),
            "resolved": not problems,
            "problems": problems,
        }

    def assign_audio_cue(
        self, project_id: str, request: AudioCueRequest
    ) -> dict[str, Any]:
        """Place one SFX, ambience or stinger cue on the final timeline.

        Only open-source audio is accepted: the licence must allow commercial use and
        modification, and attribution must be recorded where the licence requires it.
        """

        project = self.repository.get_project(project_id)
        require_active_project(
            project, message=f"Cannot cue audio on a {project.status.value} project"
        )
        artifact = self.repository.get_artifact(request.artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")
        if artifact["kind"] != ArtifactKind.AUDIO.value:
            raise IntegrityError("Audio cues must reference an audio artifact")
        rights_payload = artifact["operation"].get("rights")
        if not rights_payload:
            raise IntegrityError(
                "Audio cues require recorded licence evidence on the artifact"
            )
        rights = AssetRightsRecord.model_validate(rights_payload)
        if rights.commercial_use_allowed is not True:
            raise IntegrityError(
                f"Licence {rights.license_name!r} does not allow commercial use"
            )
        if rights.modifications_allowed is not True:
            raise IntegrityError(
                f"Licence {rights.license_name!r} does not allow modification "
                "(mixing or trimming is a derivative)"
            )
        if rights.attribution_required and not rights.attribution_text:
            raise IntegrityError(
                f"Licence {rights.license_name!r} requires attribution text"
            )
        existing = [
            cue
            for cue in self.repository.list_audio_cues(project_id)
            if cue["cue_key"] != request.cue_key
        ]
        if len(existing) >= MAX_AUDIO_CUES:
            raise IntegrityError(
                f"A film may carry at most {MAX_AUDIO_CUES} audio cues"
            )
        duration_ms = int(Decimal(str(project.duration_seconds)) * 1000)
        if request.start_ms + request.duration_ms > duration_ms:
            raise IntegrityError(
                "Audio cue runs past the end of the film "
                f"({request.start_ms + request.duration_ms} ms > {duration_ms} ms)"
            )
        decision = {
            "licence_name": rights.license_name,
            "licence_code": rights.license_code,
            "licence_url": str(rights.license_url),
            "commercial_use_allowed": rights.commercial_use_allowed,
            "modifications_allowed": rights.modifications_allowed,
            "attribution_required": rights.attribution_required,
            "attribution_text": rights.attribution_text,
            "decided_by": request.decided_by,
            "role": request.role,
        }
        return self.repository.upsert_audio_cue(
            project_id,
            cue_key=request.cue_key,
            artifact_id=request.artifact_id,
            role=request.role,
            start_ms=request.start_ms,
            duration_ms=request.duration_ms,
            gain_db=str(request.gain_db),
            loop=request.loop,
            shot_id=request.shot_id,
            verdict="accepted",
            decision=decision,
        )

    def audio_credits(self, project_id: str) -> dict[str, Any]:
        """Write the attribution block every open-source bed and cue requires."""

        entries: list[dict[str, Any]] = []
        beds = self.repository.list_audio_beds(project_id)
        for bed in beds:
            artifact = self.repository.get_artifact(bed["artifact_id"])
            metadata = self._audio_layer_metadata(artifact)
            entries.append(
                {
                    "kind": "music_bed",
                    "role": bed["role"],
                    "cue_key": None,
                    "artifact_id": artifact["id"],
                    "file": artifact["relative_path"],
                    "content_hash": artifact["content_hash"],
                    "licence": metadata["licence"],
                    "licence_url": metadata["licence_url"],
                    "attribution": metadata["attribution"],
                    "attribution_required": metadata["attribution_required"],
                }
            )
        for cue in self.repository.list_audio_cues(project_id):
            artifact = self.repository.get_artifact(cue["artifact_id"])
            metadata = self._audio_layer_metadata(artifact)
            entries.append(
                {
                    "kind": "audio_cue",
                    "role": cue["role"],
                    "cue_key": cue["cue_key"],
                    "artifact_id": artifact["id"],
                    "file": artifact["relative_path"],
                    "content_hash": artifact["content_hash"],
                    "licence": metadata["licence"],
                    "licence_url": metadata["licence_url"],
                    "attribution": metadata["attribution"],
                    "attribution_required": metadata["attribution_required"],
                }
            )
        missing = [
            entry["cue_key"] or f"bed:{entry['role']}"
            for entry in entries
            if entry["attribution_required"] and not entry["attribution"]
        ]
        payload = {
            "schema_version": 1,
            "project_id": project_id,
            "credits": entries,
            "requires_attribution": missing,
        }
        content = canonical_json(payload).encode("utf-8")
        digest = sha256_bytes(content)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"artifacts/audio/credits-{digest[:12]}.json",
            content=content,
            operation={
                "type": "audio_credits",
                "beds": len(beds),
                "cues": len(entries) - len(beds),
            },
            dependencies=tuple(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=entry["artifact_id"],
                    entity_hash=entry["content_hash"],
                )
                for entry in entries
            ),
        )
        return {
            "credits": entries,
            "missing_attribution": missing,
            "artifact": artifact,
        }

    def mux_narration(
        self,
        project_id: str,
        *,
        video_artifact_id: str,
        audio_artifact_id: str,
        adapter: MuxAdapter | None = None,
    ) -> dict[str, Any]:
        """Mux normalized narration over the silent composition render."""

        video = self.repository.get_artifact(video_artifact_id)
        audio = self.repository.get_artifact(audio_artifact_id)
        for artifact in (video, audio):
            if artifact["project_id"] != project_id:
                raise ConflictError("Artifact belongs to a different project")
        if audio["kind"] != ArtifactKind.AUDIO.value:
            raise IntegrityError("Narration artifact must be audio")
        duration = float(
            video["operation"].get("duration_seconds")
            or audio["operation"].get("duration_seconds")
            or 0
        )
        if duration <= 0:
            raise IntegrityError("Cannot mux without a known duration")
        require_active_project(
            self.repository.get_project(project_id),
            message="Cannot mix a closed project",
        )
        for item in (video, audio):
            if not self.verify_artifact(item["id"]):
                raise IntegrityError("Mux input failed hash verification")
        layers = self._audio_layer_plan(project_id)
        video_path = self.artifact_store.resolve(project_id, video["relative_path"])
        audio_path = self.artifact_store.resolve(project_id, audio["relative_path"])
        for path in (video_path, audio_path):
            if not path.is_file():
                raise NotFoundError(f"Media file missing: {path.name}")
        kwargs = {
            "video_path": video_path,
            "audio_path": audio_path,
            "duration_seconds": duration,
        }
        if layers["bed"] or layers["cues"]:
            kwargs.update(
                bed_path=layers["bed"]["path"] if layers["bed"] else None,
                bed_gain_db=BED_GAIN_DB,
                cues=layers["cues"],
            )
        muxed = (adapter or FFmpegMuxAdapter()).mux(**kwargs)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.RENDER,
            relative_path=f"renders/narrated-{muxed.content_hash[:12]}.mp4",
            content=muxed.content,
            operation={
                "type": "narration_mux",
                "duration_seconds": duration,
                "video_artifact_id": video["id"],
                "audio_artifact_id": audio["id"],
                "loudness": muxed.loudness,
                "audio_layers": layers["summary"],
                "execution_owner": "local",
                "studio_role": "none",
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=video["id"],
                    entity_hash=video["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=audio["id"],
                    entity_hash=audio["content_hash"],
                ),
                *layers["dependencies"],
            ),
        )
        return {
            "video": artifact,
            "loudness": muxed.loudness,
            "metadata": muxed.metadata,
        }

    @staticmethod
    def _audio_layer_metadata(artifact: dict[str, Any]) -> dict[str, Any]:
        """Licence fields live on the artifact; the decision only records the choice."""

        rights_payload = (artifact.get("operation") or {}).get("rights")
        if not rights_payload:
            return {
                "licence": None,
                "licence_url": None,
                "attribution": None,
                "attribution_required": False,
            }
        rights = AssetRightsRecord.model_validate(rights_payload)
        return {
            "licence": rights.license_name,
            "licence_url": str(rights.license_url),
            "attribution": rights.attribution_text,
            "attribution_required": bool(rights.attribution_required),
        }

    def _audio_layer_plan(self, project_id: str) -> dict[str, Any]:
        """Resolve the primary music bed and every cue into a deterministic mix plan."""

        beds = {bed["role"]: bed for bed in self.repository.list_audio_beds(project_id)}
        primary = beds.get("primary")
        cues = self.repository.list_audio_cues(project_id)
        dependencies: list[DependencyRef] = []
        bed_layer: dict[str, Any] | None = None
        if primary:
            artifact = self.repository.get_artifact(primary["artifact_id"])
            require_audio_rights(artifact)
            if not self.verify_artifact(artifact["id"]):
                raise IntegrityError("Music bed failed hash verification")
            path = self.artifact_store.resolve(project_id, artifact["relative_path"])
            if not path.is_file():
                raise NotFoundError(f"Music bed file missing: {path.name}")
            metadata = self._audio_layer_metadata(artifact)
            bed_layer = {
                "artifact_id": artifact["id"],
                "relative_path": artifact["relative_path"],
                "content_hash": artifact["content_hash"],
                "path": path,
                "gain_db": BED_GAIN_DB,
                "licence": metadata["licence"],
                "attribution": metadata["attribution"],
                "attribution_required": metadata["attribution_required"],
            }
            dependencies.append(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=artifact["id"],
                    entity_hash=artifact["content_hash"],
                )
            )
        cue_layers: list[dict[str, Any]] = []
        for cue in cues:
            artifact = self.repository.get_artifact(cue["artifact_id"])
            require_audio_rights(artifact)
            if not self.verify_artifact(artifact["id"]):
                raise IntegrityError(
                    f"Audio cue {cue['cue_key']!r} failed hash verification"
                )
            path = self.artifact_store.resolve(project_id, artifact["relative_path"])
            if not path.is_file():
                raise NotFoundError(f"Audio cue file missing: {path.name}")
            metadata = self._audio_layer_metadata(artifact)
            cue_layers.append(
                {
                    "cue_key": cue["cue_key"],
                    "artifact_id": artifact["id"],
                    "relative_path": artifact["relative_path"],
                    "content_hash": artifact["content_hash"],
                    "path": path,
                    "role": cue["role"],
                    "shot_id": cue["shot_id"],
                    "start_ms": int(cue["start_ms"]),
                    "duration_ms": int(cue["duration_ms"]),
                    "gain_db": float(cue["gain_db"]),
                    "loop": bool(cue["loop"]),
                    "licence": metadata["licence"],
                    "attribution": metadata["attribution"],
                    "attribution_required": metadata["attribution_required"],
                }
            )
            dependencies.append(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=artifact["id"],
                    entity_hash=artifact["content_hash"],
                )
            )
        summary = {
            "bed": (
                {
                    "artifact_id": bed_layer["artifact_id"],
                    "relative_path": bed_layer["relative_path"],
                    "content_hash": bed_layer["content_hash"],
                    "gain_db": bed_layer["gain_db"],
                    "licence": bed_layer["licence"],
                    "attribution": bed_layer["attribution"],
                    "attribution_required": bed_layer["attribution_required"],
                }
                if bed_layer
                else None
            ),
            "cues": [
                {
                    "cue_key": cue["cue_key"],
                    "artifact_id": cue["artifact_id"],
                    "relative_path": cue["relative_path"],
                    "content_hash": cue["content_hash"],
                    "role": cue["role"],
                    "shot_id": cue["shot_id"],
                    "start_ms": cue["start_ms"],
                    "duration_ms": cue["duration_ms"],
                    "gain_db": cue["gain_db"],
                    "loop": cue["loop"],
                    "licence": cue["licence"],
                    "attribution": cue["attribution"],
                    "attribution_required": cue["attribution_required"],
                }
                for cue in cue_layers
            ],
        }
        summary["plan_hash"] = sha256_json(summary)
        return {
            "bed": bed_layer,
            "cues": cue_layers,
            "dependencies": dependencies,
            "summary": summary,
            "plan_hash": summary["plan_hash"],
        }

    def mux_voiced_animatic(
        self,
        project_id: str,
        *,
        adapter: MuxAdapter | None = None,
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status not in {
            ProjectStatus.ANIMATIC_READY,
            ProjectStatus.ANIMATIC_APPROVED,
        }:
            raise ApprovalRequiredError("Voiced mux requires an animatic-ready project")
        encode_manifests = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if artifact["kind"] == ArtifactKind.PREVIEW.value
            and artifact["relative_path"].startswith("renders/encode-manifest-")
        ]
        narration_manifests = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if artifact["kind"] == ArtifactKind.PREVIEW.value
            and artifact["relative_path"].startswith("audio/narration-manifest-")
        ]
        if not encode_manifests:
            raise NotFoundError("No silent encode manifest exists for this project")
        if not narration_manifests:
            raise NotFoundError("No narration manifest exists for this project")
        encode_manifest_artifact = max(
            encode_manifests, key=lambda item: item["created_at"]
        )
        narration_manifest_artifact = max(
            narration_manifests, key=lambda item: item["created_at"]
        )
        if not self.verify_artifact(encode_manifest_artifact["id"]):
            raise IntegrityError("Silent encode manifest failed hash verification")
        if not self.verify_artifact(narration_manifest_artifact["id"]):
            raise IntegrityError("Narration manifest failed hash verification")
        encode_manifest = json.loads(
            self.read_artifact_text(encode_manifest_artifact["id"])
        )
        narration_manifest = json.loads(
            self.read_artifact_text(narration_manifest_artifact["id"])
        )
        video_artifact = self.repository.get_artifact(
            encode_manifest["video_artifact_id"]
        )
        narration_artifact = self.repository.get_artifact(
            narration_manifest["narration_artifact_id"]
        )
        if not self.verify_artifact(video_artifact["id"]):
            raise IntegrityError("Silent video failed hash verification")
        if not self.verify_artifact(narration_artifact["id"]):
            raise IntegrityError("Narration WAV failed hash verification")
        duration = decimal_seconds(encode_manifest["duration_seconds"])

        audio_layers = self._audio_layer_plan(project_id)
        layer_plan_hash = audio_layers["plan_hash"]

        tool = self.register_tool_profile(
            ToolProfileCreate(
                stable_key="ffmpeg-mux",
                name="FFmpeg narration mux",
                integration_mode=ToolIntegrationMode.NATIVE,
                executable_or_endpoint="python:imageio_ffmpeg",
                supported_operations=("mux_narration",),
                limits={"paid": False},
                capability_status=CapabilityStatus.WORKING,
            )
        )
        request = ToolOperationRequest(
            tool_profile_id=tool["id"],
            operation="mux_narration",
            idempotency_key=(
                f"mux-{video_artifact['content_hash'][:12]}-"
                f"{narration_artifact['content_hash'][:12]}-{layer_plan_hash[:12]}"
            ),
            parameters={
                "video_artifact_id": video_artifact["id"],
                "video_hash": video_artifact["content_hash"],
                "narration_artifact_id": narration_artifact["id"],
                "narration_hash": narration_artifact["content_hash"],
                "duration_seconds": str(duration),
                "audio_layer_plan_hash": layer_plan_hash,
                "bed_artifact_id": (
                    audio_layers["bed"]["artifact_id"] if audio_layers["bed"] else None
                ),
                "cue_keys": [cue["cue_key"] for cue in audio_layers["cues"]],
            },
            output_declaration={
                "media_type": "video/mp4",
                "video_codec": "h264",
                "audio_codec": "aac",
            },
            limits={"paid": False, "max_seconds": str(duration)},
        )
        operation = self.reserve_tool_operation(project_id, request)
        existing_receipt = self.repository.get_execution_receipt(operation["id"])
        if existing_receipt:
            output_ids = existing_receipt["output_artifact_ids"]
            if not output_ids:
                raise IntegrityError("Mux receipt has no output artifacts")
            voiced_artifact = self.repository.get_artifact(output_ids[0])
            mux_manifest_artifact = (
                self.repository.get_artifact(output_ids[1])
                if len(output_ids) > 1
                else None
            )
            return {
                "project": project.model_dump(mode="json"),
                "video_artifact": voiced_artifact,
                "mux_manifest_artifact": mux_manifest_artifact,
                "receipt": existing_receipt,
                "resumed": True,
            }
        if operation["status"] == ToolOperationStatus.OUTCOME_UNKNOWN.value:
            raise ConflictError("Mux outcome is unknown and requires reconciliation")
        operation = self.repository.mark_tool_operation_running(operation["id"])
        started = perf_counter()
        muxer = adapter or FFmpegMuxAdapter()
        if muxer.profile_key != tool["stable_key"]:
            raise IntegrityError(
                f"Mux adapter {muxer.profile_key!r} does not match "
                f"tool profile {tool['stable_key']!r}"
            )
        video_path = self.artifact_store.resolve(
            project_id, video_artifact["relative_path"]
        )
        audio_path = self.artifact_store.resolve(
            project_id, narration_artifact["relative_path"]
        )
        try:
            mux_kwargs: dict[str, Any] = {
                "video_path": video_path,
                "audio_path": audio_path,
                "duration_seconds": float(duration),
            }
            if audio_layers["bed"] or audio_layers["cues"]:
                mux_kwargs["bed_path"] = (
                    audio_layers["bed"]["path"] if audio_layers["bed"] else None
                )
                mux_kwargs["bed_gain_db"] = BED_GAIN_DB
                mux_kwargs["cues"] = [
                    {
                        "path": str(cue["path"]),
                        "start_ms": cue["start_ms"],
                        "duration_ms": cue["duration_ms"],
                        "gain_db": cue["gain_db"],
                        "loop": cue["loop"],
                    }
                    for cue in audio_layers["cues"]
                ]
            result = muxer.mux(**mux_kwargs)
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
                f"Voiced mux failed: {type(error).__name__}"
            ) from error

        try:
            self._validate_mux_result(result, duration)
        except IntegrityError as error:
            # The adapter already produced a file; record the rejection so the
            # operation does not stay in "running" and block reconciliation.
            self.record_execution_receipt(
                operation["id"],
                ExecutionReceiptCreate(
                    status=ToolOperationStatus.FAILED,
                    elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                    details={"type": type(error).__name__, "redacted": True},
                ),
            )
            raise

        metadata = result.metadata
        integrated = float(result.loudness.get("input_i"))
        true_peak = float(result.loudness.get("input_tp"))

        voiced_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.RENDER,
            relative_path=(f"renders/animatic-voiced-{result.content_hash[:12]}.mp4"),
            content=result.content,
            operation={
                "type": "mux_narration",
                "video_codec": "h264",
                "audio_codec": "aac",
                "duration_seconds": str(duration),
                "voice": narration_manifest.get("voice"),
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=video_artifact["id"],
                    entity_hash=video_artifact["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=narration_artifact["id"],
                    entity_hash=narration_artifact["content_hash"],
                ),
                *audio_layers["dependencies"],
            ),
        )
        mux_manifest = {
            "schema_version": 2,
            "project_id": project_id,
            "video_artifact_id": voiced_artifact["id"],
            "video_hash": voiced_artifact["content_hash"],
            "silent_video_artifact_id": video_artifact["id"],
            "silent_video_hash": video_artifact["content_hash"],
            "narration_artifact_id": narration_artifact["id"],
            "narration_hash": narration_artifact["content_hash"],
            "narration_text_hash": narration_manifest.get("text_hash"),
            "narration_duration_seconds": narration_manifest.get("duration_seconds"),
            "narration_voice": narration_manifest.get("voice"),
            "narration_speed": narration_manifest.get("speed"),
            "duration_seconds": str(duration),
            "metadata": metadata,
            "loudness": result.loudness,
            "audio_layers": audio_layers["summary"],
            "command_hash": result.command_hash,
            "encoder_version": result.encoder_version,
            "licence_note": (
                "FFmpeg GPL-enabled build with libx264 and AAC. Internal production "
                "use; review distribution obligations before distributing the binary."
            ),
        }
        manifest_bytes = canonical_json(mux_manifest).encode("utf-8")
        manifest_hash = sha256_bytes(manifest_bytes)
        mux_manifest_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.PREVIEW,
            relative_path=f"renders/mux-manifest-{manifest_hash[:12]}.json",
            content=manifest_bytes,
            operation={
                "type": "voiced_mux_manifest",
                "duration_seconds": str(duration),
                "voice": narration_manifest.get("voice"),
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=voiced_artifact["id"],
                    entity_hash=voiced_artifact["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=narration_artifact["id"],
                    entity_hash=narration_artifact["content_hash"],
                ),
            ),
        )
        receipt = self.record_execution_receipt(
            operation["id"],
            ExecutionReceiptCreate(
                status=ToolOperationStatus.SUCCEEDED,
                elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                output_artifact_ids=(
                    voiced_artifact["id"],
                    mux_manifest_artifact["id"],
                ),
                response_hash=manifest_hash,
                details={
                    "duration_seconds": str(duration),
                    "voice": narration_manifest.get("voice"),
                    "integrated_lufs": integrated,
                    "true_peak_dbtp": true_peak,
                    "audio_codec": "aac",
                    "paid": False,
                },
            ),
        )
        return {
            "project": project.model_dump(mode="json"),
            "video_artifact": voiced_artifact,
            "mux_manifest": mux_manifest,
            "mux_manifest_artifact": mux_manifest_artifact,
            "receipt": receipt,
            "resumed": False,
        }

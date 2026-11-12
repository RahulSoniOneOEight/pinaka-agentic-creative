from __future__ import annotations

import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..adapters.assembly import FFmpegAssemblyAdapter
from ..assets import (
    AssetCriteria,
)
from ..beats import quantize_durations
from ..enums import (
    ArtifactKind,
    JobStatus,
    SlotAssetRole,
)
from ..errors import (
    ConflictError,
    IntegrityError,
    NotFoundError,
)
from ..models import (
    DependencyRef,
    JobRequest,
    Money,
    ShotMotionRequest,
    SlotCreate,
)
from ..motion import affordable_takes
from ..motion import assert_route as assert_motion_route
from ..motion import estimate_minor as estimate_motion_minor
from ..motion import supports as motion_supports
from ..operations import operation as catalog_operation
from ..policy.guards import (
    require_active_project,
)
from ..utils import canonical_json, sha256_bytes, sha256_json
from ._shared import (
    DEFAULT_RUN_CAP_MINOR,
    VIDEO_MIME,
    _image_mime,
)


class MotionUseCases:
    def assemble_film(
        self,
        project_id: str,
        *,
        transition: str = "cut",
        dissolve_seconds: Decimal = Decimal("0.4"),
        fps: int = 24,
        width: int = 1080,
        height: int = 1920,
        beat_grid: tuple[float, ...] | None = None,
        beat_tolerance: float = 0.08,
        adapter: Any | None = None,
    ) -> dict[str, Any]:
        """Trim every shot clip to its planned duration and join the film."""

        if transition not in {"cut", "dissolve"}:
            raise IntegrityError("transition must be 'cut' or 'dissolve'")
        project = self.repository.get_project(project_id)
        require_active_project(
            project, message=f"Cannot assemble a {project.status.value} project"
        )
        shots = self.repository.list_shots(project_id)
        if not shots:
            raise IntegrityError("Assembly requires a shot plan")
        slots = {slot["id"]: slot for slot in self.repository.list_slots(project_id)}
        assignments = self.repository.list_slot_assets(project_id)
        segments: list[tuple[Any, Decimal]] = []
        used: list[dict[str, Any]] = []
        missing: list[str] = []
        for shot in shots:
            clip = None
            for assignment in assignments:
                slot = slots.get(assignment["slot_id"])
                if not slot or slot["shot_id"] != shot["id"]:
                    continue
                if slot["role"].lower() != "motion":
                    continue
                if assignment["role"] != SlotAssetRole.PRIMARY.value:
                    continue
                clip = self.repository.get_artifact(assignment["artifact_id"])
                break
            if clip is None:
                missing.append(shot["stable_key"])
                continue
            path = self.artifact_store.resolve(project_id, clip["relative_path"])
            if not path.is_file():
                raise NotFoundError(f"Clip file missing: {path.name}")
            segments.append((path, Decimal(str(shot["duration_seconds"]))))
            used.append(
                {
                    "shot": shot["stable_key"],
                    "artifact_id": clip["id"],
                    "content_hash": clip["content_hash"],
                }
            )
        if missing:
            raise IntegrityError(
                "Every shot needs an attached clip before assembly: "
                + ", ".join(missing)
            )
        beat_report: dict[str, Any] | None = None
        if beat_grid:
            if not 0 <= beat_tolerance <= 0.5:
                raise IntegrityError("beat_tolerance must be between 0 and 0.5s")
            quantized = quantize_durations(
                [float(duration) for _, duration in segments],
                beat_grid,
                tolerance_seconds=beat_tolerance,
            )
            segments = [
                (path, Decimal(str(duration)))
                for (path, _), duration in zip(
                    segments, quantized["durations"], strict=True
                )
            ]
            beat_report = quantized
        with tempfile.TemporaryDirectory(prefix="pinaka-assembly-") as temp:
            output = Path(temp) / "film.mp4"
            result = (adapter or FFmpegAssemblyAdapter()).assemble(
                segments=segments,
                output_path=output,
                transition=transition,
                dissolve_seconds=dissolve_seconds,
                fps=fps,
                width=width,
                height=height,
            )
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.RENDER,
            relative_path=f"renders/film-{result.content_hash[:12]}.mp4",
            content=result.content,
            operation={
                "type": "film_assembly",
                "transition": transition,
                "dissolve_seconds": str(dissolve_seconds),
                "fps": fps,
                "shots": [item["shot"] for item in used],
                "command_hash": result.command_hash,
                "encoder_version": result.encoder_version,
                **({"beat_quantization": beat_report} if beat_report else {}),
            },
            dependencies=tuple(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=item["artifact_id"],
                    entity_hash=item["content_hash"],
                )
                for item in used
            ),
        )
        return {
            "video": artifact,
            "transition": transition,
            "total_seconds": str(result.total_seconds),
            "segments": used,
            "metadata": result.metadata,
            "beat_quantization": beat_report,
        }

    def plan_motion_takes(
        self,
        project_id: str,
        *,
        family: str,
        seconds: float,
        resolution: str,
        max_takes: int = 3,
    ) -> dict[str, Any]:
        """Plan how many takes fit the remaining budget; never spends."""

        project = self.repository.get_project(project_id)
        available = (
            project.max_budget.minor - project.spent.minor - project.reserved.minor
        )
        plan = affordable_takes(
            family=family,
            seconds=seconds,
            resolution=resolution,
            available_minor=available,
            max_takes=max_takes,
        )
        plan["project_id"] = project_id
        plan["currency"] = project.max_budget.currency
        return plan

    def plan_shot_motion(
        self, project_id: str, request: ShotMotionRequest
    ) -> dict[str, Any]:
        """Estimate one per-shot Studio video generation without spending."""

        project = self.repository.get_project(project_id)
        require_active_project(
            project, message=f"Cannot plan motion for a {project.status.value} project"
        )
        shot = self._motion_shot(project_id, request)
        assert_motion_route(request.route)
        catalog_operation("video.generate").validate(request.route, model=request.model)
        if not motion_supports(request.model, request.mode):
            raise IntegrityError(
                f"{request.model} does not support mode {request.mode!r}"
            )
        estimate = estimate_motion_minor(
            request.model,
            seconds=float(request.duration_seconds),
            resolution=request.resolution,
        )
        auto_cap = DEFAULT_RUN_CAP_MINOR.get(project.max_budget.currency, 0)
        available = (
            project.max_budget.minor - project.spent.minor - project.reserved.minor
        )
        return {
            "shot": shot["stable_key"],
            "model": request.model,
            "mode": request.mode,
            "resolution": request.resolution,
            "duration_seconds": str(request.duration_seconds),
            "estimate_minor": estimate,
            "estimate": Money.from_minor(
                estimate, project.max_budget.currency
            ).model_dump(mode="json"),
            "auto_cap_minor": auto_cap,
            "requires_approval": estimate > auto_cap,
            "available_minor": available,
            "affordable": estimate <= available,
        }

    def prepare_shot_motion(
        self, project_id: str, request: ShotMotionRequest
    ) -> dict[str, Any]:
        """Reserve budget and write a per-shot motion packet for the host."""

        plan_id = request.plan_id
        item_key = request.item_key
        if not plan_id or not item_key:
            raise IntegrityError(
                "Paid shot motion needs a funded plan item (plan_id and item_key)"
            )
        planned = self.plan_shot_motion(project_id, request)
        estimate = int(planned["estimate_minor"])
        plan = self.repository.get_budget_plan(plan_id)
        if plan["project_id"] != project_id:
            raise ConflictError("Budget plan belongs to a different project")
        item = next(
            (entry for entry in plan["items"] if entry["stable_key"] == item_key),
            None,
        )
        if not item:
            raise NotFoundError(f"Budget item not found in plan: {item_key}")
        if item["provider"] != "studio-video" or item["operation"] != "video.generate":
            raise IntegrityError(
                "Motion plan items must use provider 'studio-video' and "
                "operation 'video.generate'"
            )
        if item["estimated_minor"] != estimate:
            raise IntegrityError(
                f"Plan item estimate {item['estimated_minor']} does not match the "
                f"model estimate {estimate}"
            )
        first_frame = None
        if request.first_frame_artifact_id:
            first_frame = self._motion_frame(
                project_id, request.first_frame_artifact_id, "first frame"
            )
        last_frame = None
        if request.last_frame_artifact_id:
            last_frame = self._motion_frame(
                project_id, request.last_frame_artifact_id, "last frame"
            )
        shot = self._motion_shot(project_id, request)
        output_path = f"artifacts/motion/{shot['stable_key']}-{estimate}.mp4"
        job_request = JobRequest(
            provider="studio-video",
            endpoint="studio:fal.video",
            operation="video.generate",
            idempotency_key=(
                f"motion-{shot['stable_key']}-{sha256_json(request.model_dump(mode='json'))[:16]}"
            ),
            estimated_cost=Money.from_minor(estimate, plan["currency"]),
            parameters={
                "model": request.model,
                "mode": request.mode,
                "resolution": request.resolution,
                "duration_seconds": str(request.duration_seconds),
                "output_relative_path": output_path,
            },
        )
        reserved = self.reserve_budget_item(project_id, plan_id, item_key, job_request)
        packet = {
            "schema": "pinaka.studio.motion-packet/v1",
            "project_id": project_id,
            "shot": shot,
            "route": request.route,
            "model": request.model,
            "mode": request.mode,
            "resolution": request.resolution,
            "duration_seconds": str(request.duration_seconds),
            "prompt": request.prompt,
            "first_frame": first_frame,
            "last_frame": last_frame,
            "max_cost_minor": estimate,
            "currency": plan["currency"],
            "output_relative_path": output_path,
            "controller_authority": {
                "system_of_record": "pinaka.CreativeController",
                "generated_motion": True,
                "one_take_per_request": catalog_operation("video.generate").one_take,
                "auto_retry": catalog_operation("video.generate").auto_retry,
            },
        }
        content = canonical_json(packet).encode("utf-8")
        digest = sha256_bytes(content)
        dependencies = [DependencyRef(entity_type="shot", entity_id=shot["id"])]
        for frame in (first_frame, last_frame):
            if frame:
                dependencies.append(
                    DependencyRef(
                        entity_type="artifact",
                        entity_id=frame["id"],
                        entity_hash=frame["content_hash"],
                    )
                )
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.CONTRACT,
            relative_path=f"artifacts/motion/packets/{digest[:12]}.json",
            content=content,
            operation={
                "type": "shot_motion_packet",
                "shot_id": shot["id"],
                "model": request.model,
            },
            dependencies=tuple(dependencies),
        )
        return {
            "packet_artifact": artifact,
            "packet": packet,
            "job": reserved["job"],
            "estimate_minor": estimate,
        }

    def import_shot_motion(
        self,
        project_id: str,
        *,
        job_id: str,
        source_path: str,
        content_hash: str,
        actual_cost_minor: int | None = None,
        output_relative_path: str | None = None,
        shot_id: str | None = None,
    ) -> dict[str, Any]:
        """Register a generated shot clip and settle its reservation."""

        job = self.repository.get_job(job_id)
        if job.project_id != project_id:
            raise ConflictError("Job belongs to a different project")
        if job.status != JobStatus.BUDGET_RESERVED:
            raise ConflictError(
                f"Job is {job.status.value}; a new clip needs a reserved job"
            )
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise NotFoundError(f"Generated clip not found: {source}")
        content = source.read_bytes()
        if sha256_bytes(content) != content_hash:
            raise IntegrityError("Generated clip hash does not match")
        output_path = output_relative_path or f"artifacts/motion/{job.id}.mp4"
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.GENERATED,
            relative_path=output_path,
            content=content,
            operation={
                "type": "studio_motion",
                "job_id": job.id,
            },
            dependencies=(DependencyRef(entity_type="job", entity_id=job.id),),
        )
        cost = (
            actual_cost_minor
            if actual_cost_minor is not None
            else job.estimated_cost.minor
        )
        settlement = self.settle_budget_item(
            project_id,
            job.id,
            actual_cost=Money.from_minor(cost, job.estimated_cost.currency),
            requested_by="host:studio-video",
            reason="Studio shot motion settled after generation",
            asset_artifact_id=artifact["id"],
        )
        attached = None
        if shot_id:
            attached = self.attach_shot_clip(
                project_id, shot_id=shot_id, artifact_id=artifact["id"]
            )
        return {
            "artifact": artifact,
            "settlement": settlement,
            "job_id": job.id,
            "attached": attached,
        }

    def attach_shot_clip(
        self,
        project_id: str,
        *,
        shot_id: str,
        artifact_id: str,
        role: str = "motion",
    ) -> dict[str, Any]:
        """Bind a generated clip to its shot so compositions use it."""

        shot = self.repository.get_shot(shot_id)
        if shot["project_id"] != project_id:
            raise ConflictError("Shot belongs to a different project")
        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Clip belongs to a different project")
        if str(Path(artifact["relative_path"]).suffix).lower() not in VIDEO_MIME:
            raise IntegrityError("Only video clips can be attached as motion")
        slot = next(
            (
                item
                for item in self.repository.list_slots(project_id)
                if item["shot_id"] == shot_id and item["role"].lower() == role.lower()
            ),
            None,
        )
        if slot is None:
            slot = self.add_slot(
                project_id,
                SlotCreate(
                    shot_id=shot_id,
                    stable_key=f"{shot['stable_key']}.{role}",
                    role=role,
                    identity_requirement="Generated clip for this shot",
                    fact_requirement="Matches the shot action",
                    transformation_permission="Trim and compose",
                    approved_sources=("owned", "generated"),
                ),
            )
        self.assign_slot_asset(
            project_id,
            slot["id"],
            artifact_id=artifact_id,
            role=SlotAssetRole.PRIMARY,
            criteria=AssetCriteria(),
            decided_by="controller:motion-import",
        )
        return {"slot": slot, "artifact_id": artifact_id, "role": role}

    def _motion_shot(
        self, project_id: str, request: ShotMotionRequest
    ) -> dict[str, Any]:
        shot = self.repository.get_shot(request.shot_id)
        if shot["project_id"] != project_id:
            raise ConflictError("Shot belongs to a different project")
        return shot

    def _motion_frame(
        self, project_id: str, artifact_id: str, label: str
    ) -> dict[str, Any]:
        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError(f"{label} belongs to a different project")
        if not self.verify_artifact(artifact_id):
            raise IntegrityError(f"{label} does not match its registered hash")
        if not _image_mime(Path(artifact["relative_path"])):
            raise IntegrityError(f"{label} is not an image artifact")
        return artifact

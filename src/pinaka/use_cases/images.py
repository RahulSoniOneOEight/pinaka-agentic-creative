from __future__ import annotations

from typing import Any

from ..adapters.base import GenerationAdapter
from ..adapters.fal_image import FalImageAdapter
from ..enums import ArtifactKind, JobStatus
from ..errors import IntegrityError, NotFoundError
from ..fal import FalClient
from ..models import DependencyRef, JobRequest, Money
from ..operations import operation
from ..policy.guards import require_active_project, require_funded_item
from ..utils import sha256_json


class ImagesUseCases:
    def enhance_still(
        self,
        project_id: str,
        *,
        source_artifact_id: str,
        prompt: str,
        model: str,
        plan_id: str,
        item_key: str,
        reference_artifact_ids: tuple[str, ...] = (),
        route: str = "fal.image",
        adapter: GenerationAdapter | None = None,
    ) -> dict[str, Any]:
        """One funded fal edit; originals stay immutable, unknown jobs never retry."""

        require_active_project(
            self.repository.get_project(project_id),
            message="Cannot edit a closed project",
        )
        require_funded_item(plan_id, item_key)
        operation("image.edit").validate(route, model=model)
        source = self.repository.get_artifact(source_artifact_id)
        if source["project_id"] != project_id:
            raise IntegrityError("Source artifact belongs to a different project")
        if source["kind"] == ArtifactKind.ORIGINAL.value or source["immutable"]:
            raise IntegrityError("Enhance a working copy, not a protected original")
        if not prompt.strip() or not model.strip():
            raise IntegrityError("An explicit image-edit model and prompt are required")
        if not self.verify_artifact(source["id"]):
            raise IntegrityError("Source artifact failed hash verification")
        references = []
        for reference_id in reference_artifact_ids:
            if reference_id == source_artifact_id:
                raise IntegrityError(
                    "The source is already the first image; pass additional references only"
                )
            reference = self.repository.get_artifact(reference_id)
            if reference["project_id"] != project_id:
                raise IntegrityError(
                    "Reference artifact belongs to a different project"
                )
            if (
                reference["kind"] == ArtifactKind.ORIGINAL.value
                or reference["immutable"]
            ):
                raise IntegrityError(
                    "Reference a working copy, not a protected original"
                )
            if not self.verify_artifact(reference["id"]):
                raise IntegrityError("Reference artifact failed hash verification")
            references.append(reference)
        plan = self.repository.get_budget_plan(plan_id)
        item = next(
            (entry for entry in plan["items"] if entry["stable_key"] == item_key), None
        )
        if not item:
            raise NotFoundError(f"Budget item not found in plan: {item_key}")
        if (
            plan["project_id"] != project_id
            or item["provider"] != "fal"
            or item["operation"] != "image.edit"
        ):
            raise IntegrityError(
                "Image edit requires this project's fal/image.edit plan item"
            )
        if plan["currency"] != "USD":
            raise IntegrityError(
                "fal quotes are USD; use a USD plan, not an unconverted currency"
            )
        path = self.artifact_store.resolve(project_id, source["relative_path"])
        request = JobRequest(
            provider="fal",
            endpoint=model,
            operation="image.edit",
            idempotency_key=f"plan:{plan_id}:{item_key}",
            estimated_cost=Money.from_minor(item["estimated_minor"], "USD"),
            parameters={
                "prompt": prompt,
                "model": model,
                "source_artifact_id": source["id"],
                "source_hash": source["content_hash"],
                "reference_artifact_ids": [item["id"] for item in references],
                "reference_hashes": [item["content_hash"] for item in references],
                "num_images": 1,
            },
        )
        if item.get("job_id"):
            saved = self.repository.get_job(item["job_id"])
            if saved.request_hash != sha256_json(request.model_dump(mode="json")):
                raise IntegrityError(
                    "The funded item is already bound to a different request"
                )
            if saved.status == JobStatus.SUCCEEDED:
                artifact = next(
                    (
                        a
                        for a in self.repository.list_artifacts(project_id)
                        if a["operation"].get("job_id") == saved.id
                    ),
                    None,
                )
                if artifact is None or not self.verify_artifact(artifact["id"]):
                    raise IntegrityError(
                        "Saved image result needs reconciliation; do not regenerate"
                    )
                return {
                    "job": saved.model_dump(mode="json"),
                    "artifact": artifact,
                    "resumed": True,
                }
        worker = adapter or FalImageAdapter(
            FalClient(),
            model=model,
            source_image=path.read_bytes(),
            source_mime="image/jpeg"
            if path.suffix.lower() in {".jpg", ".jpeg"}
            else "image/png",
            reference_images=tuple(
                self.artifact_store.resolve(
                    project_id, item["relative_path"]
                ).read_bytes()
                for item in references
            ),
            reference_mimes=tuple(
                "image/jpeg"
                if item["relative_path"].lower().endswith((".jpg", ".jpeg"))
                else "image/png"
                for item in references
            ),
        )
        job, artifact = self.run_adapter_once(
            project_id,
            request,
            worker,
            artifact_path=f"artifacts/assets/{plan_id}-{item_key}.png",
            budget_plan_item=(plan_id, item_key),
            parent_artifact_id=source["id"],
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=source["id"],
                    entity_hash=source["content_hash"],
                ),
                *(
                    DependencyRef(
                        entity_type="artifact",
                        entity_id=item["id"],
                        entity_hash=item["content_hash"],
                    )
                    for item in references
                ),
            ),
        )
        if job.status == JobStatus.SUCCEEDED and artifact is None:
            artifact = next(
                (
                    a
                    for a in self.repository.list_artifacts(project_id)
                    if a["operation"].get("job_id") == job.id
                ),
                None,
            )
            if artifact is None or not self.verify_artifact(artifact["id"]):
                raise IntegrityError(
                    "Saved image-edit result is missing or corrupt; reconcile, do not regenerate"
                )
        return {
            "job": job.model_dump(mode="json"),
            "artifact": artifact,
            "resumed": False,
        }

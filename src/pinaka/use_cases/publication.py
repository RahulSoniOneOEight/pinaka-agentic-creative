from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..enums import (
    ApprovalStage,
    ArtifactKind,
    ProjectStatus,
)
from ..errors import (
    ApprovalRequiredError,
    ConflictError,
    IntegrityError,
    NotFoundError,
)
from ..models import (
    ArtifactCreate,
    DependencyRef,
    PublicationAuthorizationCreate,
)
from ..publication import PublicationAuthorization
from ..utils import canonical_json


class PublicationUseCases:
    def authorize_publication(
        self, project_id: str, request: PublicationAuthorizationCreate
    ) -> dict[str, Any]:
        """Record a human publication authorization for the approved final hash."""

        project = self.repository.get_project(project_id)
        if project.status not in {
            ProjectStatus.FINAL_APPROVED,
            ProjectStatus.ARCHIVED,
        }:
            raise ConflictError(
                "Publication authorization requires a final-approved or archived project"
            )
        final = self.repository.active_approval(project_id, ApprovalStage.FINAL.value)
        if not final or not final.get("output_hash"):
            raise ApprovalRequiredError(
                "An active final approval with an exact output hash is required"
            )
        artifact = self.repository.artifact_by_content_hash(
            project_id, final["output_hash"]
        )
        if not artifact:
            raise NotFoundError(
                "The approved final artifact is no longer current; re-approve it first"
            )
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "publication_authorization"
        )
        return self.repository.record_publication_authorization(
            project_id,
            artifact_id=artifact["id"],
            artifact_hash=artifact["content_hash"],
            channels=request.channels,
            authorized_by=request.authorized_by,
            reference=request.reference,
            expires_at=request.expires_at,
        )

    def publication_authorization(self, project_id: str) -> dict[str, Any] | None:
        return self.repository.current_publication_authorization(project_id)

    def stored_publication_authorization(
        self, project_id: str, channel: str
    ) -> PublicationAuthorization:
        """Build the publication authorization for one channel from the record."""

        record = self.repository.current_publication_authorization(project_id)
        if not record or channel not in record["channels"]:
            return PublicationAuthorization(authorized=False)
        if record["expires_at"]:
            try:
                expires = datetime.fromisoformat(
                    record["expires_at"].replace("Z", "+00:00")
                )
            except ValueError:
                return PublicationAuthorization(authorized=False)
            if expires <= datetime.now(timezone.utc):
                return PublicationAuthorization(authorized=False)
        return PublicationAuthorization(
            authorized=True,
            authorized_by=record["authorized_by"],
            authorized_at=datetime.fromisoformat(
                record["authorized_at"].replace("Z", "+00:00")
            ),
            artifact_hash=record["artifact_hash"],
            channels=tuple(record["channels"]),
            reference=record["reference"],
            expires_at=(
                datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00"))
                if record["expires_at"]
                else None
            ),
        )

    def archive_project(self, project_id: str) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status == ProjectStatus.ARCHIVED:
            archive = self.repository.get_archive(project_id)
            if not archive:
                raise IntegrityError("Archived project is missing its archive record")
            return archive
        if project.status != ProjectStatus.FINAL_APPROVED:
            raise ApprovalRequiredError(
                "Project must have an exact-hash final approval before archive"
            )
        final_approval = self.repository.active_approval(
            project_id, ApprovalStage.FINAL.value
        )
        if not final_approval or not final_approval["output_hash"]:
            raise ApprovalRequiredError("Active exact-hash final approval is required")
        manifest = {
            "schema_version": 3,
            "project": project.model_dump(mode="json"),
            "final_approval": final_approval,
            "approvals": {
                "brief": self.repository.active_approval(
                    project_id, ApprovalStage.BRIEF.value
                ),
                "sourcing": self.repository.active_approval(
                    project_id, ApprovalStage.SOURCING.value
                ),
                "animatic": self.repository.active_approval(
                    project_id, ApprovalStage.ANIMATIC.value
                ),
                "final": final_approval,
            },
            "grounding_bundles": self.repository.current_grounding_bundles(project_id),
            "legacy_fact_set": self.repository.current_fact_set(project_id),
            "deliverables": [
                item.model_dump(mode="json")
                for item in self.repository.list_deliverables(project_id)
            ],
            "segments": self.repository.list_segments(project_id),
            "ab": self.ab_status(project_id),
            "ab_delivery": self.repository.latest_ab_delivery(project_id),
            "artifacts": self.repository.list_artifacts(project_id),
            "tool_operations": self.repository.list_tool_operations(project_id),
            "qa_results": self.repository.list_qa_results(project_id),
            "budget_ledger": self.repository.budget_ledger(project_id),
        }
        content = canonical_json(manifest).encode("utf-8")
        stored = self.artifact_store.write_bytes(
            project_id, "archive/manifest.json", content
        )
        existing = self.repository.artifact_by_path(project_id, stored.relative_path)
        if existing:
            artifact = existing
        else:
            artifact = self.repository.register_artifact(
                project_id,
                ArtifactCreate(
                    kind=ArtifactKind.ARCHIVE,
                    relative_path=stored.relative_path,
                    content_hash=stored.content_hash,
                    operation={
                        "type": "archive_manifest",
                        "size_bytes": stored.size_bytes,
                        "final_output_hash": final_approval["output_hash"],
                    },
                    immutable=True,
                    dependencies=(
                        DependencyRef(
                            entity_type="approval",
                            entity_id=final_approval["id"],
                            entity_hash=final_approval["subject_hash"],
                        ),
                    ),
                ),
            )
        return self.repository.register_archive(
            project_id, artifact["id"], stored.content_hash
        )

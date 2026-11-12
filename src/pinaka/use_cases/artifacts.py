from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..enums import (
    ArtifactKind,
)
from ..errors import (
    IntegrityError,
    NotFoundError,
)
from ..models import (
    ArtifactCreate,
    DependencyRef,
)


class ArtifactsUseCases:
    @staticmethod
    def _deduplicate_dependencies(
        dependencies: Iterable[DependencyRef],
    ) -> tuple[DependencyRef, ...]:
        unique: dict[tuple[str, str], DependencyRef] = {}
        for dependency in dependencies:
            key = (dependency.entity_type, dependency.entity_id)
            existing = unique.get(key)
            if existing and existing.entity_hash != dependency.entity_hash:
                raise IntegrityError(
                    f"Conflicting dependency hashes for {key[0]}:{key[1]}"
                )
            unique[key] = dependency
        return tuple(unique[key] for key in sorted(unique))

    def import_original(self, project_id: str, source: Path | str) -> dict[str, Any]:
        self.repository.get_project(project_id)
        stored = self.artifact_store.import_original(project_id, source)
        existing = self.repository.artifact_by_path(project_id, stored.relative_path)
        if existing:
            if (
                existing["content_hash"] != stored.content_hash
                or not existing["immutable"]
            ):
                raise IntegrityError(
                    f"Original registry mismatch: {stored.relative_path}"
                )
            return existing
        return self.repository.register_artifact(
            project_id,
            ArtifactCreate(
                kind=ArtifactKind.ORIGINAL,
                relative_path=stored.relative_path,
                content_hash=stored.content_hash,
                operation={"type": "import", "size_bytes": stored.size_bytes},
                immutable=True,
            ),
        )

    def write_artifact(
        self,
        project_id: str,
        *,
        kind: ArtifactKind,
        relative_path: str,
        content: bytes,
        operation: dict[str, Any],
        parent_artifact_id: str | None = None,
        dependencies: tuple[DependencyRef, ...] = (),
    ) -> dict[str, Any]:
        if kind == ArtifactKind.ORIGINAL:
            raise IntegrityError("Use import_original for protected source assets")
        stored = self.artifact_store.write_bytes(project_id, relative_path, content)
        existing = self.repository.artifact_by_path(project_id, stored.relative_path)
        if existing:
            if existing["content_hash"] != stored.content_hash:
                raise IntegrityError(
                    f"Artifact registry mismatch: {stored.relative_path}"
                )
            return existing
        return self.repository.register_artifact(
            project_id,
            ArtifactCreate(
                kind=kind,
                relative_path=stored.relative_path,
                content_hash=stored.content_hash,
                parent_artifact_id=parent_artifact_id,
                operation={**operation, "size_bytes": stored.size_bytes},
                immutable=False,
                dependencies=dependencies,
            ),
        )

    def verify_artifact(self, artifact_id: str) -> bool:
        artifact = self.repository.get_artifact(artifact_id)
        return self.artifact_store.verify(
            artifact["project_id"], artifact["relative_path"], artifact["content_hash"]
        )

    def read_artifact_text(self, artifact_id: str) -> str:
        artifact = self.repository.get_artifact(artifact_id)
        path = self.artifact_store.resolve(
            artifact["project_id"], artifact["relative_path"]
        )
        if not path.is_file():
            raise NotFoundError(
                f"Artifact file is missing: {artifact['relative_path']}"
            )
        return path.read_text(encoding="utf-8")

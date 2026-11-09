from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import ConflictError, IntegrityError, NotFoundError, UnsafePathError
from .utils import sha256_bytes, sha256_file

PROJECT_DIRECTORIES = (
    "inputs",
    "references",
    "artifacts",
    "previews",
    "assets/originals",
    "assets/derived",
    "assets/generated",
    "designs",
    "audio",
    "renders",
    "qa",
    "archive",
)


@dataclass(frozen=True)
class StoredFile:
    relative_path: str
    absolute_path: Path
    content_hash: str
    size_bytes: int


class ArtifactStore:
    """Content-addressed project files with path and overwrite protection."""

    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser().resolve()

    def project_root(self, project_id: str) -> Path:
        if not project_id or any(
            char
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for char in project_id
        ):
            raise UnsafePathError(f"Unsafe project identifier: {project_id!r}")
        return (self.root / "projects" / project_id).resolve()

    def ensure_project_layout(self, project_id: str) -> Path:
        project_root = self.project_root(project_id)
        for relative in PROJECT_DIRECTORIES:
            (project_root / relative).mkdir(parents=True, exist_ok=True)
        return project_root

    def resolve(self, project_id: str, relative_path: str) -> Path:
        pure = PurePosixPath(relative_path.replace("\\", "/"))
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise UnsafePathError(
                f"Path must remain inside the project: {relative_path!r}"
            )
        project_root = self.project_root(project_id)
        candidate = (project_root / Path(*pure.parts)).resolve()
        try:
            candidate.relative_to(project_root)
        except ValueError as error:
            raise UnsafePathError(
                f"Path escapes the project: {relative_path!r}"
            ) from error
        return candidate

    def import_original(self, project_id: str, source: Path | str) -> StoredFile:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise NotFoundError(f"Original asset not found: {source_path}")
        self.ensure_project_layout(project_id)
        content_hash = sha256_file(source_path)
        safe_name = (
            "".join(
                char if char.isalnum() or char in "._-" else "-"
                for char in source_path.name
            ).strip(".-")
            or "asset.bin"
        )
        relative_path = f"assets/originals/{content_hash[:12]}-{safe_name}"
        destination = self.resolve(project_id, relative_path)
        if destination.exists():
            if sha256_file(destination) != content_hash:
                raise IntegrityError(f"Stored original hash mismatch: {relative_path}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source_path.open("rb") as reader, destination.open("xb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
        return StoredFile(
            relative_path=relative_path,
            absolute_path=destination,
            content_hash=content_hash,
            size_bytes=destination.stat().st_size,
        )

    def write_bytes(
        self,
        project_id: str,
        relative_path: str,
        content: bytes,
        *,
        accept_identical: bool = True,
    ) -> StoredFile:
        self.ensure_project_layout(project_id)
        destination = self.resolve(project_id, relative_path)
        content_hash = sha256_bytes(content)
        if destination.exists():
            existing_hash = sha256_file(destination)
            if accept_identical and existing_hash == content_hash:
                return StoredFile(
                    relative_path=relative_path.replace("\\", "/"),
                    absolute_path=destination,
                    content_hash=content_hash,
                    size_bytes=destination.stat().st_size,
                )
            raise ConflictError(f"Artifact path already exists: {relative_path}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as writer:
            writer.write(content)
            writer.flush()
            os.fsync(writer.fileno())
        return StoredFile(
            relative_path=relative_path.replace("\\", "/"),
            absolute_path=destination,
            content_hash=content_hash,
            size_bytes=len(content),
        )

    def verify(self, project_id: str, relative_path: str, expected_hash: str) -> bool:
        path = self.resolve(project_id, relative_path)
        if not path.is_file():
            return False
        return sha256_file(path) == expected_hash

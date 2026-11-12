from __future__ import annotations

from typing import Any

from pydantic import (
    Field,
    model_validator,
)

from ..enums import (
    ArtifactKind,
    QAStatus,
)
from .common import StrictModel


class DependencyRef(StrictModel):
    entity_type: str = Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")
    entity_id: str = Field(min_length=1, max_length=120)
    entity_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ArtifactCreate(StrictModel):
    kind: ArtifactKind
    relative_path: str = Field(min_length=1, max_length=1000)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    parent_artifact_id: str | None = None
    operation: dict[str, Any] = Field(default_factory=dict)
    immutable: bool = False
    dependencies: tuple[DependencyRef, ...] = ()


class QACheck(StrictModel):
    check: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,80}$")
    status: QAStatus
    detail: str = Field(min_length=1, max_length=2000)
    evidence: dict[str, Any] = Field(default_factory=dict)


class QAReport(StrictModel):
    schema_version: int = 1
    project_id: str = Field(min_length=1, max_length=120)
    subject_artifact_id: str = Field(min_length=1, max_length=120)
    checks: tuple[QACheck, ...] = Field(min_length=1)
    overall: QAStatus
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def overall_matches_checks(self) -> QAReport:
        statuses = {check.status for check in self.checks}
        expected = (
            QAStatus.FAIL
            if QAStatus.FAIL in statuses
            else QAStatus.WARN
            if QAStatus.WARN in statuses
            else QAStatus.PASS
        )
        if self.overall != expected:
            raise ValueError(
                f"Report overall {self.overall.value!r} does not match checks {expected.value!r}"
            )
        return self

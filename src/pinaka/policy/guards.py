from __future__ import annotations

from ..enums import ProjectStatus
from ..errors import ConflictError, IntegrityError
from ..models import ProjectRecord


def require_active_project(project: ProjectRecord, *, message: str) -> None:
    """Closed projects cannot acquire new creative state; preserve caller context."""

    if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}:
        raise ConflictError(message)


def require_funded_item(plan_id: str | None, item_key: str | None) -> None:
    if not plan_id or not item_key:
        raise IntegrityError("Paid work requires a funded plan_id and item_key")


def require_one_take(parameters: dict) -> None:
    """Do not let a provider's batch/retry switches multiply an authorized take."""

    if parameters.get("num_images", 1) != 1 or parameters.get("auto_retry", False):
        raise IntegrityError("Only one take per request is authorized; no auto_retry")

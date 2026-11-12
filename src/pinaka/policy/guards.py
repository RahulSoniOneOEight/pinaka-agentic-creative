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
    """Do not let a provider's batch/retry switches multiply an authorized take.

    A curated multiplier list is used instead of one provider's field name:
    ``num_images`` is only one of several ways a request can buy more than the
    single take that was authorized.
    """

    multiplier_fields = {
        "num_images",
        "num_outputs",
        "num_samples",
        "num_variants",
        "num_results",
        "batch_size",
        "batch_count",
        "n",
        "count",
        "samples",
        "variants",
        "outputs",
        "results",
    }
    retry_fields = {"auto_retry", "retry", "retries", "max_retries"}
    for name, value in parameters.items():
        normalised = str(name).lower().replace("-", "_")
        if normalised in multiplier_fields and value != 1:
            raise IntegrityError(
                f"Only one take per request is authorized; {name} must be exactly 1"
            )
        if normalised in retry_fields and value not in (0, False, None):
            raise IntegrityError("Only one take per request is authorized; no retry")

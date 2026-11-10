from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import Field

from .models import StrictModel
from .utils import sha256_json, utc_now

RetentionClass = Literal[
    "permanent",
    "archive",
    "master",
    "derivative",
    "review",
    "temp",
    "log",
]
Action = Literal["keep", "purge", "review"]

DEFAULT_RETENTION_DAYS: dict[str, int | None] = {
    "permanent": None,
    "archive": None,
    "master": None,
    "derivative": 365,
    "review": 90,
    "temp": 7,
    "log": 180,
}
IMMUTABLE_SEGMENTS = {"archive"}


class RetentionPolicy(StrictModel):
    schema_version: str = "retention-policy/v1"
    classes: dict[str, int | None] = Field(
        default_factory=lambda: dict(DEFAULT_RETENTION_DAYS)
    )


class RetentionItem(StrictModel):
    path: str = Field(min_length=1, max_length=2000)
    retention_class: RetentionClass
    created_at: datetime
    size_bytes: int = Field(default=0, ge=0)
    pinned: bool = False


class RetentionDecision(StrictModel):
    path: str
    retention_class: str
    action: Action
    reason: str
    expires_at: str | None = None
    size_bytes: int = 0


class RetentionReport(StrictModel):
    schema_version: str = "retention-report/v1"
    generated_at: str
    policy_hash: str
    dry_run: bool = True
    counts: dict[str, int]
    reclaimed_bytes: int
    items: tuple[RetentionDecision, ...]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _immutable(path: str) -> bool:
    return bool(IMMUTABLE_SEGMENTS & set(re.split(r"[\\/]+", path.casefold())))


def plan_retention(
    items: list[RetentionItem],
    *,
    now: datetime | None = None,
    policy: RetentionPolicy | None = None,
) -> RetentionReport:
    """Build a non-destructive retention plan.

    The plan never deletes anything. ``purge`` means the item is past its policy
    window and may be removed by an explicit, separately authorised operation.
    Archive paths are always retained because the exact-hash archive is immutable.
    """

    active_policy = policy or RetentionPolicy()
    current = _aware(now or datetime.now(timezone.utc))
    decisions: list[RetentionDecision] = []
    counts = {"keep": 0, "purge": 0, "review": 0}
    reclaimed = 0

    for item in items:
        days = active_policy.classes.get(item.retention_class)
        expires_at: str | None = None
        if days is not None:
            expires_at = (_aware(item.created_at) + timedelta(days=days)).isoformat()
        if _immutable(item.path):
            action: Action = "keep"
            reason = "Archive paths are immutable and always retained."
        elif item.pinned:
            action = "keep"
            reason = "Item is pinned."
        elif item.retention_class in {"permanent", "archive", "master"}:
            action = "keep"
            reason = f"{item.retention_class} class has no expiry."
        elif days is None:
            action = "review"
            reason = "No retention window is defined; require a human decision."
        elif _aware(item.created_at) + timedelta(days=days) > current:
            action = "keep"
            reason = f"Retention window of {days} days has not elapsed."
        else:
            action = "purge"
            reason = f"Retention window of {days} days has elapsed."
            reclaimed += item.size_bytes
        counts[action] += 1
        decisions.append(
            RetentionDecision(
                path=item.path,
                retention_class=item.retention_class,
                action=action,
                reason=reason,
                expires_at=expires_at,
                size_bytes=item.size_bytes,
            )
        )

    return RetentionReport(
        generated_at=utc_now(),
        policy_hash=sha256_json(active_policy.model_dump(mode="json")),
        counts=counts,
        reclaimed_bytes=reclaimed,
        items=tuple(sorted(decisions, key=lambda decision: decision.path)),
    )


def retention_payload(report: RetentionReport) -> dict[str, Any]:
    return report.model_dump(mode="json")

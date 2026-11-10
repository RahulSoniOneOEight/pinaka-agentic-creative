from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import Field

from .models import StrictModel

DEFAULT_POLICY_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "providers"
    / "preference-policy.json"
)


class RouteCandidate(StrictModel):
    """One execution route offered for a capability."""

    route_id: str = Field(min_length=1, max_length=200)
    tier: str = Field(min_length=1, max_length=40)
    ready: bool = True
    paid: bool = False
    estimated_usd: Decimal | None = Field(default=None, ge=0)
    requires_membership: bool = False
    requires_production: bool = False
    notes: str | None = Field(default=None, max_length=500)


class PreferencePolicy(StrictModel):
    version: int = 1
    default_order: tuple[str, ...]
    rules: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    max_usd_per_call: Decimal = Field(default=Decimal("0.50"), ge=0)
    max_usd_per_project: Decimal = Field(default=Decimal("5.00"), ge=0)
    allow_paid: bool = False
    require_receipt: bool = True
    require_quote: bool = True
    fallback_on_unavailable: bool = True

    def order_for(self, capability: str) -> tuple[str, ...]:
        return self.rules.get(capability, self.default_order)

    @classmethod
    def load(cls, path: Path | str = DEFAULT_POLICY_PATH) -> PreferencePolicy:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(payload)


class RouteRejection(StrictModel):
    route_id: str
    tier: str
    reason: str


class RouteSelection(StrictModel):
    capability: str
    selected: str | None
    tier: str | None
    paid: bool
    estimated_usd: Decimal | None
    reasons: tuple[str, ...]
    rejected: tuple[RouteRejection, ...]

    @property
    def ok(self) -> bool:
        return self.selected is not None


def select_route(
    capability: str,
    candidates: list[RouteCandidate],
    policy: PreferencePolicy,
    *,
    budget_usd_remaining: Decimal | None = None,
) -> RouteSelection:
    """Pick the first eligible route in policy order, with explicit rejections."""

    order = policy.order_for(capability)
    ranked = sorted(
        candidates,
        key=lambda item: order.index(item.tier) if item.tier in order else len(order),
    )
    rejected: list[RouteRejection] = []
    reasons: list[str] = []

    for candidate in ranked:
        if candidate.tier not in order:
            rejected.append(
                RouteRejection(
                    route_id=candidate.route_id,
                    tier=candidate.tier,
                    reason=f"tier {candidate.tier!r} is not permitted for {capability}",
                )
            )
            continue
        if not candidate.ready:
            rejected.append(
                RouteRejection(
                    route_id=candidate.route_id,
                    tier=candidate.tier,
                    reason="route is not ready",
                )
            )
            if not policy.fallback_on_unavailable:
                break
            continue
        if candidate.paid and not policy.allow_paid:
            rejected.append(
                RouteRejection(
                    route_id=candidate.route_id,
                    tier=candidate.tier,
                    reason="paid routes are disabled by policy",
                )
            )
            continue
        if candidate.paid and candidate.estimated_usd is not None:
            if candidate.estimated_usd > policy.max_usd_per_call:
                rejected.append(
                    RouteRejection(
                        route_id=candidate.route_id,
                        tier=candidate.tier,
                        reason=(
                            f"estimate {candidate.estimated_usd} exceeds the per-call "
                            f"ceiling {policy.max_usd_per_call}"
                        ),
                    )
                )
                continue
            if (
                budget_usd_remaining is not None
                and candidate.estimated_usd > budget_usd_remaining
            ):
                rejected.append(
                    RouteRejection(
                        route_id=candidate.route_id,
                        tier=candidate.tier,
                        reason="estimate exceeds the remaining project budget",
                    )
                )
                continue
        if candidate.paid and policy.require_quote and candidate.estimated_usd is None:
            rejected.append(
                RouteRejection(
                    route_id=candidate.route_id,
                    tier=candidate.tier,
                    reason="a paid route requires a quote before selection",
                )
            )
            continue
        reasons.append(
            f"selected {candidate.tier} route {candidate.route_id} for {capability}"
        )
        return RouteSelection(
            capability=capability,
            selected=candidate.route_id,
            tier=candidate.tier,
            paid=candidate.paid,
            estimated_usd=candidate.estimated_usd,
            reasons=tuple(reasons),
            rejected=tuple(rejected),
        )

    reasons.append(f"no eligible route for {capability}")
    return RouteSelection(
        capability=capability,
        selected=None,
        tier=None,
        paid=False,
        estimated_usd=None,
        reasons=tuple(reasons),
        rejected=tuple(rejected),
    )


def policy_summary(policy: PreferencePolicy) -> dict[str, Any]:
    return {
        "version": policy.version,
        "default_order": list(policy.default_order),
        "rules": {key: list(value) for key, value in policy.rules.items()},
        "allow_paid": policy.allow_paid,
        "max_usd_per_call": str(policy.max_usd_per_call),
        "max_usd_per_project": str(policy.max_usd_per_project),
        "require_quote": policy.require_quote,
        "require_receipt": policy.require_receipt,
    }

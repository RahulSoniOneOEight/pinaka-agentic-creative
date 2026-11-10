from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from .enums import DataClassification
from .models import StrictModel
from .utils import sha256_json

CLASSIFICATION_ORDER: tuple[DataClassification, ...] = (
    DataClassification.PUBLIC,
    DataClassification.INTERNAL,
    DataClassification.CONFIDENTIAL,
    DataClassification.RESTRICTED,
)
QUALITY_ORDER = {"draft": 0, "standard": 1, "flagship": 2}
Policy = Literal["cost", "quality", "balanced", "priority"]


class RouteRequest(StrictModel):
    role: str = Field(min_length=1, max_length=120)
    data_classification: DataClassification = DataClassification.PUBLIC
    max_unit_cost: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    policy: Policy = "balanced"
    exclude: tuple[str, ...] = ()
    require_enabled: bool = True


class RouteCandidate(StrictModel):
    stable_key: str
    provider: str
    model: str
    data_classification: DataClassification
    unit_cost: Decimal | None = None
    quality_tier: str = "standard"
    priority: int = 100
    eligible: bool
    reasons: tuple[str, ...] = ()


class RouteDecision(StrictModel):
    schema_version: str = "provider-route/v1"
    request_hash: str
    role: str
    policy: Policy
    chosen: str | None
    fallback_chain: tuple[str, ...] = ()
    candidates: tuple[RouteCandidate, ...] = ()
    excluded: tuple[RouteCandidate, ...] = ()
    rationale: str


def _rank(value: DataClassification) -> int:
    return CLASSIFICATION_ORDER.index(value)


def _unit_cost(profile: dict[str, Any]) -> Decimal | None:
    limits = profile.get("limits") or {}
    raw = limits.get("unit_cost")
    if raw is None:
        raw = limits.get("unit_cost_usd")
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (ArithmeticError, ValueError):
        return None


def _quality(profile: dict[str, Any]) -> str:
    value = str((profile.get("limits") or {}).get("quality_tier", "standard"))
    return value if value in QUALITY_ORDER else "standard"


def _priority(profile: dict[str, Any]) -> int:
    raw = (profile.get("limits") or {}).get("priority", 100)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 100


def _eligibility(
    profile: dict[str, Any], request: RouteRequest
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    eligible = True
    if request.require_enabled and not profile.get("enabled"):
        eligible = False
        reasons.append("profile is disabled")
    if request.role not in (profile.get("approved_roles") or []):
        eligible = False
        reasons.append(f"role {request.role!r} is not approved for this profile")
    profile_classification = DataClassification(profile["data_classification"])
    if _rank(profile_classification) < _rank(request.data_classification):
        eligible = False
        reasons.append(
            f"profile is not approved for {request.data_classification.value} data"
        )
    if profile.get("stable_key") in request.exclude:
        eligible = False
        reasons.append("profile is excluded by the request")
    currency = str((profile.get("limits") or {}).get("currency", request.currency))
    if currency.upper() != request.currency.upper():
        eligible = False
        reasons.append(f"profile bills in {currency}, request is in {request.currency}")
    if request.max_unit_cost is not None:
        cost = _unit_cost(profile)
        if cost is None:
            eligible = False
            reasons.append("unit cost is unknown, so the cost ceiling cannot be met")
        elif cost > request.max_unit_cost:
            eligible = False
            reasons.append(
                f"unit cost {cost} exceeds the ceiling {request.max_unit_cost}"
            )
    if "capabilities" in (profile.get("limits") or {}):
        capabilities = set(profile["limits"]["capabilities"])
        if request.role not in capabilities:
            eligible = False
            reasons.append("profile does not declare the requested capability")
    return eligible, tuple(reasons)


def _sort_key(candidate: RouteCandidate, request: RouteRequest) -> tuple[Any, ...]:
    cost = (
        candidate.unit_cost if candidate.unit_cost is not None else Decimal("Infinity")
    )
    quality = QUALITY_ORDER[candidate.quality_tier]
    if request.policy == "cost":
        return (cost, -quality, candidate.stable_key)
    if request.policy == "quality":
        return (-quality, cost, candidate.stable_key)
    if request.policy == "priority":
        return (candidate.priority, cost, candidate.stable_key)
    return (-quality, cost, candidate.stable_key)


def _candidate(profile: dict[str, Any], eligible: bool, reasons: tuple[str, ...]):
    return RouteCandidate(
        stable_key=str(profile["stable_key"]),
        provider=str(profile.get("provider", "")),
        model=str(profile.get("model", "")),
        data_classification=DataClassification(profile["data_classification"]),
        unit_cost=_unit_cost(profile),
        quality_tier=_quality(profile),
        priority=_priority(profile),
        eligible=eligible,
        reasons=reasons,
    )


def route_profiles(
    profiles: list[dict[str, Any]], request: RouteRequest
) -> RouteDecision:
    candidates: list[RouteCandidate] = []
    excluded: list[RouteCandidate] = []
    for profile in profiles:
        eligible, reasons = _eligibility(profile, request)
        candidate = _candidate(profile, eligible, reasons)
        (candidates if eligible else excluded).append(candidate)
    candidates.sort(key=lambda item: _sort_key(item, request))
    excluded.sort(key=lambda item: item.stable_key)
    chosen = candidates[0].stable_key if candidates else None

    chain: list[str] = []
    if chosen:
        by_key = {str(profile["stable_key"]): profile for profile in profiles}
        current = by_key[chosen]
        seen = {chosen}
        for _ in range(5):
            next_key = next(
                (
                    key
                    for key in (current.get("fallback_profiles") or [])
                    if key not in seen
                    and any(candidate.stable_key == key for candidate in candidates)
                ),
                None,
            )
            if not next_key:
                break
            chain.append(next_key)
            seen.add(next_key)
            current = by_key.get(next_key) or {}

    rationale = (
        f"Selected {chosen} under the {request.policy} policy."
        if chosen
        else "No profile satisfies the role, classification and cost constraints."
    )
    return RouteDecision(
        request_hash=sha256_json(request.model_dump(mode="json")),
        role=request.role,
        policy=request.policy,
        chosen=chosen,
        fallback_chain=tuple(chain),
        candidates=tuple(candidates),
        excluded=tuple(excluded),
        rationale=rationale,
    )


def route_payload(decision: RouteDecision) -> dict[str, Any]:
    return decision.model_dump(mode="json")

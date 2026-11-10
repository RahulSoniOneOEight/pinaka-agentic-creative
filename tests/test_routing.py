from __future__ import annotations

from decimal import Decimal

from pinaka.routing import RouteRequest, route_profiles


def profile(
    key,
    *,
    roles=("image",),
    classification="public",
    cost="0.10",
    quality="standard",
    enabled=True,
    fallbacks=(),
    priority=100,
):
    return {
        "stable_key": key,
        "provider": "test",
        "model": f"model-{key}",
        "approved_roles": list(roles),
        "data_classification": classification,
        "limits": {
            "unit_cost": cost,
            "quality_tier": quality,
            "priority": priority,
            "currency": "USD",
        },
        "fallback_profiles": list(fallbacks),
        "enabled": enabled,
    }


def test_route_rejects_unapproved_role_and_disabled_profile():
    decision = route_profiles(
        [
            profile("a", roles=("video",)),
            profile("b", enabled=False),
        ],
        RouteRequest(role="image"),
    )
    assert decision.chosen is None
    reasons = {item.stable_key: item.reasons for item in decision.excluded}
    assert any("role" in reason for reason in reasons["a"])
    assert any("disabled" in reason for reason in reasons["b"])


def test_route_enforces_data_classification():
    decision = route_profiles(
        [
            profile("public", classification="public"),
            profile("confidential", classification="confidential"),
        ],
        RouteRequest(role="image", data_classification="confidential"),
    )
    assert decision.chosen == "confidential"
    assert [item.stable_key for item in decision.excluded] == ["public"]


def test_route_cost_policy_and_ceiling():
    decision = route_profiles(
        [
            profile("cheap", cost="0.05"),
            profile("expensive", cost="0.50"),
        ],
        RouteRequest(role="image", policy="cost", max_unit_cost=Decimal("0.10")),
    )
    assert decision.chosen == "cheap"
    assert [item.stable_key for item in decision.excluded] == ["expensive"]
    assert decision.request_hash


def test_route_quality_policy_prefers_flagship():
    decision = route_profiles(
        [
            profile("draft", quality="draft", cost="0.01"),
            profile("flagship", quality="flagship", cost="0.90"),
        ],
        RouteRequest(role="image", policy="quality"),
    )
    assert decision.chosen == "flagship"
    assert decision.candidates[0].quality_tier == "flagship"


def test_route_fallback_chain_skips_ineligible_and_cycles():
    decision = route_profiles(
        [
            profile("a", cost="0.01", fallbacks=("b", "c", "a")),
            profile("b", cost="0.02", fallbacks=("a",), enabled=False),
            profile("c", cost="0.03", fallbacks=("a",)),
        ],
        RouteRequest(role="image", policy="cost"),
    )
    assert decision.chosen == "a"
    assert decision.fallback_chain == ("c",)


def test_route_unknown_cost_fails_closed_under_a_ceiling():
    no_cost = profile("unknown")
    no_cost["limits"].pop("unit_cost")
    decision = route_profiles(
        [no_cost],
        RouteRequest(role="image", max_unit_cost=Decimal("1.00")),
    )
    assert decision.chosen is None
    assert "unknown" in decision.excluded[0].reasons[0]

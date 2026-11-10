from __future__ import annotations

from decimal import Decimal

from pinaka.preference import (
    PreferencePolicy,
    RouteCandidate,
    RouteSelection,
    policy_summary,
    select_route,
)


def policy(**overrides) -> PreferencePolicy:
    base = {
        "default_order": ("local", "fal_direct", "studio"),
        "rules": {
            "render": ("local", "studio"),
            "inpaint": ("fal_direct", "studio", "local"),
            "publication": ("controller",),
            "vision_judgement": ("managed_model", "fal_direct"),
        },
        "allow_paid": True,
        "max_usd_per_call": Decimal("0.50"),
    }
    base.update(overrides)
    return PreferencePolicy(**base)


def test_repository_policy_loads():
    loaded = PreferencePolicy.load()

    assert loaded.version >= 1
    assert "publication" in loaded.rules
    assert loaded.rules["publication"] == ("controller",)
    assert loaded.allow_paid is True
    assert str(loaded.max_usd_per_project) == "2.00"


def test_local_route_is_preferred_for_render():
    selection = select_route(
        "render",
        [
            RouteCandidate(route_id="studio-hyperframes", tier="studio", paid=True),
            RouteCandidate(route_id="local-ffmpeg", tier="local"),
        ],
        policy(),
    )

    assert selection.selected == "local-ffmpeg"
    assert selection.paid is False


def test_paid_route_is_rejected_when_paid_is_disabled():
    selection = select_route(
        "inpaint",
        [
            RouteCandidate(
                route_id="fal-fill",
                tier="fal_direct",
                paid=True,
                estimated_usd=Decimal("0.04"),
            ),
            RouteCandidate(route_id="studio-fill", tier="studio", paid=False),
        ],
        policy(allow_paid=False),
    )

    assert selection.selected == "studio-fill"
    assert any("paid routes are disabled" in item.reason for item in selection.rejected)


def test_estimate_above_the_ceiling_falls_back():
    selection = select_route(
        "inpaint",
        [
            RouteCandidate(
                route_id="fal-fill",
                tier="fal_direct",
                paid=True,
                estimated_usd=Decimal("2.00"),
            ),
            RouteCandidate(route_id="studio-fill", tier="studio"),
        ],
        policy(),
    )

    assert selection.selected == "studio-fill"
    assert any("per-call ceiling" in item.reason for item in selection.rejected)


def test_paid_route_requires_a_quote():
    selection = select_route(
        "inpaint",
        [RouteCandidate(route_id="fal-fill", tier="fal_direct", paid=True)],
        policy(),
    )

    assert selection.ok is False
    assert any("quote" in item.reason for item in selection.rejected)


def test_remaining_budget_limits_selection():
    selection = select_route(
        "inpaint",
        [
            RouteCandidate(
                route_id="fal-fill",
                tier="fal_direct",
                paid=True,
                estimated_usd=Decimal("0.40"),
            )
        ],
        policy(),
        budget_usd_remaining=Decimal("0.10"),
    )

    assert selection.ok is False
    assert any("remaining project budget" in item.reason for item in selection.rejected)


def test_tier_not_permitted_is_rejected():
    selection = select_route(
        "publication",
        [RouteCandidate(route_id="studio-publish", tier="studio")],
        policy(),
    )

    assert selection.ok is False
    assert any("not permitted" in item.reason for item in selection.rejected)


def test_unavailable_route_falls_back_when_allowed():
    selection = select_route(
        "render",
        [
            RouteCandidate(route_id="local-ffmpeg", tier="local", ready=False),
            RouteCandidate(route_id="studio-hyperframes", tier="studio"),
        ],
        policy(),
    )

    assert selection.selected == "studio-hyperframes"


def test_unavailable_route_stops_when_fallback_is_off():
    selection = select_route(
        "render",
        [
            RouteCandidate(route_id="local-ffmpeg", tier="local", ready=False),
            RouteCandidate(route_id="studio-hyperframes", tier="studio"),
        ],
        policy(fallback_on_unavailable=False),
    )

    assert selection.ok is False


def test_unknown_capability_uses_the_default_order():
    selection = select_route(
        "face_swap",
        [
            RouteCandidate(route_id="studio-thing", tier="studio"),
            RouteCandidate(
                route_id="fal-thing",
                tier="fal_direct",
                paid=True,
                estimated_usd=Decimal("0.10"),
            ),
        ],
        policy(),
    )

    assert selection.selected == "fal-thing"


def test_selection_serialises_with_rejections():
    selection: RouteSelection = select_route(
        "inpaint",
        [RouteCandidate(route_id="fal-fill", tier="fal_direct", paid=True)],
        policy(allow_paid=False),
    )
    payload = selection.model_dump(mode="json")

    assert payload["selected"] is None
    assert payload["rejected"][0]["route_id"] == "fal-fill"


def test_repository_policy_prefers_sourced_music_before_generation():
    loaded = PreferencePolicy.load()
    selection = select_route(
        "music",
        [
            RouteCandidate(route_id="ace-step-local", tier="local"),
            RouteCandidate(route_id="openverse-audio", tier="stock"),
        ],
        loaded,
    )

    assert selection.selected == "openverse-audio"
    assert selection.tier == "stock"


def test_repository_policy_generates_music_only_when_stock_is_unavailable():
    loaded = PreferencePolicy.load()
    selection = select_route(
        "music",
        [
            RouteCandidate(route_id="openverse-audio", tier="stock", ready=False),
            RouteCandidate(route_id="ace-step-local", tier="local"),
        ],
        loaded,
    )

    assert selection.selected == "ace-step-local"


def test_repository_policy_covers_sound_effects_as_stock_first():
    loaded = PreferencePolicy.load()

    assert loaded.rules["sound_effects"][0] == "stock"
    assert loaded.rules["ambience"][0] == "stock"


def test_policy_summary_reports_ordering():
    summary = policy_summary(policy())

    assert summary["rules"]["render"] == ["local", "studio"]
    assert summary["allow_paid"] is True

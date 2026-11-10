from __future__ import annotations

from decimal import Decimal

import pytest

from pinaka.budget import TieredBudget, evaluate_spend, spend_summary
from pinaka.errors import BudgetExceededError


def budget(**overrides) -> TieredBudget:
    base = {
        "provider": "fal",
        "primary_usd": Decimal("1.00"),
        "bonus_usd": Decimal("1.00"),
        "notify_at_usd": Decimal("1.00"),
        "max_per_call_usd": Decimal("0.50"),
    }
    base.update(overrides)
    return TieredBudget(**base)


def test_default_budget_matches_the_config_file():
    loaded = TieredBudget.load()

    assert loaded.primary_cents == 100
    assert loaded.bonus_cents == 100
    assert loaded.total_cents == 200
    assert loaded.notify_at_cents == 100


def test_spend_within_primary_has_no_notification():
    decision = evaluate_spend(0, 40, budget())

    assert decision.allowed is True
    assert decision.tier == "primary"
    assert decision.notifications == ()


def test_reaching_the_first_dollar_notifies_once():
    decision = evaluate_spend(50, 50, budget())

    assert decision.allowed is True
    assert decision.tier == "primary"
    assert decision.total_after_cents == 100
    assert len(decision.notifications) == 1
    assert "Primary budget" in decision.notifications[0]


def test_spend_after_the_threshold_uses_bonus_without_repeating():
    decision = evaluate_spend(100, 20, budget())

    assert decision.tier == "bonus"
    assert decision.notifications == ()


def test_spend_above_the_total_is_refused():
    decision = evaluate_spend(180, 40, budget())

    assert decision.allowed is False
    assert decision.tier == "refused"
    assert "above the total budget" in decision.reason


def test_per_call_ceiling_is_enforced():
    decision = evaluate_spend(0, 80, budget())

    assert decision.allowed is False
    assert "per-call ceiling" in decision.reason


def test_negative_spend_is_rejected():
    with pytest.raises(ValueError, match="positive"):
        evaluate_spend(0, 0, budget())


def test_summary_reports_remaining_tiers():
    summary = spend_summary(130, budget())

    assert summary["primary_remaining_usd"] == 0.0
    assert summary["bonus_remaining_usd"] == 0.7
    assert summary["total_remaining_usd"] == 0.7
    assert summary["primary_consumed"] is True


def test_controller_records_spend_and_notifies_at_the_threshold(controller):
    first = controller.reserve_provider_spend(
        "fal", amount_usd="0.50", purpose="first paid call"
    )
    assert first["decision"]["tier"] == "primary"
    assert first["notifications"] == []

    second = controller.reserve_provider_spend(
        "fal", amount_usd="0.50", purpose="second paid call"
    )
    assert second["notifications"], "the first dollar must notify"
    assert "Primary budget" in second["notifications"][0]

    third = controller.reserve_provider_spend(
        "fal", amount_usd="0.20", purpose="bonus call"
    )
    assert third["decision"]["tier"] == "bonus"
    assert third["notifications"] == []


def test_controller_refuses_beyond_the_total(controller):
    controller.reserve_provider_spend("fal", amount_usd="0.50", purpose="a")
    controller.reserve_provider_spend("fal", amount_usd="0.50", purpose="b")
    controller.reserve_provider_spend("fal", amount_usd="0.50", purpose="c")
    controller.reserve_provider_spend("fal", amount_usd="0.50", purpose="d")

    with pytest.raises(BudgetExceededError, match="total budget"):
        controller.reserve_provider_spend("fal", amount_usd="0.50", purpose="e")


def test_spend_summary_reports_entries(controller):
    controller.reserve_provider_spend("fal", amount_usd="0.25", purpose="probe")

    summary = controller.provider_spend_summary("fal")

    assert summary["spent_usd"] == 0.25
    assert len(summary["entries"]) == 1
    assert summary["entries"][0]["purpose"] == "probe"

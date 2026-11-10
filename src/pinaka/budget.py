from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from pydantic import Field

from .models import StrictModel

DEFAULT_BUDGET_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "providers" / "spend-budget.json"
)


class TieredBudget(StrictModel):
    """A primary allowance plus a bonus allowance with one notification threshold."""

    provider: str = "fal"
    currency: str = "USD"
    primary_usd: Decimal = Field(default=Decimal("1.00"), ge=0)
    bonus_usd: Decimal = Field(default=Decimal("1.00"), ge=0)
    notify_at_usd: Decimal = Field(default=Decimal("1.00"), ge=0)
    max_per_call_usd: Decimal = Field(default=Decimal("0.50"), gt=0)
    note: str | None = None

    @property
    def primary_cents(self) -> int:
        return int((self.primary_usd * 100).to_integral_value())

    @property
    def bonus_cents(self) -> int:
        return int((self.bonus_usd * 100).to_integral_value())

    @property
    def total_cents(self) -> int:
        return self.primary_cents + self.bonus_cents

    @property
    def notify_at_cents(self) -> int:
        return int((self.notify_at_usd * 100).to_integral_value())

    @classmethod
    def load(cls, path: Path | str = DEFAULT_BUDGET_PATH) -> TieredBudget:
        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


class SpendDecision(StrictModel):
    provider: str
    already_spent_cents: int
    amount_cents: int
    total_after_cents: int
    primary_cents: int
    total_limit_cents: int
    tier: str
    allowed: bool
    reason: str
    notifications: tuple[str, ...] = ()


def evaluate_spend(
    already_spent_cents: int,
    amount_cents: int,
    budget: TieredBudget,
) -> SpendDecision:
    """Decide which tier pays for a call, and whether the threshold is crossed."""

    if amount_cents <= 0:
        raise ValueError("Spend amount must be positive")
    per_call_limit = int((budget.max_per_call_usd * 100).to_integral_value())
    if amount_cents > per_call_limit:
        return SpendDecision(
            provider=budget.provider,
            already_spent_cents=already_spent_cents,
            amount_cents=amount_cents,
            total_after_cents=already_spent_cents + amount_cents,
            primary_cents=budget.primary_cents,
            total_limit_cents=budget.total_cents,
            tier="refused",
            allowed=False,
            reason=(
                f"call estimate {amount_cents / 100:.2f} exceeds the per-call ceiling "
                f"{per_call_limit / 100:.2f}"
            ),
        )

    total_after = already_spent_cents + amount_cents
    if total_after > budget.total_cents:
        return SpendDecision(
            provider=budget.provider,
            already_spent_cents=already_spent_cents,
            amount_cents=amount_cents,
            total_after_cents=total_after,
            primary_cents=budget.primary_cents,
            total_limit_cents=budget.total_cents,
            tier="refused",
            allowed=False,
            reason=(
                f"spend would reach {total_after / 100:.2f}, above the total budget "
                f"{budget.total_cents / 100:.2f}"
            ),
        )

    tier = "primary" if total_after <= budget.primary_cents else "bonus"
    notifications: tuple[str, ...] = ()
    if already_spent_cents < budget.notify_at_cents <= total_after:
        notifications = (
            (
                f"Primary budget of {budget.primary_cents / 100:.2f} USD is now consumed; "
                f"spending moves to the bonus budget "
                f"({budget.bonus_cents / 100:.2f} USD)."
            ),
        )
    return SpendDecision(
        provider=budget.provider,
        already_spent_cents=already_spent_cents,
        amount_cents=amount_cents,
        total_after_cents=total_after,
        primary_cents=budget.primary_cents,
        total_limit_cents=budget.total_cents,
        tier=tier,
        allowed=True,
        reason=f"charged to the {tier} budget",
        notifications=notifications,
    )


def spend_summary(already_spent_cents: int, budget: TieredBudget) -> dict[str, object]:
    remaining_total = budget.total_cents - already_spent_cents
    remaining_primary = max(budget.primary_cents - already_spent_cents, 0)
    remaining_bonus = max(remaining_total - remaining_primary, 0)
    return {
        "provider": budget.provider,
        "currency": budget.currency,
        "spent_usd": round(already_spent_cents / 100, 2),
        "primary_remaining_usd": round(remaining_primary / 100, 2),
        "bonus_remaining_usd": round(remaining_bonus / 100, 2),
        "total_remaining_usd": round(remaining_total / 100, 2),
        "primary_consumed": already_spent_cents >= budget.primary_cents,
        "total_limit_usd": round(budget.total_cents / 100, 2),
    }

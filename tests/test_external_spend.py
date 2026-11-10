from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pinaka.external_spend import (
    ExternalQuote,
    ExternalReceipt,
    SpendEnvelope,
    SpendReservation,
    authorize_submission,
    reconcile_spend,
)
from pinaka.models import Money

NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)


def money(value):
    return Money(amount=value, currency="USD")


def quote(amount="0.50", *, quote_id="q1", valid_until=None):
    return ExternalQuote(
        quote_id=quote_id,
        provider="studio",
        route="image.generate",
        amount=money(amount),
        valid_until=valid_until,
        terms_hash="a" * 64,
    )


def reservation(amount="1.00", *, approved=True):
    return SpendReservation(
        work_item_id="opening-shot",
        quote_id="q1",
        reserved=money(amount),
        approved=approved,
        approval_ref="approval-1",
    )


def receipt(amount="0.40", *, refunded=None, delivered=True):
    return ExternalReceipt(
        receipt_id="r1",
        work_item_id="opening-shot",
        provider_job_id="job-1",
        charged=money(amount),
        refunded=money(refunded) if refunded else None,
        delivered_artifact_hash="f" * 64 if delivered else None,
        received_at=NOW,
    )


def envelope(ceiling="1.00"):
    return SpendEnvelope(currency="USD", ceiling=money(ceiling))


def test_matched_receipt_reconciles_within_envelope():
    report = reconcile_spend([quote()], [reservation()], [receipt()], envelope())
    assert report.status == "pass"
    assert report.committed_minor == 40
    assert report.unspent_minor == 60
    assert report.items[0].status == "matched"


def test_refund_offsets_the_committed_amount():
    report = reconcile_spend(
        [quote()], [reservation()], [receipt(refunded="0.10")], envelope()
    )
    assert report.committed_minor == 30
    assert report.refunded_minor == 10


def test_over_quote_is_a_failure():
    report = reconcile_spend(
        [quote("0.40")], [reservation()], [receipt("0.45")], envelope()
    )
    assert report.status == "fail"
    assert report.items[0].status == "over_quote"


def test_duplicate_receipt_is_a_failure():
    report = reconcile_spend(
        [quote()], [reservation()], [receipt(), receipt()], envelope()
    )
    assert report.items[0].status == "duplicate_receipt"
    assert report.status == "fail"


def test_orphan_receipt_is_a_failure():
    report = reconcile_spend([quote()], [], [receipt()], envelope())
    assert report.status == "fail"
    assert report.orphan_receipt_ids == ("r1",)


def test_pending_reservation_holds_budget_and_warns():
    report = reconcile_spend([quote()], [reservation()], [], envelope())
    assert report.status == "warn"
    assert report.reserved_open_minor == 100
    assert report.items[0].status == "pending"


def test_missing_delivery_warns_even_when_charge_matches():
    report = reconcile_spend(
        [quote()], [reservation()], [receipt(delivered=False)], envelope()
    )
    assert report.status == "warn"
    assert report.items[0].status == "missing_delivery"


def test_envelope_over_commit_fails():
    report = reconcile_spend(
        [quote("1.20")], [reservation("1.20")], [receipt("1.10")], envelope("1.00")
    )
    assert report.status == "fail"
    assert report.unspent_minor == -10


def test_authorize_submission_fits_envelope():
    decision = authorize_submission(
        quote(valid_until=NOW + timedelta(hours=1)),
        envelope(),
        [],
        [],
        now=NOW,
    )
    assert decision.allowed is True
    assert decision.available_minor == 100


def test_authorize_submission_blocks_expired_and_over_budget():
    expired = authorize_submission(
        quote(valid_until=NOW - timedelta(minutes=1)),
        envelope(),
        [],
        [],
        now=NOW,
    )
    over = authorize_submission(
        quote("2.00"),
        envelope(),
        [],
        [],
        now=NOW,
    )
    assert expired.allowed is False
    assert "expired" in expired.reason
    assert over.allowed is False
    assert "remaining envelope" in over.reason


def test_authorize_submission_respects_open_reservations():
    decision = authorize_submission(
        quote("0.70"),
        envelope(),
        [reservation("0.20")],
        [],
        now=NOW,
    )
    assert decision.allowed is True
    blocked = authorize_submission(
        quote("0.91"),
        envelope(),
        [reservation("0.20")],
        [receipt("0.10")],
        now=NOW,
    )
    assert blocked.allowed is False
    assert blocked.available_minor == 90

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from .models import Money, StrictModel
from .utils import utc_now

ItemStatus = Literal[
    "matched",
    "pending",
    "over_reserved",
    "over_quote",
    "missing_quote",
    "not_approved",
    "duplicate_receipt",
    "currency_mismatch",
    "missing_delivery",
    "orphan_receipt",
]


class ExternalQuote(StrictModel):
    quote_id: str
    provider: str
    route: str
    amount: Money
    valid_until: datetime | None = None
    terms_hash: str | None = None
    evidence_ref: str | None = None
    refundable: bool = False


class SpendReservation(StrictModel):
    work_item_id: str
    quote_id: str
    reserved: Money
    approved: bool = False
    approval_ref: str | None = None


class ExternalReceipt(StrictModel):
    receipt_id: str
    work_item_id: str
    provider_job_id: str
    charged: Money
    refunded: Money | None = None
    delivered_artifact_hash: str | None = None
    received_at: datetime | None = None


class SpendEnvelope(StrictModel):
    currency: str
    ceiling: Money


class ReconciledItem(StrictModel):
    work_item_id: str
    quote_id: str
    status: ItemStatus
    reserved_minor: int
    charged_minor: int
    refunded_minor: int
    reasons: tuple[str, ...] = ()


class SpendReport(StrictModel):
    schema_version: str = "external-spend-report/v1"
    generated_at: str
    currency: str
    ceiling_minor: int
    committed_minor: int
    refunded_minor: int
    reserved_open_minor: int
    unspent_minor: int
    status: Literal["pass", "warn", "fail"]
    items: tuple[ReconciledItem, ...] = ()
    orphan_receipt_ids: tuple[str, ...] = ()
    unclaimed_quote_ids: tuple[str, ...] = ()


class SubmissionDecision(StrictModel):
    allowed: bool
    reason: str
    quote_id: str
    available_minor: int
    committed_minor: int
    reserved_open_minor: int


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _net_minor(receipt: ExternalReceipt) -> int:
    refunded = receipt.refunded.minor if receipt.refunded else 0
    return receipt.charged.minor - refunded


def _currency_mismatch(*values: str) -> bool:
    return len({value.upper() for value in values}) > 1


def reconcile_spend(
    quotes: list[ExternalQuote],
    reservations: list[SpendReservation],
    receipts: list[ExternalReceipt],
    envelope: SpendEnvelope,
) -> SpendReport:
    quotes_by_id = {quote.quote_id: quote for quote in quotes}
    receipts_by_work: dict[str, list[ExternalReceipt]] = {}
    for receipt in receipts:
        receipts_by_work.setdefault(receipt.work_item_id, []).append(receipt)

    items: list[ReconciledItem] = []
    committed = 0
    refunded_total = 0
    reserved_open = 0
    used_work_items = set()

    for reservation in reservations:
        used_work_items.add(reservation.work_item_id)
        quote = quotes_by_id.get(reservation.quote_id)
        work_receipts = receipts_by_work.get(reservation.work_item_id, [])
        reasons: list[str] = []
        status: ItemStatus
        charged = sum(_net_minor(receipt) for receipt in work_receipts)
        refunded = sum(
            receipt.refunded.minor for receipt in work_receipts if receipt.refunded
        )
        if quote is None:
            status = "missing_quote"
            reasons.append("No exact quote is recorded for this reservation.")
        elif not reservation.approved:
            status = "not_approved"
            reasons.append("The work item has no recorded approval.")
        elif not work_receipts:
            status = "pending"
            reasons.append("The job has no receipt yet.")
            reserved_open += reservation.reserved.minor
        elif len(work_receipts) > 1:
            status = "duplicate_receipt"
            reasons.append(
                f"{len(work_receipts)} receipts are recorded for one work item."
            )
        elif _currency_mismatch(
            envelope.currency,
            reservation.reserved.currency,
            quote.amount.currency,
            work_receipts[0].charged.currency,
        ):
            status = "currency_mismatch"
            reasons.append("Currencies differ across envelope, quote or receipt.")
        elif charged > reservation.reserved.minor:
            status = "over_reserved"
            reasons.append("The net charge exceeds the reserved amount.")
        elif charged > quote.amount.minor:
            status = "over_quote"
            reasons.append("The net charge exceeds the quoted amount.")
        elif not work_receipts[0].delivered_artifact_hash:
            status = "missing_delivery"
            reasons.append("The receipt has no delivered artifact hash.")
        else:
            status = "matched"

        if status in {"matched", "missing_delivery"}:
            committed += charged
            refunded_total += refunded
        items.append(
            ReconciledItem(
                work_item_id=reservation.work_item_id,
                quote_id=reservation.quote_id,
                status=status,
                reserved_minor=reservation.reserved.minor,
                charged_minor=charged,
                refunded_minor=refunded,
                reasons=tuple(reasons),
            )
        )

    orphan_receipts = sorted(
        receipt.receipt_id
        for receipt in receipts
        if receipt.work_item_id not in used_work_items
    )
    declared_quotes = {reservation.quote_id for reservation in reservations}
    unclaimed_quotes = sorted(set(quotes_by_id) - declared_quotes)
    fails = {
        "over_reserved",
        "over_quote",
        "missing_quote",
        "not_approved",
        "duplicate_receipt",
        "currency_mismatch",
        "orphan_receipt",
    }
    statuses = {item.status for item in items}
    if statuses & fails or orphan_receipts:
        status = "fail"
    elif statuses & {"pending", "missing_delivery"}:
        status = "warn"
    else:
        status = "pass"
    if orphan_receipts:
        items.append(
            ReconciledItem(
                work_item_id="orphan",
                quote_id="",
                status="orphan_receipt",
                reserved_minor=0,
                charged_minor=0,
                refunded_minor=0,
                reasons=("Receipts exist without a reservation.",),
            )
        )
    unspent = envelope.ceiling.minor - committed
    if unspent < 0:
        status = "fail"
    return SpendReport(
        generated_at=utc_now(),
        currency=envelope.currency,
        ceiling_minor=envelope.ceiling.minor,
        committed_minor=committed,
        refunded_minor=refunded_total,
        reserved_open_minor=reserved_open,
        unspent_minor=unspent,
        status=status,
        items=tuple(items),
        orphan_receipt_ids=tuple(orphan_receipts),
        unclaimed_quote_ids=tuple(unclaimed_quotes),
    )


def authorize_submission(
    quote: ExternalQuote,
    envelope: SpendEnvelope,
    reservations: list[SpendReservation],
    receipts: list[ExternalReceipt],
    *,
    now: datetime | None = None,
) -> SubmissionDecision:
    """Decide whether one exact quote fits the envelope. It never submits a job."""

    current = _aware(now) or datetime.now(timezone.utc)
    committed = sum(
        _net_minor(receipt)
        for receipt in receipts
        if receipt.work_item_id
        in {reservation.work_item_id for reservation in reservations}
    )
    reserved_open = sum(
        reservation.reserved.minor
        for reservation in reservations
        if reservation.approved
        and not any(
            receipt.work_item_id == reservation.work_item_id for receipt in receipts
        )
    )
    available = envelope.ceiling.minor - committed - reserved_open
    if _currency_mismatch(quote.amount.currency, envelope.currency):
        return SubmissionDecision(
            allowed=False,
            reason="Quote currency does not match the spend envelope.",
            quote_id=quote.quote_id,
            available_minor=available,
            committed_minor=committed,
            reserved_open_minor=reserved_open,
        )
    valid_until = _aware(quote.valid_until)
    if valid_until is not None and valid_until < current:
        return SubmissionDecision(
            allowed=False,
            reason="The quote has expired.",
            quote_id=quote.quote_id,
            available_minor=available,
            committed_minor=committed,
            reserved_open_minor=reserved_open,
        )
    if quote.amount.minor > available:
        return SubmissionDecision(
            allowed=False,
            reason="The quote exceeds the remaining envelope.",
            quote_id=quote.quote_id,
            available_minor=available,
            committed_minor=committed,
            reserved_open_minor=reserved_open,
        )
    return SubmissionDecision(
        allowed=True,
        reason="The quote fits the remaining envelope.",
        quote_id=quote.quote_id,
        available_minor=available,
        committed_minor=committed,
        reserved_open_minor=reserved_open,
    )


def spend_payload(report: SpendReport) -> dict[str, Any]:
    return report.model_dump(mode="json")

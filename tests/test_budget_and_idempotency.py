from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from pinaka.enums import JobStatus
from pinaka.errors import BudgetExceededError, ConflictError, IntegrityError
from pinaka.models import JobRequest, Money

from .conftest import create_project


def request(key: str, cost: str = "3.00", **parameters):
    return JobRequest(
        provider="mock",
        endpoint="local/test",
        operation="generate_still",
        idempotency_key=key,
        estimated_cost=Money(amount=Decimal(cost), currency="USD"),
        parameters=parameters,
    )


def test_idempotency_does_not_double_reserve(controller):
    project = create_project(controller, budget="5.00")
    first = controller.reserve_job(project.id, request("same-key", prompt="one"))
    repeated = controller.reserve_job(project.id, request("same-key", prompt="one"))

    assert repeated.id == first.id
    assert controller.repository.get_project(project.id).reserved.amount == Decimal(3)
    assert len(controller.repository.budget_ledger(project.id)) == 1

    with pytest.raises(ConflictError):
        controller.reserve_job(project.id, request("same-key", prompt="changed"))
    with pytest.raises(BudgetExceededError):
        controller.reserve_job(project.id, request("second-key", cost="3.00"))


def test_secret_fields_are_rejected_before_persistence(controller):
    project = create_project(controller)

    with pytest.raises(IntegrityError, match="Sensitive field"):
        controller.reserve_job(
            project.id, request("secret", api_key="must-not-persist")
        )

    assert controller.repository.recoverable_jobs(project.id) == []


def test_settlement_releases_reservation_and_charges_actual(controller):
    project = create_project(controller, budget="5.00")
    job = controller.reserve_job(project.id, request("settle"))
    controller.mark_job_submitting(job.id)
    controller.record_job_submitted(job.id, "provider-123", {"accepted": True})
    settled = controller.settle_job(
        job.id,
        target=JobStatus.SUCCEEDED,
        actual_cost=Money(amount=Decimal("2.00"), currency="USD"),
        response={"status": "succeeded"},
    )

    status = controller.repository.get_project(project.id)
    ledger = controller.repository.budget_ledger(project.id)

    assert settled.status == JobStatus.SUCCEEDED
    assert status.reserved.amount == Decimal(0)
    assert status.spent.amount == Decimal(2)
    assert [entry["kind"] for entry in ledger] == ["reserve", "release", "charge"]

    repeated = controller.settle_job(
        job.id,
        target=JobStatus.SUCCEEDED,
        actual_cost=Money(amount=Decimal("2.00"), currency="USD"),
    )
    assert repeated.status == JobStatus.SUCCEEDED
    assert len(controller.repository.budget_ledger(project.id)) == 3


def test_concurrent_reservations_are_serialized(controller):
    project = create_project(controller, budget="5.00")

    def reserve(key):
        try:
            return controller.reserve_job(project.id, request(key, cost="4.00")).id
        except BudgetExceededError:
            return "budget-exceeded"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, ("parallel-a", "parallel-b")))

    assert results.count("budget-exceeded") == 1
    assert controller.repository.get_project(project.id).reserved.amount == Decimal(4)

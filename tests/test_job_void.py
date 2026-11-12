from __future__ import annotations

from decimal import Decimal

import pytest

from pinaka.enums import JobStatus
from pinaka.errors import ConflictError
from pinaka.models import JobRequest, Money
from tests.conftest import create_project


def job_request(key: str = "void-1", cost: str = "0.10") -> JobRequest:
    return JobRequest(
        provider="mock",
        endpoint="local/test",
        operation="generate_still",
        idempotency_key=key,
        estimated_cost=Money(amount=Decimal(cost), currency="USD"),
        parameters={"prompt": "still"},
    )


def test_void_releases_the_reservation(controller):
    project = create_project(controller, budget="5.00")
    job = controller.reserve_job(project.id, job_request())
    assert controller.repository.get_project(project.id).reserved.minor == 10

    voided = controller.void_job(job.id, reason="host never answered")

    assert voided.status == JobStatus.FAILED
    assert controller.repository.get_project(project.id).reserved.minor == 0
    with pytest.raises(ConflictError, match="BUDGET_RESERVED"):
        controller.void_job(job.id, reason="again")


def test_void_refuses_a_job_that_may_have_been_submitted(controller):
    project = create_project(controller, budget="5.00")
    job = controller.reserve_job(project.id, job_request(key="void-2"))
    controller.mark_job_submitting(job.id)

    with pytest.raises(ConflictError, match="BUDGET_RESERVED"):
        controller.void_job(job.id, reason="not sure whether it was sent")

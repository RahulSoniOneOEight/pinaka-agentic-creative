from decimal import Decimal

import pytest

from pinaka.controller import CreativeController
from pinaka.enums import JobStatus, ProjectStatus
from pinaka.errors import InvalidTransitionError
from pinaka.models import JobRequest, Money

from .conftest import create_project


def test_project_reopens_from_saved_checkpoint(controller):
    project = create_project(controller)
    controller.transition_project(
        project.id, ProjectStatus.PLANNED, reason="Planning complete"
    )

    reopened = CreativeController.open(controller.artifact_store.root)
    restored = reopened.repository.get_project(project.id)

    assert restored.status == ProjectStatus.PLANNED
    assert [
        event["event_type"] for event in reopened.repository.events(project.id)
    ] == [
        "project.created",
        "project.status_changed",
    ]


def test_invalid_state_transition_is_rejected(controller):
    project = create_project(controller)

    with pytest.raises(InvalidTransitionError):
        controller.transition_project(
            project.id,
            ProjectStatus.ANIMATIC_APPROVED,
            reason="Invalid shortcut",
        )

    assert controller.repository.get_project(project.id).status == ProjectStatus.DRAFT


def test_submission_unknown_retains_budget_and_is_recoverable(controller):
    project = create_project(controller, budget="5.00")
    request = JobRequest(
        provider="mock",
        endpoint="local/test",
        operation="generate_still",
        idempotency_key="recovery-001",
        estimated_cost=Money(amount=Decimal("3.00"), currency="USD"),
    )
    job = controller.reserve_job(project.id, request)
    controller.mark_job_submitting(job.id)
    controller.mark_submission_unknown(
        job.id, {"type": "Timeout", "message": "No job ID"}
    )

    reopened = CreativeController.open(controller.artifact_store.root)
    recoverable = reopened.repository.recoverable_jobs(project.id)
    restored_project = reopened.repository.get_project(project.id)

    assert len(recoverable) == 1
    assert recoverable[0].status == JobStatus.SUBMISSION_UNKNOWN
    assert restored_project.reserved.amount == Decimal(3)
    assert restored_project.spent.amount == Decimal(0)

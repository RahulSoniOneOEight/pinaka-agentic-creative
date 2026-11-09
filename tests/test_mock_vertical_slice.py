from decimal import Decimal

from pinaka.adapters.mock import MockGenerationAdapter
from pinaka.enums import ApprovalStage, JobStatus
from pinaka.models import JobRequest, Money

from .conftest import advance_to_sourcing


def test_mock_adapter_runs_only_after_reservation_and_approval(controller):
    project, _, _ = advance_to_sourcing(controller)
    request = JobRequest(
        provider="mock",
        endpoint="local/deterministic-v1",
        operation="generate_still",
        idempotency_key="mock-take-001",
        estimated_cost=Money(amount=Decimal("1.00"), currency="USD"),
        parameters={"prompt": "test only"},
        required_approval_stage=ApprovalStage.SOURCING,
    )

    job, artifact = controller.run_adapter_once(
        project.id,
        request,
        MockGenerationAdapter(),
        artifact_path="assets/generated/mock-take-001.json",
    )

    assert job.status == JobStatus.SUCCEEDED
    assert artifact is not None
    assert controller.verify_artifact(artifact["id"])
    assert controller.repository.get_project(project.id).spent.amount == Decimal("0.50")

    repeated_job, repeated_artifact = controller.run_adapter_once(
        project.id,
        request,
        MockGenerationAdapter(),
        artifact_path="assets/generated/mock-take-001.json",
    )
    assert repeated_job.id == job.id
    assert repeated_artifact is None
    assert len(controller.repository.list_artifacts(project.id)) == 1

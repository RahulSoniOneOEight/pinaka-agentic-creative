from __future__ import annotations

from decimal import Decimal

from ..enums import JobStatus
from ..models import JobRequest, Money
from ..utils import canonical_json, sha256_bytes
from .base import PollResult, Submission


class MockGenerationAdapter:
    """Deterministic, local adapter that never contacts a provider."""

    provider_name = "mock"

    def submit(self, request: JobRequest) -> Submission:
        digest = sha256_bytes(
            canonical_json(request.model_dump(mode="json")).encode("utf-8")
        )
        provider_job_id = f"mock_{digest[:24]}"
        return Submission(
            provider_job_id=provider_job_id,
            raw={
                "provider_job_id": provider_job_id,
                "accepted": True,
                "simulated": True,
            },
        )

    def poll(self, provider_job_id: str, request: JobRequest) -> PollResult:
        payload = canonical_json(
            {
                "provider_job_id": provider_job_id,
                "operation": request.operation,
                "parameters": request.parameters,
                "simulated": True,
            }
        ).encode("utf-8")
        actual = Money(
            amount=(request.estimated_cost.amount * Decimal("0.50")).quantize(
                Decimal("0.01")
            ),
            currency=request.estimated_cost.currency,
        )
        return PollResult(
            status=JobStatus.SUCCEEDED,
            raw={
                "provider_job_id": provider_job_id,
                "status": "succeeded",
                "simulated": True,
            },
            output_bytes=payload,
            actual_cost=actual,
        )

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..adapters.base import PollResult, Submission
from ..enums import JobStatus
from ..errors import IntegrityError
from ..fal import FalClient, _data_uri, _first_output_url
from ..models import JobRequest, Money
from ..policy.guards import require_one_take
from ..utils import canonical_json, sha256_bytes

DEFAULT_IMAGE_MODEL = "fal-ai/flux/schnell"


@dataclass
class FalImageAdapter:
    """Paid fal image generation through the controller's job boundary.

    fal's queue call is blocking, so the whole generation happens during submit and
    the result is cached for poll. When fal reports a cost we use it; otherwise the
    planned estimate is charged and the receipt records that the source was the plan.
    """

    client: FalClient
    model: str = DEFAULT_IMAGE_MODEL
    provider_name: str = "fal"
    source_image: bytes | None = None
    source_mime: str = "image/png"
    reference_images: tuple[bytes, ...] = ()
    reference_mimes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self._results: dict[str, PollResult] = {}

    def submit(self, request: JobRequest) -> Submission:
        require_one_take(request.parameters)
        if request.estimated_cost.currency != "USD":
            raise IntegrityError(
                "fal estimates require USD; no implicit currency conversion"
            )
        payload = {"prompt": request.parameters.get("prompt", "")}
        image_urls: list[str] = []
        if self.source_image is not None:
            image_urls.append(_data_uri(self.source_image, self.source_mime))
        image_urls.extend(
            _data_uri(
                reference,
                self.reference_mimes[index]
                if index < len(self.reference_mimes)
                else self.source_mime,
            )
            for index, reference in enumerate(self.reference_images)
        )
        if image_urls:
            payload["image_urls"] = image_urls
        for key in (
            "aspect_ratio",
            "image_size",
            "seed",
            "num_images",
            "output_format",
        ):
            if key in request.parameters:
                payload[key] = request.parameters[key]
        result = self.client.run(self.model, payload)
        output_url = _first_output_url(result)
        content = self.client.download(output_url)
        cost = _reported_cost(result)
        cost_source = "provider"
        if cost is None:
            cost = request.estimated_cost
            cost_source = "planned_estimate"
        if cost.currency != "USD":
            raise IntegrityError(
                "fal reported a non-USD cost; reconcile without relabeling currency"
            )
        provider_job_id = f"fal_{sha256_bytes(content)[:24]}"
        self._results[provider_job_id] = PollResult(
            status=JobStatus.SUCCEEDED,
            raw={
                "model": self.model,
                "output_url": output_url,
                "cost_source": cost_source,
                "simulated": False,
            },
            output_bytes=content,
            actual_cost=Money(
                amount=cost.amount, currency=request.estimated_cost.currency
            ),
        )
        return Submission(
            provider_job_id=provider_job_id,
            raw={"model": self.model, "cost_source": cost_source},
        )

    def poll(self, provider_job_id: str, request: JobRequest) -> PollResult:
        try:
            return self._results[provider_job_id]
        except KeyError as error:
            raise ValueError(
                f"fal result for {provider_job_id} is not available in this process"
            ) from error


def _reported_cost(payload: dict[str, Any]) -> Money | None:
    """Read a cost fal reports, if the model response includes one."""

    candidates: list[Any] = [
        payload.get("cost"),
        payload.get("price"),
        (payload.get("billing") or {}).get("cost"),
        (payload.get("metrics") or {}).get("cost"),
    ]
    for value in candidates:
        if value is None:
            continue
        if isinstance(value, dict):
            amount = value.get("amount", value.get("usd"))
            currency = value.get("currency", "USD")
        else:
            amount, currency = value, "USD"
        try:
            return Money(amount=Decimal(str(amount)), currency=str(currency).upper())
        except (ArithmeticError, ValueError, TypeError):
            continue
    return None


def dry_run_payload(prompt: str, **options: Any) -> dict[str, Any]:
    """Stable request preview with no network call."""

    payload = {"prompt": prompt}
    payload.update({key: value for key, value in options.items() if value is not None})
    return {
        "schema": "pinaka.fal.image-request/v1",
        "model": options.get("model", DEFAULT_IMAGE_MODEL),
        "payload": payload,
        "request_hash": sha256_bytes(canonical_json(payload).encode("utf-8")),
    }

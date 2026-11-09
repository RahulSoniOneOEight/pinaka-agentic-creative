from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..enums import JobStatus
from ..models import JobRequest, Money


@dataclass(frozen=True)
class Submission:
    provider_job_id: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class PollResult:
    status: JobStatus
    raw: dict[str, Any]
    output_bytes: bytes | None = None
    actual_cost: Money | None = None


class GenerationAdapter(Protocol):
    provider_name: str

    def submit(self, request: JobRequest) -> Submission: ...

    def poll(self, provider_job_id: str, request: JobRequest) -> PollResult: ...

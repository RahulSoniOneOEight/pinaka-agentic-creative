from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .enums import ArtifactKind
from .utils import canonical_json, sha256_bytes

PRODUCTION_STATUSES = {
    "opening",
    "submitted",
    "working",
    "completed",
    "failed",
    "cancelled",
    "unknown",
}


class StudioPending(Exception):
    """Raised by a transport when the next Studio response is not available yet."""

    def __init__(self, message: str, *, step: str) -> None:
        super().__init__(message)
        self.step = step


@dataclass(frozen=True)
class StudioOpenRequest:
    """Everything Studio may know about a production at creation time."""

    project_id: str
    title: str
    intent: str
    autonomy: str
    max_cost_minor: int
    currency: str
    packet_hash: str
    constraints: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": "pinaka.openmontage.open-production/v1",
            "project_id": self.project_id,
            "title": self.title,
            "intent": self.intent,
            "autonomy": self.autonomy,
            "budget": {
                "max_cost_minor": self.max_cost_minor,
                "currency": self.currency,
            },
            "packet_hash": self.packet_hash,
            "constraints": self.constraints,
        }


@dataclass(frozen=True)
class StudioOpenResult:
    external_production_id: str
    status: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StudioSubmission:
    external_job_id: str
    status: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StudioPoll:
    external_job_id: str
    status: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StudioCollectedOutput:
    source_path: str
    relative_path: str
    kind: ArtifactKind
    role: str
    content_hash: str
    parent_artifact_id: str | None = None


@dataclass(frozen=True)
class StudioCollected:
    outputs: tuple[StudioCollectedOutput, ...]
    actual_cost_minor: int
    raw: dict[str, Any] = field(default_factory=dict)


class StudioTransport(Protocol):
    """Host-side bridge to OpenMontage. Never authorizes spend itself."""

    name: str

    def open_production(self, request: StudioOpenRequest) -> StudioOpenResult: ...

    def submit(
        self, production: dict[str, Any], packet: dict[str, Any]
    ) -> StudioSubmission: ...

    def poll(
        self, production: dict[str, Any], submission: StudioSubmission
    ) -> StudioPoll: ...

    def collect(
        self, production: dict[str, Any], submission: StudioSubmission
    ) -> StudioCollected: ...


class DeterministicStudioTransport:
    """Offline transport used to exercise the gateway end to end."""

    name = "deterministic"

    def __init__(
        self,
        *,
        workdir: Path | str,
        outcome: str = "completed",
        cost_minor: int = 0,
        polls_before_terminal: int = 1,
    ):
        if outcome not in {"completed", "failed", "cancelled"}:
            raise ValueError(f"Unsupported deterministic outcome: {outcome}")
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.outcome = outcome
        self.cost_minor = cost_minor
        self.polls_before_terminal = max(0, polls_before_terminal)
        self._opened = 0
        self._polls = 0
        self.submit_calls = 0
        self.open_calls = 0

    def open_production(self, request: StudioOpenRequest) -> StudioOpenResult:
        self.open_calls += 1
        self._opened += 1
        return StudioOpenResult(
            external_production_id=f"fake-prod-{self._opened}",
            status="opened",
            raw={"simulated": True},
        )

    def submit(
        self, production: dict[str, Any], packet: dict[str, Any]
    ) -> StudioSubmission:
        self.submit_calls += 1
        return StudioSubmission(
            external_job_id=f"fake-job-{self.submit_calls}",
            status="accepted",
            raw={"packet_hash": packet.get("packet_hash")},
        )

    def poll(
        self, production: dict[str, Any], submission: StudioSubmission
    ) -> StudioPoll:
        self._polls += 1
        if self._polls <= self.polls_before_terminal:
            return StudioPoll(
                external_job_id=submission.external_job_id, status="working"
            )
        return StudioPoll(
            external_job_id=submission.external_job_id, status=self.outcome
        )

    def collect(
        self, production: dict[str, Any], submission: StudioSubmission
    ) -> StudioCollected:
        payload = canonical_json(
            {
                "production": production.get("external_production_id"),
                "job": submission.external_job_id,
                "packet_hash": production.get("packet_hash"),
                "simulated": True,
            }
        ).encode("utf-8")
        target = self.workdir / f"{submission.external_job_id}.json"
        target.write_bytes(payload)
        return StudioCollected(
            outputs=(
                StudioCollectedOutput(
                    source_path=str(target),
                    relative_path=(
                        "artifacts/openmontage/results/"
                        f"{submission.external_job_id}.json"
                    ),
                    kind=ArtifactKind.DERIVATIVE,
                    role="deterministic_result",
                    content_hash=sha256_bytes(payload),
                ),
            ),
            actual_cost_minor=self.cost_minor,
            raw={"simulated": True},
        )


class SpoolStudioTransport:
    """File bridge: a host (agent or operator) performs each Studio step.

    Requests are written as JSON; the host replies with a matching response file.
    This keeps live MCP calls outside the controller while preserving idempotency.
    """

    name = "spool"

    def __init__(self, spool: Path | str):
        self.spool = Path(spool)
        self.spool.mkdir(parents=True, exist_ok=True)

    def _exchange(self, step: str, key: str, request: dict[str, Any]) -> dict[str, Any]:
        request_path = self.spool / f"{key}.{step}.request.json"
        response_path = self.spool / f"{key}.{step}.response.json"
        if not request_path.exists():
            request_path.write_text(canonical_json(request), encoding="utf-8")
        if not response_path.exists():
            raise StudioPending(
                f"Awaiting Studio response: {response_path.name}", step=step
            )
        import json

        return json.loads(response_path.read_text(encoding="utf-8"))

    def open_production(self, request: StudioOpenRequest) -> StudioOpenResult:
        key = request.packet_hash[:16]
        reply = self._exchange("open", key, request.to_payload())
        return StudioOpenResult(
            external_production_id=str(reply["production_id"]),
            status=str(reply.get("status", "opened")),
            raw=reply,
        )

    def submit(
        self, production: dict[str, Any], packet: dict[str, Any]
    ) -> StudioSubmission:
        key = production["packet_hash"][:16]
        reply = self._exchange(
            "submit",
            key,
            {
                "schema": "pinaka.openmontage.submit/v1",
                "production_id": production.get("external_production_id"),
                "packet": packet,
            },
        )
        return StudioSubmission(
            external_job_id=str(reply["job_id"]),
            status=str(reply.get("status", "accepted")),
            raw=reply,
        )

    def poll(
        self, production: dict[str, Any], submission: StudioSubmission
    ) -> StudioPoll:
        key = production["packet_hash"][:16]
        reply = self._exchange(
            f"poll-{submission.external_job_id}",
            key,
            {
                "schema": "pinaka.openmontage.poll/v1",
                "production_id": production.get("external_production_id"),
                "job_id": submission.external_job_id,
            },
        )
        return StudioPoll(
            external_job_id=submission.external_job_id,
            status=str(reply["status"]),
            detail=reply,
        )

    def collect(
        self, production: dict[str, Any], submission: StudioSubmission
    ) -> StudioCollected:
        key = production["packet_hash"][:16]
        reply = self._exchange(
            f"collect-{submission.external_job_id}",
            key,
            {
                "schema": "pinaka.openmontage.collect/v1",
                "production_id": production.get("external_production_id"),
                "job_id": submission.external_job_id,
            },
        )
        outputs = tuple(
            StudioCollectedOutput(
                source_path=item["source_path"],
                relative_path=item["relative_path"],
                kind=ArtifactKind(item.get("kind", "derivative")),
                role=item["role"],
                content_hash=item["content_hash"],
                parent_artifact_id=item.get("parent_artifact_id"),
            )
            for item in reply.get("outputs", ())
        )
        return StudioCollected(
            outputs=outputs,
            actual_cost_minor=int(reply.get("actual_cost_minor", 0)),
            raw=reply,
        )

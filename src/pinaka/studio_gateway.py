from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .enums import ArtifactKind
from .errors import ConfigurationError, IntegrityError
from .utils import canonical_json, sha256_bytes

PRODUCTION_STATUSES = {
    "opened",
    "opening",
    "submitted",
    "working",
    "completed",
    "failed",
    "cancelled",
    "unknown",
}

STUDIO_JOB_STATUSES = PRODUCTION_STATUSES | {"accepted", "queued"}


class StudioPending(Exception):
    """Raised by a transport when the next Studio response is not available yet."""

    def __init__(self, message: str, *, step: str) -> None:
        super().__init__(message)
        self.step = step


class StudioStale(StudioPending):
    """A spool request outlived its window without a host response."""

    def __init__(
        self, message: str, *, step: str, age_seconds: float, request_path: str
    ) -> None:
        super().__init__(message, step=step)
        self.age_seconds = age_seconds
        self.request_path = request_path


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
    A request that outlives ``stale_after_seconds`` raises ``StudioStale`` so a
    held reservation can be voided deliberately instead of waiting forever.
    """

    name = "spool"

    def __init__(self, spool: Path | str, *, stale_after_seconds: int = 900):
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self.spool = Path(spool)
        self.spool.mkdir(parents=True, exist_ok=True)
        self.stale_after_seconds = stale_after_seconds

    def _write_once(self, path: Path, request: dict[str, Any]) -> None:
        """Create the request atomically; a racing writer leaves it untouched."""

        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(canonical_json(request))
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            return

    def _exchange(self, step: str, key: str, request: dict[str, Any]) -> dict[str, Any]:
        request_path = self.spool / f"{key}.{step}.request.json"
        response_path = self.spool / f"{key}.{step}.response.json"
        self._write_once(request_path, request)
        if not response_path.exists():
            age = max(0.0, time.time() - request_path.stat().st_mtime)
            if age >= self.stale_after_seconds:
                raise StudioStale(
                    f"Studio has not answered {request_path.name} in "
                    f"{int(age)}s; void the held reservation or have the host respond",
                    step=step,
                    age_seconds=age,
                    request_path=str(request_path),
                )
            raise StudioPending(
                f"Awaiting Studio response: {response_path.name}", step=step
            )
        try:
            reply = json.loads(response_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ConfigurationError(
                f"Studio {step} response is not valid JSON: {response_path.name}"
            ) from error
        if not isinstance(reply, dict):
            raise ConfigurationError(
                f"Studio {step} response must be a JSON object: {response_path.name}"
            )
        return reply

    @staticmethod
    def _required_text(reply: dict[str, Any], name: str, step: str) -> str:
        value = reply.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(
                f"Studio {step} response needs a non-empty string {name!r}"
            )
        return value

    @staticmethod
    def _checked_status(
        reply: dict[str, Any], step: str, *, required: bool = False
    ) -> str:
        if "status" not in reply:
            if required:
                raise ConfigurationError(f"Studio {step} response needs a 'status'")
            return "unknown"
        status = str(reply["status"])
        if status not in STUDIO_JOB_STATUSES:
            raise ConfigurationError(
                f"Studio {step} response reported an unknown status: {status!r}"
            )
        return status

    def open_production(self, request: StudioOpenRequest) -> StudioOpenResult:
        key = request.packet_hash[:16]
        reply = self._exchange("open", key, request.to_payload())
        return StudioOpenResult(
            external_production_id=self._required_text(reply, "production_id", "open"),
            status=self._checked_status(reply, "open"),
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
            external_job_id=self._required_text(reply, "job_id", "submit"),
            status=self._checked_status(reply, "submit"),
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
            status=self._checked_status(reply, "poll", required=True),
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
        raw_outputs = reply.get("outputs")
        if not isinstance(raw_outputs, list):
            raise ConfigurationError("Studio collect response needs an 'outputs' list")
        actual_minor = reply.get("actual_cost_minor")
        if (
            not isinstance(actual_minor, int)
            or isinstance(actual_minor, bool)
            or actual_minor < 0
        ):
            raise ConfigurationError(
                "Studio collect response needs a non-negative integer 'actual_cost_minor'"
            )
        outputs = []
        for index, item in enumerate(raw_outputs):
            if not isinstance(item, dict):
                raise ConfigurationError(
                    f"Studio collect output {index} must be a JSON object"
                )
            content_hash = self._required_text(item, "content_hash", "collect")
            if len(content_hash) != 64 or any(
                character not in "0123456789abcdef" for character in content_hash
            ):
                raise IntegrityError(
                    f"Studio collect output {index} has an invalid sha256"
                )
            output = StudioCollectedOutput(
                source_path=self._required_text(item, "source_path", "collect"),
                relative_path=self._required_text(item, "relative_path", "collect"),
                kind=ArtifactKind(item.get("kind", "derivative")),
                role=self._required_text(item, "role", "collect"),
                content_hash=content_hash,
                parent_artifact_id=item.get("parent_artifact_id"),
            )
            if not output.relative_path.replace("\\", "/").startswith("artifacts/"):
                raise IntegrityError("Studio outputs must be stored beneath artifacts/")
            outputs.append(output)
        return StudioCollected(
            outputs=tuple(outputs),
            actual_cost_minor=actual_minor,
            raw=reply,
        )

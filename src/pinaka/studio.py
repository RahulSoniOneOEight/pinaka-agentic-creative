from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .enums import ArtifactKind
from .utils import canonical_json, sha256_bytes


@dataclass(frozen=True)
class StudioAdapterOutput:
    relative_path: str
    kind: ArtifactKind
    role: str
    content: bytes
    parent_artifact_id: str | None = None


@dataclass(frozen=True)
class StudioAdapterResult:
    outputs: tuple[StudioAdapterOutput, ...]
    details: dict[str, Any]
    response_hash: str


class StudioAdapter(Protocol):
    profile_key: str

    def execute(self, work_packet: dict[str, Any]) -> StudioAdapterResult: ...


class DeterministicStudioAdapter:
    """Zero-spend fixture that exercises the Studio packet/return boundary."""

    def __init__(
        self,
        *,
        profile_key: str = "openmontage-deterministic",
        outcome: str = "success",
    ):
        if outcome not in {"success", "failure", "timeout"}:
            raise ValueError(f"Unsupported deterministic Studio outcome: {outcome}")
        self.profile_key = profile_key
        self.outcome = outcome

    def execute(self, work_packet: dict[str, Any]) -> StudioAdapterResult:
        if self.outcome == "timeout":
            raise TimeoutError("Deterministic Studio timeout fixture")
        if self.outcome == "failure":
            raise RuntimeError("Deterministic Studio failure fixture")
        packet_hash = work_packet["packet_hash"]
        payload = canonical_json(
            {
                "schema": "pinaka.openmontage.fake-result/v1",
                "packet_hash": packet_hash,
                "phases": work_packet["scope"]["phases"],
                "simulated": True,
            }
        ).encode("utf-8")
        output = StudioAdapterOutput(
            relative_path=(
                f"artifacts/openmontage/results/{packet_hash[:12]}/result.json"
            ),
            kind=ArtifactKind.DERIVATIVE,
            role="deterministic_result",
            content=payload,
        )
        return StudioAdapterResult(
            outputs=(output,),
            details={"simulated": True, "adapter": self.profile_key},
            response_hash=sha256_bytes(payload),
        )

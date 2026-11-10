from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..models import ToolOperationRequest
from ..utils import canonical_json, sha256_bytes


@dataclass(frozen=True)
class ToolExecutionResult:
    output_bytes: bytes
    details: dict[str, Any]
    response_hash: str


class ToolAdapter(Protocol):
    profile_key: str

    def execute(self, request: ToolOperationRequest) -> ToolExecutionResult: ...


class DeterministicToolAdapter:
    """Local test adapter with explicit success, failure and timeout outcomes."""

    def __init__(
        self,
        *,
        profile_key: str = "deterministic-layout",
        outcome: str = "success",
    ):
        if outcome not in {"success", "failure", "timeout"}:
            raise ValueError(f"Unsupported deterministic outcome: {outcome}")
        self.profile_key = profile_key
        self.outcome = outcome

    def execute(self, request: ToolOperationRequest) -> ToolExecutionResult:
        if self.outcome == "timeout":
            raise TimeoutError("Deterministic timeout fixture")
        if self.outcome == "failure":
            raise RuntimeError("Deterministic failure fixture")
        output = canonical_json(
            {
                "operation": request.operation,
                "parameters": request.parameters,
                "input_artifact_ids": request.input_artifact_ids,
                "output_declaration": request.output_declaration,
                "simulated": True,
            }
        ).encode("utf-8")
        return ToolExecutionResult(
            output_bytes=output,
            details={"simulated": True, "adapter": self.profile_key},
            response_hash=sha256_bytes(output),
        )

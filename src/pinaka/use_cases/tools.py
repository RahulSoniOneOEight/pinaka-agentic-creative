from __future__ import annotations

from time import perf_counter
from typing import Any

from ..adapters.tool import ToolAdapter
from ..capabilities import probe_all
from ..enums import (
    ArtifactKind,
    ToolOperationStatus,
)
from ..errors import (
    ApprovalRequiredError,
    ConflictError,
    IntegrityError,
)
from ..models import (
    DependencyRef,
    ExecutionReceiptCreate,
    ProviderModelProfileCreate,
    ToolOperationRequest,
    ToolProfileCreate,
)
from ..utils import sha256_json


class ToolsUseCases:
    def register_provider_profile(
        self, request: ProviderModelProfileCreate
    ) -> dict[str, Any]:
        if request.enabled:
            raise ApprovalRequiredError(
                "Live provider activation is disabled by the current zero-spend pilot policy"
            )
        self._reject_sensitive_payload(
            request.model_dump(mode="json"), "provider_profile"
        )
        return self.repository.register_provider_profile(request)

    def register_tool_profile(self, request: ToolProfileCreate) -> dict[str, Any]:
        self._reject_sensitive_payload(request.model_dump(mode="json"), "tool_profile")
        return self.repository.register_tool_profile(request)

    def reserve_tool_operation(
        self, project_id: str, request: ToolOperationRequest
    ) -> dict[str, Any]:
        payload = request.model_dump(mode="json")
        self._reject_sensitive_payload(payload, "tool_operation")
        if request.required_approval_stage:
            approval = self.repository.active_approval(
                project_id, request.required_approval_stage.value
            )
            if not approval:
                raise ApprovalRequiredError(
                    f"Active {request.required_approval_stage.value} approval is required"
                )
        return self.repository.reserve_tool_operation(
            project_id, request, sha256_json(payload)
        )

    def record_execution_receipt(
        self, operation_id: str, request: ExecutionReceiptCreate
    ) -> dict[str, Any]:
        self._reject_sensitive_payload(request.model_dump(mode="json"), "receipt")
        return self.repository.record_execution_receipt(operation_id, request)

    def run_tool_once(
        self,
        project_id: str,
        request: ToolOperationRequest,
        adapter: ToolAdapter,
        *,
        artifact_path: str,
        artifact_kind: ArtifactKind = ArtifactKind.DERIVATIVE,
        dependencies: tuple[DependencyRef, ...] = (),
        parent_artifact_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        profile = self.repository.get_tool_profile(request.tool_profile_id)
        if profile["stable_key"] != adapter.profile_key:
            raise IntegrityError(
                f"Tool profile {profile['stable_key']!r} does not match adapter "
                f"{adapter.profile_key!r}"
            )
        operation = self.reserve_tool_operation(project_id, request)
        existing_receipt = self.repository.get_execution_receipt(operation["id"])
        if existing_receipt:
            artifact = None
            output_ids = existing_receipt["output_artifact_ids"]
            if output_ids:
                artifact = self.repository.get_artifact(output_ids[0])
            return operation, existing_receipt, artifact
        if operation["status"] == ToolOperationStatus.OUTCOME_UNKNOWN.value:
            raise ConflictError(
                "Tool operation outcome is unknown and requires reconciliation"
            )
        operation = self.repository.mark_tool_operation_running(operation["id"])
        started = perf_counter()
        try:
            result = adapter.execute(request)
        except TimeoutError:
            receipt = self.record_execution_receipt(
                operation["id"],
                ExecutionReceiptCreate(
                    status=ToolOperationStatus.OUTCOME_UNKNOWN,
                    elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                    details={"type": "TimeoutError", "redacted": True},
                ),
            )
            return self.repository.get_tool_operation(operation["id"]), receipt, None
        except (OSError, RuntimeError, ValueError) as error:
            receipt = self.record_execution_receipt(
                operation["id"],
                ExecutionReceiptCreate(
                    status=ToolOperationStatus.FAILED,
                    elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                    details={"type": type(error).__name__, "redacted": True},
                ),
            )
            return self.repository.get_tool_operation(operation["id"]), receipt, None
        artifact = self.write_artifact(
            project_id,
            kind=artifact_kind,
            relative_path=artifact_path,
            content=result.output_bytes,
            operation={
                "type": "tool_operation",
                "operation_id": operation["id"],
                "tool_profile": profile["stable_key"],
            },
            parent_artifact_id=parent_artifact_id,
            dependencies=(
                DependencyRef(entity_type="tool_operation", entity_id=operation["id"]),
                *dependencies,
            ),
        )
        receipt = self.record_execution_receipt(
            operation["id"],
            ExecutionReceiptCreate(
                status=ToolOperationStatus.SUCCEEDED,
                elapsed_ms=max(0, int((perf_counter() - started) * 1000)),
                output_artifact_ids=(artifact["id"],),
                response_hash=result.response_hash,
                details=result.details,
            ),
        )
        return self.repository.get_tool_operation(operation["id"]), receipt, artifact

    def check_capabilities(self) -> list[dict[str, Any]]:
        results = probe_all()
        for result in results:
            self.repository.record_capability(result)
        return [result.model_dump(mode="json") for result in results]

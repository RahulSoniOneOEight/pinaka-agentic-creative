import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from pinaka.adapters.tool import DeterministicToolAdapter
from pinaka.database import Database
from pinaka.enums import (
    ApprovalStage,
    CapabilityStatus,
    DataClassification,
    GroundingBundleKind,
    ProjectStatus,
    ProviderExecutionMode,
    ToolIntegrationMode,
    ToolOperationStatus,
)
from pinaka.errors import ApprovalRequiredError, ConflictError, IntegrityError
from pinaka.models import (
    ApprovalRequest,
    ExecutionReceiptCreate,
    GroundingBundleCreate,
    Money,
    PilotContract,
    ProviderModelProfileCreate,
    ToolOperationRequest,
    ToolProfileCreate,
)
from pinaka.schema import SCHEMA_V1, SCHEMA_VERSION

from .conftest import (
    advance_to_sourcing,
    complete_flow_ledger,
    create_project,
    record_final_candidate,
)

PILOT_CONTRACT = (
    Path(__file__).parents[1] / "config" / "pilots" / "pinaka-virtual-cfo-10s.json"
)


def load_pilot_contract() -> PilotContract:
    return PilotContract.model_validate_json(PILOT_CONTRACT.read_text(encoding="utf-8"))


def test_schema_v1_migrates_additively_without_losing_project(tmp_path):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA_V1)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
            ("2026-11-09T00:00:00.000Z",),
        )
        connection.execute(
            """
            INSERT INTO projects(
                id, slug, name, objective, mode, audience, output_format, language,
                duration_seconds, status, max_budget_minor, currency, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "prj_legacy",
                "legacy",
                "Legacy",
                "Preserve me",
                "product",
                "Reviewers",
                "9:16",
                "en",
                "10",
                "draft",
                0,
                "INR",
                "2026-11-09T00:00:00.000Z",
                "2026-11-09T00:00:00.000Z",
            ),
        )
        connection.execute("PRAGMA user_version = 1")

    Database(path).initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert (
            connection.execute(
                "SELECT name FROM projects WHERE id = 'prj_legacy'"
            ).fetchone()[0]
            == "Legacy"
        )
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "deliverables",
            "segments",
            "grounding_bundles",
            "provider_profiles",
            "tool_profiles",
            "tool_operations",
            "execution_receipts",
            "archives",
        }.issubset(tables)
        shot_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(shots)")
        }
        assert {"deliverable_id", "segment_id"}.issubset(shot_columns)


def test_pilot_contract_creates_resumable_plan_and_grounding(controller):
    contract = load_pilot_contract()
    assert contract.provider_policy is not None
    assert contract.provider_policy.mode == ProviderExecutionMode.DISABLED
    assert contract.provider_policy.max_paid_spend.amount == Decimal(0)

    project, artifact = controller.create_project_from_contract(contract)
    assert controller.verify_artifact(artifact["id"])
    assert artifact["immutable"] is True

    first = controller.apply_pilot_plan(project.id, contract)
    repeated = controller.apply_pilot_plan(project.id, contract)
    assert first["deliverable"]["id"] == repeated["deliverable"]["id"]
    assert len(repeated["segments"]) == 4
    assert controller.repository.get_project(project.id).status == ProjectStatus.PLANNED

    controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.BRIEF,
            approved_by="rahul-soni",
            scope={"contract_artifact_id": artifact["id"], "paid_calls": False},
        ),
    )
    bundles = controller.ground_pilot_from_contract(project.id, contract)
    repeated_bundles = controller.ground_pilot_from_contract(project.id, contract)

    assert {item["kind"] for item in bundles} == {"brand", "claims", "sources"}
    assert [item["id"] for item in bundles] == [item["id"] for item in repeated_bundles]
    assert (
        controller.repository.get_project(project.id).status
        == ProjectStatus.GROUNDING_LOCKED
    )
    assert controller.next_action(project.id)["action"] == "begin_sourcing"


def test_grounding_change_invalidates_only_dependent_approval(controller):
    contract = load_pilot_contract()
    project, _ = controller.create_project_from_contract(contract)
    controller.apply_pilot_plan(project.id, contract)
    controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.BRIEF,
            approved_by="rahul-soni",
            scope={"pilot": "virtual-cfo-10s"},
        ),
    )
    controller.ground_pilot_from_contract(project.id, contract)
    controller.transition_project(
        project.id, ProjectStatus.SOURCING, reason="Begin governed sourcing"
    )
    sourcing = controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.SOURCING,
            approved_by="rahul-soni",
            scope={"paid_calls": False, "sources": ["authorized-client-site"]},
        ),
    )

    controller.add_grounding_bundle(
        project.id,
        GroundingBundleCreate(
            kind=GroundingBundleKind.CLAIMS,
            source_ref="human-approved-campaign-claim-v2",
            values={"approved": ["Virtual CFO supports planning and insight"]},
            evidence_refs=("approval://rahul-soni/virtual-cfo-v2",),
        ),
    )

    assert (
        controller.repository.active_approval(project.id, ApprovalStage.SOURCING.value)
        is None
    )
    assert (
        controller.repository.active_approval(project.id, ApprovalStage.BRIEF.value)
        is not None
    )
    assert (
        controller.repository.get_project(project.id).status
        == ProjectStatus.GROUNDING_LOCKED
    )
    invalidated = {
        event["entity_id"]
        for event in controller.repository.events(project.id)
        if event["event_type"] == "approval.invalidated"
    }
    assert sourcing["id"] in invalidated


def test_tool_profiles_operations_and_receipts_are_governed(controller):
    project = create_project(controller, budget="0.00")
    tool = controller.register_tool_profile(
        ToolProfileCreate(
            stable_key="deterministic-layout",
            name="Deterministic layout fixture",
            integration_mode=ToolIntegrationMode.NATIVE,
            executable_or_endpoint="python:fixture",
            supported_operations=("render_layout",),
            capability_status=CapabilityStatus.WORKING,
        )
    )
    request = ToolOperationRequest(
        tool_profile_id=tool["id"],
        operation="render_layout",
        idempotency_key="layout-001",
        parameters={"width": 1080, "height": 1920},
        output_declaration={"media_type": "application/json"},
    )
    operation = controller.reserve_tool_operation(project.id, request)
    repeated = controller.reserve_tool_operation(project.id, request)
    assert repeated["id"] == operation["id"]

    receipt = controller.record_execution_receipt(
        operation["id"],
        ExecutionReceiptCreate(
            status=ToolOperationStatus.SUCCEEDED,
            elapsed_ms=12,
            details={"fixture": True},
        ),
    )
    assert receipt["status"] == ToolOperationStatus.SUCCEEDED.value

    with pytest.raises(IntegrityError):
        controller.record_execution_receipt(
            operation["id"],
            ExecutionReceiptCreate(
                status=ToolOperationStatus.SUCCEEDED,
                elapsed_ms=12,
                actual_cost=Money(amount=Decimal("0.01"), currency="USD"),
            ),
        )

    provider = controller.register_provider_profile(
        ProviderModelProfileCreate(
            stable_key="openai-planning-disabled",
            provider="openai",
            endpoint="https://api.openai.com/v1/responses",
            model="UNSELECTED",
            approved_roles=("planning",),
            credential_ref="OPENAI_API_KEY",
            data_classification=DataClassification.PUBLIC,
            enabled=False,
        )
    )
    with pytest.raises(ApprovalRequiredError):
        controller.register_provider_profile(
            ProviderModelProfileCreate(
                stable_key="openai-live-blocked",
                provider="openai",
                endpoint="https://api.openai.com/v1/responses",
                model="UNSELECTED",
                approved_roles=("planning",),
                credential_ref="OPENAI_API_KEY",
                enabled=True,
            )
        )
    with pytest.raises(ConflictError):
        controller.reserve_tool_operation(
            project.id,
            request.model_copy(update={"provider_profile_id": provider["id"]}),
        )

    run_request = request.model_copy(update={"idempotency_key": "layout-run-001"})
    run_operation, run_receipt, artifact = controller.run_tool_once(
        project.id,
        run_request,
        DeterministicToolAdapter(),
        artifact_path="artifacts/layout-run-001.json",
    )
    repeated_operation, repeated_receipt, repeated_artifact = controller.run_tool_once(
        project.id,
        run_request,
        DeterministicToolAdapter(),
        artifact_path="artifacts/layout-run-001.json",
    )
    assert run_operation["status"] == ToolOperationStatus.SUCCEEDED.value
    assert artifact is not None and controller.verify_artifact(artifact["id"])
    assert repeated_operation["id"] == run_operation["id"]
    assert repeated_receipt["id"] == run_receipt["id"]
    assert repeated_artifact is not None

    timeout_request = request.model_copy(
        update={"idempotency_key": "layout-timeout-001"}
    )
    timeout_operation, timeout_receipt, timeout_artifact = controller.run_tool_once(
        project.id,
        timeout_request,
        DeterministicToolAdapter(outcome="timeout"),
        artifact_path="artifacts/layout-timeout-001.json",
    )
    assert timeout_operation["status"] == ToolOperationStatus.OUTCOME_UNKNOWN.value
    assert timeout_receipt["status"] == ToolOperationStatus.OUTCOME_UNKNOWN.value
    assert timeout_artifact is None


def test_archive_requires_and_preserves_exact_final_hash(controller):
    project, _, _ = advance_to_sourcing(controller)
    controller.transition_project(
        project.id, ProjectStatus.ANIMATIC_READY, reason="Animatic ready"
    )
    controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.ANIMATIC,
            approved_by="reviewer",
            scope={"animatic": "v1"},
        ),
    )
    controller.transition_project(
        project.id, ProjectStatus.FINAL_CANDIDATE, reason="Final rendered"
    )
    candidate = record_final_candidate(controller, project.id)
    final_hash = candidate["content_hash"]
    complete_flow_ledger(controller, project.id)
    controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.FINAL,
            approved_by="reviewer",
            scope={"candidate": "final-v1"},
            output_hash=final_hash,
        ),
    )

    archive = controller.archive_project(project.id)
    repeated = controller.archive_project(project.id)

    assert repeated["id"] == archive["id"]
    assert archive["manifest_hash"]
    assert (
        controller.repository.get_project(project.id).status == ProjectStatus.ARCHIVED
    )
    manifest_artifact = controller.repository.get_artifact(
        archive["manifest_artifact_id"]
    )
    assert controller.verify_artifact(manifest_artifact["id"])

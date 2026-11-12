from __future__ import annotations

import shutil
import sqlite3
from decimal import Decimal

import pytest

from pinaka.contracts.flow import FlowStepCreate, MotionLevelCreate
from pinaka.controller import CreativeController
from pinaka.enums import (
    ApprovalStage,
    ArtifactKind,
    CapabilityStatus,
    ContentMode,
    ProjectStatus,
    QAStatus,
)
from pinaka.models import (
    ApprovalRequest,
    FactSetCreate,
    Money,
    ProjectCreate,
    StudioReadinessReport,
)


def record_studio_ready(controller: CreativeController) -> dict:
    """Record one healthy Studio readiness probe for session-gate tests."""

    return controller.record_studio_readiness(
        StudioReadinessReport(
            connected=True,
            account_state="signed-in",
            full_access=True,
            providers=("Google", "OpenRouter", "fal"),
            studio_version="0.1.6",
            status=CapabilityStatus.WORKING,
        )
    )


@pytest.fixture(scope="session")
def migrated_runtime(tmp_path_factory):
    """One fully migrated runtime, copied per test.

    Every controller used to replay all 18 schema migrations on open (~77 ms).
    Building the schema once per session and copying it costs ~2 ms per test.
    """

    root = tmp_path_factory.mktemp("runtime-template") / "runtime"
    CreativeController.open(root)
    database = root / "state" / "pinaka.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return root


@pytest.fixture
def controller(tmp_path, migrated_runtime):
    root = tmp_path / "runtime"
    shutil.copytree(migrated_runtime, root)
    return CreativeController.open(root)


def complete_flow_ledger(controller: CreativeController, project_id: str) -> None:
    """Record every G0-G7 gate as done so final approval can pass its ledger gate."""

    from pinaka.use_cases.flow import REQUIRED_FLOW_STEPS

    for step in REQUIRED_FLOW_STEPS:
        controller.record_flow_step(
            project_id,
            FlowStepCreate(
                step=step,
                status="done",
                actor="test",
                evidence=(f"evidence://{step}",),
            ),
        )


def complete_motion_levels(
    controller: CreativeController, project_id: str, *, level: str = "M3"
) -> None:
    """Give every shot a passing motion level before animatic approval."""

    for shot in controller.repository.list_shots(project_id):
        controller.record_motion_level(
            project_id,
            MotionLevelCreate(
                shot_id=shot["id"],
                level=level,
                source="composition",
                evidence=(f"evidence://motion/{shot['stable_key']}",),
            ),
        )


def create_project(
    controller: CreativeController,
    *,
    name: str = "Pilot Product",
    budget: str = "10.00",
):
    return controller.create_project(
        ProjectCreate(
            name=name,
            objective="Create one governed product-video pilot",
            mode=ContentMode.PRODUCT,
            audience="Pilot reviewers",
            output_format="9:16",
            language="en",
            duration_seconds=Decimal(25),
            max_budget=Money(amount=Decimal(budget), currency="USD"),
        )
    )


def advance_to_sourcing(controller: CreativeController, *, name: str = "Pilot Product"):
    project = create_project(controller, name=name)
    controller.transition_project(
        project.id, ProjectStatus.PLANNED, reason="Plan ready"
    )
    controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.BRIEF,
            approved_by="reviewer@example.test",
            scope={"script": "v1", "budget": "10 USD"},
        ),
    )
    fact_set = controller.add_fact_set(
        project.id,
        FactSetCreate(
            source_ref="approved-fact-sheet-v1",
            values={"sku": "SKU-001", "price": "9.99", "currency": "USD"},
            evidence_refs=("test://facts/v1",),
        ),
    )
    controller.transition_project(
        project.id, ProjectStatus.SOURCING, reason="Begin sourcing"
    )
    sourcing_approval = controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.SOURCING,
            approved_by="reviewer@example.test",
            scope={"selected_sources": ["own-library"], "still_allowance": "2 USD"},
        ),
    )
    return project, fact_set, sourcing_approval


def record_final_candidate(controller: CreativeController, project_id: str) -> dict:
    """Register a verifiable final render plus passing QA for approval tests."""

    artifact = controller.write_artifact(
        project_id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/final-candidate-v1.mp4",
        content=b"final candidate v1",
        operation={"type": "test_final_render"},
    )
    controller.repository.record_qa_result(
        project_id=project_id,
        artifact_id=artifact["id"],
        check_name="technical.container",
        status=QAStatus.PASS.value,
        findings={"duration_seconds": 5, "codec": "h264"},
        evidence_hash=artifact["content_hash"],
    )
    return artifact

from __future__ import annotations

from decimal import Decimal

import pytest

from pinaka.adapters.mock import MockGenerationAdapter
from pinaka.enums import ApprovalStage, ArtifactKind, BudgetCategory, ProjectStatus
from pinaka.errors import ApprovalRequiredError
from pinaka.models import (
    ApprovalRequest,
    BudgetPlanCreate,
    BudgetPlanItemCreate,
    JobRequest,
    Money,
    ShotCreate,
    SlotCreate,
)

from .conftest import advance_to_sourcing, create_project, record_final_candidate


def _amount(payload: dict) -> Decimal:
    return Decimal(str(payload["amount"]))


def _slot_project(controller):
    project = create_project(controller, budget="10.00")
    shot = controller.add_shot(
        project.id,
        ShotCreate(
            stable_key="shot-01",
            ordinal=1,
            beat="Hero frame",
            duration_seconds=Decimal(5),
            narrative="A single representative frame.",
        ),
    )
    slot = controller.add_slot(
        project.id,
        SlotCreate(
            shot_id=shot["id"],
            stable_key="shot-01.hero",
            role="hero image",
            identity_requirement="Original non-protected subject",
            fact_requirement="One visible subject",
            transformation_permission="Generate from an approved prompt",
            approved_sources=("generated",),
        ),
    )
    plan = controller.plan_creative_budget(
        project.id,
        BudgetPlanCreate(
            creative_summary="Hero frame",
            items=(
                BudgetPlanItemCreate(
                    stable_key="hero-image",
                    ordinal=1,
                    category=BudgetCategory.ASSET_GENERATION,
                    provider="mock",
                    operation="image_generate",
                    estimated_cost=Money(amount=Decimal("0.40"), currency="USD"),
                    shot_id=shot["id"],
                    slot_id=slot["id"],
                ),
            ),
        ),
    )["plan"]
    return project, slot, plan


def _generation_job():
    return JobRequest(
        provider="mock",
        endpoint="mock://image",
        operation="image_generate",
        idempotency_key="asset-cost-1",
        estimated_cost=Money(amount=Decimal("0.40"), currency="USD"),
    )


def test_asset_generation_cost_is_recorded_per_asset(controller):
    project, slot, plan = _slot_project(controller)
    job, artifact = controller.run_adapter_once(
        project.id,
        _generation_job(),
        MockGenerationAdapter(),
        artifact_path="artifacts/frames/hero.png",
        budget_plan_item=(plan["id"], "hero-image"),
    )
    assert job.status.value == "succeeded"
    assert artifact is not None

    ledger = controller.asset_costs(project.id)
    assert ledger["count"] == 1
    entry = ledger["assets"][0]
    assert entry["slot_id"] == slot["id"]
    assert entry["slot_key"] == "shot-01.hero"
    assert entry["artifact_id"] == artifact["id"]
    assert entry["job_id"] == job.id
    assert entry["provider"] == "mock"
    assert entry["estimated_minor"] == 40
    assert entry["actual_minor"] == 20
    assert entry["variance_minor"] == -20
    assert entry["variance_direction"] == "under"
    assert _amount(ledger["total_estimated"]) == Decimal("0.40")
    assert _amount(ledger["total_actual"]) == Decimal("0.20")
    assert ledger["total_variance_direction"] == "under"
    assert _amount(ledger["by_provider"]["mock"]["actual"]) == Decimal("0.20")

    summary = controller.budget_summary(project.id)
    assert summary["assets"]["count"] == 1
    assert summary["assets"]["assets"][0]["slot_key"] == "shot-01.hero"


def test_asset_cost_ledger_is_idempotent_per_job(controller):
    project, _, plan = _slot_project(controller)
    job, artifact = controller.run_adapter_once(
        project.id,
        _generation_job(),
        MockGenerationAdapter(),
        artifact_path="artifacts/frames/hero.png",
        budget_plan_item=(plan["id"], "hero-image"),
    )
    repeated = controller.repository.record_asset_cost(
        project.id,
        job_id=job.id,
        plan_id=plan["id"],
        item_id=None,
        slot_id=None,
        artifact_id=artifact["id"],
        provider="mock",
        operation="image_generate",
        currency="USD",
        estimated_minor=40,
        actual_minor=20,
    )
    assert controller.asset_costs(project.id)["count"] == 1
    assert repeated["actual_minor"] == 20


def _to_final_candidate(controller):
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
    return project


def _final_approval(output_hash: str) -> ApprovalRequest:
    return ApprovalRequest(
        stage=ApprovalStage.FINAL,
        approved_by="reviewer",
        scope={"candidate": "final-v1"},
        output_hash=output_hash,
    )


def test_final_approval_rejects_an_unknown_hash(controller):
    project = _to_final_candidate(controller)
    with pytest.raises(
        ApprovalRequiredError, match="does not match a current artifact"
    ):
        controller.approve(project.id, _final_approval("b" * 64))


def test_final_approval_requires_qa_for_the_exact_candidate(controller):
    project = _to_final_candidate(controller)
    artifact = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/unchecked.mp4",
        content=b"unchecked render",
        operation={"type": "test_render"},
    )
    with pytest.raises(ApprovalRequiredError, match="requires recorded QA"):
        controller.approve(project.id, _final_approval(artifact["content_hash"]))


def test_final_approval_rejects_a_failing_qa_candidate(controller):
    project = _to_final_candidate(controller)
    artifact = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/failing.mp4",
        content=b"failing render",
        operation={"type": "test_render"},
    )
    controller.repository.record_qa_result(
        project_id=project.id,
        artifact_id=artifact["id"],
        check_name="technical.loudness",
        status="fail",
        findings={"true_peak_dbtp": 3.1},
        evidence_hash=artifact["content_hash"],
    )
    with pytest.raises(ApprovalRequiredError, match="failing QA"):
        controller.approve(project.id, _final_approval(artifact["content_hash"]))


def test_final_approval_accepts_a_verified_candidate(controller):
    project = _to_final_candidate(controller)
    candidate = record_final_candidate(controller, project.id)
    approval = controller.approve(
        project.id, _final_approval(candidate["content_hash"])
    )
    assert approval["subject_hash"]
    assert (
        controller.repository.get_project(project.id).status
        == ProjectStatus.FINAL_APPROVED
    )

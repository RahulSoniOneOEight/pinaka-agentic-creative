from __future__ import annotations

from decimal import Decimal

import pytest

from pinaka.adapters.mock import MockGenerationAdapter
from pinaka.contracts.flow import FlowStepCreate, SourcingDecisionCreate
from pinaka.enums import BudgetCategory
from pinaka.errors import IntegrityError
from pinaka.models import (
    BudgetPlanCreate,
    BudgetPlanItemCreate,
    JobRequest,
    Money,
    ShotCreate,
    SlotCreate,
    TextBriefRequest,
)
from pinaka.recipes import build_plan

from .conftest import create_project
from .test_creative_rules import make_profile


def _project_with_slot(controller, *, approved_sources=("owned", "generated")):
    project = create_project(controller, budget="10.00")
    shot = controller.add_shot(
        project.id,
        ShotCreate(
            stable_key="shot-01",
            ordinal=1,
            beat="Duck swims",
            duration_seconds=Decimal(4),
            narrative="A duck crosses the pond.",
        ),
    )
    slot = controller.add_slot(
        project.id,
        SlotCreate(
            shot_id=shot["id"],
            stable_key="shot-01.hero",
            role="hero visual",
            identity_requirement="One duck",
            fact_requirement="Pond setting",
            transformation_permission="Crop and grade",
            approved_sources=approved_sources,
        ),
    )
    return project, shot, slot


def _plan(controller, project, shot, *, slot_id=None):
    return controller.plan_creative_budget(
        project.id,
        BudgetPlanCreate(
            creative_summary="One generated hero frame",
            items=(
                BudgetPlanItemCreate(
                    stable_key="hero",
                    ordinal=1,
                    category=BudgetCategory.ASSET_GENERATION,
                    provider="mock",
                    operation="image.generate",
                    estimated_cost=Money(amount=Decimal("0.10"), currency="USD"),
                    shot_id=shot["id"],
                    slot_id=slot_id,
                ),
            ),
        ),
    )["plan"]


def _job():
    return JobRequest(
        provider="mock",
        endpoint="mock://image",
        operation="image.generate",
        idempotency_key="flow-gate-1",
        estimated_cost=Money(amount=Decimal("0.10"), currency="USD"),
    )


def test_idea_only_slot_cascade_searches_stock_before_generation():
    profile = make_profile(
        source_material="idea_only",
        asset_readiness="no_assets",
        heavy_generation=True,
        subjects=["fiction"],
    )
    plan = build_plan(profile)
    slot = next(item for item in plan.slots if item.role == "primary_visual")
    assert slot.allowed_sources[:3] == ("owned", "official_api", "manual_import")
    assert slot.allowed_sources[-1] == "generated"


def test_generated_decision_requires_exhaustion_evidence():
    with pytest.raises(ValueError, match="stock providers"):
        SourcingDecisionCreate(
            slot_id="slot_1",
            decision="generated",
            reason="Nothing suitable",
            approved_by="producer",
        )
    with pytest.raises(ValueError, match="why stock was unsuitable"):
        SourcingDecisionCreate(
            slot_id="slot_1",
            decision="generated",
            reason="Nothing suitable",
            providers=("pexels", "openverse"),
            candidates_considered=12,
            approved_by="producer",
        )


def test_paid_generation_requires_a_slot(controller):
    project, shot, _slot = _project_with_slot(controller)
    plan = _plan(controller, project, shot)
    with pytest.raises(IntegrityError, match="requires a slot_id"):
        controller.reserve_budget_item(project.id, plan["id"], "hero", _job())


def test_paid_generation_requires_a_generated_sourcing_decision(controller):
    project, shot, slot = _project_with_slot(controller)
    plan = _plan(controller, project, shot, slot_id=slot["id"])
    with pytest.raises(IntegrityError, match="sourcing decision"):
        controller.reserve_budget_item(project.id, plan["id"], "hero", _job())

    controller.record_sourcing_decision(
        project.id,
        SourcingDecisionCreate(
            slot_id=slot["id"],
            decision="stock",
            reason="Pexels had a suitable pond clip",
            providers=("pexels",),
            candidates_considered=6,
            approved_by="producer",
        ),
    )
    with pytest.raises(IntegrityError, match="not 'generated'"):
        controller.reserve_budget_item(project.id, plan["id"], "hero", _job())


def test_paid_generation_proceeds_after_a_generated_decision(controller):
    project, shot, slot = _project_with_slot(controller)
    plan = _plan(controller, project, shot, slot_id=slot["id"])
    controller.record_sourcing_decision(
        project.id,
        SourcingDecisionCreate(
            slot_id=slot["id"],
            decision="generated",
            reason="No stock footage matched the exact rhyme character",
            providers=("pexels", "openverse"),
            candidates_considered=14,
            rejection_reason="All candidates were photographic, not illustrated",
            approved_by="producer",
        ),
    )
    job, artifact = controller.run_adapter_once(
        project.id,
        _job(),
        MockGenerationAdapter(),
        artifact_path="artifacts/assets/duck.png",
        budget_plan_item=(plan["id"], "hero"),
    )
    assert job.status.value == "succeeded"
    assert artifact is not None


def test_flow_contract_rejects_silent_skips():
    with pytest.raises(ValueError, match="named approver"):
        FlowStepCreate(
            step="g3_sourcing",
            status="skipped",
            reason="No time",
            actor="agent",
            approved_by=None,
        )
    with pytest.raises(ValueError, match="next_action"):
        FlowStepCreate(
            step="g5_production",
            status="blocked",
            reason="Studio credits are zero",
            actor="agent",
        )
    with pytest.raises(ValueError, match="evidence"):
        FlowStepCreate(step="g0_intake", status="done", actor="agent")


def test_persist_slots_materialises_slots_before_spend(controller):
    project = create_project(controller)
    result = controller.plan_from_text(
        project.id,
        TextBriefRequest(
            text="Make a five second rhyme about a duck on a pond",
            persist_slots=True,
        ),
    )
    assert result["slots"]
    assert len(result["slots"]) == len(result["shots"])
    assert result["slots"][0]["approved_sources"][-1] == "generated"


def test_flow_completeness_needs_every_gate(controller):
    project = create_project(controller)
    report = controller.flow_completeness(project.id)
    assert report["complete"] is False
    assert "g3_sourcing" in report["missing"]

    for step in report["required"]:
        controller.record_flow_step(
            project.id,
            FlowStepCreate(
                step=step,
                status="done",
                actor="agent",
                evidence=(f"evidence://{step}",),
            ),
        )
    assert controller.flow_completeness(project.id)["complete"] is True

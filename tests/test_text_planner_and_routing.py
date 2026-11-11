from __future__ import annotations

from decimal import Decimal

import pytest

from pinaka.assets import AssetCriteria
from pinaka.enums import (
    ApprovalStage,
    ArtifactKind,
    BudgetCategory,
    ProjectStatus,
    SlotAssetRole,
    StudioWorkPhase,
    ToolIntegrationMode,
)
from pinaka.errors import IntegrityError, RightsEligibilityError
from pinaka.models import (
    ApprovalRequest,
    BudgetPlanCreate,
    BudgetPlanItemCreate,
    JobRequest,
    Money,
    ShotCreate,
    SlotCreate,
    StudioScope,
    StudioWorkPacketRequest,
    TextBriefRequest,
    ToolProfileCreate,
)

from .conftest import advance_to_sourcing, create_project, record_studio_ready


def _text_request(text: str, **overrides) -> TextBriefRequest:
    return TextBriefRequest(text=text, **overrides)


def test_plan_from_text_derives_a_governed_brief(controller):
    project = create_project(controller, budget="10.00")
    result = controller.plan_from_text(
        project.id,
        _text_request(
            "Make a 5-second counting rhyme for children aged 4 to 6: "
            '"One, two, three little ducks, swimming in a row."'
        ),
    )
    source = result["source_artifact"]
    assert source["kind"] == "original"
    assert source["immutable"] is True

    analysis = result["analysis"]
    assert analysis["duration_seconds"] == "5"
    assert analysis["audience"] == "Children"
    assert analysis["age_range"] == "4 to 6"
    assert analysis["content_family"] == "rhyme"
    assert analysis["spoken_copy"].startswith("One, two, three")

    decisions = {
        item["field"]: item for item in result["brief"]["package"]["decisions"]
    }
    assert decisions["project.duration"]["state"] == "supplied"
    assert decisions["project.audience"]["state"] == "derived"
    assert decisions["script.spoken_copy"]["state"] == "supplied"
    assert "facts.claims" not in decisions

    assert result["shots"], "the planner should create shots"
    assert sum(
        Decimal(str(shot["duration_seconds"])) for shot in result["shots"]
    ) == Decimal(5)
    assert result["scene_plan"] is not None
    assert "Scene plan" in controller.scene_plan(project.id)["markdown"]

    element_keys = {item["stable_key"] for item in result["proposed_elements"]}
    assert "character.duck" in element_keys
    assert result["proposed_slots"][0]["approved_sources"] == ["owned", "stock"]


def test_plan_from_text_assumes_a_duration_when_missing(controller):
    project = create_project(controller, budget="10.00")
    result = controller.plan_from_text(
        project.id, _text_request("Make a short rhyme for kids")
    )
    assert result["analysis"]["duration_seconds"] == "10"
    decisions = {
        item["field"]: item for item in result["brief"]["package"]["decisions"]
    }
    assert decisions["project.duration"]["state"] == "assumed"
    assert decisions["project.duration"]["reversible"] is True


def test_plan_from_text_blocks_unverified_claims(controller):
    project = create_project(controller, budget="10.00")
    result = controller.plan_from_text(
        project.id,
        _text_request("Make a 10 second ad claiming our juice is 50% cheaper and best"),
    )
    decisions = {
        item["field"]: item for item in result["brief"]["package"]["decisions"]
    }
    claim = decisions["facts.claims"]
    assert claim["state"] == "blocked"
    assert claim["reversible"] is False
    assert set(claim["blocked_work"]) == {"final_approval", "publication"}


def _slot_project(controller, *, approved_sources):
    project = create_project(controller, budget="10.00")
    shot = controller.add_shot(
        project.id,
        ShotCreate(
            stable_key="shot-01",
            ordinal=1,
            beat="Hero",
            duration_seconds=Decimal(5),
            narrative="A representative frame.",
        ),
    )
    slot = controller.add_slot(
        project.id,
        SlotCreate(
            shot_id=shot["id"],
            stable_key="shot-01.hero",
            role="hero image",
            identity_requirement="Original subject",
            fact_requirement="One visible subject",
            transformation_permission="Generate from an approved prompt",
            approved_sources=approved_sources,
        ),
    )
    plan = controller.plan_creative_budget(
        project.id,
        BudgetPlanCreate(
            creative_summary="Hero",
            items=(
                BudgetPlanItemCreate(
                    stable_key="hero",
                    ordinal=1,
                    category=BudgetCategory.ASSET_GENERATION,
                    provider="mock",
                    operation="image_generate",
                    estimated_cost=Money(amount=Decimal("0.40"), currency="USD"),
                    slot_id=slot["id"],
                ),
            ),
        ),
    )["plan"]
    return project, shot, slot, plan


def _job():
    return JobRequest(
        provider="mock",
        endpoint="mock://image",
        operation="image_generate",
        idempotency_key="routing-1",
        estimated_cost=Money(amount=Decimal("0.40"), currency="USD"),
    )


def test_paid_generation_requires_an_approved_generated_source(controller):
    project, _, _, plan = _slot_project(controller, approved_sources=("owned", "stock"))
    with pytest.raises(RightsEligibilityError, match="not an approved source"):
        controller.reserve_budget_item(project.id, plan["id"], "hero", _job())


def test_paid_generation_is_allowed_when_the_slot_permits_it(controller):
    project, _, _, plan = _slot_project(
        controller, approved_sources=("owned", "stock", "generated")
    )
    reserved = controller.reserve_budget_item(project.id, plan["id"], "hero", _job())
    assert reserved["job"]["status"] == "budget_reserved"


def test_paid_generation_is_refused_when_a_primary_already_exists(controller):
    project, _shot, slot, plan = _slot_project(
        controller, approved_sources=("owned", "generated")
    )
    owned = controller.write_artifact(
        project.id,
        kind=ArtifactKind.GENERATED,
        relative_path="assets/owned-hero.png",
        content=b"owned hero",
        operation={"type": "sourced_import"},
    )
    controller.assign_slot_asset(
        project.id,
        slot["id"],
        artifact_id=owned["id"],
        role=SlotAssetRole.PRIMARY,
        criteria=AssetCriteria(),
        decided_by="reviewer",
    )
    with pytest.raises(Exception, match="already satisfies this slot"):
        controller.reserve_budget_item(project.id, plan["id"], "hero", _job())


def _studio_profile(controller):
    record_studio_ready(controller)
    return controller.register_tool_profile(
        ToolProfileCreate(
            stable_key="openmontage-deterministic",
            name="OpenMontage fixture",
            integration_mode=ToolIntegrationMode.SPECIALIST_HANDOFF,
            executable_or_endpoint="controller:deterministic-studio",
            supported_operations=("openmontage.execute_work_packet",),
        )
    )


def test_render_requires_primary_and_backup_coverage(controller):
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
    controller.plan_from_text(
        project.id, _text_request("Make a 5 second rhyme for kids")
    )
    shot = controller.repository.list_shots(project.id)[0]
    controller.add_slot(
        project.id,
        SlotCreate(
            shot_id=shot["id"],
            stable_key=f"{shot['stable_key']}.visual",
            role="primary visual",
            identity_requirement="Original subject",
            fact_requirement="Matches the shot",
            transformation_permission="Composition",
            approved_sources=("owned",),
        ),
    )
    profile = _studio_profile(controller)

    with pytest.raises(IntegrityError, match="primary and backup asset"):
        controller.prepare_studio_work_packet(
            project.id,
            StudioWorkPacketRequest(
                tool_profile_id=profile["id"],
                idempotency_key="render-coverage-1",
                instructions="Render the final video.",
                scope=StudioScope(phases=(StudioWorkPhase.RENDER,)),
                output_declaration={"roles": ["final_render"]},
            ),
        )

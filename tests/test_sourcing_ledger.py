from __future__ import annotations

from decimal import Decimal

import pytest

from pinaka.contracts.flow import (
    SlotPickCreate,
    SourcingCandidateCreate,
    SourcingDecisionCreate,
)
from pinaka.enums import ApprovalStage, ProjectStatus
from pinaka.errors import ConflictError, IntegrityError, NotFoundError
from pinaka.models import (
    ApprovalRequest,
    ShotCreate,
    ShotElementBindingCreate,
    SlotCreate,
    TextBriefRequest,
)

from .conftest import (
    advance_to_sourcing,
    complete_flow_ledger,
    create_project,
    record_final_candidate,
)


def _slot(controller, *, ceiling="ai_created"):
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
            role="primary visual",
            identity_requirement="One duck",
            fact_requirement="Pond setting",
            transformation_permission="Crop and grade",
            approved_sources=("owned", "official_api", "generated"),
            treatment_ceiling=ceiling,
        ),
    )
    return project, slot


def _candidate(
    slot, suffix="a", *, provider="pexels", treatment="as_is", eligibility="eligible"
):
    return SourcingCandidateCreate(
        slot_id=slot["id"],
        provider=provider,
        provider_asset_id=f"asset-{suffix}",
        media_type="video",
        source_url=f"https://example.test/{suffix}",
        creator="Example Creator",
        licence="Pexels licence",
        licence_url="https://example.test/licence",
        eligibility=eligibility,
        eligibility_reason=None if eligibility == "eligible" else "Wrong subject",
        treatment=treatment,
        evidence=(f"evidence://{suffix}",),
    )


def test_treatment_cannot_exceed_the_slot_ceiling(controller):
    project, slot = _slot(controller, ceiling="match")
    with pytest.raises(IntegrityError, match="exceeds the slot ceiling"):
        controller.record_sourcing_candidate(
            project.id, _candidate(slot, treatment="generate")
        )


def test_eligible_candidates_are_scored_lower_for_heavier_treatments(controller):
    project, slot = _slot(controller)
    as_is = controller.record_sourcing_candidate(project.id, _candidate(slot, "as-is"))
    generated = controller.record_sourcing_candidate(
        project.id, _candidate(slot, "generated", provider="fal", treatment="generate")
    )
    ineligible = controller.record_sourcing_candidate(
        project.id,
        _candidate(slot, "bad", eligibility="ineligible"),
    )
    assert as_is["score"] > generated["score"]
    assert ineligible["score"] == 0


def test_backup_must_be_an_independent_eligible_candidate(controller):
    project, slot = _slot(controller)
    primary = controller.record_sourcing_candidate(project.id, _candidate(slot, "a"))
    backup = controller.record_sourcing_candidate(
        project.id, _candidate(slot, "b", provider="openverse")
    )
    ineligible = controller.record_sourcing_candidate(
        project.id, _candidate(slot, "c", eligibility="ineligible")
    )
    with pytest.raises(NotFoundError, match="not found"):
        controller.pick_slot_assets(
            project.id,
            SlotPickCreate(
                slot_id=slot["id"],
                primary_candidate_id=primary["id"],
                backup_candidate_id="cand_missing",
                decided_by="producer",
                reason="Two-pick",
            ),
        )
    with pytest.raises(ConflictError, match="different"):
        controller.pick_slot_assets(
            project.id,
            SlotPickCreate(
                slot_id=slot["id"],
                primary_candidate_id=primary["id"],
                backup_candidate_id=primary["id"],
                decided_by="producer",
                reason="Two-pick",
            ),
        )
    with pytest.raises(ConflictError, match="ineligible"):
        controller.pick_slot_assets(
            project.id,
            SlotPickCreate(
                slot_id=slot["id"],
                primary_candidate_id=primary["id"],
                backup_candidate_id=ineligible["id"],
                decided_by="producer",
                reason="Two-pick",
            ),
        )

    pick = controller.pick_slot_assets(
        project.id,
        SlotPickCreate(
            slot_id=slot["id"],
            primary_candidate_id=primary["id"],
            backup_candidate_id=backup["id"],
            decided_by="producer",
            reason="Primary matches the shot; backup is an independent source",
        ),
    )
    assert pick["primary_candidate_id"] == primary["id"]
    summary = controller.slot_pick_summary(project.id)
    assert summary["complete"] is True
    assert summary["slots"][0]["primary"]["provider"] == "pexels"
    assert summary["slots"][0]["backup"]["provider"] == "openverse"


def _to_final_candidate_with_slot(controller):
    project, _, _ = advance_to_sourcing(controller)
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
            role="primary visual",
            identity_requirement="One duck",
            fact_requirement="Pond setting",
            transformation_permission="Crop and grade",
            approved_sources=("owned", "pexels"),
        ),
    )
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
    return project, slot


def test_final_approval_requires_two_picks_for_visual_slots(controller):
    project, slot = _to_final_candidate_with_slot(controller)
    candidate = record_final_candidate(controller, project.id)
    complete_flow_ledger(controller, project.id)
    with pytest.raises(ConflictError, match="primary and an independent backup"):
        controller.approve(
            project.id,
            ApprovalRequest(
                stage=ApprovalStage.FINAL,
                approved_by="reviewer",
                scope={"candidate": "final-v1"},
                output_hash=candidate["content_hash"],
            ),
        )

    primary = controller.record_sourcing_candidate(project.id, _candidate(slot, "a"))
    backup = controller.record_sourcing_candidate(
        project.id, _candidate(slot, "b", provider="openverse")
    )
    controller.pick_slot_assets(
        project.id,
        SlotPickCreate(
            slot_id=slot["id"],
            primary_candidate_id=primary["id"],
            backup_candidate_id=backup["id"],
            decided_by="producer",
            reason="Two independent pond clips",
        ),
    )
    approval = controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.FINAL,
            approved_by="reviewer",
            scope={"candidate": "final-v1"},
            output_hash=candidate["content_hash"],
        ),
    )
    assert approval["subject_hash"]


def test_sourcing_manifest_is_hashed_and_discoverable(controller):
    project, slot = _slot(controller)
    controller.record_sourcing_decision(
        project.id,
        SourcingDecisionCreate(
            slot_id=slot["id"],
            decision="stock",
            reason="Pexels matched the shot",
            providers=("pexels",),
            candidates_considered=1,
            approved_by="producer",
        ),
    )
    controller.record_sourcing_candidate(project.id, _candidate(slot, "a"))
    manifest = controller.write_sourcing_manifest(project.id)
    assert manifest["manifest"]["counts"]["candidates"] == 1
    assert controller.verify_artifact(manifest["artifact"]["id"])
    current = controller.current_sourcing_manifest(project.id)
    assert current["manifest_hash"] == manifest["manifest_hash"]


def test_elements_derive_slots_across_bound_shots(controller):
    project = create_project(controller)
    plan = controller.plan_from_text(
        project.id,
        TextBriefRequest(
            text="Make a five second rhyme about a duck swimming on a pond",
            persist_elements=True,
        ),
    )
    element = next(
        item for item in plan["recorded_elements"] if item["kind"] == "character"
    )
    shot = plan["shots"][0]
    controller.bind_element_to_shot(
        project.id,
        ShotElementBindingCreate(
            shot_id=shot["id"],
            element_id=element["id"],
            role="subject",
        ),
    )
    result = controller.derive_element_slots(project.id)
    assert result["created_count"] == 1
    slot = result["created"][0]["slot"]
    assert slot["role"] == "subject"
    assert slot["approved_sources"][-1] == "generated"
    assert controller.derive_element_slots(project.id)["created_count"] == 0

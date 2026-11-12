from __future__ import annotations

from pinaka.contracts.effects import EffectCueCreate, EffectsPlanCreate
from pinaka.contracts.flow import (
    ABExperimentCreate,
    ABVariantBind,
    FlowStepCreate,
    MotionLevelCreate,
    SlotPickCreate,
    SourcingCandidateCreate,
    SourcingDecisionCreate,
)
from pinaka.enums import ArtifactKind
from pinaka.models import TextBriefRequest
from pinaka.use_cases.flow import REQUIRED_FLOW_STEPS

from .conftest import create_project


def test_the_full_policy_chain_passes_the_quality_bar(controller):
    project = create_project(controller, budget="10.00")
    plan = controller.plan_from_text(
        project.id,
        TextBriefRequest(
            text="Make a five second rhyme about a duck swimming on a pond",
            persist_slots=True,
            persist_elements=True,
        ),
    )
    assert plan["production_contract"]["artifact"]["id"]

    slots = plan["slots"]
    for index, slot in enumerate(slots, start=1):
        controller.record_sourcing_decision(
            project.id,
            SourcingDecisionCreate(
                slot_id=slot["id"],
                decision="stock",
                reason="Pexels and Openverse were searched before generation",
                providers=("pexels", "openverse"),
                candidates_considered=2,
                approved_by="producer",
            ),
        )
        primary = controller.record_sourcing_candidate(
            project.id,
            SourcingCandidateCreate(
                slot_id=slot["id"],
                provider="pexels",
                provider_asset_id=f"pexels-{index}",
                media_type="image",
                source_url=f"https://pexels.example/{index}",
                creator="Pexels Creator",
                licence="Pexels licence",
                eligibility="eligible",
                treatment="as_is",
                evidence=("evidence://pexels",),
            ),
        )
        backup = controller.record_sourcing_candidate(
            project.id,
            SourcingCandidateCreate(
                slot_id=slot["id"],
                provider="openverse",
                provider_asset_id=f"openverse-{index}",
                media_type="image",
                source_url=f"https://openverse.example/{index}",
                creator="Openverse Creator",
                licence="CC BY 4.0",
                eligibility="eligible",
                treatment="correct",
                evidence=("evidence://openverse",),
            ),
        )
        controller.pick_slot_assets(
            project.id,
            SlotPickCreate(
                slot_id=slot["id"],
                primary_candidate_id=primary["id"],
                backup_candidate_id=backup["id"],
                decided_by="producer",
                reason="Primary matches the beat; backup is an independent source",
            ),
        )

    for shot in plan["shots"]:
        controller.record_motion_level(
            project.id,
            MotionLevelCreate(
                shot_id=shot["id"],
                level="M3",
                source="composition",
                evidence=(f"evidence://motion/{shot['stable_key']}",),
            ),
        )
    for step in REQUIRED_FLOW_STEPS:
        controller.record_flow_step(
            project.id,
            FlowStepCreate(
                step=step,
                status="done",
                actor="producer",
                evidence=(f"evidence://{step}",),
            ),
        )

    variant_a = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/ab-a.mp4",
        content=b"a-variant",
        operation={"type": "ab_render", "label": "A"},
    )
    variant_b = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/ab-b.mp4",
        content=b"b-variant",
        operation={"type": "ab_render", "label": "B"},
    )
    experiment = controller.create_ab_experiment(
        project.id,
        ABExperimentCreate(
            axis="opening visual",
            hypothesis="A duck close-up holds longer than the wide pond",
            primary_metric="3-second view rate",
            traffic_split="50/50",
            sample_plan="1,000 impressions per variant",
        ),
    )
    for label, artifact in (("A", variant_a), ("B", variant_b)):
        controller.bind_ab_variant(
            project.id,
            ABVariantBind(
                experiment_id=experiment["id"],
                label=label,
                artifact_id=artifact["id"],
            ),
        )

    controller.record_effects_plan(
        project.id,
        EffectsPlanCreate(
            cues=(
                EffectCueCreate(
                    shot_key=plan["shots"][0]["stable_key"],
                    kind="camera_push_in",
                    start_seconds="0",
                    duration_seconds="2",
                    rights="owned:composition",
                ),
                EffectCueCreate(
                    shot_key=plan["shots"][0]["stable_key"],
                    kind="sfx",
                    start_seconds="0.5",
                    duration_seconds="1",
                    rights="licensed:pond-ambience",
                ),
            ),
            sound_design_notes="Soft pond ambience under the narration.",
        ),
    )

    change = controller.change_request(project.id)
    assert "change_request" in change["change_request"]
    assert change["change_request"]["slots"]

    bar = controller.quality_bar(project.id)
    assert bar["passed"] is True, bar["failed"]
    assert {item["gate"] for item in bar["gates"]} >= {
        "brief",
        "production_contract",
        "sourcing_decisions",
        "two_pick",
        "motion",
        "flow_ledger",
        "ab",
        "change_request",
    }

    record = controller.production_record(project.id)
    assert record["record"]["flow"]["complete"] is True
    assert record["record"]["ab"]["complete"] is True
    assert record["record_hash"]

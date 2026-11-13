from __future__ import annotations

import numpy as np
import pytest

from pinaka.assets import AssetCriteria
from pinaka.contracts.flow import FlowStepCreate, MotionLevelCreate
from pinaka.contracts.planning import ScriptApproval, ScriptCreate, ShotCreate
from pinaka.enums import ApprovalStage, ArtifactKind, ProjectStatus, SlotAssetRole
from pinaka.errors import ApprovalRequiredError, ConflictError, IntegrityError
from pinaka.models import ApprovalRequest, TextBriefRequest
from pinaka.motion_verify import verify_frames
from pinaka.production import ReuseDecisionCreate, ReuseItem, ShotProductionCreate

from .conftest import create_project


def test_motion_verifier_rejects_camera_only_motion():
    frames = []
    for index in range(6):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        frame[80:160, 40 + index * 4 : 120 + index * 4] = 200
        frames.append(frame)
    report = verify_frames(frames)
    assert report["verdict"] == "fail"


def test_motion_verifier_accepts_two_moving_parts():
    frames = []
    for index in range(6):
        frame = np.zeros((240, 320, 3), dtype=np.uint8)
        frame[60:120, 40:100] = 180
        frame[60 + index * 3 : 120 + index * 3, 200:260] = 220
        frame[150:190, 120 + index * 5 : 180 + index * 5] = 240
        frames.append(frame)
    report = verify_frames(frames)
    assert report["verdict"] == "pass"
    assert report["components"] >= 2


def test_cartoon_slot_rejects_a_photo_candidate(controller):
    project = create_project(controller)
    plan = controller.plan_from_text(
        project.id,
        TextBriefRequest(
            text="Make a five second rhyme about a duck", persist_slots=True
        ),
    )
    slot = next(item for item in plan["slots"] if item["role"] == "primary_visual")
    from pinaka.contracts.flow import SourcingCandidateCreate

    with pytest.raises(IntegrityError, match="does not fit the slot style"):
        controller.record_sourcing_candidate(
            project.id,
            SourcingCandidateCreate(
                slot_id=slot["id"],
                provider="local",
                provider_asset_id="photo-1",
                media_type="image",
                source_url="https://example.test/photo.jpg",
                licence="owned",
                eligibility="eligible",
                treatment="as_is",
                asset_class="photo",
            ),
        )


def test_shot_production_requires_background_for_character():
    with pytest.raises(ValueError, match="background layer"):
        ShotProductionCreate.model_validate(
            {
                "shot_id": "shot_1",
                "appears": "A duck",
                "preset_id": "storybook-motion-comic",
                "style_id": "flat-2d-cartoon",
                "method": "cutout_rig",
                "fallback": "hold the shot",
                "layers": [{"role": "character", "media": "cutout", "z": 10}],
            }
        )


def test_script_and_delivery_metadata_are_gated(controller):
    project = create_project(controller)
    with pytest.raises(IntegrityError, match="approved narration script"):
        controller.require_approved_script(project.id)
    with pytest.raises(ConflictError, match="Delivery metadata"):
        controller.require_delivery_metadata(project.id)
    script = controller.record_script(
        project.id, ScriptCreate(text="Five little ducks went swimming one day.")
    )
    with pytest.raises(IntegrityError, match="not approved"):
        controller.require_approved_script(project.id)
    controller.approve_script(
        project.id, script["id"], ScriptApproval(approved_by="producer")
    )
    assert controller.require_approved_script(project.id)["approved_by"] == "producer"


def test_animatic_qa_gate_rejects_a_render_without_qa(controller):
    project = create_project(controller)
    render = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/animatic.mp4",
        content=b"animatic",
        operation={"type": "composition_render"},
    )
    with pytest.raises(ApprovalRequiredError, match="recorded QA"):
        controller.require_qa_pass(project.id, render["id"])


def test_flow_evidence_is_unioned_across_records(controller):
    project = create_project(controller)
    controller.record_flow_step(
        project.id,
        FlowStepCreate(
            step="g3_sourcing", status="done", actor="producer", evidence=("a",)
        ),
    )
    controller.record_flow_step(
        project.id,
        FlowStepCreate(
            step="g3_sourcing", status="done", actor="producer", evidence=("b",)
        ),
    )
    report = controller.flow_completeness(project.id)
    step = next(item for item in report["steps"] if item["step"] == "g3_sourcing")
    assert {"a", "b"} <= set(step["evidence"])


def _rights(attribution: str | None = None) -> dict:
    return {
        "license_name": "Owned",
        "license_code": "owned",
        "license_url": "https://example.invalid/owned",
        "terms_url": "https://example.invalid/terms",
        "terms_reviewed_at": "2026-11-13",
        "commercial_use_allowed": True,
        "modifications_allowed": True,
        "editorial_only": False,
        "attribution_required": bool(attribution),
        "attribution_text": attribution,
        "license_evidence_ref": "owned:local",
        "license_evidence_hash": "0" * 64,
        "origin_license_verified": True,
        "contains_recognizable_people_or_brands": False,
        "third_party_rights_cleared": True,
        "rights_uncertainty": [],
        "obligations": [],
    }


def test_delivery_metadata_writes_attribution_credits(controller):
    project = create_project(controller)
    plan = controller.plan_from_text(
        project.id,
        TextBriefRequest(
            text="Make a five second rhyme about a duck",
            persist_slots=True,
        ),
    )
    slot = next(item for item in plan["slots"] if item["role"] == "primary_visual")
    artifact = controller.write_artifact(
        project.id,
        kind=ArtifactKind.GENERATED,
        relative_path="assets/generated/credited.png",
        content=b"credited",
        operation={
            "type": "owned_asset",
            "rights": _rights("Example Artist / CC BY 4.0"),
        },
    )
    controller.assign_slot_asset(
        project.id,
        slot["id"],
        artifact_id=artifact["id"],
        role=SlotAssetRole.PRIMARY,
        criteria=AssetCriteria(),
        decided_by="producer",
    )
    delivery = controller.write_delivery_metadata(project.id)
    assert "Example Artist / CC BY 4.0" in delivery["credits"]
    assert controller.require_delivery_metadata(project.id)["credits"]


def test_exact_item_with_generated_source_is_rejected(controller):
    project = create_project(controller)
    plan = controller.plan_from_text(
        project.id,
        TextBriefRequest(
            text="Make a five second rhyme about a duck",
            persist_slots=True,
        ),
    )
    controller.plan_production(project.id)
    shot = plan["shots"][0]
    production = controller.shot_productions(project.id)[0]
    from pinaka.production import ShotProductionCreate

    payload = {
        **production,
        "shot_id": shot["id"],
        "exact_items": ("logo",),
        "asset_source": {"logo": "generated"},
    }
    for key in ("id", "project_id", "created_at", "updated_at"):
        payload.pop(key, None)
    controller.record_shot_production(
        project.id, ShotProductionCreate.model_validate(payload)
    )
    with pytest.raises(IntegrityError, match="no approved source"):
        controller.require_shot_productions(project.id)


def test_hybrid_style_map_is_applied_per_shot(controller):
    project = create_project(controller)
    plan = controller.plan_from_text(
        project.id,
        TextBriefRequest(
            text="Make an eight second rhyme about a duck",
            duration_seconds=8,
            persist_slots=True,
            production_preset_id="storybook-motion-comic",
            visual_style_id="flat-2d-cartoon",
            style_map={},
        ),
    )
    shots = plan["shots"]
    style_map = {shots[-1]["stable_key"]: "studio-product"}
    plan = controller.plan_from_text(
        project.id,
        TextBriefRequest(
            text="Make an eight second rhyme about a duck",
            duration_seconds=8,
            persist_slots=True,
            production_preset_id="storybook-motion-comic",
            visual_style_id="flat-2d-cartoon",
            style_map=style_map,
        ),
    )
    controller.plan_production(project.id)
    productions = controller.shot_productions(project.id)
    assert productions[-1]["style_id"] == "studio-product"


def test_live_action_video_candidate_is_style_eligible(controller):
    project = create_project(controller)
    plan = controller.plan_from_text(
        project.id,
        TextBriefRequest(
            text="Make an eight second duck story",
            duration_seconds=8,
            persist_slots=True,
            production_preset_id="cinematic-story",
            visual_style_id="cinematic-live-action",
        ),
    )
    slot = next(item for item in plan["slots"] if item["role"] == "primary_visual")
    from pinaka.contracts.flow import SourcingCandidateCreate

    candidate = controller.record_sourcing_candidate(
        project.id,
        SourcingCandidateCreate(
            slot_id=slot["id"],
            provider="local",
            provider_asset_id="stock-clip-1",
            media_type="video",
            source_url="https://example.test/clip.mp4",
            licence="licensed",
            eligibility="eligible",
            treatment="as_is",
            asset_class="footage",
        ),
    )
    assert candidate["style_fit"] > 0


def test_reuse_decision_is_required_and_recorded(controller):
    project = create_project(controller)
    with pytest.raises(IntegrityError, match="build/adapt/reuse/reject"):
        controller.require_reuse_decision(project.id)
    decision = controller.record_reuse_decision(
        project.id,
        ReuseDecisionCreate(
            approved_by="producer",
            items=(
                ReuseItem(
                    component="renderer",
                    source="hyperframes",
                    decision="reuse",
                    license="Apache-2.0",
                ),
            ),
        ),
    )
    assert (
        controller.require_reuse_decision(project.id)["id"]
        == decision["artifact"]["id"]
    )


def test_qa_pass_rejects_a_failing_animatic_check(controller):
    project = create_project(controller)
    render = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/animatic-fail.mp4",
        content=b"animatic-fail",
        operation={"type": "composition_render"},
    )
    controller.repository.record_qa_result(
        project_id=project.id,
        artifact_id=render["id"],
        check_name="technical.duration",
        status="fail",
        findings={"duration_seconds": 1, "expected": 8},
        evidence_hash=render["content_hash"],
    )
    with pytest.raises(ApprovalRequiredError, match="failing QA"):
        controller.require_qa_pass(project.id, render["id"])


def test_motion_plan_requires_level_action_parts_secondary_camera(controller):
    project = create_project(controller)
    controller.add_shot(
        project.id,
        ShotCreate(
            stable_key="shot-01",
            ordinal=1,
            beat="establish",
            duration_seconds=4,
            narrative="A duck on a pond",
            requirements={"subject": {"action": "bob"}},
        ),
    )
    with pytest.raises(IntegrityError, match="motion plan"):
        controller.require_motion_plan(project.id)


def test_motion_budget_records_route_and_cost_per_shot(controller):
    project = create_project(controller)
    plan = controller.plan_from_text(
        project.id,
        TextBriefRequest(
            text="Make an eight second rhyme about a duck on a pond",
            duration_seconds=8,
            persist_slots=True,
        ),
    )
    controller.plan_production(project.id)
    budget = controller.require_motion_budget(project.id)
    rows = budget["motion_budget"]["shots"]
    assert len(rows) == len(plan["shots"])
    assert all(row["route"] for row in rows)
    assert all(row["estimated_minor"] == 0 for row in rows)
    assert budget["motion_budget"]["total_minor"] == 0


def test_script_is_bound_to_the_current_brief(controller):
    project = create_project(controller)
    plan = controller.plan_from_text(
        project.id,
        TextBriefRequest(
            text="Make a five second rhyme about a duck", persist_slots=True
        ),
    )
    script = controller.record_script(
        project.id, ScriptCreate(text="Five little ducks went swimming one day.")
    )
    assert script["brief_id"] == plan["brief"]["id"]
    controller.approve_script(
        project.id, script["id"], ScriptApproval(approved_by="producer")
    )
    bound = controller.require_script_binding(project.id)
    assert bound["id"] == script["id"]

    controller.plan_from_text(
        project.id,
        TextBriefRequest(
            text="Make a six second rhyme about a goose on a lake",
            duration_seconds=6,
            create_plan=False,
        ),
    )
    with pytest.raises(IntegrityError, match="stale brief"):
        controller.require_script_binding(project.id)


def test_m1_without_evidence_artifacts_is_rejected(controller):
    project = create_project(controller)
    plan = controller.plan_from_text(
        project.id,
        TextBriefRequest(
            text="Make a five second rhyme about a duck", persist_slots=True
        ),
    )
    for shot in plan["shots"]:
        controller.record_motion_level(
            project.id,
            MotionLevelCreate(
                shot_id=shot["id"],
                level="M1",
                source="composition",
                evidence=("evidence://declared-only",),
            ),
        )
    with pytest.raises(ConflictError, match="no motion evidence"):
        controller.require_motion_levels(project.id)


def test_style_required_layers_are_enforced():
    with pytest.raises(ValueError, match="requires layers: character"):
        ShotProductionCreate.model_validate(
            {
                "shot_id": "shot_1",
                "appears": "A product pack shot",
                "preset_id": "product-showcase",
                "style_id": "studio-product",
                "method": "composite",
                "fallback": "hold the shot",
                "layers": [
                    {"role": "background", "media": "still", "z": 0},
                    {"role": "effect", "media": "procedural", "z": 20},
                ],
            }
        )


def test_shot_production_requires_a_fallback():
    with pytest.raises(ValueError, match="fallback"):
        ShotProductionCreate.model_validate(
            {
                "shot_id": "shot_1",
                "appears": "A duck",
                "preset_id": "storybook-motion-comic",
                "style_id": "flat-2d-cartoon",
                "method": "cutout_rig",
                "fallback": "",
                "layers": [
                    {"role": "background", "media": "still", "z": 0},
                    {"role": "character", "media": "cutout", "z": 10},
                ],
            }
        )


def test_active_animatic_approval_is_invalidated_by_a_new_render(controller):
    project = create_project(controller)
    approval = controller.repository.create_approval(
        project.id,
        ApprovalRequest(stage=ApprovalStage.ANIMATIC, approved_by="producer"),
        (),
        "0" * 64,
        expected_project_status=ProjectStatus.DRAFT,
        resulting_project_status=None,
    )
    assert controller.repository.active_approval(project.id, "animatic")
    invalidated = controller.repository.invalidate_active_approval(
        project.id, "animatic", reason="new render"
    )
    assert invalidated["status"] == "invalidated"
    assert invalidated["id"] == approval["id"]
    assert controller.repository.active_approval(project.id, "animatic") is None


def test_imported_original_can_carry_provider_rights(controller, tmp_path):
    project = create_project(controller)
    source = tmp_path / "stock.webb"
    source.write_bytes(b"provider-bytes")
    artifact = controller.import_original(
        project.id,
        source,
        operation={
            "type": "provider_import",
            "provider": "wikimedia_commons",
            "rights": {
                "license_name": "CC BY 4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0",
                "terms_url": "https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use",
                "terms_reviewed_at": "2026-11-13",
                "attribution_required": True,
                "attribution_text": "Example / Wikimedia Commons / CC BY 4.0",
                "origin_license_verified": True,
            },
        },
    )
    assert artifact["kind"] == ArtifactKind.ORIGINAL.value
    assert artifact["operation"]["type"] == "provider_import"
    assert artifact["operation"]["rights"]["license_name"] == "CC BY 4.0"
    assert artifact["operation"]["size_bytes"] == len(b"provider-bytes")
    assert controller.verify_artifact(artifact["id"]) is True

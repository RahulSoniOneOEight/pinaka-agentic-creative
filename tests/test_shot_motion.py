from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from pinaka.enums import ArtifactKind, BudgetCategory
from pinaka.errors import IntegrityError, NotFoundError
from pinaka.models import (
    BudgetPlanCreate,
    BudgetPlanItemCreate,
    Money,
    ShotMotionRequest,
    SlotCreate,
    TextBriefRequest,
)
from pinaka.motion import DEFAULT_MODEL, estimate_minor, plan_preview, supports

from .conftest import create_project

PNG = b"\x89PNG\r\n\x1a\n" + b"frame"


def test_rate_card_matches_the_studio_prices():
    assert estimate_minor(DEFAULT_MODEL, seconds=5, resolution="768p") == 30
    assert (
        estimate_minor("google/gemini-omni-flash/v1.2", seconds=6, resolution="720p")
        == 50
    )
    assert (
        estimate_minor("lightricks/ltx-2.5/fast", seconds=16, resolution="720p") == 144
    )
    assert estimate_minor("minimax/h3", seconds=5, resolution="480p") == 25


def test_rate_card_rejects_bad_input():
    with pytest.raises(ValueError, match="at most 15s"):
        estimate_minor("minimax/h3", seconds=20, resolution="768p")
    with pytest.raises(ValueError, match="does not offer"):
        estimate_minor("minimax/h3", seconds=5, resolution="720p")
    with pytest.raises(ValueError, match="Unknown Studio video model"):
        estimate_minor("nope/model", seconds=5, resolution="720p")
    with pytest.raises(ValueError, match="billed by tokens"):
        estimate_minor("bytedance/seedance-2.5", seconds=5, resolution="720p")


def test_mode_support_and_batch_preview():
    assert supports("minimax/h3", "first-frame") is True
    assert supports("google/gemini-omni-flash/v1.2", "video-to-video") is False
    preview = plan_preview(family="minimax/h3", seconds=5, resolution="768p", count=8)
    assert preview["per_shot_minor"] == 30
    assert preview["total_minor"] == 240


def _setup(controller, *, budget="10.00", estimate=30):
    project = create_project(controller, budget=budget)
    controller.plan_from_text(
        project.id, TextBriefRequest(text="Make a 5 second rhyme for kids")
    )
    shot = controller.repository.list_shots(project.id)[0]
    slot = controller.add_slot(
        project.id,
        SlotCreate(
            shot_id=shot["id"],
            stable_key="shot-01.subject",
            role="subject",
            identity_requirement="i",
            fact_requirement="f",
            transformation_permission="t",
            approved_sources=("owned", "generated"),
        ),
    )
    frame = controller.write_artifact(
        project.id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path="renders/shot-01.png",
        content=PNG,
        operation={"type": "composition_render_still"},
    )
    plan = controller.plan_creative_budget(
        project.id,
        BudgetPlanCreate(
            creative_summary="shot 1",
            tolerance_bps=5000,
            items=(
                BudgetPlanItemCreate(
                    stable_key="shot-01-motion",
                    ordinal=1,
                    category=BudgetCategory.ASSET_GENERATION,
                    provider="studio-video",
                    operation="video.generate",
                    estimated_cost=Money(amount=Decimal("0.30"), currency="USD"),
                    slot_id=slot["id"],
                ),
            ),
        ),
    )["plan"]
    return project, shot, slot, frame, plan


def _request(shot, frame, plan, **overrides):
    payload = {
        "shot_id": shot["id"],
        "model": "minimax/h3",
        "mode": "first-frame",
        "resolution": "768p",
        "duration_seconds": Decimal(5),
        "prompt": "Slow push in as the duck glides.",
        "first_frame_artifact_id": frame["id"],
        "plan_id": plan["id"] if plan else None,
        "item_key": "shot-01-motion",
    }
    payload.update(overrides)
    return ShotMotionRequest(**payload)


def test_plan_motion_estimates_without_spending(controller):
    project, shot, _, frame, plan = _setup(controller)
    result = controller.plan_shot_motion(project.id, _request(shot, frame, plan))
    assert result["estimate_minor"] == 30
    assert result["requires_approval"] is False
    assert result["affordable"] is True
    assert controller.repository.get_project(project.id).reserved.minor == 0


def test_plan_motion_flags_a_shot_above_the_auto_cap(controller):
    project, shot, _, _frame, _ = _setup(controller)
    heavy = ShotMotionRequest(
        shot_id=shot["id"],
        model="kling/video-4.0",
        mode="text-to-video",
        resolution="1080p",
        duration_seconds=Decimal(6),
        prompt="Hero shot",
    )
    result = controller.plan_shot_motion(project.id, heavy)
    assert result["estimate_minor"] > 100
    assert result["requires_approval"] is True


def test_motion_requires_a_funded_plan_item(controller):
    project, shot, _, frame, _ = _setup(controller)
    request = _request(shot, frame, None)
    with pytest.raises(IntegrityError, match="funded plan item"):
        controller.prepare_shot_motion(project.id, request)


def test_motion_item_must_match_provider_and_estimate(controller):
    project, shot, _, frame, plan = _setup(controller)
    with pytest.raises(IntegrityError, match="does not match the model estimate"):
        controller.prepare_shot_motion(
            project.id,
            _request(shot, frame, plan, resolution="480p"),
        )


def test_prepare_reserves_and_import_settles(controller, tmp_path):
    project, shot, _slot, frame, plan = _setup(controller)
    prepared = controller.prepare_shot_motion(project.id, _request(shot, frame, plan))
    job = prepared["job"]
    assert job["status"] == "budget_reserved"
    assert controller.repository.get_project(project.id).reserved.minor == 30
    packet = prepared["packet"]
    assert packet["model"] == "minimax/h3"
    assert packet["first_frame"]["content_hash"] == frame["content_hash"]
    assert packet["controller_authority"]["auto_retry"] is False
    assert packet["output_relative_path"].startswith("artifacts/motion/")

    clip = tmp_path / "shot-01.mp4"
    clip.write_bytes(b"GENERATED-CLIP")
    from pinaka.utils import sha256_bytes

    imported = controller.import_shot_motion(
        project.id,
        job_id=job["id"],
        source_path=str(clip),
        content_hash=sha256_bytes(clip.read_bytes()),
    )
    assert imported["artifact"]["kind"] == "generated"
    assert imported["settlement"]["status"] == "settled"
    settled = controller.repository.get_project(project.id)
    assert settled.reserved.minor == 0
    assert settled.spent.minor == 30
    costs = controller.asset_costs(project.id)
    assert costs["count"] == 1
    assert costs["assets"][0]["actual_minor"] == 30


def test_import_rejects_a_tampered_clip(controller, tmp_path):
    project, shot, _, frame, plan = _setup(controller)
    prepared = controller.prepare_shot_motion(project.id, _request(shot, frame, plan))
    clip = tmp_path / "bad.mp4"
    clip.write_bytes(b"WRONG")
    with pytest.raises(IntegrityError, match="hash does not match"):
        controller.import_shot_motion(
            project.id,
            job_id=prepared["job"]["id"],
            source_path=str(clip),
            content_hash="a" * 64,
        )


def test_import_rejects_an_unknown_job(controller):
    project, _, _, _, _ = _setup(controller)
    with pytest.raises(NotFoundError):
        controller.import_shot_motion(
            project.id,
            job_id="job_missing",
            source_path="x",
            content_hash="a" * 64,
        )


def test_import_attaches_the_clip_and_composition_uses_it(controller, tmp_path):
    project, shot, _, frame, plan = _setup(controller)
    prepared = controller.prepare_shot_motion(project.id, _request(shot, frame, plan))
    clip = tmp_path / "shot-01.mp4"
    clip.write_bytes(b"GENERATED-CLIP")
    from pinaka.utils import sha256_bytes

    imported = controller.import_shot_motion(
        project.id,
        job_id=prepared["job"]["id"],
        source_path=str(clip),
        content_hash=sha256_bytes(clip.read_bytes()),
        shot_id=shot["id"],
    )
    assert imported["attached"]["role"] == "motion"
    slots = [
        slot
        for slot in controller.repository.list_slots(project.id)
        if slot["shot_id"] == shot["id"] and slot["role"] == "motion"
    ]
    assert slots, "a motion slot should be created for the shot"
    assignment = controller.repository.slot_asset_by_role(slots[0]["id"], "primary")
    assert assignment["artifact_id"] == imported["artifact"]["id"]

    authored = controller.author_composition(project.id, width=540, height=960)
    html = controller.read_artifact_text(authored["artifact"]["id"])
    assert '<video class="layer clip"' in html
    assert "file:///" in html
    assert "v.currentTime = local" in html


def test_attach_rejects_a_non_video(controller):
    project, shot, _, frame, _ = _setup(controller)
    with pytest.raises(IntegrityError, match="video"):
        controller.attach_shot_clip(
            project.id, shot_id=shot["id"], artifact_id=frame["id"]
        )


def test_first_frame_is_required_for_keyframe_modes():
    with pytest.raises(ValidationError, match="requires first_frame_artifact_id"):
        ShotMotionRequest(
            shot_id="s",
            model="minimax/h3",
            mode="first-frame",
            resolution="768p",
            duration_seconds=Decimal(5),
            prompt="x",
        )

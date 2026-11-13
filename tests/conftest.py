from __future__ import annotations

import json
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


def record_motion_evidence_stub(
    controller: CreativeController,
    project_id: str,
    shot_id: str,
    *,
    level: str = "M3",
) -> str:
    """Write a passing motion-evidence artifact for gate tests."""

    body = json.dumps(
        {
            "schema": "motion-evidence/v1",
            "project_id": project_id,
            "shot_id": shot_id,
            "level": level,
            "verdict": "pass",
            "residual_ratio": 0.08,
            "components": 3,
        }
    ).encode("utf-8")
    artifact = controller.write_artifact(
        project_id,
        kind=ArtifactKind.DESIGN,
        relative_path=f"motion/evidence-{shot_id}.json",
        content=body,
        operation={"type": "motion_evidence", "shot_id": shot_id, "level": level},
    )
    return artifact["id"]


def complete_motion_levels(
    controller: CreativeController, project_id: str, *, level: str = "M3"
) -> None:
    """Give every shot a passing motion level before animatic approval."""

    for shot in controller.repository.list_shots(project_id):
        evidence_id = record_motion_evidence_stub(
            controller, project_id, shot["id"], level=level
        )
        controller.record_motion_level(
            project_id,
            MotionLevelCreate(
                shot_id=shot["id"],
                level=level,
                source="composition",
                evidence=(f"evidence://motion/{shot['stable_key']}",),
                evidence_artifact_id=evidence_id,
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


def record_minimal_production_plan(
    controller: CreativeController, project_id: str
) -> dict:
    """Build a structurally complete plan from the existing shots and assets.

    This is test scaffolding: it proves the readiness gate can be satisfied
    without pretending the creative values are final.
    """

    from pinaka.contracts.production_plan import ProductionPlanCreate

    project = controller.repository.get_project(project_id)
    shots = controller.repository.list_shots(project_id)
    slots = {slot["id"]: slot for slot in controller.repository.list_slots(project_id)}
    assignments = controller.repository.list_slot_assets(project_id)
    assets_by_shot: dict[str, list[str]] = {}
    for assignment in assignments:
        slot = slots.get(assignment["slot_id"])
        if slot:
            assets_by_shot.setdefault(slot["shot_id"], []).append(
                assignment["artifact_id"]
            )
    asset_ids = sorted(
        {asset for values in assets_by_shot.values() for asset in values}
    )
    if not asset_ids:
        asset_ids = [f"asset-{shot['stable_key']}" for shot in shots]
        for shot, asset_id in zip(shots, asset_ids, strict=False):
            assets_by_shot[shot["id"]] = [asset_id]
    shot_directions = []
    beats = []
    timings = []
    cursor = 0.0
    for index, shot in enumerate(shots, start=1):
        duration = float(shot["duration_seconds"])
        shot_assets = assets_by_shot.get(shot["id"], [asset_ids[0]])
        shot_key = shot["stable_key"]
        shot_directions.append(
            {
                "shot_key": shot_key,
                "purpose": f"Deliver the story beat for {shot_key}",
                "timing_cue": f"{cursor:.2f}s-{cursor + duration:.2f}s",
                "duration_seconds": duration,
                "composition": {
                    "framing": "medium",
                    "angle": "eye level",
                    "subject_placement": "centre",
                    "focal_point": "subject",
                    "camera_movement": "push in",
                    "caption_room": "lower third",
                },
                "performance": {
                    "action_start": "enter",
                    "action_end": "hold",
                    "pause_or_reaction": "brief hold",
                    "eyeline": "camera",
                },
                "lighting": {
                    "direction": "soft key",
                    "softness": "soft",
                    "contrast": "low",
                    "palette": ["warm", "green"],
                    "change": "none",
                },
                "graphics": {
                    "effects": ["none"],
                    "trigger": "none",
                    "duration_seconds": 0,
                    "intensity": "none",
                    "placement": "none",
                    "editable": True,
                },
                "audio": {
                    "narration": "story line",
                    "music": None,
                    "ambience": "room tone",
                    "sfx": [],
                    "silence": None,
                    "carry_over": None,
                },
                "asset_ids": shot_assets,
                "method": "layered_2d_animation",
                "fallback": "owned still",
                "acceptance_checks": ["subject visible", "duration exact"],
                "reviewer_check": "Watch the shot against the beat",
            }
        )
        beats.append(
            {
                "beat_id": f"beat-{index:02d}",
                "purpose": f"Advance beat {index}",
                "viewer_need": "see",
                "script_line": "story line",
                "visual_action": "subject acts",
                "duration_seconds": duration,
                "shot_keys": [shot_key],
            }
        )
        timings.append(
            {
                "beat_id": f"beat-{index:02d}",
                "start_seconds": cursor,
                "end_seconds": cursor + duration,
            }
        )
        cursor += duration
    assets = [
        {
            "asset_id": asset_id,
            "kind": "image",
            "description": "owned visual asset",
            "source": "owned",
            "method": "layered_2d_animation",
            "approval_status": "owned" if assignments else "planned",
            "fallback": "owned still",
            "reuse": "single shot",
        }
        for asset_id in asset_ids
    ]
    payload = {
        "production_format": "sing_along",
        "visual_style": "flat_2d_cartoon",
        "delivery_format": "vertical_reel",
        "production_method": "layered_2d_animation",
        "brief": {
            "objective": project.objective,
            "audience": project.audience,
            "language": project.language,
            "required_content": ["story beats"],
            "constraints": ["USD 10"],
            "assumptions": [],
            "missing_inputs": [],
        },
        "treatment": {
            "premise": "A short counted rhyme",
            "emotional_progression": ["calm", "playful", "resolve"],
            "visual_language": "flat vector shapes and a warm pond",
            "pacing": "one beat every four seconds",
            "sound": "narration and a light bed",
        },
        "beats": beats,
        "shot_directions": shot_directions,
        "asset_manifest": assets,
        "continuity_notes": ["keep the same palette and character scale"],
        "rough_cut": {
            "duration_seconds": cursor,
            "beat_timings": timings,
            "audio_reference": "narration-v1",
            "caption_notes": "phrase-level captions in the lower third",
            "holds": ["hold the final frame"],
        },
    }
    return controller.record_production_plan(
        project_id, ProductionPlanCreate.model_validate(payload)
    )

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

import cv2
import numpy as np
import yaml

from pinaka.assets import AssetCriteria
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
from pinaka.contracts.quality import (
    CreativeReviewCreate,
    IntentBeatDelivery,
    IntentMatrixCreate,
    QualityContractCreate,
)
from pinaka.controller import CreativeController
from pinaka.enums import (
    ApprovalStage,
    ArtifactKind,
    BudgetCategory,
    ProjectStatus,
    SlotAssetRole,
)
from pinaka.models import (
    ApprovalRequest,
    BudgetPlanCreate,
    BudgetPlanItemCreate,
    FactSetCreate,
    Money,
    ProductionPlanCreate,
    ProjectCreate,
    ScriptApproval,
    ScriptCreate,
    SlotCreate,
    TextBriefRequest,
)
from pinaka.openverse import default_context
from pinaka.production import ReuseDecisionCreate, ReuseItem
from pinaka.utils import canonical_json

ROOT = Path(r"D:\pinaka-agentic-creative\workspace\complete-flow-qc")
if ROOT.exists():
    shutil.rmtree(ROOT)
controller = CreativeController.open(ROOT)
project = controller.create_project(
    ProjectCreate(
        name="Pinaka Counting Rhyme QC",
        objective="Produce a 10-second counting rhyme under a USD 1.00 cap",
        mode="entertainment",
        audience="children and reviewers",
        output_format="9:16",
        language="en",
        duration_seconds=Decimal(10),
        max_budget=Money(amount=Decimal("1.00"), currency="USD"),
    )
)


controller.record_quality_contract(
    project.id,
    QualityContractCreate(
        purpose="flow_fixture",
        publication_allowed=False,
        human_review_required=False,
        review_required=False,
        hard_minimums={
            "resolution": "1080x1920",
            "fps": "24",
            "lufs": "-16",
            "true_peak_dbtp": "-2",
            "cps": "15",
            "motion_area": "0.005",
        },
        must_have_beats=(
            "five_ducklings",
            "mother_duck",
            "pond",
            "count_1_5",
            "quack",
        ),
    ),
)


def critique(stage: str, artifact_id: str | None = None) -> dict:
    result = controller.auto_critique(project.id, stage, artifact_id=artifact_id)
    print(
        "critique",
        stage,
        result["critique"]["verdict"],
        "findings",
        len(result["critique"]["findings"]),
        "actions",
        len(result["actions"]),
    )
    return result


controller.plan_creative_budget(
    project.id,
    BudgetPlanCreate(
        creative_summary="USD 1.00 cap; A/B presentation labels; owned assets only",
        items=(
            BudgetPlanItemCreate(
                stable_key="rhyme-a",
                ordinal=1,
                category=BudgetCategory.ASSET_GENERATION,
                provider="local",
                operation="owned_render",
                estimated_cost=Money(amount=Decimal("0.01"), currency="USD"),
                variant_label="A",
            ),
            BudgetPlanItemCreate(
                stable_key="rhyme-b",
                ordinal=2,
                category=BudgetCategory.ASSET_GENERATION,
                provider="local",
                operation="owned_render",
                estimated_cost=Money(amount=Decimal("0.01"), currency="USD"),
                variant_label="B",
            ),
        ),
    ),
)

controller.record_reuse_decision(
    project.id,
    ReuseDecisionCreate(
        approved_by="producer",
        items=(
            ReuseItem(
                component="layer contract",
                source="pinaka",
                decision="build",
                license="owned",
            ),
            ReuseItem(
                component="HTML renderer",
                source="hyperframes",
                decision="reuse",
                license="Apache-2.0",
                version="0.8.x",
            ),
            ReuseItem(
                component="layer actions",
                source="shortgpt",
                decision="adapt",
                license="MIT",
            ),
        ),
    ),
)
plan = controller.plan_from_text(
    project.id,
    TextBriefRequest(
        text=(
            "Make a 10 second counting rhyme for kids: five little ducks swim "
            "on the pond, count one two three four five, mother duck says "
            "quack quack quack."
        ),
        duration_seconds=Decimal(10),
        create_plan=True,
        persist_slots=True,
        persist_elements=True,
        production_preset_id="storybook-motion-comic",
        visual_style_id="flat-2d-cartoon",
    ),
)
for shot in plan["shots"]:
    controller.add_slot(
        project.id,
        SlotCreate(
            shot_id=shot["id"],
            stable_key=f"{shot['stable_key']}.background",
            role="background",
            identity_requirement="pond and sky",
            fact_requirement="pond setting",
            transformation_permission="crop and grade",
            approved_sources=("owned",),
            media_requirement="image",
            style_id="flat-2d-cartoon",
        ),
    )
    requirements = shot["requirements"]
    requirements.setdefault("subject", {})["action"] = "swim"
    requirements.setdefault("motion", {})["action"] = "swim"
    with controller.repository.database.transaction() as connection:
        connection.execute(
            "UPDATE shots SET requirements_json = ? WHERE id = ?",
            (canonical_json(requirements), shot["id"]),
        )
production = controller.plan_production(project.id)
TOTAL_SECONDS = 10.0
SECONDS = TOTAL_SECONDS / len(plan["shots"])
print(
    "slots",
    len(controller.repository.list_slots(project.id)),
    "shots",
    len(plan["shots"]),
)
print("shot productions", len(production["productions"]))
critique("shots")
critique("style")

controller.transition_project(project.id, ProjectStatus.PLANNED, reason="Plan ready")
controller.approve(
    project.id,
    ApprovalRequest(
        stage=ApprovalStage.BRIEF,
        approved_by="producer",
        scope={
            "brief_id": plan["brief"]["id"],
            "brief_hash": plan["brief"]["content_hash"],
            "recipe": plan["creative_plan"]["recipe_id"],
        },
    ),
)
controller.add_fact_set(
    project.id,
    FactSetCreate(
        source_ref="rhyme-canon/five-little-ducks",
        values={"rhyme": "Five Little Ducks", "setting": "pond", "count": "1-5"},
        evidence_refs=("https://en.wikipedia.org/wiki/Two_Little_Dicks",),
    ),
)
controller.transition_project(
    project.id, ProjectStatus.SOURCING, reason="Begin owned/stock-first sourcing"
)

BASE = Path(r"C:\Users\LENOVO\AppData\Local\Temp\pinaka-complete-assets")
if BASE.exists():
    shutil.rmtree(BASE)
BASE.mkdir(parents=True)
WIDTH, HEIGHT, FPS = 1080, 1920, 24


def background_array(seed: int) -> np.ndarray:
    """A soft pond: sky, sun, layered hills, water, ripples and edge reeds."""

    canvas = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    horizon = int(HEIGHT * 0.44)
    for y in range(HEIGHT):
        if y < horizon:
            blue = 238 - int(30 * y / HEIGHT)
            canvas[y, :, :] = (blue, blue - 8, 150)
        else:
            depth = (y - horizon) / max(1, HEIGHT - horizon)
            canvas[y, :, :] = (
                205 - int(70 * depth),
                225 - int(75 * depth),
                190 - int(55 * depth),
            )
    cv2.circle(canvas, (180, 250), 120, (228, 238, 248), -1)
    back_hill = np.array(
        [
            [0, horizon],
            [0, int(HEIGHT * 0.34)],
            [int(WIDTH * 0.18), int(HEIGHT * 0.30)],
            [int(WIDTH * 0.34), int(HEIGHT * 0.36)],
            [int(WIDTH * 0.52), int(HEIGHT * 0.29)],
            [int(WIDTH * 0.70), int(HEIGHT * 0.35)],
            [int(WIDTH * 0.86), int(HEIGHT * 0.31)],
            [WIDTH, int(HEIGHT * 0.37)],
            [WIDTH, horizon],
        ],
        dtype=np.int32,
    )
    front_hill = np.array(
        [
            [0, horizon],
            [0, int(HEIGHT * 0.39)],
            [int(WIDTH * 0.22), int(HEIGHT * 0.35)],
            [int(WIDTH * 0.42), int(HEIGHT * 0.41)],
            [int(WIDTH * 0.62), int(HEIGHT * 0.34)],
            [int(WIDTH * 0.82), int(HEIGHT * 0.40)],
            [WIDTH, int(HEIGHT * 0.36)],
            [WIDTH, horizon],
        ],
        dtype=np.int32,
    )
    cv2.fillPoly(canvas, [back_hill], (150, 190, 120))
    cv2.fillPoly(canvas, [front_hill], (105, 160, 95))
    for index in range(7):
        cx = int(WIDTH * (0.10 + index * 0.13))
        cy = int(HEIGHT * (0.80 + 0.015 * (index % 3)))
        cv2.ellipse(
            canvas,
            (cx, cy),
            (int(WIDTH * 0.14), 10),
            0,
            0,
            360,
            (185, 215, 205),
            2,
        )
    for x in (24, 52, 80, WIDTH - 90, WIDTH - 60, WIDTH - 28):
        cv2.line(
            canvas,
            (x, horizon + 20),
            (x + 8, int(HEIGHT * 0.92)),
            (70, 120, 80),
            5,
        )
    return cv2.GaussianBlur(canvas, (0, 0), 1.2)


def _paint_duck(
    bgr: np.ndarray,
    mask: np.ndarray,
    *,
    cx: int,
    cy: int,
    scale: float,
    color: tuple[int, int, int],
    mother: bool = False,
) -> None:
    body = ((cx, cy), (int(scale * 0.34), int(scale * 0.20)), 0)
    head = ((cx + int(scale * 0.24), cy - int(scale * 0.16)), int(scale * 0.15))
    wing = (
        (cx - int(scale * 0.08), cy),
        (int(scale * 0.17), int(scale * 0.08)),
        -22,
    )
    beak = (
        (cx + int(scale * 0.38), cy - int(scale * 0.08)),
        (int(scale * 0.07), int(scale * 0.03)),
        0,
    )
    for image, paint in ((bgr, color), (mask, 255)):
        cv2.ellipse(image, body[0], body[1], body[2], 0, 360, paint, -1)
        cv2.circle(image, head[0], head[1], paint, -1)
        cv2.ellipse(image, wing[0], wing[1], wing[2], 0, 360, paint, -1)
        cv2.ellipse(image, beak[0], beak[1], beak[2], 0, 360, paint, -1)
    eye = (cx + int(scale * 0.28), cy - int(scale * 0.20))
    cv2.circle(bgr, eye, max(2, int(scale * 0.018)), (20, 20, 20), -1)
    cv2.circle(mask, eye, max(2, int(scale * 0.018)), 255, -1)
    if mother:
        cv2.circle(bgr, (eye[0] - 4, eye[1] - 5), 3, (250, 250, 250), -1)


def family_cutout(size: int = 900, *, backup: bool = False) -> np.ndarray:
    """Mother duck plus five ducklings as one transparent character layer."""

    bgr = np.zeros((size, size, 3), dtype=np.uint8)
    mask = np.zeros((size, size), dtype=np.uint8)
    mother_color = (60, 100, 160) if backup else (55, 95, 150)
    duckling_color = (55, 185, 245) if backup else (40, 205, 250)
    _paint_duck(
        bgr,
        mask,
        cx=int(size * 0.58),
        cy=int(size * 0.42),
        scale=size * 0.72,
        color=mother_color,
        mother=True,
    )
    positions = [
        (0.14, 0.66),
        (0.29, 0.72),
        (0.44, 0.75),
        (0.70, 0.74),
        (0.85, 0.67),
    ]
    for index, (px, py) in enumerate(positions):
        _paint_duck(
            bgr,
            mask,
            cx=int(size * px),
            cy=int(size * py),
            scale=size * (0.30 if index % 2 == 0 else 0.33),
            color=duckling_color,
        )
    rgba = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    rgba[:, :, 3] = mask
    return rgba


def rights() -> dict:
    return {
        "license_name": "Owned / generated locally",
        "license_code": "owned",
        "license_url": "https://example.invalid/owned",
        "terms_url": "https://example.invalid/terms",
        "terms_reviewed_at": "2026-11-13",
        "commercial_use_allowed": True,
        "modifications_allowed": True,
        "editorial_only": False,
        "attribution_required": False,
        "attribution_text": None,
        "license_evidence_ref": "owned:local",
        "license_evidence_hash": "0" * 64,
        "origin_license_verified": True,
        "contains_recognizable_people_or_brands": False,
        "third_party_rights_cleared": True,
        "rights_uncertainty": [],
        "obligations": [],
    }


context = default_context(monetized=True)
visuals = {}
for index, shot in enumerate(plan["shots"], start=1):
    background_path = BASE / f"background-{index}.png"
    backup_background_path = BASE / f"background-{index}-b.png"
    character_path = BASE / f"character-{index}.png"
    backup_character_path = BASE / f"character-{index}-b.png"
    cv2.imwrite(str(background_path), background_array(1))
    cv2.imwrite(str(backup_background_path), background_array(3))
    cv2.imwrite(str(character_path), family_cutout())
    cv2.imwrite(str(backup_character_path), family_cutout(backup=True))
    background = controller.write_artifact(
        project.id,
        kind=ArtifactKind.GENERATED,
        relative_path=f"assets/originals/background-{index}.png",
        content=background_path.read_bytes(),
        operation={"type": "owned_asset", "rights": rights()},
    )
    backup_background = controller.write_artifact(
        project.id,
        kind=ArtifactKind.GENERATED,
        relative_path=f"assets/originals/background-{index}-b.png",
        content=backup_background_path.read_bytes(),
        operation={"type": "owned_asset", "rights": rights()},
    )
    character = controller.write_artifact(
        project.id,
        kind=ArtifactKind.GENERATED,
        relative_path=f"assets/originals/character-{index}.png",
        content=character_path.read_bytes(),
        operation={"type": "owned_asset", "rights": rights()},
    )
    backup_character = controller.write_artifact(
        project.id,
        kind=ArtifactKind.GENERATED,
        relative_path=f"assets/originals/character-{index}-b.png",
        content=backup_character_path.read_bytes(),
        operation={"type": "owned_asset", "rights": rights()},
    )
    visuals[shot["stable_key"]] = {
        "background": background,
        "background_backup": backup_background,
        "character": character,
        "character_backup": backup_character,
    }

for shot in plan["shots"]:
    pair = visuals[shot["stable_key"]]
    for role, key, backup_key, asset_class in (
        ("background", "background", "background_backup", "background"),
        ("primary_visual", "character", "character_backup", "cutout"),
    ):
        slot = next(
            item
            for item in controller.repository.list_slots(project.id)
            if item["shot_id"] == shot["id"] and item["role"] == role
        )
        controller.record_sourcing_decision(
            project.id,
            SourcingDecisionCreate(
                slot_id=slot["id"],
                decision="owned",
                reason=f"Locally authored {asset_class} for the approved style",
                providers=("local",),
                candidates_considered=2,
                approved_by="producer",
            ),
        )
        primary = controller.record_sourcing_candidate(
            project.id,
            SourcingCandidateCreate(
                slot_id=slot["id"],
                provider="local",
                provider_asset_id=f"{slot['stable_key']}-primary",
                media_type="video" if role == "primary_visual" else "image",
                source_url="https://example.invalid/owned",
                creator="Pinaka local rig",
                licence="Owned",
                eligibility="eligible",
                treatment="as_is",
                asset_class=asset_class,
                evidence=("owned:local",),
            ),
        )
        backup = controller.record_sourcing_candidate(
            project.id,
            SourcingCandidateCreate(
                slot_id=slot["id"],
                provider="local",
                provider_asset_id=f"{slot['stable_key']}-backup",
                media_type="video" if role == "primary_visual" else "image",
                source_url="https://example.invalid/owned-backup",
                creator="Pinaka local rig",
                licence="Owned",
                eligibility="eligible",
                treatment="as_is",
                asset_class=asset_class,
                evidence=("owned:local-backup",),
            ),
        )
        controller.pick_slot_assets(
            project.id,
            SlotPickCreate(
                slot_id=slot["id"],
                primary_candidate_id=primary["id"],
                backup_candidate_id=backup["id"],
                decided_by="producer",
                reason="Two independent owned variants",
            ),
        )
        controller.assign_slot_asset(
            project.id,
            slot["id"],
            artifact_id=pair[key]["id"],
            role=SlotAssetRole.PRIMARY,
            criteria=AssetCriteria(),
            context=context,
            decided_by="producer",
        )
        controller.assign_slot_asset(
            project.id,
            slot["id"],
            artifact_id=pair[backup_key]["id"],
            role=SlotAssetRole.BACKUP,
            criteria=AssetCriteria(),
            context=context,
            decided_by="producer",
        )

decided = {item["slot_id"] for item in controller.sourcing_decisions(project.id)}
for slot in controller.repository.list_slots(project.id):
    if slot["id"] in decided:
        continue
    controller.record_sourcing_decision(
        project.id,
        SourcingDecisionCreate(
            slot_id=slot["id"],
            decision="deterministic",
            reason=f"{slot['role']} is produced deterministically",
            approved_by="producer",
        ),
    )

asset_manifest = []
seen_assets = set()
shot_assets: dict[str, list[str]] = {}
for shot in plan["shots"]:
    pair = visuals[shot["stable_key"]]
    ids = []
    for key in ("background", "background_backup", "character", "character_backup"):
        artifact = pair[key]
        ids.append(artifact["id"])
        if artifact["id"] in seen_assets:
            continue
        seen_assets.add(artifact["id"])
        asset_manifest.append(
            {
                "asset_id": artifact["id"],
                "kind": "image",
                "description": f"{key} for {shot['stable_key']}",
                "source": "owned",
                "method": "layered_2d_animation",
                "approval_status": "owned",
                "fallback": "owned still",
                "reuse": "single shot",
            }
        )
    shot_assets[shot["stable_key"]] = ids

beats = []
timings = []
shot_directions = []
cursor = 0.0
for index, shot in enumerate(plan["shots"], start=1):
    shot_key = shot["stable_key"]
    duration = float(SECONDS)
    beats.append(
        {
            "beat_id": f"beat-{index:02d}",
            "purpose": f"Deliver rhyme beat {index}",
            "viewer_need": "understand",
            "script_line": "Five little ducks. Mother duck said quack quack quack.",
            "visual_action": "The duck swims across the pond",
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
    shot_directions.append(
        {
            "shot_key": shot_key,
            "purpose": "Carry the rhyme beat and keep the duck readable",
            "timing_cue": f"{cursor:.2f}s-{cursor + duration:.2f}s",
            "duration_seconds": duration,
            "composition": {
                "framing": "medium",
                "angle": "eye level",
                "subject_placement": "centre",
                "focal_point": "duck",
                "camera_movement": "push in",
                "caption_room": "lower third",
            },
            "performance": {
                "action_start": "duck enters frame",
                "action_end": "duck holds centre",
                "pause_or_reaction": "small bob",
                "eyeline": "camera",
            },
            "lighting": {
                "direction": "soft key",
                "softness": "soft",
                "contrast": "low",
                "palette": ["green", "cream"],
                "change": "none",
            },
            "graphics": {
                "effects": ["particles"],
                "trigger": "beat start",
                "duration_seconds": 1.5,
                "intensity": "subtle",
                "placement": "around the duck",
                "editable": True,
            },
            "audio": {
                "narration": "rhyme line",
                "music": None,
                "ambience": "pond",
                "sfx": [],
                "silence": None,
                "carry_over": None,
            },
            "asset_ids": shot_assets[shot_key],
            "method": "layered_2d_animation",
            "fallback": "owned still",
            "acceptance_checks": ["duck visible", "captions readable"],
            "reviewer_check": "Watch the beat and read the caption",
        }
    )
    cursor += duration

production_plan = controller.record_production_plan(
    project.id,
    ProductionPlanCreate.model_validate(
        {
            "production_format": "sing_along",
            "visual_style": "flat_2d_cartoon",
            "delivery_format": "vertical_reel",
            "production_method": "layered_2d_animation",
            "brief": {
                "objective": project.objective,
                "audience": project.audience,
                "language": project.language,
                "required_content": [
                    "five ducklings",
                    "mother duck",
                    "pond",
                    "count 1-5",
                    "quack",
                ],
                "constraints": ["USD 1.00 cap"],
                "assumptions": [],
                "missing_inputs": [],
            },
            "treatment": {
                "premise": "Two ducklings swim and answer the mother duck",
                "emotional_progression": ["calm", "playful", "resolve"],
                "visual_language": "flat vector pond with a clean duck cutout",
                "pacing": "one beat every four seconds",
                "sound": "narration with pond ambience",
            },
            "beats": beats,
            "shot_directions": shot_directions,
            "asset_manifest": asset_manifest,
            "continuity_notes": ["same duck identity and palette across shots"],
            "rough_cut": {
                "duration_seconds": cursor,
                "beat_timings": timings,
                "audio_reference": "narration-v1",
                "caption_notes": "phrase-level captions in the lower third",
                "holds": ["hold the final frame"],
            },
        }
    ),
)
print(
    "production plan",
    production_plan["status"]["ready"],
    production_plan["status"]["issues"],
)
critique("assets")
critique("style")
controller.approve(
    project.id,
    ApprovalRequest(
        stage=ApprovalStage.SOURCING,
        approved_by="producer",
        scope={
            "sources": ["local-owned"],
            "picks": controller.slot_pick_summary(project.id),
        },
    ),
)
controller.transition_project(
    project.id, ProjectStatus.ANIMATIC_READY, reason="Owned layers ready"
)

effect_cues = []
for shot_index, shot in enumerate(plan["shots"]):
    effect_cues.extend(
        (
            EffectCueCreate(
                shot_key=shot["stable_key"],
                kind="camera_push_in",
                start_seconds=str(shot_index * SECONDS),
                duration_seconds=str(SECONDS),
                rights="owned:local",
            ),
            EffectCueCreate(
                shot_key=shot["stable_key"],
                kind="particles",
                start_seconds=str(shot_index * SECONDS + 0.5),
                duration_seconds="3",
                rights="owned:local",
            ),
            EffectCueCreate(
                shot_key=shot["stable_key"],
                kind="transition_dissolve",
                start_seconds=str(shot_index * SECONDS),
                duration_seconds="0.5",
                rights="owned:local",
            ),
        )
    )
controller.record_effects_plan(
    project.id,
    EffectsPlanCreate(
        cues=tuple(effect_cues),
        sound_design_notes="Narration only; no external music.",
    ),
)
composition = controller.author_composition(project.id, width=WIDTH, height=HEIGHT)
render = controller.render_composition(
    project.id,
    composition_artifact_id=composition["artifact"]["id"],
    fps=FPS,
    width=WIDTH,
    height=HEIGHT,
)
qa_animatic = controller.qa_render(
    project.id,
    artifact_id=render["video"]["id"],
    expected_duration=TOTAL_SECONDS,
    expected_width=WIDTH,
    expected_height=HEIGHT,
    expected_fps=FPS,
    expect_audio=False,
)
assert qa_animatic["overall"] != "fail", qa_animatic

render_path = controller.artifact_store.resolve(
    project.id, render["video"]["relative_path"]
)
for shot_index, shot in enumerate(plan["shots"]):
    capture = cv2.VideoCapture(str(render_path))
    frames = []
    for sample in range(12):
        timestamp = shot_index * SECONDS + (sample + 0.5) * SECONDS / 12
        capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
        ok, frame = capture.read()
        if not ok:
            break
        frames.append(frame)
    capture.release()
    evidence = controller.write_motion_evidence(
        project.id,
        shot_id=shot["id"],
        level="M2",
        frames=frames,
        source="composition",
    )
    assert evidence["evidence"]["verdict"] == "pass", evidence
    controller.record_motion_level(
        project.id,
        MotionLevelCreate(
            shot_id=shot["id"],
            level="M2",
            source="composition",
            evidence=(evidence["artifact"]["id"],),
            evidence_artifact_id=evidence["artifact"]["id"],
        ),
    )

critique("motion", render["video"]["id"])
critique("visual", render["video"]["id"])

controller.record_flow_step(
    project.id,
    FlowStepCreate(
        step="g0_intake",
        status="done",
        actor="producer",
        tool="pinaka",
        evidence=(f"brief:{plan['brief']['id']}",),
    ),
)
controller.record_flow_step(
    project.id,
    FlowStepCreate(
        step="g1_classification",
        status="done",
        actor="producer",
        tool="pinaka.creative_rules",
        evidence=(
            f"profile:{plan['creative_plan']['profile_hash']}",
            f"recipe:{plan['creative_plan']['recipe_id']}",
            f"preset:{plan['production']['preset_id']}",
            f"style:{plan['production']['style_id']}",
        ),
    ),
)
controller.record_flow_step(
    project.id,
    FlowStepCreate(
        step="g2_creative_plan",
        status="done",
        actor="producer",
        tool="pinaka.plan_from_text",
        evidence=(
            f"creative_plan:{plan['creative_plan']['artifact']['id']}",
            f"contract:{plan['production_contract']['artifact']['id']}",
        ),
    ),
)
controller.record_flow_step(
    project.id,
    FlowStepCreate(
        step="g3_sourcing",
        status="done",
        actor="producer",
        tool="pinaka.owned-assets",
        evidence=(f"picks:{controller.slot_pick_summary(project.id)['complete']}",),
    ),
)
controller.record_flow_step(
    project.id,
    FlowStepCreate(
        step="g4_storyboard",
        status="done",
        actor="producer",
        tool="pinaka.plan_production",
        evidence=(f"composition:{composition['artifact']['id']}",),
        execution_owner="local",
        studio_role="none",
    ),
)
controller.record_flow_step(
    project.id,
    FlowStepCreate(
        step="g5_production",
        status="done",
        actor="producer",
        tool="pinaka.render_composition",
        evidence=(f"render:{render['video']['id']}",),
        execution_owner="local",
        studio_role="fallback",
        fallback_of="studio",
        trade_off=(
            "Studio fal.video is admission-blocked at credits 0; the local "
            "HyperFrames render is deterministic and costs USD 0.00"
        ),
    ),
)
controller.record_flow_step(
    project.id,
    FlowStepCreate(
        step="g6_qa",
        status="done",
        actor="producer",
        tool="pinaka.qa_render",
        evidence=(f"qa:{qa_animatic['overall']}",),
        execution_owner="local",
        studio_role="none",
    ),
)

controller.approve(
    project.id,
    ApprovalRequest(
        stage=ApprovalStage.ANIMATIC,
        approved_by="producer",
        scope={
            "render": render["video"]["id"],
            "qa": qa_animatic["overall"],
            "motion": "M2",
        },
    ),
)
controller.transition_project(
    project.id, ProjectStatus.FINAL_CANDIDATE, reason="Animatic approved"
)

script_text = "Five little ducks. Quack quack. One, two, three, four, five!"
script = controller.record_script(project.id, ScriptCreate(text=script_text))
controller.approve_script(
    project.id, script["id"], ScriptApproval(approved_by="producer")
)
critique("script")
narration = controller.synthesize_narration(project.id, script=script_text, speed=0.82)
muxed = controller.mux_narration(
    project.id,
    video_artifact_id=render["video"]["id"],
    audio_artifact_id=narration["artifact"]["id"],
)
captions = controller.export_captions(
    project.id,
    source="asr",
    pad_to_seconds=float(TOTAL_SECONDS),
    max_cue_seconds=3.5,
)
print(
    "captions",
    captions["source"],
    "tail",
    captions["artifact"]["operation"].get("tail_hold_seconds"),
)
captioned = controller.burn_captions(
    project.id,
    video_artifact_id=muxed["video"]["id"],
    captions_artifact_id=captions["artifact"]["id"],
    mode="hard",
)
qa_final = controller.qa_render(
    project.id,
    artifact_id=captioned["video"]["id"],
    expected_duration=TOTAL_SECONDS,
    expected_width=WIDTH,
    expected_height=HEIGHT,
    expected_fps=FPS,
    captions_artifact_id=captions["artifact"]["id"],
)
assert qa_final["overall"] != "fail", qa_final
coverage = next(
    item for item in qa_final["checks"] if item["check"] == "captions.coverage"
)
assert coverage["status"] == "pass", coverage
critique("captions")
critique("audio", captioned["video"]["id"])
critique("visual", captioned["video"]["id"])
critique("motion", captioned["video"]["id"])

shot_keys = [shot["stable_key"] for shot in plan["shots"]]
family_ids = [visuals[shot["stable_key"]]["character"]["id"] for shot in plan["shots"]]
background_ids = [
    visuals[shot["stable_key"]]["background"]["id"] for shot in plan["shots"]
]
controller.record_intent_matrix(
    project.id,
    IntentMatrixCreate(
        beats=(
            IntentBeatDelivery(
                beat_id="five_ducklings",
                status="delivered",
                shot_keys=tuple(shot_keys),
                asset_ids=tuple(family_ids),
                evidence=tuple(f"artifact:{item}" for item in family_ids),
            ),
            IntentBeatDelivery(
                beat_id="mother_duck",
                status="delivered",
                shot_keys=tuple(shot_keys),
                asset_ids=tuple(family_ids),
                evidence=tuple(f"artifact:{item}" for item in family_ids),
            ),
            IntentBeatDelivery(
                beat_id="pond",
                status="delivered",
                shot_keys=tuple(shot_keys),
                asset_ids=tuple(background_ids),
                evidence=tuple(f"artifact:{item}" for item in background_ids),
            ),
            IntentBeatDelivery(
                beat_id="count_1_5",
                status="delivered",
                shot_keys=tuple(shot_keys),
                asset_ids=tuple(family_ids),
                audio_evidence=narration["artifact"]["id"],
                caption_evidence=captions["artifact"]["id"],
                evidence=(
                    f"captions:{captions['artifact']['id']}",
                    f"narration:{narration['artifact']['id']}",
                ),
            ),
            IntentBeatDelivery(
                beat_id="quack",
                status="delivered",
                shot_keys=tuple(shot_keys),
                audio_evidence=narration["artifact"]["id"],
                caption_evidence=captions["artifact"]["id"],
                evidence=(
                    f"narration:{narration['artifact']['id']}",
                    f"captions:{captions['artifact']['id']}",
                ),
            ),
        )
    ),
)
review_packet = controller.build_review_packet(
    project.id, artifact_id=captioned["video"]["id"]
)
controller.record_creative_review(
    project.id,
    CreativeReviewCreate(
        artifact_id=captioned["video"]["id"],
        artifact_hash=captioned["video"]["content_hash"],
        reviewer="pinaka.fixture_review",
        reviewer_kind="model",
        scores={
            "character": 3,
            "world": 3,
            "story": 3,
            "motion": 4,
            "composition": 3,
            "captions": 4,
            "audio": 3,
            "brand": 3,
            "overall": 3,
        },
        notes="Flow fixture; not a publication-quality creative.",
        verdict="revise",
    ),
)

graded = controller.grade_asset(
    project.id, captioned["video"]["id"], saturation=1.1, brightness=0.02
)
experiment = controller.create_ab_experiment(
    project.id,
    ABExperimentCreate(
        axis="colour grade",
        hypothesis="A warmer grade holds attention",
        primary_metric="3-second view rate",
        traffic_split="50/50",
        sample_plan="500 impressions each",
    ),
)
controller.bind_ab_variant(
    project.id,
    ABVariantBind(
        experiment_id=experiment["id"],
        label="A",
        artifact_id=captioned["video"]["id"],
    ),
)
controller.bind_ab_variant(
    project.id,
    ABVariantBind(
        experiment_id=experiment["id"],
        label="B",
        artifact_id=graded["artifact"]["id"],
    ),
)
ab_delivery = controller.build_ab_delivery_package(
    project.id, str(ROOT / "ab-delivery")
)
critique("delivery")
controller.write_delivery_metadata(project.id)
controller.record_flow_step(
    project.id,
    FlowStepCreate(
        step="g7_delivery",
        status="done",
        actor="producer",
        tool="pinaka.delivery-metadata",
        evidence=(
            f"captions:{captions['artifact']['id']}",
            f"ab:{experiment['id']}",
        ),
        execution_owner="local",
        studio_role="none",
    ),
)
change = controller.change_request(project.id)
record = controller.production_record(project.id)
bar = controller.quality_bar(project.id, artifact_id=captioned["video"]["id"])
print(
    json.dumps(
        {"quality": bar, "final": captioned["video"]["id"], "qa": qa_final["overall"]},
        indent=2,
    )
)
if not bar["passed"]:
    raise SystemExit(1)
controller.approve(
    project.id,
    ApprovalRequest(
        stage=ApprovalStage.FINAL,
        approved_by="producer",
        scope={"candidate": "final", "qa": qa_final["overall"], "ab": "A/B"},
        output_hash=captioned["video"]["content_hash"],
    ),
)
archive = controller.archive_project(project.id)

# F28/F29/F30: submit a filled template, score its impact, then revision it.
generated = controller.change_request(project.id)
document = yaml.safe_load(generated["yaml"])
document["change_request"].update(
    {
        "requested_by": "producer",
        "requested_at": "2026-11-13T15:00:00Z",
        "change_type": "metadata",
        "element_or_artifact_id": captioned["video"]["id"],
        "current_hash": captioned["video"]["content_hash"],
        "requested_change": "Replace the delivery title with the approved rhyme title",
        "reason": "The first packet still used the working title",
        "approver": "client",
        "version_scope": "both",
    }
)
submitted = controller.submit_change_request(
    project.id, text=yaml.safe_dump(document, sort_keys=False)
)
decided = controller.decide_change_request(
    submitted["request"]["id"],
    decision="approve",
    decided_by="client",
    reason="Title-only change; no re-render of media required",
)
revision = controller.create_revision(
    project.id,
    name="Pinaka Complete Flow revision",
    objective="Apply the approved metadata change",
    change_request_id=submitted["request"]["id"],
    change_list=("replace delivery title",),
    requested_by="client",
)
change_status = controller.change_request_status(project.id, submitted["request"]["id"])

print(
    json.dumps(
        {
            "project": project.id,
            "archive": archive["id"],
            "quality_bar": {
                "passed": bar["passed"],
                "gates": len(bar["gates"]),
                "failed": bar["failed"],
            },
            "qa_final": qa_final["overall"],
            "caption_source": captions["source"],
            "caption_coverage": coverage["evidence"],
            "budget": controller.budget_summary(project.id)["items"],
            "ab_delivery": ab_delivery["delivery"]["manifest_hash"],
            "change_request": {
                "id": submitted["request"]["id"],
                "classification": submitted["request"]["classification"],
                "status": change_status["request"]["status"],
            },
            "revision": {
                "id": revision.id,
                "parent": revision.parent_project_id,
                "change_request_id": revision.change_request_id,
            },
            "flow": controller.flow_completeness(project.id)["complete"],
            "critiques": controller.critique_summary(
                project.id, artifact_id=captioned["video"]["id"]
            )["complete"],
            "quality": controller.quality_status(
                project.id, artifact_id=captioned["video"]["id"]
            )["readiness"],
            "review_packet": review_packet["artifact"]["id"],
        },
        indent=2,
    )
)
print(
    json.dumps(
        controller.critique_summary(project.id, artifact_id=captioned["video"]["id"]),
        indent=2,
        default=str,
    )
)
if not bar["passed"]:
    raise SystemExit(1)

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import cv2
import numpy as np

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
from pinaka.controller import CreativeController
from pinaka.enums import ApprovalStage, ArtifactKind, ProjectStatus, SlotAssetRole
from pinaka.models import (
    ApprovalRequest,
    BudgetPlanCreate,
    BudgetPlanItemCreate,
    FactSetCreate,
    Money,
    ProjectCreate,
    ScriptApproval,
    ScriptCreate,
    TextBriefRequest,
)
from pinaka.openverse import default_context
from pinaka.production import ReuseDecisionCreate, ReuseItem

ROOT = Path(r"D:\pinaka-agentic-creative\workspace\mandatory-flow-qc")
if ROOT.exists():
    shutil.rmtree(ROOT)
controller = CreativeController.open(ROOT)
project = controller.create_project(
    ProjectCreate(
        name="Pinaka Mandatory Flow QC",
        objective="Prove every mandatory production gate with a real rendered clip",
        mode="entertainment",
        audience="children and reviewers",
        output_format="9:16",
        language="en",
        duration_seconds=Decimal(8),
        max_budget=Money(amount=Decimal("1.00"), currency="USD"),
    )
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
            "Make an 8 second rhyme for kids: Two little ducks went out one day, "
            "over the hill and far away. Mother duck said quack quack quack."
        ),
        duration_seconds=Decimal(8),
        create_plan=True,
        persist_slots=True,
        persist_elements=True,
        production_preset_id="storybook-motion-comic",
        visual_style_id="flat-2d-cartoon",
    ),
)
production = controller.plan_production(project.id)
TOTAL_SECONDS = 4 * len(plan["shots"])
print("slots", len(plan["slots"]), "shots", len(plan["shots"]))
print("shot productions", len(production["productions"]))

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
        source_ref="rhyme-canon/two-little-ducks",
        values={"rhyme": "Two Little Ducks", "setting": "pond", "sound": "quack"},
        evidence_refs=("https://en.wikipedia.org/wiki/Two_Little_Dicks",),
    ),
)
controller.transition_project(
    project.id, ProjectStatus.SOURCING, reason="Begin owned/stock-first sourcing"
)

BASE = Path(r"C:\Users\LENOVO\AppData\Local\Temp\pinaka-mandatory-assets")
if BASE.exists():
    shutil.rmtree(BASE)
BASE.mkdir(parents=True)
WIDTH, HEIGHT, FPS, SECONDS = 540, 960, 12, 4
FRAMES = FPS * SECONDS


def background_array(seed: int) -> np.ndarray:
    canvas = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    for y in range(HEIGHT):
        canvas[y, :, :] = (
            80 + min(60, y // 16 + seed * 8),
            140 - min(50, y // 20),
            120 + min(40, seed * 5),
        )
    cv2.circle(canvas, (90 + seed * 20, 160), 70, (210, 220, 230), -1)
    cv2.rectangle(canvas, (0, int(HEIGHT * 0.65)), (WIDTH, HEIGHT), (70, 90, 60), -1)
    return canvas


def duck_frame(base: np.ndarray, phase: float, color: tuple[int, int, int]) -> np.ndarray:
    frame = base.copy()
    cx = WIDTH // 2
    cy = int(HEIGHT * 0.55)
    bob = int(6 * np.sin(phase))
    cv2.ellipse(frame, (cx, cy + bob), (95, 58), 0, 0, 360, color, -1)
    cv2.circle(frame, (cx + 70, cy - 28 + bob), 36, color, -1)
    cv2.circle(frame, (cx + 82, cy - 35 + bob), 5, (20, 20, 20), -1)
    cv2.ellipse(
        frame,
        (cx - 35, cy - 5 + bob),
        (50, 24),
        -25 + 18 * np.sin(phase),
        0,
        360,
        (40, 90, 170),
        -1,
    )
    cv2.ellipse(frame, (cx + 108, cy - 20 + bob), (18, 8), 0, 0, 360, (30, 120, 220), -1)
    return frame


def write_clip(path: Path, seed: int, backup: bool = False) -> None:
    ffmpeg = subprocess.check_output(
        [sys.executable, "-c", "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"],
        text=True,
    ).strip()
    command = [
        ffmpeg,
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-s",
        f"{WIDTH}x{HEIGHT}",
        "-r",
        str(FPS),
        "-i",
        "-",
        "-c:v",
        "libvpx-vp9",
        "-b:v",
        "1M",
        "-pix_fmt",
        "yuv420p",
        "-deadline",
        "realtime",
        "-cpu-used",
        "5",
        str(path),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = background_array(seed)
    for index in range(FRAMES):
        phase = (index / FRAMES) * 2 * np.pi
        color = (40, 180, 240) if not backup else (70, 140, 220)
        frame = duck_frame(base, phase if not backup else -phase, color)
        process.stdin.write(frame.tobytes())
    process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError(f"ffmpeg failed for {path}")


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
    clip_path = BASE / f"character-{index}.webm"
    backup_clip_path = BASE / f"character-{index}-b.webm"
    cv2.imwrite(str(background_path), background_array(index))
    cv2.imwrite(str(backup_background_path), background_array(index + 2))
    write_clip(clip_path, index)
    write_clip(backup_clip_path, index, backup=True)
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
        relative_path=f"assets/originals/character-{index}.webm",
        content=clip_path.read_bytes(),
        operation={"type": "owned_asset", "rights": rights()},
    )
    backup_character = controller.write_artifact(
        project.id,
        kind=ArtifactKind.GENERATED,
        relative_path=f"assets/originals/character-{index}-b.webm",
        content=backup_clip_path.read_bytes(),
        operation={"type": "owned_asset", "rights": rights()},
    )
    visuals[shot["stable_key"]] = {
        "background": background,
        "background_backup": backup_background,
        "character": character,
        "character_backup": backup_character,
        "clip": clip_path,
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

decided = {
    item["slot_id"] for item in controller.sourcing_decisions(project.id)
}
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

controller.approve(
    project.id,
    ApprovalRequest(
        stage=ApprovalStage.SOURCING,
        approved_by="producer",
        scope={"sources": ["local-owned"], "picks": controller.slot_pick_summary(project.id)},
    ),
)
controller.transition_project(
    project.id, ProjectStatus.ANIMATIC_READY, reason="Owned layers ready"
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

for shot in plan["shots"]:
    clip = visuals[shot["stable_key"]]["clip"]
    capture = cv2.VideoCapture(str(clip))
    frames = []
    while len(frames) < 12:
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

controller.record_effects_plan(
    project.id,
    EffectsPlanCreate(
        cues=(
            EffectCueCreate(
                shot_key=plan["shots"][0]["stable_key"],
                kind="camera_push_in",
                start_seconds="0",
                duration_seconds="4",
                rights="owned:local",
            ),
            EffectCueCreate(
                shot_key=plan["shots"][0]["stable_key"],
                kind="particles",
                start_seconds="0.5",
                duration_seconds="3",
                rights="owned:local",
            ),
            EffectCueCreate(
                shot_key=plan["shots"][1]["stable_key"],
                kind="transition_fade",
                start_seconds="3.7",
                duration_seconds="0.6",
                rights="owned:local",
            ),
            EffectCueCreate(
                shot_key=plan["shots"][1]["stable_key"],
                kind="particles",
                start_seconds="4.2",
                duration_seconds="3",
                rights="owned:local",
            ),
        ),
        sound_design_notes="Narration only; no external music.",
    ),
)

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

script_text = (
    "Two little ducks went out one day, over the hill and far away. "
    "Mother duck said quack quack quack. Goodnight, little ducks."
)
script = controller.record_script(project.id, ScriptCreate(text=script_text))
controller.approve_script(project.id, script["id"], ScriptApproval(approved_by="producer"))
narration = controller.synthesize_narration(project.id, script=script_text)
muxed = controller.mux_narration(
    project.id,
    video_artifact_id=render["video"]["id"],
    audio_artifact_id=narration["artifact"]["id"],
)
wav_path = controller.artifact_store.resolve(
    project.id, narration["artifact"]["relative_path"]
)
srt_path = BASE / "speech.srt"
transcript_path = BASE / "speech.json"
subprocess.run(
    [
        sys.executable,
        "-m",
        "pinaka",
        "transcribe-audio",
        "--audio",
        str(wav_path),
        "--output",
        str(transcript_path),
        "--srt",
        str(srt_path),
        "--locale",
        "en",
    ],
    cwd=r"D:\pinaka-agentic-creative",
    check=True,
)
captions = controller.write_artifact(
    project.id,
    kind=ArtifactKind.DERIVATIVE,
    relative_path="captions/speech-aligned.srt",
    content=srt_path.read_bytes(),
    operation={"type": "captions", "source": "asr"},
)
captioned = controller.burn_captions(
    project.id,
    video_artifact_id=muxed["video"]["id"],
    captions_artifact_id=captions["id"],
    mode="hard",
)
qa_final = controller.qa_render(
    project.id,
    artifact_id=captioned["video"]["id"],
    expected_duration=TOTAL_SECONDS,
    expected_width=WIDTH,
    expected_height=HEIGHT,
    expected_fps=FPS,
    captions_artifact_id=captions["id"],
)
assert qa_final["overall"] != "fail", qa_final

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
controller.write_delivery_metadata(project.id)
controller.record_flow_step(
    project.id,
    FlowStepCreate(
        step="g7_delivery",
        status="done",
        actor="producer",
        tool="pinaka.delivery-metadata",
        evidence=(f"captions:{captions['id']}", f"ab:{experiment['id']}"),
    ),
)
change = controller.change_request(project.id)
record = controller.production_record(project.id)
bar = controller.quality_bar(project.id, artifact_id=captioned["video"]["id"])
print(json.dumps({"quality": bar, "final": captioned["video"]["id"], "qa": qa_final["overall"]}, indent=2))
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
print(json.dumps({"archive": archive["id"], "project": project.id}, indent=2))

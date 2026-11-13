from __future__ import annotations

import sqlite3

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from pinaka.contracts.critique import (
    CritiqueFinding,
    StageCritiqueCreate,
)
from pinaka.database import Database
from pinaka.enums import ArtifactKind
from pinaka.errors import ApprovalRequiredError
from pinaka.schema import MIGRATIONS, SCHEMA_VERSION
from pinaka.visual_qc import analyze_video

from .conftest import create_project


def _critique(stage: str, findings=(), verdict="pass"):
    return StageCritiqueCreate(
        stage=stage,
        subject_type="project",
        subject_id="project",
        reviewer="qc",
        reviewer_kind="human",
        findings=findings,
        verdict=verdict,
    )


def test_open_high_finding_requires_an_action_and_blocks(controller):
    project = create_project(controller)
    finding = CritiqueFinding(
        dimension="visual",
        severity="high",
        summary="A one-second black gap at the cut",
        evidence=("frame:3.25-4.25",),
        corrective_action="Replace the stacked fades with a crossfade",
    )
    request = _critique("visual", (finding,), verdict="revise")
    request = request.model_copy(update={"subject_id": project.id})
    result = controller.record_stage_critique(project.id, request)
    assert result["critique"]["verdict"] == "revise"
    assert len(result["actions"]) == 1

    with pytest.raises(ApprovalRequiredError):
        controller.stage_critique_gate(project.id, "visual")

    action = result["actions"][0]
    controller.close_corrective_action(
        action["id"],
        closed_by="qc",
        evidence=("render:new-hash",),
        result="Crossfade replaced the black gap; frame scan clean",
    )
    controller.record_stage_critique(
        project.id,
        _critique("visual", findings=(), verdict="pass").model_copy(
            update={"subject_id": project.id}
        ),
    )
    gate = controller.stage_critique_gate(project.id, "visual")
    assert gate["blocking_actions"] == []


def test_pass_verdict_cannot_hide_an_open_high_finding():
    with pytest.raises(ValidationError):
        StageCritiqueCreate(
            stage="assets",
            subject_type="project",
            subject_id="project",
            reviewer="qc",
            reviewer_kind="human",
            findings=(
                CritiqueFinding(
                    dimension="assets",
                    severity="blocker",
                    summary="Character has no alpha channel",
                    corrective_action="Replace with a cutout",
                ),
            ),
            verdict="pass",
        )


def test_visual_qc_finds_a_dark_gap(tmp_path):
    path = tmp_path / "dark-gap.mp4"
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 24.0, (540, 960)
    )
    rng = np.random.default_rng(7)
    for frame_index in range(48):
        dark = 12 <= frame_index < 18
        if dark:
            frame = np.full((960, 540, 3), 8, dtype=np.uint8)
        else:
            frame = rng.integers(50, 180, (960, 540, 3), dtype=np.uint8)
        writer.write(frame)
    writer.release()

    report = analyze_video(path)
    assert report["frames"] == 48
    assert report["dark_runs"]
    run = report["dark_runs"][0]
    assert run["frames"] >= 6
    assert run["min_luma"] < 15


def test_auto_visual_critique_records_actions_and_can_be_rechecked(
    controller, tmp_path
):
    project = create_project(controller)
    path = tmp_path / "candidate.mp4"
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 24.0, (1080, 1920)
    )
    rng = np.random.default_rng(9)
    for frame_index in range(48):
        if 16 <= frame_index < 22:
            frame = np.full((1920, 1080, 3), 8, dtype=np.uint8)
        else:
            frame = rng.integers(50, 180, (1920, 1080, 3), dtype=np.uint8)
        writer.write(frame)
    writer.release()
    artifact = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/candidate.mp4",
        content=path.read_bytes(),
        operation={"type": "test_render"},
    )
    result = controller.auto_critique(project.id, "visual", artifact_id=artifact["id"])
    assert result["critique"]["verdict"] == "block"
    assert result["actions"]
    with pytest.raises(ApprovalRequiredError):
        controller.stage_critique_gate(project.id, "visual", artifact_id=artifact["id"])

    for action in result["actions"]:
        controller.close_corrective_action(
            action["id"],
            closed_by="qc",
            evidence=("render:repaired",),
            result="Repaired candidate",
        )
    repaired = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/candidate-repaired.mp4",
        content=b"repaired-render",
        operation={"type": "test_render"},
    )
    controller.record_stage_critique(
        project.id,
        StageCritiqueCreate(
            stage="visual",
            subject_type="artifact",
            subject_id=repaired["id"],
            subject_hash=repaired["content_hash"],
            reviewer="qc",
            reviewer_kind="human",
            findings=(),
            verdict="pass",
        ),
    )
    gate = controller.stage_critique_gate(
        project.id, "visual", artifact_id=repaired["id"]
    )
    assert gate["critique"]["verdict"] == "pass"


def test_schema_v25_migrates_to_v26(tmp_path):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        for version in range(1, 26):
            connection.executescript(MIGRATIONS[version])
        connection.execute("PRAGMA user_version = 25")
    Database(path).initialize()
    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert version == SCHEMA_VERSION == 26
    assert {"stage_critiques", "corrective_actions"} <= tables

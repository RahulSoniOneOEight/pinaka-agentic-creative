from __future__ import annotations

import pytest

from pinaka.enums import ArtifactKind, QAStatus
from pinaka.errors import ConflictError, IntegrityError
from pinaka.models import TextBriefRequest

from .conftest import create_project

GOOD = {
    "format": "mov,mp4,m4a,3gp",
    "duration_seconds": 10.0,
    "codec": "h264",
    "width_px": 540,
    "height_px": 960,
    "fps": 10.0,
    "has_audio": True,
    "size_bytes": 283500,
}


def _render(controller, project_id, content=b"MP4DATA"):
    return controller.write_artifact(
        project_id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/final.mp4",
        content=content,
        operation={"type": "narration_mux", "duration_seconds": "10"},
    )


def _probe(**overrides):
    def run(_path):
        return {**GOOD, **overrides}

    return run


def _loud(_path):
    return {"input_i": "-16.0", "input_tp": "-2.0"}


def test_qa_render_passes_a_good_deliverable(controller):
    project = create_project(controller, budget="10.00")
    artifact = _render(controller, project.id)
    result = controller.qa_render(
        project.id,
        artifact_id=artifact["id"],
        expected_duration=10.0,
        expected_width=540,
        expected_height=960,
        expected_fps=10.0,
        probe=_probe(),
        measure_loudness=_loud,
    )
    assert result["overall"] == QAStatus.PASS.value
    names = {check["check"] for check in result["checks"]}
    assert {
        "technical.container",
        "technical.codec",
        "technical.duration",
        "technical.resolution",
        "technical.frame_rate",
        "technical.audio",
    } <= names
    report = result["report_artifact"]
    assert report["kind"] == "qa"
    assert report["relative_path"].startswith("qa/render-report-")
    assert controller.verify_artifact(report["id"])
    stored = controller.repository.list_qa_results(project.id)
    assert len(stored) >= 6
    assert all(row["artifact_id"] == artifact["id"] for row in stored)


def test_qa_render_fails_a_wrong_resolution(controller):
    project = create_project(controller, budget="10.00")
    artifact = _render(controller, project.id)
    result = controller.qa_render(
        project.id,
        artifact_id=artifact["id"],
        expected_width=1080,
        expected_height=1920,
        probe=_probe(),
        measure_loudness=_loud,
    )
    assert result["overall"] == QAStatus.FAIL.value
    resolution = next(
        check for check in result["checks"] if check["check"] == "technical.resolution"
    )
    assert resolution["status"] == "fail"


def test_qa_render_fails_missing_audio(controller):
    project = create_project(controller, budget="10.00")
    artifact = _render(controller, project.id)
    result = controller.qa_render(
        project.id,
        artifact_id=artifact["id"],
        probe=_probe(has_audio=False),
        measure_loudness=_loud,
    )
    assert result["overall"] == QAStatus.FAIL.value


def test_qa_render_warns_on_a_short_duration(controller):
    project = create_project(controller, budget="10.00")
    artifact = _render(controller, project.id)
    result = controller.qa_render(
        project.id,
        artifact_id=artifact["id"],
        expected_duration=12.0,
        probe=_probe(),
        measure_loudness=_loud,
    )
    assert result["overall"] in {QAStatus.WARN.value, QAStatus.FAIL.value}
    duration = next(
        check for check in result["checks"] if check["check"] == "technical.duration"
    )
    assert duration["status"] in {"warn", "fail"}


def test_qa_render_rejects_a_tampered_file(controller):
    project = create_project(controller, budget="10.00")
    artifact = _render(controller, project.id)
    path = controller.artifact_store.resolve(project.id, artifact["relative_path"])
    path.write_bytes(b"TAMPERED")
    with pytest.raises(IntegrityError, match="registered hash"):
        controller.qa_render(
            project.id,
            artifact_id=artifact["id"],
            probe=_probe(),
            measure_loudness=_loud,
        )


def test_qa_render_rejects_another_project(controller):
    project = create_project(controller, budget="10.00")
    other = create_project(controller, name="Other", budget="10.00")
    artifact = _render(controller, project.id)
    with pytest.raises(ConflictError, match="different project"):
        controller.qa_render(
            other.id, artifact_id=artifact["id"], probe=_probe(), measure_loudness=_loud
        )


def _srt(cues):
    blocks = []
    for index, (start, end, text) in enumerate(cues, start=1):
        blocks.append(f"{index}\n{start} --> {end}\n{text}\n")
    return "\n".join(blocks)


def test_qa_checks_loudness_and_true_peak(controller):
    project = create_project(controller, budget="10.00")
    artifact = _render(controller, project.id)

    def quiet(_path):
        return {"input_i": "-30.0", "input_tp": "-12.0"}

    result = controller.qa_render(
        project.id,
        artifact_id=artifact["id"],
        probe=_probe(),
        measure_loudness=quiet,
    )
    loudness = next(
        check for check in result["checks"] if check["check"] == "audio.loudness"
    )
    assert loudness["status"] == "fail"
    assert result["overall"] == QAStatus.FAIL.value

    def clipped(_path):
        return {"input_i": "-16.0", "input_tp": "1.5"}

    clipped_result = controller.qa_render(
        project.id,
        artifact_id=artifact["id"],
        probe=_probe(),
        measure_loudness=clipped,
    )
    peak = next(
        check
        for check in clipped_result["checks"]
        if check["check"] == "audio.true_peak"
    )
    assert peak["status"] == "fail"


def test_qa_caption_coverage_pass_and_fail(controller):
    project = create_project(controller, budget="10.00")
    artifact = _render(controller, project.id)
    full = controller.write_artifact(
        project.id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path="captions/full.srt",
        content=_srt(
            [
                ("00:00:00,000", "00:00:04,000", "One"),
                ("00:00:04,000", "00:00:07,000", "Two"),
                ("00:00:07,000", "00:00:10,000", "Three"),
            ]
        ).encode(),
        operation={"type": "captions"},
    )
    good = controller.qa_render(
        project.id,
        artifact_id=artifact["id"],
        probe=_probe(),
        measure_loudness=_loud,
        captions_artifact_id=full["id"],
    )
    coverage = next(
        check for check in good["checks"] if check["check"] == "captions.coverage"
    )
    assert coverage["status"] == "pass"

    partial = controller.write_artifact(
        project.id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path="captions/partial.srt",
        content=_srt([("00:00:00,000", "00:00:03,000", "One")]).encode(),
        operation={"type": "captions"},
    )
    bad = controller.qa_render(
        project.id,
        artifact_id=artifact["id"],
        probe=_probe(),
        measure_loudness=_loud,
        captions_artifact_id=partial["id"],
    )
    partial_check = next(
        check for check in bad["checks"] if check["check"] == "captions.coverage"
    )
    assert partial_check["status"] == "fail"


def test_qa_flags_a_cue_that_overruns(controller):
    project = create_project(controller, budget="10.00")
    artifact = _render(controller, project.id)
    overrun = controller.write_artifact(
        project.id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path="captions/overrun.srt",
        content=_srt([("00:00:00,000", "00:00:12,000", "Too long")]).encode(),
        operation={"type": "captions"},
    )
    result = controller.qa_render(
        project.id,
        artifact_id=artifact["id"],
        probe=_probe(),
        measure_loudness=_loud,
        captions_artifact_id=overrun["id"],
    )
    timing = next(
        check for check in result["checks"] if check["check"] == "captions.timing"
    )
    assert timing["status"] == "fail"


def test_final_approval_accepts_a_qa_checked_render(controller):
    from pinaka.enums import ApprovalStage, ProjectStatus
    from pinaka.models import ApprovalRequest

    from .conftest import advance_to_sourcing

    project, _, _ = advance_to_sourcing(controller)
    controller.plan_from_text(
        project.id, TextBriefRequest(text="Make a 10 second rhyme for kids")
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
    artifact = _render(controller, project.id)
    qa = controller.qa_render(
        project.id, artifact_id=artifact["id"], probe=_probe(), measure_loudness=_loud
    )
    assert qa["overall"] == QAStatus.PASS.value
    approval = controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.FINAL,
            approved_by="reviewer",
            scope={"candidate": "final-v1"},
            output_hash=artifact["content_hash"],
        ),
    )
    assert approval["subject_hash"]
    assert (
        controller.repository.get_project(project.id).status
        == ProjectStatus.FINAL_APPROVED
    )
    archive = controller.archive_project(project.id)
    assert archive["manifest_hash"]
    assert (
        controller.repository.get_project(project.id).status == ProjectStatus.ARCHIVED
    )

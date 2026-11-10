from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from pinaka.analysis import AssetAnalyzer, AssetObservation
from pinaka.assets import AssetCriteria
from pinaka.correction import CorrectionResult, build_filter_graph
from pinaka.enums import ArtifactKind, AssetMediaType, AssetUseClass, TreatmentVerdict
from pinaka.errors import ApprovalRequiredError, ConflictError
from pinaka.models import AssetUseContext, ShotCreate, SlotCreate
from pinaka.utils import sha256_bytes

from .conftest import create_project

# --- correction adapter ---------------------------------------------------


def test_filter_graph_builds_crop_scale_and_pad():
    graph = build_filter_graph(
        [
            {"type": "crop", "width": 800, "height": 800, "x": 10, "y": 20},
            {"type": "scale", "width": 1080, "height": 1080},
            {
                "type": "pad",
                "width": 1080,
                "height": 1920,
                "y": 420,
                "color": "black",
            },
        ]
    )

    assert graph == (
        "crop=800:800:10:20,scale=1080:1080:flags=lanczos,pad=1080:1920:0:420:black"
    )


def test_filter_graph_rejects_unknown_operations():
    with pytest.raises(ValueError, match="Unsupported correction operation"):
        build_filter_graph([{"type": "inpaint", "width": 1, "height": 1}])
    with pytest.raises(ValueError, match="positive dimensions"):
        build_filter_graph([{"type": "crop", "width": 0, "height": 10}])
    with pytest.raises(ValueError, match="At least one"):
        build_filter_graph([])


def test_correction_adapter_runs_ffmpeg_and_hashes_the_result(monkeypatch, tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(b"source-bytes")

    def fake_run(argv, **kwargs):
        target = Path(argv[-1])
        if target.suffix == ".png":
            target.write_bytes(b"corrected-bytes")
        return SimpleNamespace(returncode=0, stdout="ffmpeg version test\n", stderr="")

    monkeypatch.setattr("pinaka.correction.subprocess.run", fake_run)
    from pinaka.correction import FFmpegCorrectionAdapter

    adapter = FFmpegCorrectionAdapter(executable="ffmpeg-test")
    result = adapter.correct(source, [{"type": "crop", "width": 10, "height": 10}])

    assert result.content == b"corrected-bytes"
    assert result.content_hash == sha256_bytes(b"corrected-bytes")
    assert result.filter_graph == "crop=10:10:0:0"
    assert len(result.command_hash) == 64


# --- analysis -------------------------------------------------------------


def test_criteria_are_proposed_from_observations():
    observation = AssetObservation(
        media_type=AssetMediaType.IMAGE,
        width=800,
        height=600,
        aspect_ratio=1.333333,
        required_aspect_ratio=0.5625,
        required_width=1080,
        required_height=1920,
        text_detected=True,
        text_sample="SALE",
        face_count=2,
        sharpness=10.0,
    )

    criteria = observation.proposed_criteria()

    assert criteria.framing_ok is False
    assert criteria.resolution_sufficient is False
    assert criteria.contains_conflicting_text is True
    assert criteria.contains_recognizable_person_or_brand is True
    assert criteria.has_visible_artifacts is True
    assert criteria.notes


def test_clean_observation_proposes_use_as_is():
    observation = AssetObservation(
        media_type=AssetMediaType.IMAGE,
        width=1080,
        height=1920,
        aspect_ratio=0.5625,
        required_aspect_ratio=0.5625,
        required_width=1080,
        required_height=1920,
        text_detected=False,
        face_count=0,
        sharpness=250.0,
    )

    criteria = observation.proposed_criteria()

    assert criteria.framing_ok is True
    assert criteria.resolution_sufficient is True
    assert criteria.contains_conflicting_text is False
    assert criteria.has_visible_artifacts is False


def test_analyzer_uses_injected_measurements(tmp_path):
    asset = tmp_path / "asset.jpg"
    asset.write_bytes(b"x")

    def probe(path):
        return {
            "format": {"duration": "3.0"},
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080}],
        }

    analyzer = AssetAnalyzer(
        probe=probe,
        ocr=lambda frame: "SPECIAL OFFER",
        faces=lambda frame: 1,
        sharpness=lambda frame: 12.5,
        frame=lambda path: path,
    )
    observation = analyzer.analyze(asset, required_width=1080, required_height=1920)

    assert observation.width == 1920
    assert observation.height == 1080
    assert observation.text_detected is True
    assert observation.face_count == 1
    assert observation.sharpness == 12.5
    assert observation.proposed_criteria().framing_ok is False


# --- use context ----------------------------------------------------------


def test_default_use_context_is_conservative(controller):
    project = create_project(controller)

    context = controller.asset_use_context(project.id)

    assert context.use_class == AssetUseClass.COMMERCIAL
    assert context.monetized is True
    assert context.advertising is False


def test_recorded_use_context_is_returned(controller):
    project = create_project(controller)
    controller.set_asset_use_context(
        project.id,
        AssetUseContext(
            use_class=AssetUseClass.EDITORIAL,
            monetized=False,
            advertising=False,
            modification_planned=False,
            attribution_can_be_delivered=True,
        ),
    )

    context = controller.asset_use_context(project.id)

    assert context.use_class == AssetUseClass.EDITORIAL
    assert context.modification_planned is False


def test_weaker_context_is_refused(controller):
    project = create_project(controller)

    with pytest.raises(ConflictError, match="weaker"):
        controller.finalize_asset(
            project.id,
            "slot_missing",
            artifact_id="art_missing",
            criteria=AssetCriteria(),
            context=AssetUseContext(
                use_class=AssetUseClass.NONCOMMERCIAL,
                monetized=False,
            ),
            decided_by="reviewer@example.test",
        )


# --- correction and composition require governed inputs -------------------


def _project_slot_artifact(controller):
    project = create_project(controller)
    shot = controller.add_shot(
        project.id,
        ShotCreate(
            stable_key="hero",
            ordinal=1,
            beat="Hero frame",
            duration_seconds=Decimal(2),
            narrative="Hero frame",
        ),
    )
    slot = controller.add_slot(
        project.id,
        SlotCreate(
            shot_id=shot["id"],
            stable_key="hero-image",
            role="hero image",
            identity_requirement="no recognizable brand",
            fact_requirement="shows the product",
            transformation_permission="crop and scale",
        ),
    )
    artifact = controller.write_artifact(
        project.id,
        kind=ArtifactKind.PREVIEW,
        relative_path="previews/hero.png",
        content=b"source-bytes",
        operation={"type": "test_source"},
    )
    return project, slot, artifact


class _FakeAdapter:
    def __init__(self, content: bytes):
        self.content = content

    def correct(self, path, operations):
        return self._result(operations)

    def compose(self, background, overlay, **kwargs):
        return self._result([{"type": "composite", **kwargs}])

    def _result(self, operations):
        return CorrectionResult(
            content=self.content,
            content_hash=sha256_bytes(self.content),
            filter_graph="crop=1:1:0:0",
            command_hash="b" * 64,
            encoder_version="test",
            operations=tuple(operations),
        )


def test_correction_requires_a_recorded_decision(controller):
    project, slot, artifact = _project_slot_artifact(controller)

    with pytest.raises(ApprovalRequiredError, match="correct"):
        controller.correct_asset(
            project.id,
            slot_id=slot["id"],
            artifact_id=artifact["id"],
            operations=[{"type": "crop", "width": 10, "height": 10}],
            adapter=_FakeAdapter(b"corrected"),
        )


def test_correction_writes_a_derivative_after_the_decision(controller):
    project, slot, artifact = _project_slot_artifact(controller)
    controller.repository.register_treatment_decision(
        project.id,
        {
            "slot_id": slot["id"],
            "artifact_id": artifact["id"],
            "verdict": TreatmentVerdict.CORRECT.value,
            "criteria": AssetCriteria().model_dump(mode="json"),
            "rights": None,
            "reasons": ["correction permitted"],
        },
        decision_hash="c" * 64,
        artifact_id=artifact["id"],
        decided_by="reviewer@example.test",
        created_at="2026-11-11T00:00:00.000Z",
    )

    result = controller.correct_asset(
        project.id,
        slot_id=slot["id"],
        artifact_id=artifact["id"],
        operations=[{"type": "crop", "width": 10, "height": 10}],
        adapter=_FakeAdapter(b"corrected"),
    )

    derivative = result["artifact"]
    assert derivative["kind"] == ArtifactKind.DERIVATIVE.value
    assert derivative["parent_artifact_id"] == artifact["id"]
    assert derivative["operation"]["type"] == "correction"


def test_composite_writes_a_derivative_with_both_inputs(controller):
    project, _slot, artifact = _project_slot_artifact(controller)
    overlay = controller.write_artifact(
        project.id,
        kind=ArtifactKind.PREVIEW,
        relative_path="previews/overlay.png",
        content=b"overlay-bytes",
        operation={"type": "test_overlay"},
    )

    result = controller.compose_assets(
        project.id,
        background_id=artifact["id"],
        overlay_id=overlay["id"],
        x=10,
        y=20,
        adapter=_FakeAdapter(b"composed"),
    )

    derivative = result["artifact"]
    assert derivative["kind"] == ArtifactKind.DERIVATIVE.value
    assert derivative["parent_artifact_id"] == artifact["id"]
    assert derivative["operation"]["overlay_artifact_id"] == overlay["id"]

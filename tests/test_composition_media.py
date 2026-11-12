from __future__ import annotations

import io
import wave

import pytest

from pinaka.adapters.mux import MuxedVideo
from pinaka.adapters.narration import SynthesizedNarration
from pinaka.assets import AssetCriteria
from pinaka.enums import ArtifactKind, SlotAssetRole
from pinaka.errors import IntegrityError
from pinaka.models import SlotCreate, TextBriefRequest
from pinaka.utils import sha256_bytes

from .conftest import create_project


def _wav(seconds: float = 0.5) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24_000)
        handle.writeframes(b"\x00\x00" * int(24_000 * seconds))
    return buffer.getvalue()


class FakeNarration:
    profile_key = "fake-tts"

    def synthesize(self, text: str) -> SynthesizedNarration:
        content = _wav(0.5)
        return SynthesizedNarration(
            content=content,
            content_hash=sha256_bytes(content),
            text_hash=sha256_bytes(text.encode()),
            voice="Kore",
            speed=1.0,
            sample_rate=24_000,
            duration_seconds=0.5,
            model_name="fake-tts",
            model_sha256="a" * 64,
            voices_sha256="b" * 64,
            licence_note="fixture",
            command_hash="c" * 64,
        )


class FakeMux:
    profile_key = "fake-mux"

    def mux(self, *, video_path, audio_path, duration_seconds):
        content = b"FAKE-MUXED" + bytes(int(duration_seconds))
        return MuxedVideo(
            content=content,
            content_hash=sha256_bytes(content),
            metadata={"duration_seconds": duration_seconds, "has_audio": True},
            loudness={"integrated_lufs": -16.0},
            command_hash="d" * 64,
            encoder_version="fake/1",
        )


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" fill="#f2b544"/></svg>'


def _project_with_slot(controller):
    project = create_project(controller, budget="10.00")
    controller.plan_from_text(
        project.id, TextBriefRequest(text="Make a 5 second rhyme for kids")
    )
    shot = controller.repository.list_shots(project.id)[0]
    slot = controller.add_slot(
        project.id,
        SlotCreate(
            shot_id=shot["id"],
            stable_key=f"{shot['stable_key']}.visual",
            role="primary visual",
            identity_requirement="Original subject",
            fact_requirement="One subject",
            transformation_permission="Compose",
            approved_sources=("owned",),
        ),
    )
    artwork = controller.write_artifact(
        project.id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path="assets/duck.svg",
        content=SVG,
        operation={"type": "sourced_import"},
    )
    controller.assign_slot_asset(
        project.id,
        slot["id"],
        artifact_id=artwork["id"],
        role=SlotAssetRole.PRIMARY,
        criteria=AssetCriteria(),
        decided_by="reviewer",
    )
    return project, shot, slot, artwork


def test_composition_embeds_assigned_image_layers(controller):
    project, shot, _, _ = _project_with_slot(controller)
    result = controller.author_composition(project.id, width=540, height=960)
    assert result["layers_embedded"] == 1
    assert result["layers_skipped"] == []
    html = controller.read_artifact_text(result["artifact"]["id"])
    assert "data:image/svg+xml;base64," in html
    assert f'data-shot="{shot["stable_key"]}"' in html


def test_composition_can_skip_layers(controller):
    project, _, _, _ = _project_with_slot(controller)
    result = controller.author_composition(
        project.id, width=540, height=960, embed_layers=False
    )
    assert result["layers_embedded"] == 0
    html = controller.read_artifact_text(result["artifact"]["id"])
    assert "data:image/" not in html


def test_local_script_narration_registers_audio(controller):
    project, _, _, _ = _project_with_slot(controller)
    result = controller.synthesize_narration(
        project.id,
        script="One, two, three little ducks.",
        adapter=FakeNarration(),
    )
    artifact = result["artifact"]
    assert artifact["kind"] == "audio"
    assert artifact["operation"]["duration_seconds"] == 0.5
    assert artifact["operation"]["chars"] == len("One, two, three little ducks.")
    assert controller.verify_artifact(artifact["id"])
    assert artifact["relative_path"].startswith("audio/narration-")


def test_local_narration_never_spends_budget(controller):
    project = create_project(controller, budget="10.00")
    result = controller.synthesize_narration(
        project.id,
        script="One, two, three little ducks.",
        adapter=FakeNarration(),
    )
    assert result["reservation"] is None
    settled = controller.repository.get_project(project.id)
    assert settled.reserved.minor == 0
    assert settled.spent.minor == 0
    assert result["receipt"]["status"] == "succeeded"


def test_narration_rejects_empty_script(controller):
    project = create_project(controller, budget="10.00")
    with pytest.raises(IntegrityError, match="empty"):
        controller.synthesize_narration(
            project.id, script="   ", adapter=FakeNarration()
        )


def test_mux_records_lineage_and_loudness(controller):
    project, _, _, _ = _project_with_slot(controller)
    video = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/silent.mp4",
        content=b"SILENT",
        operation={"type": "composition_render", "duration_seconds": "5"},
    )
    narration = controller.synthesize_narration(
        project.id, script="One, two, three.", adapter=FakeNarration()
    )["artifact"]

    result = controller.mux_narration(
        project.id,
        video_artifact_id=video["id"],
        audio_artifact_id=narration["id"],
        adapter=FakeMux(),
    )
    muxed = result["video"]
    assert muxed["kind"] == "render"
    assert muxed["relative_path"].startswith("renders/narrated-")
    assert result["loudness"]["integrated_lufs"] == -16.0
    assert controller.verify_artifact(muxed["id"])
    dependency_ids = {item["entity_id"] for item in muxed["dependencies"]}
    assert dependency_ids == {video["id"], narration["id"]}


def test_mux_rejects_a_non_audio_artifact(controller):
    project, _, _, _ = _project_with_slot(controller)
    video = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/silent.mp4",
        content=b"SILENT",
        operation={"type": "composition_render", "duration_seconds": "5"},
    )
    with pytest.raises(IntegrityError, match="must be audio"):
        controller.mux_narration(
            project.id,
            video_artifact_id=video["id"],
            audio_artifact_id=video["id"],
            adapter=FakeMux(),
        )


def test_mux_requires_a_known_duration(controller):
    project, _, _, _ = _project_with_slot(controller)
    video = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/silent.mp4",
        content=b"SILENT",
        operation={"type": "composition_render"},
    )
    audio = controller.write_artifact(
        project.id,
        kind=ArtifactKind.AUDIO,
        relative_path="audio/unknown-length.wav",
        content=_wav(0.25),
        operation={"type": "tts"},
    )
    with pytest.raises(IntegrityError, match="known duration"):
        controller.mux_narration(
            project.id,
            video_artifact_id=video["id"],
            audio_artifact_id=audio["id"],
            adapter=FakeMux(),
        )

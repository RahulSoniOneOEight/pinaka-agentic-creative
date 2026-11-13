from __future__ import annotations

from decimal import Decimal

import pytest

from pinaka.adapters.burned_captions import BurnedVideo
from pinaka.adapters.captions import CaptionedVideo
from pinaka.captions import build_srt
from pinaka.composition import build_composition
from pinaka.enums import ArtifactKind
from pinaka.errors import IntegrityError
from pinaka.models import TextBriefRequest
from pinaka.utils import sha256_bytes

from .conftest import create_project

SHOTS = [
    {
        "stable_key": "shot-01",
        "duration_seconds": "4",
        "beat": "establish",
        "narrative": "One, two, three little ducks",
        "requirements": {"subject": {"action": "swim", "count": 3}},
    },
    {
        "stable_key": "shot-02",
        "duration_seconds": "3",
        "beat": "develop",
        "narrative": "swimming in a row",
        "requirements": {"subject": {"action": "bob", "count": 3}},
    },
    {
        "stable_key": "shot-03",
        "duration_seconds": "3",
        "beat": "resolve",
        "narrative": "Quack, quack, quack!",
        "requirements": {"subject": {"action": "count", "count": 3}},
    },
]


def _layer():
    return {
        "role": "subject",
        "mime": "image/svg+xml",
        "data": "PHN2Zy8+",
    }


def test_subject_action_and_count_come_from_the_shot():
    layers = {shot["stable_key"]: [_layer()] for shot in SHOTS}
    html = build_composition(
        title="x", width=540, height=960, shots=SHOTS, layers=layers
    )
    for shot in SHOTS:
        action = shot["requirements"]["subject"]["action"]
        assert f'data-action="{action}"' in html
    assert html.count('<img class="layer subject"') == 9
    assert html.count('data-copy="0"') == 3
    assert html.count('data-copy="2"') == 3


def test_subject_keyframes_are_windowed():
    layers = {shot["stable_key"]: [_layer()] for shot in SHOTS}
    html = build_composition(
        title="x", width=540, height=960, shots=SHOTS, layers=layers
    )
    assert "@keyframes subj-1-1-0" in html
    assert "translateX(-18%)" in html
    assert "rotate(6deg)" in html or "scale(" in html


def test_background_layers_stay_static():
    layers = {
        SHOTS[0]["stable_key"]: [
            {"role": "background", "mime": "image/svg+xml", "data": "PHN2Zy8+"}
        ]
    }
    html = build_composition(
        title="x", width=540, height=960, shots=[SHOTS[0]], layers=layers
    )
    assert '<img class="layer background"' in html
    assert '<img class="layer subject"' not in html


def test_planner_sets_distinct_subject_actions(controller):
    project = create_project(controller, budget="10.00")
    result = controller.plan_from_text(
        project.id,
        TextBriefRequest(
            text='Make a 10 second rhyme for kids: "One, two, three little ducks, swimming in a row. Quack, quack, quack!"'
        ),
    )
    actions = [shot["requirements"]["subject"]["action"] for shot in result["shots"]]
    assert len(set(actions)) == len(actions)
    assert all(
        shot["requirements"]["subject"]["count"] == 3 for shot in result["shots"]
    )


def test_srt_is_deterministic_and_ordered():
    srt = build_srt(SHOTS)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:04,000\n")
    assert "00:00:04,000 --> 00:00:07,000" in srt
    assert "00:00:07,000 --> 00:00:10,000" in srt
    assert "Quack, quack, quack!" in srt


def test_srt_requires_cues():
    with pytest.raises(ValueError):
        build_srt([])


def test_export_captions_registers_an_srt(controller):
    project = create_project(controller, budget="10.00")
    controller.plan_from_text(
        project.id, TextBriefRequest(text="Make a 10 second rhyme for kids")
    )
    result = controller.export_captions(project.id)
    artifact = result["artifact"]
    assert artifact["relative_path"].endswith(".srt")
    assert artifact["operation"]["source"] == "shot_plan"
    assert controller.verify_artifact(artifact["id"])
    assert any(
        dependency["entity_type"] == "shot" for dependency in artifact["dependencies"]
    )
    assert controller.read_artifact_text(artifact["id"]).startswith("1\n")


class FakeCaptionMux:
    def mux(self, *, video_path, srt_path, output_path):
        content = b"CAPTIONED"
        return CaptionedVideo(
            content=content,
            content_hash=sha256_bytes(content),
            metadata={"subtitle_track": 1},
            command_hash="e" * 64,
            encoder_version="fake/1",
            source_video_hash="f" * 64,
            subtitle_hash="a" * 64,
        )


def test_burn_captions_records_lineage(controller):
    project = create_project(controller, budget="10.00")
    controller.plan_from_text(
        project.id, TextBriefRequest(text="Make a 10 second rhyme for kids")
    )
    srt = controller.export_captions(project.id)["artifact"]
    video = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/silent.mp4",
        content=b"SILENT",
        operation={"type": "composition_render", "duration_seconds": "10"},
    )
    result = controller.burn_captions(
        project.id,
        video_artifact_id=video["id"],
        captions_artifact_id=srt["id"],
        adapter=FakeCaptionMux(),
    )
    burned = result["video"]
    assert burned["kind"] == "render"
    assert burned["relative_path"].startswith("renders/captioned-")
    dependency_ids = {item["entity_id"] for item in burned["dependencies"]}
    assert dependency_ids == {video["id"], srt["id"]}
    assert controller.verify_artifact(burned["id"])


def test_burn_captions_rejects_a_non_srt(controller):
    project = create_project(controller, budget="10.00")
    video = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/silent.mp4",
        content=b"SILENT",
        operation={"type": "composition_render", "duration_seconds": "10"},
    )
    with pytest.raises(IntegrityError, match="SRT"):
        controller.burn_captions(
            project.id,
            video_artifact_id=video["id"],
            captions_artifact_id=video["id"],
            adapter=FakeCaptionMux(),
        )


class FakeHardBurn:
    def burn(self, *, video_path, srt_path, output_path):
        content = b"HARD-BURNED"
        return BurnedVideo(
            content=content,
            content_hash=sha256_bytes(content),
            metadata={"burned_captions": True},
            command_hash="b" * 64,
            encoder_version="fake/1",
            subtitle_hash="c" * 64,
        )


def test_hard_burn_records_the_mode(controller):
    project = create_project(controller, budget="10.00")
    controller.plan_from_text(
        project.id, TextBriefRequest(text="Make a 10 second rhyme for kids")
    )
    srt = controller.export_captions(project.id)["artifact"]
    video = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/silent.mp4",
        content=b"SILENT",
        operation={"type": "composition_render", "duration_seconds": "10"},
    )
    result = controller.burn_captions(
        project.id,
        video_artifact_id=video["id"],
        captions_artifact_id=srt["id"],
        adapter=FakeHardBurn(),
        mode="hard",
    )
    assert result["video"]["operation"]["mode"] == "hard"
    assert result["metadata"]["burned_captions"] is True


def test_unknown_caption_mode_is_refused(controller):
    project = create_project(controller, budget="10.00")
    video = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/silent.mp4",
        content=b"SILENT",
        operation={"type": "composition_render"},
    )
    srt = controller.write_artifact(
        project.id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path="captions/x.srt",
        content=b"1\n00:00:00,000 --> 00:00:01,000\nhi\n",
        operation={"type": "captions"},
    )
    with pytest.raises(IntegrityError, match="soft.*hard"):
        controller.burn_captions(
            project.id,
            video_artifact_id=video["id"],
            captions_artifact_id=srt["id"],
            adapter=FakeHardBurn(),
            mode="both",
        )


def test_subject_path_overrides_the_action_curve():
    shots = [
        {
            "stable_key": "shot-01",
            "duration_seconds": "5",
            "beat": "establish",
            "narrative": "Duck crosses the pond",
            "requirements": {
                "subject": {
                    "action": "swim",
                    "count": 1,
                    "path": [
                        {"t": 0.0, "x": 0, "y": 0},
                        {"t": 0.5, "x": -20, "y": -8},
                        {"t": 1.0, "x": 25, "y": 0},
                    ],
                }
            },
        }
    ]
    layers = {"shot-01": [_layer()]}
    html = build_composition(
        title="x", width=540, height=960, shots=shots, layers=layers
    )
    assert "data-path='" in html
    assert '"x": -20' in html or '"x":-20' in html
    assert "translate(-20%, -8%)" in html
    assert "var track = [{" in html


def test_subject_path_ignores_malformed_points():
    shots = [
        {
            "stable_key": "shot-01",
            "duration_seconds": "5",
            "beat": "establish",
            "narrative": "x",
            "requirements": {
                "subject": {"action": "bob", "path": [{"t": 0.5}, "nonsense"]}
            },
        }
    ]
    html = build_composition(
        title="x", width=540, height=960, shots=shots, layers={"shot-01": [_layer()]}
    )
    assert "data-path=" not in html
    assert 'data-action="bob"' in html


def test_scene_durations_still_sum_to_the_deliverable():
    total = sum(Decimal(shot["duration_seconds"]) for shot in SHOTS)
    assert total == Decimal(10)

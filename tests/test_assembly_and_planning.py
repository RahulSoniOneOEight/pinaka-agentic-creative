from __future__ import annotations

from decimal import Decimal

import pytest

from pinaka.adapters.assembly import AssembledFilm
from pinaka.assembly import Segment, build_plan
from pinaka.enums import ArtifactKind
from pinaka.errors import IntegrityError
from pinaka.models import TextBriefRequest

from .conftest import create_project


def test_cut_plan_concatenates_and_sums_durations():
    plan = build_plan(
        [
            Segment(index=0, path="a.mp4", duration=Decimal(5)),
            Segment(index=1, path="b.mp4", duration=Decimal(3)),
        ]
    )
    assert "concat=n=2:v=1:a=0[outv]" in plan.filter_complex
    assert "scale=1080:1920" in plan.filter_complex
    assert plan.total_seconds == Decimal(8)


def test_dissolve_plan_uses_xfade_with_offsets():
    plan = build_plan(
        [
            Segment(index=0, path="a.mp4", duration=Decimal(5)),
            Segment(index=1, path="b.mp4", duration=Decimal(5)),
        ],
        transition="dissolve",
        dissolve_seconds=Decimal("0.5"),
    )
    assert "xfade=transition=fade:duration=0.5:offset=4.5" in plan.filter_complex
    assert plan.total_seconds == Decimal("9.5")


def test_dissolve_longer_than_the_first_shot_is_refused():
    with pytest.raises(ValueError, match="longer than the first shot"):
        build_plan(
            [
                Segment(index=0, path="a.mp4", duration=Decimal("0.4")),
                Segment(index=1, path="b.mp4", duration=Decimal(5)),
            ],
            transition="dissolve",
            dissolve_seconds=Decimal(1),
        )


def test_unknown_transition_is_refused():
    with pytest.raises(ValueError, match="transition"):
        build_plan(
            [Segment(index=0, path="a.mp4", duration=Decimal(5))],
            transition="wipe",
        )


class FakeAssembly:
    def assemble(
        self,
        *,
        segments,
        output_path,
        transition,
        dissolve_seconds,
        fps,
        width,
        height,
    ):
        assert segments, "segments must be provided"
        content = b"ASSEMBLED-FILM"
        return AssembledFilm(
            content=content,
            content_hash=__import__("hashlib").sha256(content).hexdigest(),
            metadata={"duration_seconds": 10.0, "codec": "h264"},
            command_hash="a" * 64,
            encoder_version="fake/1",
            transition=transition,
            total_seconds=Decimal(10),
        )


def _film_project(controller, clips=None):
    project = create_project(controller, budget="10.00")
    controller.plan_from_text(
        project.id,
        TextBriefRequest(text="Make a 10 second rhyme for kids"),
    )
    shots = controller.repository.list_shots(project.id)
    selected = shots if clips is None else shots[:clips]
    for index, shot in enumerate(selected):
        clip = controller.write_artifact(
            project.id,
            kind=ArtifactKind.GENERATED,
            relative_path=f"artifacts/motion/shot-{index}.mp4",
            content=b"CLIP" + bytes([index]),
            operation={"type": "studio_motion"},
        )
        controller.attach_shot_clip(
            project.id, shot_id=shot["id"], artifact_id=clip["id"]
        )
    return project, shots


def test_assemble_film_trims_and_joins_every_shot(controller):
    project, shots = _film_project(controller)
    result = controller.assemble_film(
        project.id, transition="cut", adapter=FakeAssembly()
    )
    video = result["video"]
    assert video["kind"] == "render"
    assert video["relative_path"].startswith("renders/film-")
    assert video["operation"]["transition"] == "cut"
    assert len(video["operation"]["shots"]) == len(shots)
    assert controller.verify_artifact(video["id"])
    dependencies = {item["entity_id"] for item in video["dependencies"]}
    assert len(dependencies) == len(shots)
    # the first shot's clip has to be among them
    assert all(
        item["shot"] in {shot["stable_key"] for shot in shots}
        for item in result["segments"]
    )


def test_assemble_film_requires_a_clip_for_every_shot(controller):
    project, _shots = _film_project(controller, clips=1)
    with pytest.raises(IntegrityError, match="needs an attached clip"):
        controller.assemble_film(project.id)


class CapturingAssembly(FakeAssembly):
    def __init__(self) -> None:
        self.segments = None

    def assemble(self, *, segments, **kwargs):
        self.segments = segments
        return super().assemble(segments=segments, **kwargs)


def test_assemble_film_rejects_an_unknown_transition(controller):
    project, _ = _film_project(controller)
    with pytest.raises(IntegrityError, match="transition"):
        controller.assemble_film(project.id, transition="wipe")


def test_assemble_film_snaps_cuts_to_a_beat_grid(controller):
    project, shots = _film_project(controller)
    boundaries = []
    cursor = 0.0
    for shot in shots:
        cursor += float(shot["duration_seconds"]) + 0.03
        boundaries.append(cursor)
    adapter = CapturingAssembly()

    result = controller.assemble_film(
        project.id,
        adapter=adapter,
        beat_grid=tuple(boundaries),
        beat_tolerance=0.1,
    )

    assert result["beat_quantization"]["snapped"] == len(shots)
    assert result["video"]["operation"]["beat_quantization"]["snapped"] == len(shots)
    assert [round(float(duration), 2) for _, duration in adapter.segments] == [
        round(value, 2) for value in result["beat_quantization"]["durations"]
    ]


def test_motion_takes_fit_the_remaining_budget(controller):
    project = create_project(controller, budget="10.00")
    plan = controller.plan_motion_takes(
        project.id, family="minimax/h3", seconds=5, resolution="768p"
    )
    assert plan["per_take_minor"] == 30
    assert plan["affordable_takes"] == 3
    assert plan["total_minor"] == 90


def test_motion_takes_report_zero_when_budget_is_spent(controller):
    project = create_project(controller, budget="0.20")
    plan = controller.plan_motion_takes(
        project.id, family="minimax/h3", seconds=5, resolution="768p"
    )
    assert plan["affordable_takes"] == 0


class FakeScript:
    def draft(self, *, prompt, max_words):
        assert "children" in prompt.lower() or "audience" in prompt.lower()
        return "Three little ducks swim in a row. Quack, quack, quack, watch them go."


def test_draft_script_writes_an_artifact(controller):
    project = create_project(controller, budget="10.00")
    controller.plan_from_text(
        project.id, TextBriefRequest(text="Make a 10 second rhyme for kids")
    )
    result = controller.draft_script(project.id, adapter=FakeScript())
    assert result["requires_approval"] is True
    assert result["artifact"]["kind"] == "design"
    assert result["artifact"]["relative_path"].startswith("inputs/script-draft-")
    assert result["artifact"]["operation"]["type"] == "script_draft"
    assert result["words"] == len(result["text"].split())
    assert controller.verify_artifact(result["artifact"]["id"])


def test_draft_script_requires_a_brief(controller):
    project = create_project(controller, budget="10.00")
    with pytest.raises(IntegrityError, match="workable brief"):
        controller.draft_script(project.id, adapter=FakeScript())

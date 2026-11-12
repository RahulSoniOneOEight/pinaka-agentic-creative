from __future__ import annotations

from types import SimpleNamespace

import pytest

from pinaka.finishing import (
    atempo_chain,
    build_grade_filter,
    build_key_filter,
    build_speed_filters,
)
from pinaka.utils import sha256_bytes
from tests.conftest import create_project


def result(payload: bytes, filter_graph: str = "f") -> SimpleNamespace:
    return SimpleNamespace(
        content=payload,
        content_hash=sha256_bytes(payload),
        filter_graph=filter_graph,
        command_hash="c" * 64,
        encoder_version="ffmpeg test",
    )


def video(controller, project_id: str, name: str) -> dict:
    from pinaka.enums import ArtifactKind

    return controller.write_artifact(
        project_id,
        kind=ArtifactKind.GENERATED,
        relative_path=f"renders/{name}.mp4",
        content=name.encode("utf-8"),
        operation={"type": "test_video"},
    )


def test_grade_filter_requires_something_to_apply():
    with pytest.raises(ValueError, match="LUT or at least one"):
        build_grade_filter()
    assert build_grade_filter(lut_filename="grade.cube") == (
        "lut3d=grade.cube,format=yuv420p"
    )


def test_grade_filter_rejects_a_path_in_the_filename():
    with pytest.raises(ValueError, match="bare file name"):
        build_grade_filter(lut_filename="C:/tmp/grade.cube")


def test_key_filter_is_a_composite_graph():
    graph = build_key_filter(key_color="0x00FF00", similarity=0.2, blend=0.05)

    assert "[1:v]chromakey=0x00FF00:0.2:0.05,despill=type=green[fg]" in graph
    assert "[0:v][fg]overlay=shortest=1" in graph
    assert graph.endswith("[outv]")


def test_speed_chains_atempo_outside_its_range():
    assert atempo_chain(1.0) == "atempo=1"
    assert atempo_chain(4.0) == "atempo=2,atempo=2"
    assert atempo_chain(0.25) == "atempo=0.5,atempo=0.5"
    video_filter, audio_filter = build_speed_filters(0.5)
    assert video_filter == "setpts=PTS/0.5"
    assert audio_filter == "atempo=0.5"


def test_speed_is_bounded():
    with pytest.raises(ValueError, match="between 0.25 and 4"):
        build_speed_filters(0.1)


def test_grade_asset_registers_a_derivative(controller):
    project = create_project(controller, budget="10.00")
    artifact = video(controller, project.id, "source")

    graded = controller.grade_asset(
        project.id,
        artifact["id"],
        saturation=1.2,
        adapter=SimpleNamespace(grade=lambda *a, **k: result(b"graded")),
    )

    assert graded["artifact"]["content_hash"] == sha256_bytes(b"graded")
    assert graded["artifact"]["operation"]["type"] == "grade"
    assert graded["artifact"]["operation"]["saturation"] == 1.2
    assert graded["parent_artifact_id"] == artifact["id"]


def test_key_asset_links_both_inputs(controller):
    project = create_project(controller, budget="10.00")
    background = video(controller, project.id, "bg")
    foreground = video(controller, project.id, "fg")

    keyed = controller.key_asset(
        project.id,
        background_artifact_id=background["id"],
        foreground_artifact_id=foreground["id"],
        adapter=SimpleNamespace(key_composite=lambda *a, **k: result(b"keyed")),
    )

    assert keyed["artifact"]["content_hash"] == sha256_bytes(b"keyed")
    assert keyed["parent_artifact_id"] == foreground["id"]
    assert keyed["background_artifact_id"] == background["id"]
    assert any(
        dependency["entity_id"] == background["id"]
        for dependency in keyed["artifact"]["dependencies"]
    )


def test_speed_change_registers_a_derivative(controller):
    project = create_project(controller, budget="10.00")
    artifact = video(controller, project.id, "speed")

    slowed = controller.change_asset_speed(
        project.id,
        artifact["id"],
        speed=0.5,
        adapter=SimpleNamespace(change_speed=lambda *a, **k: result(b"slow")),
    )

    assert slowed["artifact"]["content_hash"] == sha256_bytes(b"slow")
    assert slowed["artifact"]["operation"]["speed"] == 0.5

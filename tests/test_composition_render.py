from __future__ import annotations

from pathlib import Path

import pytest

from pinaka.adapters.encode import EncodedVideo
from pinaka.adapters.frames import CapturedFrame, FrameCaptureResult
from pinaka.errors import IntegrityError, NotFoundError
from pinaka.models import TextBriefRequest
from pinaka.utils import sha256_bytes

from .conftest import create_project

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class FakeCapture:
    profile_key = "fake-capture"

    def __init__(self):
        self.calls = []

    def capture(self, *, html_path, frame_count, fps, width, height):
        self.calls.append(
            {
                "path": Path(html_path),
                "frame_count": frame_count,
                "fps": fps,
                "width": width,
                "height": height,
            }
        )
        frames = tuple(
            CapturedFrame(
                index=index,
                time_seconds=str(index / fps),
                content=PNG,
                content_hash=sha256_bytes(PNG),
            )
            for index in range(frame_count)
        )
        return FrameCaptureResult(
            frames=frames, renderer_version="fake/1", command_hash="c" * 64
        )


class FakeEncoder:
    profile_key = "fake-encoder"

    def __init__(self):
        self.calls = []

    def encode(self, *, frames_dir, frame_count, fps, width, height):
        self.calls.append(
            {
                "frames_on_disk": len(list(Path(frames_dir).glob("frame-*.png"))),
                "frame_count": frame_count,
                "fps": fps,
            }
        )
        content = b"FAKE-MP4" + bytes(frame_count)
        return EncodedVideo(
            content=content,
            content_hash=sha256_bytes(content),
            metadata={"duration_seconds": frame_count / fps, "codec": "h264"},
            command_hash="d" * 64,
            encoder_version="fake-ffmpeg/1",
        )


def _composition_project(controller, *, text="Make a 5 second rhyme for kids"):
    project = create_project(controller, budget="10.00")
    controller.plan_from_text(project.id, TextBriefRequest(text=text))
    authored = controller.author_composition(project.id, width=540, height=960)
    return project, authored


def test_render_composition_produces_a_verified_video(controller):
    project, authored = _composition_project(controller)
    capture, encoder = FakeCapture(), FakeEncoder()
    result = controller.render_composition(
        project.id, fps=10, width=540, height=960, capture=capture, encoder=encoder
    )
    assert result["frames"] == 50
    assert result["fps"] == 10
    assert result["duration_seconds"] == "5"
    assert capture.calls[0]["frame_count"] == 50
    assert encoder.calls[0]["frames_on_disk"] == 50

    video = result["video"]
    assert video["kind"] == "render"
    assert video["relative_path"].startswith("renders/composition-")
    assert controller.verify_artifact(video["id"])
    assert video["operation"]["fps"] == 10
    assert video["operation"]["frames"] == 50
    assert video["operation"]["encoder"] == "fake-encoder"

    dependency = video["dependencies"][0]
    assert dependency["entity_type"] == "artifact"
    assert dependency["entity_id"] == authored["artifact"]["id"]


def test_render_is_idempotent_for_identical_content(controller):
    project, _ = _composition_project(controller)
    first = controller.render_composition(
        project.id, fps=5, capture=FakeCapture(), encoder=FakeEncoder()
    )
    second = controller.render_composition(
        project.id, fps=5, capture=FakeCapture(), encoder=FakeEncoder()
    )
    assert second["video"]["id"] == first["video"]["id"]


def test_render_refuses_an_unknown_composition(controller):
    project = create_project(controller, budget="10.00")
    with pytest.raises(NotFoundError, match="No composition"):
        controller.render_composition(project.id, capture=FakeCapture())


def test_render_rejects_an_invalid_fps(controller):
    project, _ = _composition_project(controller)
    with pytest.raises(IntegrityError, match="fps"):
        controller.render_composition(project.id, fps=0, capture=FakeCapture())


def test_render_refuses_a_runaway_frame_count(controller):
    project, _ = _composition_project(controller)
    with pytest.raises(IntegrityError, match="limit is"):
        controller.render_composition(
            project.id, fps=60, max_frames=10, capture=FakeCapture()
        )


def test_render_rejects_a_tampered_composition(controller):
    project, authored = _composition_project(controller)
    path = controller.artifact_store.resolve(
        project.id, authored["artifact"]["relative_path"]
    )
    path.write_text("<!doctype html><title>x</title>", encoding="utf-8")
    with pytest.raises(IntegrityError, match="validation"):
        controller.render_composition(project.id, capture=FakeCapture())

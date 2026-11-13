from __future__ import annotations

import cv2
import numpy as np

from pinaka.enums import ArtifactKind

from .conftest import create_project


def test_review_packet_builds_contact_sheet_and_evidence(controller, tmp_path):
    project = create_project(controller)
    path = tmp_path / "review.mp4"
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 24.0, (1080, 1920)
    )
    rng = np.random.default_rng(3)
    for _ in range(24):
        writer.write(rng.integers(40, 190, (1920, 1080, 3), dtype=np.uint8))
    writer.release()
    artifact = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="renders/review.mp4",
        content=path.read_bytes(),
        operation={"type": "test_render", "duration_seconds": 1.0},
    )
    packet = controller.build_review_packet(project.id, artifact_id=artifact["id"])
    assert packet["packet"]["artifact"]["hash"] == artifact["content_hash"]
    assert packet["packet"]["video"]["frames"] == 24
    assert packet["contact_sheet"]["relative_path"].endswith(".jpg")
    assert packet["artifact"]["relative_path"].endswith(".json")
    assert (
        packet["packet"]["contact_sheet_artifact_id"] == packet["contact_sheet"]["id"]
    )

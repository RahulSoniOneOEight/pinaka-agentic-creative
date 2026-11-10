from __future__ import annotations

import json

from pinaka.enums import ArtifactKind
from pinaka.reconciliation import reconcile_project

from .conftest import create_project


def test_reconcile_clean_project(controller):
    project = create_project(controller)
    controller.write_artifact(
        project.id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path="artifacts/note.txt",
        content=b"clean",
        operation={"type": "test"},
    )

    report = reconcile_project(controller, project.id)

    assert report["status"] == "clean"
    assert report["counts"]["registered_artifacts"] == 1
    assert report["orphaned_files"] == []
    assert report["missing_artifacts"] == []
    assert report["hash_mismatches"] == []


def test_reconcile_reports_orphaned_file(controller):
    project = create_project(controller)
    controller.artifact_store.write_bytes(project.id, "artifacts/orphan.txt", b"orphan")

    report = reconcile_project(controller, project.id)

    assert report["status"] == "issues"
    assert "artifacts/orphan.txt" in report["orphaned_files"]


def test_reconcile_reports_missing_and_mismatched_artifacts(controller):
    project = create_project(controller)
    missing = controller.write_artifact(
        project.id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path="artifacts/missing.txt",
        content=b"missing",
        operation={"type": "test"},
    )
    tampered = controller.write_artifact(
        project.id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path="artifacts/tampered.txt",
        content=b"original",
        operation={"type": "test"},
    )
    root = controller.artifact_store.project_root(project.id)
    (root / missing["relative_path"]).unlink()
    (root / tampered["relative_path"]).write_bytes(b"changed")

    report = reconcile_project(controller, project.id)

    assert report["status"] == "issues"
    assert [item["relative_path"] for item in report["missing_artifacts"]] == [
        "artifacts/missing.txt"
    ]
    assert [item["relative_path"] for item in report["hash_mismatches"]] == [
        "artifacts/tampered.txt"
    ]


def test_reconcile_recognizes_frame_manifest_coverage(controller):
    project = create_project(controller)
    frame_path = "renders/frames-test/frame-0000.png"
    controller.artifact_store.write_bytes(project.id, frame_path, b"\x89PNG\r\n\x1a\n")
    manifest = {
        "schema_version": 1,
        "frames": [
            {
                "index": 0,
                "relative_path": frame_path,
                "content_hash": "0" * 64,
            }
        ],
    }
    controller.write_artifact(
        project.id,
        kind=ArtifactKind.PREVIEW,
        relative_path="renders/frame-manifest-test.json",
        content=json.dumps(manifest).encode(),
        operation={"type": "test_manifest"},
    )

    report = reconcile_project(controller, project.id)

    assert report["counts"]["manifest_covered_files"] == 1
    assert frame_path not in report["orphaned_files"]
    assert report["status"] == "clean"

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pinaka.backup import create_backup, verify_backup
from pinaka.enums import ArtifactKind

from .conftest import create_project


def test_backup_and_verify_round_trip(controller, tmp_path):
    project = create_project(controller)
    controller.write_artifact(
        project.id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path="artifacts/backup-note.txt",
        content=b"backup me",
        operation={"type": "test"},
    )
    home = Path(controller.artifact_store.root)
    destination = tmp_path / "runtime-backup"

    manifest = create_backup(home, destination)
    report = verify_backup(destination)

    assert manifest["items"]
    assert report["status"] == "pass"
    assert report["database_integrity"] == "ok"
    assert report["schema_version"] >= 2
    assert report["missing"] == []
    assert report["mismatched"] == []


def test_backup_detects_tampering(controller, tmp_path):
    project = create_project(controller)
    controller.write_artifact(
        project.id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path="artifacts/backup-note.txt",
        content=b"backup me",
        operation={"type": "test"},
    )
    home = Path(controller.artifact_store.root)
    destination = tmp_path / "runtime-backup"
    create_backup(home, destination)
    target = next(
        path
        for path in destination.rglob("*")
        if path.is_file() and path.name.endswith(".txt")
    )
    target.write_bytes(b"tampered")

    report = verify_backup(destination)

    assert report["status"] == "fail"
    assert report["mismatched"]


def test_backup_refuses_existing_destination(controller, tmp_path):
    home = Path(controller.artifact_store.root)
    destination = tmp_path / "runtime-backup"
    destination.mkdir()

    with pytest.raises(FileExistsError):
        create_backup(home, destination)


def test_restore_into_empty_home_verifies(controller, tmp_path):
    from pinaka.backup import restore_backup

    project = create_project(controller)
    controller.write_artifact(
        project.id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path="artifacts/restore-note.txt",
        content=b"restore me",
        operation={"type": "test"},
    )
    home = Path(controller.artifact_store.root)
    backup = tmp_path / "runtime-backup"
    create_backup(home, backup)

    report = restore_backup(backup, tmp_path / "restored")

    assert report["status"] == "pass"
    assert report["database_integrity"] == "ok"
    assert report["schema_version"] >= 2
    assert (tmp_path / "restored" / "state" / "pinaka.sqlite").is_file()
    assert (
        tmp_path
        / "restored"
        / "projects"
        / project.id
        / "artifacts"
        / "restore-note.txt"
    ).read_bytes() == b"restore me"


def test_restore_refuses_non_empty_target(controller, tmp_path):
    import pytest

    from pinaka.backup import restore_backup

    home = Path(controller.artifact_store.root)
    backup = tmp_path / "runtime-backup"
    create_backup(home, backup)
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "keep.txt").write_text("do not overwrite", encoding="utf-8")

    with pytest.raises(FileExistsError):
        restore_backup(backup, target)
    assert (target / "keep.txt").read_text(encoding="utf-8") == "do not overwrite"


def test_restore_drill_verifies_and_cleans_up(controller, tmp_path):
    from pinaka.backup import drill_restore

    home = Path(controller.artifact_store.root)
    backup = tmp_path / "runtime-backup"
    create_backup(home, backup)

    report = drill_restore(backup, workdir=tmp_path / "drills")

    assert report["drill"] is True
    assert report["status"] == "pass"
    assert report["database_integrity"] == "ok"
    assert report["restored_to"] is None
    assert not any((tmp_path / "drills").glob("pinaka-restore-drill-*"))


def test_mirror_backup_copies_and_verifies(controller, tmp_path):
    from pinaka.backup import mirror_backup

    project = create_project(controller)
    controller.write_artifact(
        project.id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path="artifacts/mirror-note.txt",
        content=b"mirror me",
        operation={"type": "test"},
    )
    home = Path(controller.artifact_store.root)
    backup = tmp_path / "runtime-backup"
    create_backup(home, backup)

    report = mirror_backup(backup, tmp_path / "offsite-copy")

    assert report["status"] == "pass"
    assert report["database_integrity"] == "ok"
    assert report["items"] >= 2
    mirror_manifest = json.loads(
        (tmp_path / "offsite-copy" / "mirror-manifest.json").read_text(encoding="utf-8")
    )
    assert mirror_manifest["source_manifest_sha256"] == report["source_manifest_sha256"]
    assert mirror_manifest["status"] == "pass"
    assert (
        tmp_path
        / "offsite-copy"
        / "projects"
        / project.id
        / "artifacts"
        / "mirror-note.txt"
    ).read_bytes() == b"mirror me"


def test_mirror_backup_refuses_non_empty_destination(controller, tmp_path):
    import pytest

    from pinaka.backup import mirror_backup

    home = Path(controller.artifact_store.root)
    backup = tmp_path / "runtime-backup"
    create_backup(home, backup)
    destination = tmp_path / "offsite-copy"
    destination.mkdir()
    (destination / "keep.txt").write_text("do not overwrite", encoding="utf-8")

    with pytest.raises(FileExistsError):
        mirror_backup(backup, destination)
    assert (destination / "keep.txt").read_text(encoding="utf-8") == "do not overwrite"


def test_mirror_backup_refuses_destination_inside_source(controller, tmp_path):
    import pytest

    from pinaka.backup import mirror_backup

    home = Path(controller.artifact_store.root)
    backup = tmp_path / "runtime-backup"
    create_backup(home, backup)

    with pytest.raises(ValueError, match="outside"):
        mirror_backup(backup, backup / "nested")


def test_mirror_can_be_restored(controller, tmp_path):
    from pinaka.backup import mirror_backup, restore_backup

    home = Path(controller.artifact_store.root)
    backup = tmp_path / "runtime-backup"
    create_backup(home, backup)
    mirror_backup(backup, tmp_path / "offsite-copy")

    report = restore_backup(
        tmp_path / "offsite-copy", tmp_path / "restored-from-mirror"
    )

    assert report["status"] == "pass"
    assert report["database_integrity"] == "ok"
    assert report["schema_version"] >= 2

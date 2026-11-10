from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .errors import IntegrityError
from .utils import sha256_file, utc_now


def _hash_tree(root: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        items.append(
            {
                "relative_path": path.relative_to(root).as_posix(),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return items


def create_backup(home: Path | str, output: Path | str) -> dict[str, Any]:
    source_home = Path(home).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    database = source_home / "state" / "pinaka.sqlite"
    if not database.is_file():
        raise FileNotFoundError(f"Runtime database not found: {database}")
    if destination.exists():
        raise FileExistsError(f"Backup destination already exists: {destination}")
    destination.mkdir(parents=True)

    database_target = destination / "state" / "pinaka.sqlite"
    database_target.parent.mkdir(parents=True)
    source_connection = sqlite3.connect(database)
    try:
        target_connection = sqlite3.connect(database_target)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
    finally:
        source_connection.close()

    projects = source_home / "projects"
    if projects.is_dir():
        shutil.copytree(projects, destination / "projects")

    items = _hash_tree(destination)
    manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "source_home": str(source_home),
        "items": items,
        "notes": [
            "Runtime backup containing the SQLite database and project artifacts.",
            "Restore by copying state/ and projects/ into an empty runtime home.",
        ],
    }
    manifest_path = destination / "backup-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    manifest["manifest"] = str(manifest_path)
    return manifest


def verify_backup(backup: Path | str) -> dict[str, Any]:
    root = Path(backup).expanduser().resolve()
    manifest_path = root / "backup-manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Backup manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    missing: list[str] = []
    mismatched: list[str] = []
    for item in manifest.get("items", []):
        path = root / item["relative_path"]
        if not path.is_file():
            missing.append(item["relative_path"])
            continue
        if sha256_file(path) != item["sha256"]:
            mismatched.append(item["relative_path"])
    database = root / "state" / "pinaka.sqlite"
    integrity = "missing"
    schema_version = None
    if database.is_file():
        connection = sqlite3.connect(database)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        finally:
            connection.close()
    ok = not missing and not mismatched and integrity == "ok"
    return {
        "backup": str(root),
        "status": "pass" if ok else "fail",
        "items": len(manifest.get("items", [])),
        "missing": missing,
        "mismatched": mismatched,
        "database_integrity": integrity,
        "schema_version": schema_version,
    }


def _verify_files(
    root: Path, items: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    mismatched: list[str] = []
    for item in items:
        path = root / item["relative_path"]
        if not path.is_file():
            missing.append(item["relative_path"])
            continue
        if sha256_file(path) != item["sha256"]:
            mismatched.append(item["relative_path"])
    return missing, mismatched


def restore_backup(backup: Path | str, target_home: Path | str) -> dict[str, Any]:
    """Restore a verified backup into an empty runtime home.

    Refuses to overwrite an existing non-empty target so a live runtime cannot be
    damaged by an accidental restore. Verification is repeated after the copy.
    """

    root = Path(backup).expanduser().resolve()
    report = verify_backup(root)
    if report["status"] != "pass":
        raise IntegrityError(
            "Backup verification failed; refusing to restore "
            f"({len(report['missing'])} missing, {len(report['mismatched'])} mismatched)"
        )
    target = Path(target_home).expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"Restore target is not empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root / "state", target / "state", dirs_exist_ok=True)
    if (root / "projects").is_dir():
        shutil.copytree(root / "projects", target / "projects", dirs_exist_ok=True)
    manifest = json.loads((root / "backup-manifest.json").read_text(encoding="utf-8"))
    missing, mismatched = _verify_files(target, manifest.get("items", []))
    database = target / "state" / "pinaka.sqlite"
    integrity = "missing"
    schema_version = None
    if database.is_file():
        connection = sqlite3.connect(database)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        finally:
            connection.close()
    ok = not missing and not mismatched and integrity == "ok"
    return {
        "backup": str(root),
        "restored_to": str(target),
        "status": "pass" if ok else "fail",
        "items": report["items"],
        "missing": missing,
        "mismatched": mismatched,
        "database_integrity": integrity,
        "schema_version": schema_version,
    }


def drill_restore(
    backup: Path | str, *, workdir: Path | str | None = None
) -> dict[str, Any]:
    """Restore into a throwaway directory and verify, without touching live state."""

    parent = Path(workdir).expanduser().resolve() if workdir else None
    if parent:
        parent.mkdir(parents=True, exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix="pinaka-restore-drill-", dir=parent))
    report = restore_backup(backup, target)
    report["drill"] = True
    if report["status"] == "pass":
        shutil.rmtree(target, ignore_errors=True)
        report["restored_to"] = None
    else:
        report["retained_for_inspection"] = str(target)
    return report


def mirror_backup(backup: Path | str, destination: Path | str) -> dict[str, Any]:
    """Copy a verified backup to a second location and verify the copy.

    This is the offsite-copy primitive. It never deletes or mutates the source
    backup and refuses a non-empty destination or a destination inside the source.
    """

    root = Path(backup).expanduser().resolve()
    report = verify_backup(root)
    if report["status"] != "pass":
        raise IntegrityError(
            "Backup verification failed; refusing to mirror "
            f"({len(report['missing'])} missing, {len(report['mismatched'])} mismatched)"
        )
    target = Path(destination).expanduser().resolve()
    if target == root or root in target.parents:
        raise ValueError("Mirror destination must be outside the source backup")
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"Mirror destination is not empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root / "state", target / "state", dirs_exist_ok=True)
    if (root / "projects").is_dir():
        shutil.copytree(root / "projects", target / "projects", dirs_exist_ok=True)
    shutil.copy2(root / "backup-manifest.json", target / "backup-manifest.json")
    manifest = json.loads((root / "backup-manifest.json").read_text(encoding="utf-8"))
    missing, mismatched = _verify_files(target, manifest.get("items", []))
    database = target / "state" / "pinaka.sqlite"
    integrity = "missing"
    schema_version = None
    if database.is_file():
        connection = sqlite3.connect(database)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        finally:
            connection.close()
    status = "pass" if not missing and not mismatched and integrity == "ok" else "fail"
    source_hash = sha256_file(root / "backup-manifest.json")
    mirror_manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "source_backup": str(root),
        "destination": str(target),
        "source_manifest_sha256": source_hash,
        "items": _hash_tree(target),
        "status": status,
    }
    mirror_path = target / "mirror-manifest.json"
    mirror_path.write_text(
        json.dumps(mirror_manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {
        "source": str(root),
        "destination": str(target),
        "status": status,
        "items": report["items"],
        "missing": missing,
        "mismatched": mismatched,
        "database_integrity": integrity,
        "schema_version": schema_version,
        "source_manifest_sha256": source_hash,
        "mirror_manifest": str(mirror_path),
    }

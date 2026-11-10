from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .controller import CreativeController
from .utils import sha256_file, utc_now

IGNORED_FILENAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


def _manifest_covered_files(
    controller: CreativeController, artifacts: list[dict[str, Any]]
) -> set[str]:
    covered: set[str] = set()
    for artifact in artifacts:
        if not artifact["relative_path"].startswith("renders/frame-manifest-"):
            continue
        try:
            manifest = json.loads(controller.read_artifact_text(artifact["id"]))
        except (OSError, ValueError):
            continue
        for frame in manifest.get("frames", []):
            relative_path = frame.get("relative_path")
            if isinstance(relative_path, str) and relative_path:
                covered.add(relative_path)
    return covered


def reconcile_project(
    controller: CreativeController, project_id: str
) -> dict[str, Any]:
    project = controller.repository.get_project(project_id)
    artifacts = controller.repository.list_artifacts(project_id)
    root = controller.artifact_store.project_root(project_id)
    registered = {artifact["relative_path"]: artifact for artifact in artifacts}
    covered = _manifest_covered_files(controller, artifacts)

    files: set[str] = set()
    if root.is_dir():
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.name in IGNORED_FILENAMES:
                continue
            files.add(path.relative_to(root).as_posix())

    missing_artifacts: list[dict[str, Any]] = []
    hash_mismatches: list[dict[str, Any]] = []
    for artifact in artifacts:
        path = root / artifact["relative_path"]
        if not path.is_file():
            missing_artifacts.append(
                {
                    "id": artifact["id"],
                    "relative_path": artifact["relative_path"],
                    "content_hash": artifact["content_hash"],
                }
            )
            continue
        observed_hash = sha256_file(path)
        if observed_hash != artifact["content_hash"]:
            hash_mismatches.append(
                {
                    "id": artifact["id"],
                    "relative_path": artifact["relative_path"],
                    "expected_hash": artifact["content_hash"],
                    "observed_hash": observed_hash,
                }
            )

    orphaned_files = sorted(
        path for path in files if path not in registered and path not in covered
    )

    recoverable_jobs = [
        job.model_dump(mode="json")
        for job in controller.repository.recoverable_jobs(project_id)
    ]
    unresolved_operations = [
        operation
        for operation in controller.repository.list_tool_operations(project_id)
        if operation["status"] in {"authorized", "running", "outcome_unknown"}
    ]
    ledger = controller.repository.budget_ledger(project_id)

    issues = bool(
        missing_artifacts
        or hash_mismatches
        or orphaned_files
        or recoverable_jobs
        or unresolved_operations
        or project.reserved.minor
    )
    return {
        "schema_version": 1,
        "checked_at": utc_now(),
        "project_id": project_id,
        "project_status": project.status.value,
        "status": "issues" if issues else "clean",
        "counts": {
            "registered_artifacts": len(artifacts),
            "files_on_disk": len(files),
            "manifest_covered_files": len(covered),
            "missing_artifacts": len(missing_artifacts),
            "hash_mismatches": len(hash_mismatches),
            "orphaned_files": len(orphaned_files),
            "recoverable_jobs": len(recoverable_jobs),
            "unresolved_operations": len(unresolved_operations),
        },
        "missing_artifacts": missing_artifacts,
        "hash_mismatches": hash_mismatches,
        "orphaned_files": orphaned_files,
        "recoverable_jobs": recoverable_jobs,
        "unresolved_operations": unresolved_operations,
        "budget": {
            "reserved": project.reserved.model_dump(mode="json"),
            "spent": project.spent.model_dump(mode="json"),
            "ledger_entries": len(ledger),
        },
    }


def write_report(report: dict[str, Any], path: Path | str) -> Path:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return destination

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .utils import sha256_file, utc_now


def build_delivery_packet(
    *,
    master: Path | str,
    captioned: Path | str,
    captions: Path | str,
    formats_manifest: Path | str,
    metadata: Path | str,
    output_dir: Path | str,
    formats_dir: Path | str | None = None,
    credits: Path | str | None = None,
) -> dict[str, Any]:
    sources = {
        "master.mp4": Path(master).expanduser().resolve(),
        "captioned.mp4": Path(captioned).expanduser().resolve(),
        "captions.srt": Path(captions).expanduser().resolve(),
        "formats-manifest.json": Path(formats_manifest).expanduser().resolve(),
        "metadata.json": Path(metadata).expanduser().resolve(),
    }
    if credits is not None:
        sources["credits.json"] = Path(credits).expanduser().resolve()
    missing = [str(path) for path in sources.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Delivery inputs are missing: {missing}")
    format_files: list[Path] = []
    if formats_dir is not None:
        format_root = Path(formats_dir).expanduser().resolve()
        if not format_root.is_dir():
            raise FileNotFoundError(f"Formats directory not found: {format_root}")
        format_files = sorted(format_root.glob("*.mp4"))
        if not format_files:
            raise FileNotFoundError(
                f"No MP4 derivatives found in formats directory: {format_root}"
            )
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"Delivery directory already exists: {destination}")
    destination.mkdir(parents=True)

    items: list[dict[str, Any]] = []
    for name, source in sources.items():
        target = destination / name
        shutil.copy2(source, target)
        items.append(
            {
                "name": name,
                "bytes": target.stat().st_size,
                "sha256": sha256_file(target),
            }
        )
    if format_files:
        format_target_dir = destination / "formats"
        format_target_dir.mkdir()
        for source in format_files:
            target = format_target_dir / source.name
            shutil.copy2(source, target)
            items.append(
                {
                    "name": f"formats/{source.name}",
                    "bytes": target.stat().st_size,
                    "sha256": sha256_file(target),
                }
            )

    manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "output_dir": str(destination),
        "publishing_authorized": False,
        "items": items,
        "notes": [
            "Publication-ready packet only; it does not authorize publishing.",
            "The archived master remains the approved source of record.",
            "The captioned file is a review derivative until separately approved by exact hash.",
            "Format derivatives use a blurred crop of the approved master as their background.",
        ],
    }
    manifest_path = destination / "delivery-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    manifest["manifest"] = str(manifest_path)
    return manifest


def build_ab_delivery_packet(
    *,
    project_id: str,
    experiment: dict[str, Any],
    variants: list[dict[str, Any]],
    output_dir: Path | str,
    shared_files: dict[str, Path | str] | None = None,
) -> dict[str, Any]:
    """Package A/ and B/ with an experiment manifest and metrics plan."""

    if {item["label"] for item in variants} != {"A", "B"}:
        raise ValueError("An A/B delivery packet needs exactly one A and one B")
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f"A/B delivery directory already exists: {destination}")
    destination.mkdir(parents=True)
    items: list[dict[str, Any]] = []
    variant_rows = []
    for variant in variants:
        label = variant["label"]
        source = Path(str(variant["source"])).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Variant {label} source is missing: {source}")
        folder = destination / label
        folder.mkdir()
        target = folder / source.name
        shutil.copy2(source, target)
        copied_hash = sha256_file(target)
        if copied_hash != variant["artifact_hash"]:
            raise ValueError(
                f"Variant {label} hash mismatch after copy: "
                f"{copied_hash} != {variant['artifact_hash']}"
            )
        items.append(
            {
                "name": f"{label}/{source.name}",
                "bytes": target.stat().st_size,
                "sha256": copied_hash,
            }
        )
        variant_rows.append(
            {
                "label": label,
                "artifact_id": variant["artifact_id"],
                "artifact_hash": variant["artifact_hash"],
                "path": f"{label}/{source.name}",
                "bytes": target.stat().st_size,
                "sha256": copied_hash,
                "notes": variant.get("notes"),
            }
        )
    for name, source in (shared_files or {}).items():
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"Shared delivery file is missing: {source_path}")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)
        items.append(
            {
                "name": name,
                "bytes": target.stat().st_size,
                "sha256": sha256_file(target),
            }
        )

    created_at = utc_now()
    variant_a = next(item for item in variant_rows if item["label"] == "A")
    experiment_manifest = {
        "schema": "ab-experiment-manifest/v1",
        "created_at": created_at,
        "project_id": project_id,
        "experiment": {
            "id": experiment["id"],
            "axis": experiment["axis"],
            "hypothesis": experiment["hypothesis"],
            "primary_metric": experiment["primary_metric"],
            "traffic_split": experiment["traffic_split"],
            "sample_plan": experiment["sample_plan"],
            "status": experiment["status"],
        },
        "variants": variant_rows,
        "axis_constant": "Everything except the declared axis is held constant",
        "held_back": None,
    }
    metrics_plan = {
        "schema": "ab-metrics-plan/v1",
        "experiment_id": experiment["id"],
        "primary_metric": experiment["primary_metric"],
        "traffic_split": experiment["traffic_split"],
        "sample_plan": experiment["sample_plan"],
        "control": "A",
        "variants": ["A", "B"],
        "minimum_sample_per_variant": None,
        "decision_rule": (
            "Compare the primary metric after the declared sample is reached; "
            "do not promote a variant on a partial sample."
        ),
        "status": "planned",
    }
    manifest = {
        "schema_version": 2,
        "packet_type": "ab_delivery",
        "created_at": created_at,
        "project_id": project_id,
        "experiment_id": experiment["id"],
        "publishing_authorized": False,
        "control": variant_a["label"],
        "variants": [item["label"] for item in variant_rows],
        "items": items,
        "notes": [
            "A/B delivery packet only; it does not authorize publication.",
            "Each variant is hash-verified against its bound artifact.",
        ],
    }
    (destination / "experiment.json").write_text(
        json.dumps(experiment_manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (destination / "metrics-plan.json").write_text(
        json.dumps(metrics_plan, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    manifest_path = destination / "delivery-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "experiment_manifest": experiment_manifest,
        "metrics_plan": metrics_plan,
        "output_dir": str(destination),
        "items": items,
    }

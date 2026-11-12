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

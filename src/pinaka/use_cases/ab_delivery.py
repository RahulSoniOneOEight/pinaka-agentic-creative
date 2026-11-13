from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..delivery import build_ab_delivery_packet
from ..enums import ArtifactKind
from ..errors import ConflictError, IntegrityError
from ..utils import sha256_bytes


class ABDeliveryUseCases:
    """Package both A/B variants with their experiment and metrics plan."""

    def build_ab_delivery_package(
        self,
        project_id: str,
        output_dir: str,
        *,
        experiment_id: str | None = None,
    ) -> dict[str, Any]:
        status = self.ab_status(project_id)
        experiments = [
            item
            for item in status["experiments"]
            if experiment_id is None or item["id"] == experiment_id
        ]
        if not experiments:
            raise ConflictError("No A/B experiment matches the delivery request")
        if len(experiments) > 1:
            raise ConflictError(
                "More than one A/B experiment exists; name experiment_id explicitly"
            )
        experiment = experiments[0]
        if not experiment["complete"]:
            raise ConflictError("A/B delivery needs both A and B bound")
        variants = []
        for variant in experiment["variants"]:
            artifact = self.repository.get_artifact(variant["artifact_id"])
            if artifact["project_id"] != project_id:
                raise IntegrityError("A/B variant belongs to a different project")
            if artifact["status"] != "current":
                raise IntegrityError(
                    f"Variant {variant['label']} artifact is not current"
                )
            if artifact["content_hash"] != variant["artifact_hash"]:
                raise IntegrityError(
                    f"Variant {variant['label']} hash does not match its binding"
                )
            source = self.artifact_store.resolve(project_id, artifact["relative_path"])
            variants.append(
                {
                    "label": variant["label"],
                    "artifact_id": artifact["id"],
                    "artifact_hash": artifact["content_hash"],
                    "source": source,
                    "notes": variant.get("notes"),
                }
            )
        shared: dict[str, Path | str] = {}
        metadata = self.repository.list_artifacts(project_id)
        credits = [
            artifact
            for artifact in metadata
            if artifact["relative_path"].startswith("delivery/CREDITS-")
        ]
        if credits:
            shared["credits.txt"] = self.artifact_store.resolve(
                project_id, credits[-1]["relative_path"]
            )
        packet = build_ab_delivery_packet(
            project_id=project_id,
            experiment=experiment,
            variants=variants,
            output_dir=output_dir,
            shared_files=shared,
        )
        manifest_bytes = json.dumps(
            packet["manifest"], indent=2, ensure_ascii=False
        ).encode("utf-8")
        manifest_hash = sha256_bytes(manifest_bytes)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.REFERENCE,
            relative_path=f"delivery/AB-MANIFEST-{manifest_hash[:12]}.json",
            content=manifest_bytes,
            operation={
                "type": "ab_delivery_manifest",
                "experiment_id": experiment["id"],
                "manifest_hash": manifest_hash,
                "variants": [item["label"] for item in variants],
                "output_dir": str(Path(output_dir).expanduser().resolve()),
            },
        )
        record = self.repository.record_ab_delivery(
            project_id,
            experiment_id=experiment["id"],
            output_dir=str(Path(output_dir).expanduser().resolve()),
            manifest_artifact_id=artifact["id"],
            manifest_hash=manifest_hash,
            manifest=packet["manifest"],
        )
        return {
            "delivery": record,
            "artifact": artifact,
            "packet": packet,
            "experiment": experiment,
        }

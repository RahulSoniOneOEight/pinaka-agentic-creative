from __future__ import annotations

from pathlib import Path
from typing import Any

from ..assets import (
    AssetCriteria,
    evaluate_treatment,
    verdict_binds_asset,
    verdict_modifies_bytes,
)
from ..enums import (
    ArtifactKind,
    AssetEligibilityStatus,
)
from ..errors import (
    ConflictError,
    IntegrityError,
    RightsEligibilityError,
)
from ..evidence import capture_document
from ..models import (
    ArtifactCreate,
    AssetCandidate,
    AssetEligibilityResult,
    AssetRightsRecord,
    AssetUseContext,
)
from ..sourcing import clear_resolved_uncertainty, evaluate_asset_candidate
from ..utils import sha256_bytes, sha256_json


class SourcingUseCases:
    def evaluate_asset_candidate(
        self, candidate: AssetCandidate, context: AssetUseContext
    ) -> AssetEligibilityResult:
        return evaluate_asset_candidate(candidate, context)

    def import_sourced_original(
        self,
        project_id: str,
        source: Path | str,
        *,
        candidate: AssetCandidate,
        context: AssetUseContext,
    ) -> dict[str, Any]:
        """Import only rights-eligible stock and retain its complete source manifest."""

        self.repository.get_project(project_id)
        eligibility = self.evaluate_asset_candidate(candidate, context)
        if eligibility.status != AssetEligibilityStatus.ELIGIBLE:
            reasons = (*eligibility.blocking_reasons, *eligibility.review_reasons)
            raise RightsEligibilityError(
                f"Asset is {eligibility.status.value}: {'; '.join(reasons)}"
            )

        stored = self.artifact_store.import_original(project_id, source)
        manifest = {
            "candidate": candidate.model_dump(mode="json"),
            "use_context": context.model_dump(mode="json"),
        }
        operation = {
            "type": "sourced_import",
            "size_bytes": stored.size_bytes,
            "source": manifest["candidate"]["source"],
            "rights": manifest["candidate"]["rights"],
            "provider_compliance": manifest["candidate"]["compliance"],
            "use_context": manifest["use_context"],
            "eligibility": eligibility.model_dump(mode="json"),
            "source_manifest_hash": sha256_json(manifest),
        }

        existing = self.repository.artifact_by_path(project_id, stored.relative_path)
        if existing:
            if (
                existing["content_hash"] != stored.content_hash
                or not existing["immutable"]
                or existing["operation"] != operation
            ):
                raise IntegrityError(
                    f"Sourced original provenance mismatch: {stored.relative_path}"
                )
            return existing

        return self.repository.register_artifact(
            project_id,
            ArtifactCreate(
                kind=ArtifactKind.ORIGINAL,
                relative_path=stored.relative_path,
                content_hash=stored.content_hash,
                operation=operation,
                immutable=True,
            ),
        )

    def capture_asset_evidence(
        self,
        project_id: str,
        candidate: AssetCandidate,
        *,
        fetcher: Any | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Fetch and store the item and licence pages, then mark the licence verified."""

        self.repository.get_project(project_id)
        provider = candidate.source.provider.value
        asset_key = "".join(
            char if char.isalnum() or char in "-_" else "-"
            for char in candidate.source.provider_asset_id
        )[:60]
        pages = []
        if candidate.source.source_page_url is not None:
            pages.append(("item_page", str(candidate.source.source_page_url)))
        pages.append(("licence_page", str(candidate.rights.license_url)))
        documents: list[dict[str, Any]] = []
        for kind, url in pages:
            document = capture_document(kind, url, fetcher=fetcher, timeout=timeout)
            evidence = document.evidence
            relative = (
                f"references/licence/{provider}-{asset_key}-{kind}-"
                f"{evidence.content_hash[:12]}.txt"
            )
            artifact = self.write_artifact(
                project_id,
                kind=ArtifactKind.REFERENCE,
                relative_path=relative,
                content=document.content,
                operation={
                    "type": "licence_evidence",
                    "kind": kind,
                    "url": url,
                    "content_type": evidence.content_type,
                    "fetched_at": evidence.fetched_at,
                },
            )
            documents.append(
                {
                    "kind": kind,
                    "url": url,
                    "artifact_id": artifact["id"],
                    "relative_path": artifact["relative_path"],
                    "content_hash": evidence.content_hash,
                    "content_type": evidence.content_type,
                    "fetched_at": evidence.fetched_at,
                }
            )

        licence = next(item for item in documents if item["kind"] == "licence_page")
        rights = candidate.rights.model_copy(
            update={
                "license_evidence_ref": licence["relative_path"],
                "license_evidence_hash": licence["content_hash"],
                "origin_license_verified": True,
                "rights_uncertainty": clear_resolved_uncertainty(
                    candidate.rights.rights_uncertainty
                ),
            }
        )
        updated = candidate.model_copy(update={"rights": rights})
        return {
            "candidate": updated,
            "evidence": documents,
            "evidence_hash": sha256_json(documents),
        }

    def import_stock_asset(
        self,
        project_id: str,
        *,
        candidate: AssetCandidate,
        context: AssetUseContext | None = None,
        content: bytes,
        filename: str,
    ) -> dict[str, Any]:
        """Register verified stock bytes as an immutable sourced original."""

        self.repository.get_project(project_id)
        context = self._enforce_context(project_id, context)
        if not content:
            raise IntegrityError("Downloaded asset is empty")
        if not candidate.rights.origin_license_verified:
            raise RightsEligibilityError(
                "Licence evidence must be captured before a stock asset is imported"
            )
        eligibility = self.evaluate_asset_candidate(candidate, context)
        if eligibility.status != AssetEligibilityStatus.ELIGIBLE:
            reasons = (*eligibility.blocking_reasons, *eligibility.review_reasons)
            raise RightsEligibilityError(
                f"Asset is {eligibility.status.value}: {'; '.join(reasons)}"
            )

        content_hash = sha256_bytes(content)
        safe_name = (
            "".join(
                char if char.isalnum() or char in "._-" else "-" for char in filename
            ).strip(".-")
            or "asset.bin"
        )
        relative = f"assets/originals/{content_hash[:12]}-{safe_name}"
        stored = self.artifact_store.write_bytes(project_id, relative, content)

        operation = {
            "type": "sourced_download",
            "size_bytes": stored.size_bytes,
            "source": candidate.source.model_dump(mode="json"),
            "rights": candidate.rights.model_dump(mode="json"),
            "provider_compliance": candidate.compliance.model_dump(mode="json"),
            "use_context": context.model_dump(mode="json"),
            "eligibility": eligibility.model_dump(mode="json"),
            "source_manifest_hash": sha256_json(
                {
                    "candidate": candidate.model_dump(mode="json"),
                    "use_context": context.model_dump(mode="json"),
                }
            ),
        }
        existing = self.repository.artifact_by_path(project_id, stored.relative_path)
        if existing:
            if (
                existing["content_hash"] != stored.content_hash
                or not existing["immutable"]
                or existing["operation"] != operation
            ):
                raise IntegrityError(
                    f"Sourced original provenance mismatch: {stored.relative_path}"
                )
            return existing
        return self.repository.register_artifact(
            project_id,
            ArtifactCreate(
                kind=ArtifactKind.ORIGINAL,
                relative_path=stored.relative_path,
                content_hash=stored.content_hash,
                operation=operation,
                immutable=True,
            ),
        )

    def download_stock_asset(
        self,
        project_id: str,
        *,
        candidate: AssetCandidate,
        context: AssetUseContext | None = None,
        fetcher: Any | None = None,
        timeout: int = 120,
        max_bytes: int = 80_000_000,
        download_event_url: str | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """Download a verified stock asset and register it with its provider duties."""

        import urllib.request

        self.repository.get_project(project_id)
        asset_url = candidate.source.direct_asset_url
        if asset_url is None:
            raise RightsEligibilityError(
                "The candidate has no direct asset URL to download"
            )

        def _fetch(url: str) -> tuple[bytes, str | None]:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "pinaka-agentic-creative/0.1 (governed import)"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type")
                data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise IntegrityError(f"Asset exceeds {max_bytes} bytes: {url}")
            return data, content_type

        fetch = fetcher or _fetch
        if download_event_url:
            fetch(download_event_url)
        content, content_type = fetch(str(asset_url))
        if content_type and not content_type.lower().startswith(
            ("image/", "video/", "audio/")
        ):
            raise IntegrityError(f"Unexpected asset content type: {content_type}")
        compliance = candidate.compliance.model_copy(
            update={"download_event_recorded": True}
        )
        updated = candidate.model_copy(update={"compliance": compliance})
        suffix = ""
        if content_type:
            lowered = content_type.lower()
            for marker, ext in (
                ("jpeg", ".jpg"),
                ("png", ".png"),
                ("mp4", ".mp4"),
                ("webm", ".webm"),
                ("mpeg", ".mp3"),
                ("wav", ".wav"),
                ("ogg", ".ogg"),
                ("flac", ".flac"),
            ):
                if marker in lowered:
                    suffix = ext
                    break
        name = filename or f"{candidate.source.provider_asset_id}{suffix or '.bin'}"
        artifact = self.import_stock_asset(
            project_id,
            candidate=updated,
            context=context,
            content=content,
            filename=name,
        )
        return {
            "artifact": artifact,
            "candidate": updated.model_dump(mode="json"),
            "content_type": content_type,
            "size_bytes": len(content),
        }

    def finalize_asset(
        self,
        project_id: str,
        slot_id: str,
        *,
        artifact_id: str,
        criteria: AssetCriteria,
        context: AssetUseContext | None = None,
        decided_by: str,
    ) -> dict[str, Any]:
        """Record how one slot is satisfied: as-is, corrected, replaced or rejected."""

        context = self._enforce_context(project_id, context)
        slot = self.repository.get_slot(slot_id)
        if slot["project_id"] != project_id:
            raise ConflictError("Slot belongs to a different project")
        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")

        rights_payload = artifact["operation"].get("rights")
        rights = (
            AssetRightsRecord.model_validate(rights_payload) if rights_payload else None
        )
        decision = evaluate_treatment(
            slot_id=slot_id,
            criteria=criteria,
            context=context,
            decided_by=decided_by,
            rights=rights,
            artifact_id=artifact_id,
        )
        recorded = self.repository.register_treatment_decision(
            project_id,
            decision.model_dump(mode="json"),
            decision_hash=decision.decision_hash,
            artifact_id=artifact_id if verdict_binds_asset(decision.verdict) else None,
            decided_by=decided_by,
            created_at=decision.decided_at,
        )
        return {
            "decision": recorded,
            "verdict": decision.verdict.value,
            "bound": verdict_binds_asset(decision.verdict),
            "requires_new_artifact": verdict_modifies_bytes(decision.verdict),
            "reasons": list(decision.reasons),
        }

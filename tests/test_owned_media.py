from __future__ import annotations

from pinaka.enums import (
    ArtifactKind,
    AssetEligibilityStatus,
    AssetMediaType,
    AssetUseClass,
)
from pinaka.models import AssetUseContext
from pinaka.sourcing import (
    build_owned_candidate,
    evaluate_asset_candidate,
)

from .conftest import create_project

AUTHORISATION = "https://internal.example.test/authorisations/2026-11-pinaka.pdf"


def context() -> AssetUseContext:
    return AssetUseContext(
        use_class=AssetUseClass.COMMERCIAL,
        monetized=True,
        modification_planned=True,
        attribution_can_be_delivered=True,
    )


def test_owned_candidate_uses_the_authorisation_as_evidence():
    candidate = build_owned_candidate(
        asset_id="founder-interview",
        media_type=AssetMediaType.VIDEO,
        creator_name="Pinaka client",
        authorisation_url=AUTHORISATION,
    )

    assert candidate.source.provider.value == "owned"
    assert candidate.source.acquisition_method.value == "manual_import"
    assert candidate.source.source_page_url is None
    assert candidate.rights.license_code == "owned"
    assert candidate.rights.commercial_use_allowed is True
    assert candidate.rights.modifications_allowed is True
    assert candidate.rights.attribution_required is False
    assert candidate.rights.origin_license_verified is False


def test_owned_media_is_blocked_until_evidence_is_captured():
    candidate = build_owned_candidate(
        asset_id="founder-interview",
        media_type=AssetMediaType.IMAGE,
        creator_name="Pinaka client",
        authorisation_url=AUTHORISATION,
    )

    result = evaluate_asset_candidate(candidate, context())

    assert result.status == AssetEligibilityStatus.NEEDS_REVIEW
    assert any("evidence" in reason.casefold() for reason in result.review_reasons)


def test_owned_media_becomes_eligible_after_capture(controller):
    project = create_project(controller)
    candidate = build_owned_candidate(
        asset_id="brand-still",
        media_type=AssetMediaType.IMAGE,
        creator_name="Pinaka client",
        authorisation_url=AUTHORISATION,
    )

    captured = controller.capture_asset_evidence(
        project.id,
        candidate,
        fetcher=lambda url: (b"<html>signed authorisation</html>", "text/html"),
    )

    assert captured["candidate"].rights.origin_license_verified is True
    assert len(captured["evidence"]) == 1
    assert captured["evidence"][0]["kind"] == "licence_page"

    result = evaluate_asset_candidate(captured["candidate"], context())
    assert result.status == AssetEligibilityStatus.ELIGIBLE


def test_owned_media_imports_as_an_immutable_original(controller, tmp_path):
    project = create_project(controller)
    source = tmp_path / "brand-still.png"
    source.write_bytes(b"owned-image-bytes")
    candidate = build_owned_candidate(
        asset_id="brand-still",
        media_type=AssetMediaType.IMAGE,
        creator_name="Pinaka client",
        authorisation_url=AUTHORISATION,
    )
    captured = controller.capture_asset_evidence(
        project.id,
        candidate,
        fetcher=lambda url: (b"<html>signed authorisation</html>", "text/html"),
    )

    artifact = controller.import_sourced_original(
        project.id,
        source,
        candidate=captured["candidate"],
        context=context(),
    )

    assert artifact["kind"] == ArtifactKind.ORIGINAL.value
    assert artifact["immutable"] is True
    assert artifact["operation"]["rights"]["license_code"] == "owned"
    assert artifact["operation"]["source"]["provider"] == "owned"

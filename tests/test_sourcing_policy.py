from datetime import date

import pytest

from pinaka.enums import (
    AssetAcquisitionMethod,
    AssetEligibilityStatus,
    AssetMediaType,
    AssetProvider,
    AssetUseClass,
    ProviderIntegrationMode,
)
from pinaka.errors import RightsEligibilityError
from pinaka.models import (
    AssetCandidate,
    AssetRightsRecord,
    AssetSourceRecord,
    AssetUseContext,
    ProviderComplianceRecord,
)
from pinaka.sourcing import provider_profile

from .conftest import create_project

EVIDENCE_HASH = "a" * 64
API_RESPONSE_HASH = "b" * 64


def source_record(
    provider: AssetProvider,
    *,
    method: AssetAcquisitionMethod = AssetAcquisitionMethod.MANUAL_IMPORT,
    media_type: AssetMediaType = AssetMediaType.IMAGE,
) -> AssetSourceRecord:
    return AssetSourceRecord(
        provider=provider,
        provider_asset_id="asset-123",
        media_type=media_type,
        acquisition_method=method,
        source_page_url=f"https://example.test/{provider.value}/asset-123",
        creator_name="Example Creator",
        creator_url="https://example.test/creators/example",
        direct_asset_url="https://cdn.example.test/asset-123.jpg",
        api_response_hash=API_RESPONSE_HASH
        if method == AssetAcquisitionMethod.OFFICIAL_API
        else None,
    )


def rights_record(**overrides) -> AssetRightsRecord:
    values = {
        "license_name": "Example commercial licence",
        "license_code": "example-commercial",
        "license_url": "https://example.test/license",
        "terms_url": "https://example.test/terms",
        "terms_reviewed_at": date(2026, 11, 9),
        "commercial_use_allowed": True,
        "modifications_allowed": True,
        "editorial_only": False,
        "attribution_required": False,
        "license_evidence_ref": "references/licences/example-asset.html",
        "license_evidence_hash": EVIDENCE_HASH,
        "origin_license_verified": True,
    }
    values.update(overrides)
    return AssetRightsRecord(**values)


def candidate(
    provider: AssetProvider,
    *,
    method: AssetAcquisitionMethod = AssetAcquisitionMethod.MANUAL_IMPORT,
    media_type: AssetMediaType = AssetMediaType.IMAGE,
    rights: AssetRightsRecord | None = None,
    compliance: ProviderComplianceRecord | None = None,
) -> AssetCandidate:
    return AssetCandidate(
        source=source_record(provider, method=method, media_type=media_type),
        rights=rights or rights_record(),
        compliance=compliance or ProviderComplianceRecord(),
    )


def commercial_context(**overrides) -> AssetUseContext:
    values = {
        "use_class": AssetUseClass.COMMERCIAL,
        "monetized": True,
        "advertising": False,
        "modification_planned": True,
        "attribution_can_be_delivered": True,
    }
    values.update(overrides)
    return AssetUseContext(**values)


def test_provider_profiles_distinguish_api_and_manual_sources():
    assert provider_profile(AssetProvider.OPENVERSE).integration_mode == (
        ProviderIntegrationMode.API_SPIKE_ALLOWED
    )
    assert provider_profile(AssetProvider.PEXELS).integration_mode == (
        ProviderIntegrationMode.EXISTING_KEY_REQUIRED
    )
    assert provider_profile(AssetProvider.MIXKIT).acquisition_methods == (
        AssetAcquisitionMethod.MANUAL_IMPORT,
    )
    assert provider_profile(AssetProvider.VIDEEZY).acquisition_methods == (
        AssetAcquisitionMethod.MANUAL_IMPORT,
    )


@pytest.mark.parametrize(
    "rights",
    (
        rights_record(commercial_use_allowed=False),
        rights_record(editorial_only=True, commercial_use_allowed=False),
    ),
)
def test_noncommercial_or_editorial_rights_block_commercial_production(
    controller, rights
):
    result = controller.evaluate_asset_candidate(
        candidate(AssetProvider.MIXKIT, media_type=AssetMediaType.VIDEO, rights=rights),
        commercial_context(),
    )

    assert result.status == AssetEligibilityStatus.BLOCKED
    assert any("commercial" in reason.lower() for reason in result.blocking_reasons)


def test_uncertainty_and_missing_evidence_require_review(controller):
    result = controller.evaluate_asset_candidate(
        candidate(
            AssetProvider.PEXELS,
            rights=rights_record(
                license_evidence_ref=None,
                license_evidence_hash=None,
                rights_uncertainty=("Model release not confirmed",),
            ),
        ),
        commercial_context(),
    )

    assert result.status == AssetEligibilityStatus.NEEDS_REVIEW
    assert len(result.review_reasons) == 3


def test_openverse_requires_origin_licence_verification(controller):
    result = controller.evaluate_asset_candidate(
        candidate(
            AssetProvider.OPENVERSE,
            method=AssetAcquisitionMethod.OFFICIAL_API,
            rights=rights_record(origin_license_verified=False),
            compliance=ProviderComplianceRecord(openverse_disclosure_planned=True),
        ),
        commercial_context(),
    )

    assert result.status == AssetEligibilityStatus.NEEDS_REVIEW
    assert any("origin" in reason.lower() for reason in result.review_reasons)


def test_unsplash_api_requires_human_selection_tracking_and_attribution(controller):
    incomplete = controller.evaluate_asset_candidate(
        candidate(
            AssetProvider.UNSPLASH,
            method=AssetAcquisitionMethod.OFFICIAL_API,
            compliance=ProviderComplianceRecord(),
        ),
        commercial_context(),
    )
    complete = controller.evaluate_asset_candidate(
        candidate(
            AssetProvider.UNSPLASH,
            method=AssetAcquisitionMethod.OFFICIAL_API,
            compliance=ProviderComplianceRecord(
                human_selected=True,
                hotlink_preview_used=True,
                download_event_recorded=True,
                provider_credit_planned=True,
                creator_credit_planned=True,
            ),
        ),
        commercial_context(),
    )

    assert incomplete.status == AssetEligibilityStatus.NEEDS_REVIEW
    assert complete.status == AssetEligibilityStatus.ELIGIBLE


@pytest.mark.parametrize("provider", (AssetProvider.MIXKIT, AssetProvider.VIDEEZY))
def test_manual_only_providers_reject_api_acquisition(controller, provider):
    result = controller.evaluate_asset_candidate(
        candidate(
            provider,
            method=AssetAcquisitionMethod.OFFICIAL_API,
            media_type=AssetMediaType.VIDEO,
        ),
        commercial_context(),
    )

    assert result.status == AssetEligibilityStatus.BLOCKED
    assert any("does not permit" in reason for reason in result.blocking_reasons)


def test_required_attribution_must_be_deliverable(controller):
    asset = candidate(
        AssetProvider.VIDEEZY,
        media_type=AssetMediaType.VIDEO,
        rights=rights_record(
            license_name="Videezy Standard",
            attribution_required=True,
            attribution_text="Free Stock Footage by Videezy.com",
        ),
    )

    allowed = controller.evaluate_asset_candidate(asset, commercial_context())
    blocked = controller.evaluate_asset_candidate(
        asset,
        commercial_context(attribution_can_be_delivered=False),
    )

    assert allowed.status == AssetEligibilityStatus.ELIGIBLE
    assert allowed.obligations == (
        "Include attribution: Free Stock Footage by Videezy.com",
    )
    assert blocked.status == AssetEligibilityStatus.BLOCKED


def test_sourced_import_preserves_rights_provenance_and_hashes(controller, tmp_path):
    project = create_project(controller)
    source = tmp_path / "licensed-photo.jpg"
    source.write_bytes(b"licensed-stock-content")
    asset = candidate(AssetProvider.PEXELS)

    imported = controller.import_sourced_original(
        project.id,
        source,
        candidate=asset,
        context=commercial_context(),
    )
    repeated = controller.import_sourced_original(
        project.id,
        source,
        candidate=asset,
        context=commercial_context(),
    )

    assert repeated["id"] == imported["id"]
    assert imported["immutable"] is True
    assert imported["operation"]["type"] == "sourced_import"
    assert imported["operation"]["source"]["provider"] == "pexels"
    assert imported["operation"]["rights"]["license_evidence_hash"] == EVIDENCE_HASH
    assert len(imported["operation"]["source_manifest_hash"]) == 64
    assert controller.verify_artifact(imported["id"])


def test_unresolved_rights_cannot_enter_artifact_store(controller, tmp_path):
    project = create_project(controller)
    source = tmp_path / "unclear-video.mp4"
    source.write_bytes(b"unclear-rights-content")
    asset = candidate(
        AssetProvider.MIXKIT,
        media_type=AssetMediaType.VIDEO,
        rights=rights_record(
            commercial_use_allowed=None,
            license_evidence_ref=None,
            license_evidence_hash=None,
        ),
    )

    with pytest.raises(RightsEligibilityError, match="needs_review"):
        controller.import_sourced_original(
            project.id,
            source,
            candidate=asset,
            context=commercial_context(),
        )

    assert controller.repository.list_artifacts(project.id) == []

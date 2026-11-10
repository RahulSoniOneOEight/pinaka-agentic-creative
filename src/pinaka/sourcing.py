from __future__ import annotations

from datetime import date

from .enums import (
    AssetAcquisitionMethod,
    AssetEligibilityStatus,
    AssetMediaType,
    AssetProvider,
    AssetUseClass,
    ProviderIntegrationMode,
)
from .models import (
    AssetCandidate,
    AssetEligibilityResult,
    AssetRightsRecord,
    AssetSourceRecord,
    AssetUseContext,
    ProviderComplianceRecord,
    ProviderProfile,
)

REVIEW_DATE = date(2026, 11, 9)

EVIDENCE_PENDING = "Licence evidence has not been archived for this item."


def clear_resolved_uncertainty(items: tuple[str, ...]) -> tuple[str, ...]:
    """Drop the evidence-pending marker once the evidence has been archived."""

    return tuple(item for item in items if item != EVIDENCE_PENDING)


PROVIDER_PROFILES: dict[AssetProvider, ProviderProfile] = {
    AssetProvider.OWNED: ProviderProfile(
        provider=AssetProvider.OWNED,
        media_types=(
            AssetMediaType.IMAGE,
            AssetMediaType.VIDEO,
            AssetMediaType.AUDIO,
            AssetMediaType.TEMPLATE,
        ),
        acquisition_methods=(AssetAcquisitionMethod.MANUAL_IMPORT,),
        integration_mode=ProviderIntegrationMode.MANUAL_ONLY,
        official_urls=(),
        terms_reviewed_at=REVIEW_DATE,
        notes=(
            "Client-owned or commissioned media. The signed authorisation is the rights evidence.",
            "Possession is not permission: record the authorisation reference and its scope.",
            "Owned media is the first preference in the sourcing cascade.",
        ),
    ),
    AssetProvider.PEXELS: ProviderProfile(
        provider=AssetProvider.PEXELS,
        media_types=(AssetMediaType.IMAGE, AssetMediaType.VIDEO),
        acquisition_methods=(
            AssetAcquisitionMethod.OFFICIAL_API,
            AssetAcquisitionMethod.MANUAL_IMPORT,
        ),
        integration_mode=ProviderIntegrationMode.EXISTING_KEY_REQUIRED,
        official_urls=(
            "https://www.pexels.com/api/documentation/",
            "https://www.pexels.com/license/",
            "https://www.pexels.com/terms-of-service/",
        ),
        terms_reviewed_at=REVIEW_DATE,
        notes=(
            "New API key issuance is paused; enable API access only with an existing key.",
            "API use requires a prominent Pexels link; contributor credit should be retained.",
            "Do not scrape, bulk-copy, build a competing library, or create a training dataset.",
        ),
    ),
    AssetProvider.OPENVERSE: ProviderProfile(
        provider=AssetProvider.OPENVERSE,
        media_types=(AssetMediaType.IMAGE, AssetMediaType.AUDIO),
        acquisition_methods=(
            AssetAcquisitionMethod.OFFICIAL_API,
            AssetAcquisitionMethod.MANUAL_IMPORT,
        ),
        integration_mode=ProviderIntegrationMode.API_SPIKE_ALLOWED,
        official_urls=(
            "https://api.openverse.org/v1/",
            "https://docs.openverse.org/terms_of_service.html",
            "https://openverse.org/about/",
        ),
        terms_reviewed_at=REVIEW_DATE,
        notes=(
            "Openverse exposes images and audio, not a stock-video API.",
            "Openverse does not verify licence accuracy; verify the originating page.",
            "Respect exact CC terms and identify the integration as not endorsed by Openverse.",
        ),
    ),
    AssetProvider.UNSPLASH: ProviderProfile(
        provider=AssetProvider.UNSPLASH,
        media_types=(AssetMediaType.IMAGE,),
        acquisition_methods=(
            AssetAcquisitionMethod.OFFICIAL_API,
            AssetAcquisitionMethod.MANUAL_IMPORT,
        ),
        integration_mode=ProviderIntegrationMode.API_SPIKE_ALLOWED,
        official_urls=(
            "https://unsplash.com/documentation",
            "https://help.unsplash.com/en/articles/2511245-unsplash-api-guidelines",
            "https://unsplash.com/license",
        ),
        terms_reviewed_at=REVIEW_DATE,
        notes=(
            "API previews must use returned hotlinked URLs.",
            "Selection must trigger the returned download-location endpoint.",
            "API displays require photographer and Unsplash attribution with UTM links.",
            "Use must be a non-automated, high-quality, authentic experience.",
        ),
    ),
    AssetProvider.MIXKIT: ProviderProfile(
        provider=AssetProvider.MIXKIT,
        media_types=(
            AssetMediaType.VIDEO,
            AssetMediaType.AUDIO,
            AssetMediaType.TEMPLATE,
        ),
        acquisition_methods=(AssetAcquisitionMethod.MANUAL_IMPORT,),
        integration_mode=ProviderIntegrationMode.MANUAL_ONLY,
        official_urls=(
            "https://mixkit.co/license/",
            "https://mixkit.co/terms/",
        ),
        terms_reviewed_at=REVIEW_DATE,
        notes=(
            "Check the per-item licence; Stock Video Free and Restricted differ materially.",
            "Restricted stock video is not eligible for commercial, company-social, or monetized use.",
            "Scripts, scraping, and mass downloads are prohibited.",
        ),
    ),
    AssetProvider.VIDEEZY: ProviderProfile(
        provider=AssetProvider.VIDEEZY,
        media_types=(AssetMediaType.VIDEO, AssetMediaType.TEMPLATE),
        acquisition_methods=(AssetAcquisitionMethod.MANUAL_IMPORT,),
        integration_mode=ProviderIntegrationMode.MANUAL_ONLY,
        official_urls=(
            "https://www.videezy.com/terms",
            "https://support.videezy.com/en_us/categories/license-types-Bk1PKxowF",
        ),
        terms_reviewed_at=REVIEW_DATE,
        notes=(
            "Check each item's Standard, Community, Pro, CC, or Editorial licence.",
            "Standard and Community uses generally require attribution; Pro does not.",
            "Editorial and noncommercial CC assets cannot enter commercial productions.",
            "Automated access, scraping, and unauthorized scripts are prohibited.",
        ),
    ),
    AssetProvider.WIKIMEDIA: ProviderProfile(
        provider=AssetProvider.WIKIMEDIA,
        media_types=(
            AssetMediaType.IMAGE,
            AssetMediaType.AUDIO,
            AssetMediaType.VIDEO,
        ),
        acquisition_methods=(
            AssetAcquisitionMethod.OFFICIAL_API,
            AssetAcquisitionMethod.MANUAL_IMPORT,
        ),
        integration_mode=ProviderIntegrationMode.API_SPIKE_ALLOWED,
        official_urls=(
            "https://commons.wikimedia.org/w/api.php",
            "https://commons.wikimedia.org/wiki/Commons:Licensing",
            "https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use",
        ),
        terms_reviewed_at=REVIEW_DATE,
        notes=(
            "Commons is a community-maintained repository; licence metadata can be wrong.",
            "Every file must be verified at its description page before approval.",
            "Non-free content, fair-use files, and unclear licences are not eligible.",
            "Respect attribution, share-alike, and personality/trademark restrictions.",
        ),
    ),
    AssetProvider.INTERNET_ARCHIVE: ProviderProfile(
        provider=AssetProvider.INTERNET_ARCHIVE,
        media_types=(
            AssetMediaType.IMAGE,
            AssetMediaType.AUDIO,
            AssetMediaType.VIDEO,
        ),
        acquisition_methods=(
            AssetAcquisitionMethod.OFFICIAL_API,
            AssetAcquisitionMethod.MANUAL_IMPORT,
        ),
        integration_mode=ProviderIntegrationMode.API_SPIKE_ALLOWED,
        official_urls=(
            "https://archive.org/advancedsearch.php",
            "https://archive.org/about/terms.php",
            "https://archive.org/developers/",
        ),
        terms_reviewed_at=REVIEW_DATE,
        notes=(
            "Item-level licences vary; the search index is metadata, not a licence grant.",
            "Verify the item page and each selected file before approval.",
            "Do not use items whose uploader lacks rights or whose licence is unclear.",
            "Respect takedown status, access restrictions, and third-party rights.",
        ),
    ),
    AssetProvider.FREESOUND: ProviderProfile(
        provider=AssetProvider.FREESOUND,
        media_types=(AssetMediaType.AUDIO,),
        acquisition_methods=(
            AssetAcquisitionMethod.OFFICIAL_API,
            AssetAcquisitionMethod.MANUAL_IMPORT,
        ),
        integration_mode=ProviderIntegrationMode.EXISTING_KEY_REQUIRED,
        official_urls=(
            "https://freesound.org/docs/api/",
            "https://freesound.org/help/tos_web/",
            "https://freesound.org/help/faq/",
        ),
        terms_reviewed_at=REVIEW_DATE,
        notes=(
            "An existing API key is required; new-key availability is not assumed.",
            "Each sound carries its own Creative Commons licence and attribution terms.",
            "Preview files are for audition only; the original requires a governed import.",
            "Do not bulk-download, scrape, or redistribute the Freesound library.",
        ),
    ),
}


def provider_profile(provider: AssetProvider) -> ProviderProfile:
    return PROVIDER_PROFILES[provider]


def build_owned_candidate(
    *,
    asset_id: str,
    media_type: AssetMediaType,
    creator_name: str,
    authorisation_url: str,
) -> AssetCandidate:
    """Describe client-owned media whose authorisation is the rights evidence."""

    return AssetCandidate(
        source=AssetSourceRecord(
            provider=AssetProvider.OWNED,
            provider_asset_id=asset_id[:200],
            media_type=media_type,
            acquisition_method=AssetAcquisitionMethod.MANUAL_IMPORT,
            source_page_url=None,
            creator_name=creator_name[:300],
            creator_url=None,
            direct_asset_url=None,
            api_response_hash=None,
        ),
        rights=AssetRightsRecord(
            license_name="Client authorisation",
            license_code="owned",
            license_url=authorisation_url,
            terms_url=authorisation_url,
            terms_reviewed_at=REVIEW_DATE,
            commercial_use_allowed=True,
            modifications_allowed=True,
            editorial_only=False,
            attribution_required=False,
            attribution_text=None,
            license_evidence_ref=None,
            license_evidence_hash=None,
            origin_license_verified=False,
            rights_uncertainty=(EVIDENCE_PENDING,),
            obligations=(
                "Retain the signed authorisation with the project.",
                "Use the asset only within the authorised scope and territory.",
            ),
        ),
        compliance=ProviderComplianceRecord(
            human_selected=True,
            provider_credit_planned=False,
            creator_credit_planned=False,
        ),
    )


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def evaluate_asset_candidate(
    candidate: AssetCandidate, context: AssetUseContext
) -> AssetEligibilityResult:
    """Evaluate rights before visual quality or creative fit may be considered."""

    profile = provider_profile(candidate.source.provider)
    source = candidate.source
    rights = candidate.rights
    compliance = candidate.compliance
    blocking: list[str] = []
    review: list[str] = []
    obligations = list(rights.obligations)

    if source.media_type not in profile.media_types:
        blocking.append(
            f"{source.provider.value} does not support {source.media_type.value} in this flow"
        )
    if source.acquisition_method not in profile.acquisition_methods:
        blocking.append(
            f"{source.provider.value} does not permit {source.acquisition_method.value} acquisition"
        )
    if context.standalone_redistribution:
        blocking.append(
            "Standalone redistribution is outside the approved sourcing flow"
        )
    if context.implies_endorsement:
        blocking.append(
            "The proposed use implies endorsement by a depicted person or brand"
        )

    if rights.editorial_only and context.use_class != AssetUseClass.EDITORIAL:
        blocking.append(
            "Editorial-only media cannot be used outside an editorial production"
        )

    if context.requires_commercial_rights:
        if rights.commercial_use_allowed is False:
            blocking.append("The licence does not permit commercial or monetized use")
        elif rights.commercial_use_allowed is None:
            review.append("Commercial-use permission has not been verified")

    if context.modification_planned:
        if rights.modifications_allowed is False:
            blocking.append("The licence does not permit the planned modification")
        elif rights.modifications_allowed is None:
            review.append("Modification permission has not been verified")

    if rights.attribution_required is True:
        if not context.attribution_can_be_delivered:
            blocking.append(
                "Required attribution cannot be delivered in the target output"
            )
        elif not rights.attribution_text:
            review.append("Required attribution text is missing")
        else:
            obligations.append(f"Include attribution: {rights.attribution_text}")
    elif rights.attribution_required is None:
        review.append("Attribution requirements have not been verified")

    if not rights.license_evidence_ref:
        review.append("Per-item licence evidence reference is missing")
    if not rights.license_evidence_hash:
        review.append("Per-item licence evidence hash is missing")
    review.extend(f"Rights uncertainty: {item}" for item in rights.rights_uncertainty)

    if rights.contains_recognizable_people_or_brands:
        if rights.third_party_rights_cleared is False:
            blocking.append(
                "Required third-party personality or trademark rights are not cleared"
            )
        elif rights.third_party_rights_cleared is None:
            review.append("Third-party personality or trademark rights require review")

    if source.provider == AssetProvider.OPENVERSE:
        if not rights.origin_license_verified:
            review.append(
                "Openverse licence metadata has not been verified at the origin"
            )
        if (
            source.acquisition_method == AssetAcquisitionMethod.OFFICIAL_API
            and compliance.openverse_disclosure_planned is not True
        ):
            review.append("Openverse non-endorsement disclosure is not recorded")

    if source.provider == AssetProvider.WIKIMEDIA:
        if not rights.origin_license_verified:
            review.append(
                "Wikimedia Commons licence metadata has not been verified at the "
                "file description page"
            )
        if (
            compliance.creator_credit_planned is not True
            and rights.attribution_required
        ):
            review.append("Wikimedia Commons creator attribution is not planned")

    if source.provider == AssetProvider.INTERNET_ARCHIVE:
        if not rights.origin_license_verified:
            review.append(
                "Internet Archive item licence has not been verified at the item page"
            )
        if rights.commercial_use_allowed is None:
            review.append(
                "Internet Archive search does not establish commercial-use permission"
            )

    if source.provider == AssetProvider.FREESOUND:
        if not rights.origin_license_verified:
            review.append(
                "Freesound licence metadata has not been verified at the sound page"
            )
        if rights.commercial_use_allowed is None:
            review.append(
                "Freesound search does not establish commercial-use permission"
            )
        if compliance.creator_credit_planned is not True:
            review.append("Freesound creator attribution is not planned")

    if (
        source.provider == AssetProvider.UNSPLASH
        and source.acquisition_method == AssetAcquisitionMethod.OFFICIAL_API
    ):
        if not compliance.human_selected:
            blocking.append(
                "Unsplash API assets require a non-automated authentic selection"
            )
        if compliance.hotlink_preview_used is not True:
            review.append("Unsplash API preview hotlinking is not confirmed")
        if compliance.download_event_recorded is not True:
            review.append("Unsplash download-location event is not recorded")
        if compliance.provider_credit_planned is not True:
            review.append("Unsplash provider attribution is not planned")
        if compliance.creator_credit_planned is not True:
            review.append("Unsplash photographer attribution is not planned")

    if (
        source.provider == AssetProvider.PEXELS
        and source.acquisition_method == AssetAcquisitionMethod.OFFICIAL_API
        and compliance.provider_credit_planned is not True
    ):
        review.append("The prominent Pexels API link is not planned")

    if blocking:
        status = AssetEligibilityStatus.BLOCKED
    elif review:
        status = AssetEligibilityStatus.NEEDS_REVIEW
    else:
        status = AssetEligibilityStatus.ELIGIBLE

    return AssetEligibilityResult(
        status=status,
        blocking_reasons=_unique(blocking),
        review_reasons=_unique(review),
        obligations=_unique(obligations),
    )

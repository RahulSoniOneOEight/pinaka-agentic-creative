from __future__ import annotations

from datetime import date

from pydantic import (
    Field,
    HttpUrl,
    field_validator,
)

from ..enums import (
    AssetAcquisitionMethod,
    AssetEligibilityStatus,
    AssetMediaType,
    AssetProvider,
    AssetUseClass,
    ProviderIntegrationMode,
)
from .common import StrictModel


class AssetSourceRecord(StrictModel):
    provider: AssetProvider
    provider_asset_id: str = Field(min_length=1, max_length=200)
    media_type: AssetMediaType
    acquisition_method: AssetAcquisitionMethod
    source_page_url: HttpUrl | None = None
    creator_name: str = Field(min_length=1, max_length=300)
    creator_url: HttpUrl | None = None
    direct_asset_url: HttpUrl | None = None
    api_response_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class AssetRightsRecord(StrictModel):
    license_name: str = Field(min_length=1, max_length=300)
    license_code: str | None = Field(default=None, max_length=100)
    license_url: HttpUrl
    terms_url: HttpUrl
    terms_reviewed_at: date
    commercial_use_allowed: bool | None = None
    modifications_allowed: bool | None = None
    editorial_only: bool = False
    attribution_required: bool | None = None
    attribution_text: str | None = Field(default=None, max_length=2000)
    license_evidence_ref: str | None = Field(default=None, max_length=1000)
    license_evidence_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    origin_license_verified: bool = False
    contains_recognizable_people_or_brands: bool = False
    third_party_rights_cleared: bool | None = None
    rights_uncertainty: tuple[str, ...] = ()
    obligations: tuple[str, ...] = ()

    @field_validator("terms_reviewed_at")
    @classmethod
    def terms_date_cannot_be_future(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("terms_reviewed_at cannot be in the future")
        return value


class ProviderComplianceRecord(StrictModel):
    human_selected: bool = True
    hotlink_preview_used: bool | None = None
    download_event_recorded: bool | None = None
    provider_credit_planned: bool | None = None
    creator_credit_planned: bool | None = None
    openverse_disclosure_planned: bool | None = None


class AssetCandidate(StrictModel):
    source: AssetSourceRecord
    rights: AssetRightsRecord
    compliance: ProviderComplianceRecord = Field(
        default_factory=ProviderComplianceRecord
    )


class AssetUseContext(StrictModel):
    use_class: AssetUseClass
    monetized: bool = False
    advertising: bool = False
    modification_planned: bool = True
    attribution_can_be_delivered: bool = True
    standalone_redistribution: bool = False
    implies_endorsement: bool = False

    @property
    def requires_commercial_rights(self) -> bool:
        return self.use_class == AssetUseClass.COMMERCIAL or any(
            (self.monetized, self.advertising)
        )


class ProviderProfile(StrictModel):
    provider: AssetProvider
    media_types: tuple[AssetMediaType, ...]
    acquisition_methods: tuple[AssetAcquisitionMethod, ...]
    integration_mode: ProviderIntegrationMode
    official_urls: tuple[HttpUrl, ...] = ()
    terms_reviewed_at: date
    notes: tuple[str, ...] = ()


class AssetEligibilityResult(StrictModel):
    status: AssetEligibilityStatus
    blocking_reasons: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()
    obligations: tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        return self.status == AssetEligibilityStatus.ELIGIBLE

from __future__ import annotations

import json

import pytest

from pinaka.enums import AssetEligibilityStatus, AssetUseClass
from pinaka.errors import ConfigurationError
from pinaka.models import AssetUseContext
from pinaka.unsplash import UnsplashClient, map_unsplash_result
from pinaka.utils import sha256_bytes

FIXTURE = {
    "total": 2,
    "total_pages": 2,
    "results": [
        {
            "id": "eOLpJytrbsQ",
            "created_at": "2014-11-18T14:35:36-05:00",
            "width": 4000,
            "height": 3000,
            "color": "#A7A2A1",
            "user": {
                "id": "Ul0QVz12Goo",
                "username": "ugmonk",
                "name": "Jeff Sheldon",
                "links": {
                    "self": "https://api.unsplash.com/users/ugmonk",
                    "html": "https://unsplash.com/@ugmonk",
                    "photos": "https://api.unsplash.com/users/ugmonk/photos",
                },
            },
            "urls": {
                "raw": "https://images.unsplash.com/photo-1416339306562-f3d12fefd36f",
                "regular": (
                    "https://images.unsplash.com/photo-1416339306562-f3d12fefd36f"
                    "?ixlib=rb-0.3.5&q=80&fm=jpg&crop=entropy&cs=tinysrgb&w=1080"
                ),
            },
            "links": {
                "self": "https://api.unsplash.com/photos/eOLpJytrbsQ",
                "html": "https://unsplash.com/photos/eOLpJytrbsQ",
                "download": "https://unsplash.com/photos/eOLpJytrbsQ/download",
            },
        },
        {
            "id": "premium-1",
            "width": 4000,
            "height": 6000,
            "premium": True,
            "user": {"username": "studio", "name": "Studio Photographer"},
            "urls": {"regular": "https://images.unsplash.com/photo-premium"},
            "links": {
                "html": "https://unsplash.com/photos/premium-1",
                "download": "https://unsplash.com/photos/premium-1/download",
            },
        },
    ],
}


def context() -> AssetUseContext:
    return AssetUseContext(
        use_class=AssetUseClass.COMMERCIAL,
        monetized=True,
        modification_planned=True,
        attribution_can_be_delivered=True,
    )


def test_unsplash_requires_an_existing_key(monkeypatch, tmp_path):
    monkeypatch.delenv("UNSPLASH_ACCESS_KEY", raising=False)
    monkeypatch.delenv("UNSPLASH_ACCESS_KEY_FILE", raising=False)
    monkeypatch.setattr(
        "pinaka.unsplash.DEFAULT_ACCESS_KEY_FILE", tmp_path / "missing-key"
    )
    with pytest.raises(ConfigurationError, match="UNSPLASH_ACCESS_KEY"):
        UnsplashClient(access_key="").search("teamwork")


def test_unsplash_mapping_applies_policy_licence_and_credit_duties():
    candidate = map_unsplash_result(FIXTURE["results"][0], response_hash="a" * 64)

    assert candidate.source.provider.value == "unsplash"
    assert candidate.source.acquisition_method.value == "official_api"
    assert candidate.source.provider_asset_id == "eOLpJytrbsQ"
    assert candidate.source.creator_name == "Jeff Sheldon"
    assert candidate.source.source_page_url.host == "unsplash.com"
    assert candidate.rights.license_code == "unsplash"
    assert candidate.rights.commercial_use_allowed is True
    assert candidate.rights.modifications_allowed is True
    assert candidate.rights.attribution_required is True
    assert candidate.rights.attribution_text == "Photo by Jeff Sheldon on Unsplash"
    assert candidate.rights.origin_license_verified is False
    assert candidate.rights.license_evidence_ref is None
    assert candidate.compliance.human_selected is False
    assert candidate.compliance.download_event_recorded is False
    assert candidate.compliance.hotlink_preview_used is True
    duties = " ".join(candidate.rights.obligations).casefold()
    assert "photographer" in duties
    assert "utm" in duties
    assert "download endpoint" in duties


def test_unsplash_premium_content_blocks_commercial_use():
    candidate = map_unsplash_result(FIXTURE["results"][1], response_hash="b" * 64)
    assert candidate.rights.commercial_use_allowed is False

    from pinaka.sourcing import evaluate_asset_candidate

    eligibility = evaluate_asset_candidate(candidate, context())
    assert eligibility.status == AssetEligibilityStatus.BLOCKED
    assert any(
        "commercial" in reason.casefold() for reason in eligibility.blocking_reasons
    )


def test_unsplash_result_is_blocked_until_the_operator_selects_it():
    raw = json.dumps(FIXTURE).encode()
    client = UnsplashClient(access_key="test-key", fetcher=lambda url, key: raw)

    payload = client.search_with_eligibility("teamwork", context(), limit=2)

    statuses = [item["eligibility"]["status"] for item in payload["results"]]
    assert statuses == ["blocked", "blocked"]
    reasons = " ".join(
        reason
        for item in payload["results"]
        for reason in item["eligibility"]["blocking_reasons"]
    ).casefold()
    assert "selection" in reasons
    assert payload["provider"] == "unsplash"
    assert payload["media_type"] == "image"


def test_unsplash_selected_result_needs_review_for_unrecorded_duties():
    raw = json.dumps(FIXTURE).encode()
    client = UnsplashClient(access_key="test-key", fetcher=lambda url, key: raw)

    payload = client.search_with_eligibility(
        "teamwork", context(), limit=2, selected_asset_id="eOLpJytrbsQ"
    )

    first, second = payload["results"]
    assert first["eligibility"]["status"] == "needs_review"
    assert second["eligibility"]["status"] == "blocked"
    review = " ".join(first["eligibility"]["review_reasons"]).casefold()
    assert "download" in review
    assert "photo page" in review or "rights uncertainty" in review


def test_unsplash_search_uses_injected_transport_and_hashes_the_response():
    raw = json.dumps(FIXTURE).encode()
    client = UnsplashClient(access_key="test-key", fetcher=lambda url, key: raw)

    result = client.search("teamwork", limit=2)

    assert result.result_count == 2
    assert len(result.candidates) == 2
    assert result.response_hash == sha256_bytes(raw)
    assert result.page == 1


def test_unsplash_rejects_unknown_search_options():
    client = UnsplashClient(access_key="test-key", fetcher=lambda url, key: b"{}")

    with pytest.raises(ValueError, match="orientation"):
        client.search("teamwork", orientation="diagonal")
    with pytest.raises(ValueError, match="content_filter"):
        client.search("teamwork", content_filter="medium")
    with pytest.raises(ValueError, match="order_by"):
        client.search("teamwork", order_by="popular")


def test_unsplash_missing_download_endpoint_is_flagged():
    photo = dict(FIXTURE["results"][0])
    photo["links"] = {"html": "https://unsplash.com/photos/eOLpJytrbsQ"}

    candidate = map_unsplash_result(photo, response_hash="c" * 64)

    assert any(
        "download endpoint" in item.casefold()
        for item in candidate.rights.rights_uncertainty
    )

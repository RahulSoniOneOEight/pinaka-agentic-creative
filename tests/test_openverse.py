from __future__ import annotations

import json

from pinaka.enums import AssetEligibilityStatus, AssetMediaType, AssetUseClass
from pinaka.models import AssetUseContext
from pinaka.openverse import OpenverseClient, map_openverse_result
from pinaka.sourcing import evaluate_asset_candidate
from pinaka.utils import sha256_bytes

FIXTURE = {
    "result_count": 2,
    "page": 1,
    "results": [
        {
            "id": "openverse-1",
            "media_type": "image",
            "title": "Finance desk",
            "creator": "Alice",
            "creator_url": "https://example.com/alice",
            "license": "by",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "source": "Flickr",
            "foreign_landing_url": "https://example.com/origin/1",
            "url": "https://example.com/assets/1.jpg",
        },
        {
            "id": "openverse-2",
            "media_type": "image",
            "title": "Budget notebook",
            "creator": "Bob",
            "creator_url": "https://example.com/bob",
            "license": "by-nc",
            "license_url": "https://creativecommons.org/licenses/by-nc/4.0/",
            "source": "Flickr",
            "foreign_landing_url": "https://example.com/origin/2",
            "url": "https://example.com/assets/2.jpg",
        },
    ],
}


def test_openverse_mapping_preserves_rights_and_needs_review():
    raw = json.dumps(FIXTURE["results"][0], sort_keys=True).encode()
    candidate = map_openverse_result(
        FIXTURE["results"][0],
        media_type=AssetMediaType.IMAGE,
        response_hash=sha256_bytes(raw),
    )

    assert candidate.source.provider.value == "openverse"
    assert candidate.source.acquisition_method.value == "official_api"
    assert candidate.rights.license_code == "by"
    assert candidate.rights.commercial_use_allowed is True
    assert candidate.rights.attribution_required is True
    assert candidate.rights.origin_license_verified is False
    assert candidate.rights.license_evidence_ref is None
    assert candidate.rights.attribution_text

    eligibility = evaluate_asset_candidate(
        candidate,
        AssetUseContext(
            use_class=AssetUseClass.COMMERCIAL,
            monetized=True,
            modification_planned=True,
            attribution_can_be_delivered=True,
        ),
    )
    assert eligibility.status == AssetEligibilityStatus.NEEDS_REVIEW
    assert any("origin" in reason.casefold() for reason in eligibility.review_reasons)


def test_openverse_noncommercial_licence_blocks_commercial_use():
    raw = json.dumps(FIXTURE["results"][1], sort_keys=True).encode()
    candidate = map_openverse_result(
        FIXTURE["results"][1],
        media_type=AssetMediaType.IMAGE,
        response_hash=sha256_bytes(raw),
    )
    eligibility = evaluate_asset_candidate(
        candidate,
        AssetUseContext(
            use_class=AssetUseClass.COMMERCIAL,
            monetized=True,
            modification_planned=True,
            attribution_can_be_delivered=True,
        ),
    )

    assert candidate.rights.commercial_use_allowed is False
    assert eligibility.status == AssetEligibilityStatus.BLOCKED


def test_openverse_audio_uses_the_singular_endpoint():
    seen: list[str] = []

    def fetcher(url: str) -> bytes:
        seen.append(url)
        return json.dumps({"result_count": 0, "results": []}).encode()

    OpenverseClient(fetcher=fetcher).search("ukulele", media_type=AssetMediaType.AUDIO)

    assert "/v1/audio/" in seen[0]
    assert "/v1/audios/" not in seen[0]


def test_openverse_client_search_uses_injected_transport():
    raw = json.dumps(FIXTURE).encode()
    client = OpenverseClient(fetcher=lambda url: raw)

    result = client.search("finance", limit=2)

    assert result.result_count == 2
    assert len(result.candidates) == 2
    assert result.response_hash == sha256_bytes(raw)
    assert result.candidates[0].source.provider_asset_id == "openverse-1"


def test_openverse_search_with_eligibility_returns_gate_results():
    raw = json.dumps(FIXTURE).encode()
    client = OpenverseClient(fetcher=lambda url: raw)
    context = AssetUseContext(
        use_class=AssetUseClass.COMMERCIAL,
        monetized=True,
        modification_planned=True,
        attribution_can_be_delivered=True,
    )

    payload = client.search_with_eligibility("finance", context, limit=2)

    statuses = [item["eligibility"]["status"] for item in payload["results"]]
    assert statuses == ["needs_review", "blocked"]
    assert payload["response_hash"] == sha256_bytes(raw)

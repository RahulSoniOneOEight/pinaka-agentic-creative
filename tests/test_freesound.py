from __future__ import annotations

import json

import pytest

from pinaka.enums import AssetEligibilityStatus, AssetUseClass
from pinaka.errors import ConfigurationError
from pinaka.freesound import FreesoundClient, map_freesound_result
from pinaka.models import AssetUseContext

FIXTURE = {
    "count": 2,
    "results": [
        {
            "id": 101,
            "name": "Soft click",
            "license": "http://creativecommons.org/licenses/by/4.0/",
            "username": "Alice",
            "url": "https://freesound.org/people/Alice/sounds/101/",
            "previews": {
                "preview-hq-mp3": "https://cdn.freesound.org/previews/101.mp3"
            },
        },
        {
            "id": 102,
            "name": "Room tone",
            "license": "http://creativecommons.org/licenses/by-nc/4.0/",
            "username": "Bob",
            "url": "https://freesound.org/people/Bob/sounds/102/",
            "previews": {},
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


def test_freesound_requires_an_existing_key(monkeypatch, tmp_path):
    monkeypatch.delenv("FREESOUND_API_KEY", raising=False)
    monkeypatch.delenv("FREESOUND_API_KEY_FILE", raising=False)
    monkeypatch.setattr("pinaka.freesound.DEFAULT_TOKEN_FILE", tmp_path / "missing-key")
    with pytest.raises(ConfigurationError, match="FREESOUND_API_KEY"):
        FreesoundClient(api_key="").search("click")


def test_freesound_mapping_keeps_attribution_and_no_direct_download():
    candidate = map_freesound_result(FIXTURE["results"][0], response_hash="a" * 64)
    assert candidate.source.provider.value == "freesound"
    assert candidate.source.creator_name == "Alice"
    assert candidate.source.direct_asset_url is None
    assert candidate.rights.license_code == "by"
    assert candidate.rights.commercial_use_allowed is True
    assert candidate.rights.origin_license_verified is False
    assert "Soft click" in (candidate.rights.attribution_text or "")
    assert any("audition-only" in item for item in candidate.rights.rights_uncertainty)


def test_freesound_search_with_eligibility():
    raw = json.dumps(FIXTURE).encode()
    client = FreesoundClient(api_key="test-key", fetcher=lambda url, key: raw)
    payload = client.search_with_eligibility("click", context(), limit=2)
    statuses = [item["eligibility"]["status"] for item in payload["results"]]
    assert statuses == ["needs_review", "blocked"]
    assert payload["result_count"] == 2
    assert payload["response_hash"]


def test_freesound_noncommercial_is_blocked():
    candidate = map_freesound_result(FIXTURE["results"][1], response_hash="b" * 64)
    from pinaka.sourcing import evaluate_asset_candidate

    result = evaluate_asset_candidate(candidate, context())
    assert result.status == AssetEligibilityStatus.BLOCKED

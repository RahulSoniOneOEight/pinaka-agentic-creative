from __future__ import annotations

import json

from pinaka.enums import AssetEligibilityStatus, AssetMediaType, AssetUseClass
from pinaka.internet_archive import InternetArchiveClient, map_archive_doc
from pinaka.models import AssetUseContext
from pinaka.openverse import default_context
from pinaka.wikimedia import WikimediaClient, map_wikimedia_page

WIKIMEDIA_FIXTURE = {
    "query": {
        "searchinfo": {"totalhits": 1},
        "pages": [
            {
                "pageid": 123,
                "title": "File:Finance desk.jpg",
                "imageinfo": [
                    {
                        "url": "https://upload.wikimedia.org/finance.jpg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Finance_desk.jpg",
                        "mime": "image/jpeg",
                        "extmetadata": {
                            "LicenseShortName": {"value": "CC BY-SA 4.0"},
                            "LicenseUrl": {
                                "value": "https://creativecommons.org/licenses/by-sa/4.0/"
                            },
                            "Artist": {"value": "<a href='/wiki/User:Alice'>Alice</a>"},
                            "Credit": {"value": "Own work"},
                        },
                    }
                ],
            }
        ],
    }
}

ARCHIVE_FIXTURE = {
    "response": {
        "numFound": 3,
        "docs": [
            {
                "identifier": "finance-reel",
                "title": "Finance reel",
                "creator": ["Alice", "Bob"],
                "licenseurl": "https://creativecommons.org/licenses/by/4.0/",
                "mediatype": "movies",
            },
            {
                "identifier": "unknown-licence",
                "title": "Budget clip",
                "creator": "Carol",
                "licenseurl": "https://archive.org/details/terms",
                "mediatype": "movies",
            },
            {
                "identifier": "wrong-media",
                "title": "Book scan",
                "licenseurl": "https://creativecommons.org/publicdomain/zero/1.0/",
                "mediatype": "texts",
            },
        ],
    }
}


def commercial_context() -> AssetUseContext:
    return AssetUseContext(
        use_class=AssetUseClass.COMMERCIAL,
        monetized=True,
        modification_planned=True,
        attribution_can_be_delivered=True,
    )


def test_wikimedia_mapping_keeps_attribution_and_share_alike():
    candidate = map_wikimedia_page(
        WIKIMEDIA_FIXTURE["query"]["pages"][0],
        media_type=AssetMediaType.IMAGE,
        response_hash="a" * 64,
    )
    assert candidate.source.provider.value == "wikimedia"
    assert candidate.source.creator_name == "Alice"
    assert candidate.rights.license_code == "by-sa"
    assert candidate.rights.commercial_use_allowed is True
    assert candidate.rights.modifications_allowed is True
    assert candidate.rights.attribution_required is True
    assert candidate.rights.origin_license_verified is False
    assert "CC BY-SA" in (candidate.rights.attribution_text or "")
    assert any(
        "Preserve the share-alike" in item for item in candidate.rights.obligations
    )


def test_wikimedia_search_returns_needs_review_for_cc_by_sa():
    client = WikimediaClient(fetcher=lambda url: json.dumps(WIKIMEDIA_FIXTURE).encode())
    payload = client.search_with_eligibility(
        "finance desk", commercial_context(), limit=1
    )
    assert payload["result_count"] == 1
    assert payload["results"][0]["eligibility"]["status"] == "needs_review"
    assert payload["response_hash"]


def test_archive_mapping_reads_item_metadata_without_downloading():
    candidate = map_archive_doc(
        ARCHIVE_FIXTURE["response"]["docs"][0],
        media_type=AssetMediaType.VIDEO,
        response_hash="b" * 64,
    )
    assert candidate is not None
    assert candidate.source.provider.value == "internet_archive"
    assert candidate.source.creator_name == "Alice, Bob"
    assert candidate.source.direct_asset_url is None
    assert candidate.rights.license_code == "by"
    assert candidate.rights.origin_license_verified is False
    assert candidate.source.source_page_url.host == "archive.org"


def test_archive_search_skips_media_type_mismatch_and_reports_it():
    client = InternetArchiveClient(
        fetcher=lambda url: json.dumps(ARCHIVE_FIXTURE).encode()
    )
    result = client.search("finance", media_type=AssetMediaType.VIDEO, limit=5)
    assert len(result.candidates) == 2
    assert result.skipped == 1
    assert result.result_count == 3


def test_archive_unknown_licence_requires_review():
    client = InternetArchiveClient(
        fetcher=lambda url: json.dumps(ARCHIVE_FIXTURE).encode()
    )
    payload = client.search_with_eligibility(
        "finance", commercial_context(), media_type=AssetMediaType.VIDEO, limit=5
    )
    statuses = {
        item["candidate"]["source"]["provider_asset_id"]: item["eligibility"]["status"]
        for item in payload["results"]
    }
    assert statuses["finance-reel"] == "needs_review"
    assert statuses["unknown-licence"] == "needs_review"


def test_openverse_default_context_is_reused_across_sources():
    context = default_context(monetized=True)
    assert context.requires_commercial_rights is True
    assert context.use_class == AssetUseClass.COMMERCIAL
    assert context.modification_planned is True
    assert AssetEligibilityStatus.NEEDS_REVIEW.value == "needs_review"

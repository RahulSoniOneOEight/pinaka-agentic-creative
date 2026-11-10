from __future__ import annotations

import json

import pytest

from pinaka.enums import AssetEligibilityStatus, AssetMediaType, AssetUseClass
from pinaka.errors import ConfigurationError
from pinaka.models import AssetUseContext
from pinaka.pexels import PexelsClient, map_pexels_result
from pinaka.sourcing import evaluate_asset_candidate
from pinaka.utils import sha256_bytes

PHOTO_FIXTURE = {
    "page": 1,
    "per_page": 1,
    "total_results": 8000,
    "photos": [
        {
            "id": 13802140,
            "width": 4000,
            "height": 6000,
            "url": "https://www.pexels.com/photo/people-in-a-meeting-13802140/",
            "photographer": "Fauxels",
            "photographer_url": "https://www.pexels.com/@fauxels",
            "avg_color": "#8C8C8C",
            "src": {
                "original": "https://images.pexels.com/photos/13802140/original.jpg",
                "large2x": "https://images.pexels.com/photos/13802140/large2x.jpg",
                "large": "https://images.pexels.com/photos/13802140/large.jpg",
                "medium": "https://images.pexels.com/photos/13802140/medium.jpg",
                "small": "https://images.pexels.com/photos/13802140/small.jpg",
            },
            "alt": "People in a meeting",
        }
    ],
}

VIDEO_FIXTURE = {
    "page": 1,
    "per_page": 1,
    "total_results": 120,
    "videos": [
        {
            "id": 3195394,
            "width": 1920,
            "height": 1080,
            "url": "https://www.pexels.com/video/people-in-an-office-3195394/",
            "image": "https://images.pexels.com/videos/3195394/pictures/preview-0.jpg",
            "duration": 12,
            "user": {
                "id": 2659,
                "name": "Cottonbro",
                "url": "https://www.pexels.com/@cottonbro",
            },
            "video_files": [
                {
                    "id": 1,
                    "quality": "sd",
                    "file_type": "video/mp4",
                    "width": 640,
                    "height": 360,
                    "link": "https://videos.pexels.com/video-files/3195394/3195394-sd.mp4",
                },
                {
                    "id": 2,
                    "quality": "hd",
                    "file_type": "video/mp4",
                    "width": 1920,
                    "height": 1080,
                    "link": "https://videos.pexels.com/video-files/3195394/3195394-hd.mp4",
                },
            ],
        }
    ],
}


def context() -> AssetUseContext:
    return AssetUseContext(
        use_class=AssetUseClass.COMMERCIAL,
        monetized=True,
        modification_planned=True,
        attribution_can_be_delivered=True,
    )


def test_pexels_requires_an_existing_key(monkeypatch, tmp_path):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.delenv("PEXELS_API_KEY_FILE", raising=False)
    monkeypatch.setattr("pinaka.pexels.DEFAULT_API_KEY_FILE", tmp_path / "missing-key")
    with pytest.raises(ConfigurationError, match="PEXELS_API_KEY"):
        PexelsClient(api_key="").search("teamwork")


def test_pexels_photo_mapping_applies_licence_and_provider_credit():
    candidate = map_pexels_result(
        PHOTO_FIXTURE["photos"][0],
        media_type=AssetMediaType.IMAGE,
        response_hash="a" * 64,
    )

    assert candidate.source.provider.value == "pexels"
    assert candidate.source.provider_asset_id == "13802140"
    assert candidate.source.media_type == AssetMediaType.IMAGE
    assert candidate.source.creator_name == "Fauxels"
    assert candidate.source.creator_url is not None
    assert str(candidate.source.direct_asset_url).endswith("original.jpg")
    assert candidate.rights.license_code == "pexels"
    assert candidate.rights.commercial_use_allowed is True
    assert candidate.rights.modifications_allowed is True
    assert candidate.rights.attribution_required is False
    assert candidate.rights.origin_license_verified is False
    assert candidate.compliance.provider_credit_planned is True
    duties = " ".join(candidate.rights.obligations).casefold()
    assert "prominent link to pexels" in duties
    assert "fauxels" in duties


def test_pexels_video_mapping_selects_the_largest_mp4():
    candidate = map_pexels_result(
        VIDEO_FIXTURE["videos"][0],
        media_type=AssetMediaType.VIDEO,
        response_hash="b" * 64,
    )

    assert candidate.source.media_type == AssetMediaType.VIDEO
    assert candidate.source.creator_name == "Cottonbro"
    assert str(candidate.source.direct_asset_url).endswith("3195394-hd.mp4")
    assert candidate.rights.license_code == "pexels"


def test_pexels_photo_needs_review_for_missing_licence_evidence():
    candidate = map_pexels_result(
        PHOTO_FIXTURE["photos"][0],
        media_type=AssetMediaType.IMAGE,
        response_hash="c" * 64,
    )

    eligibility = evaluate_asset_candidate(candidate, context())

    assert eligibility.status == AssetEligibilityStatus.NEEDS_REVIEW
    reasons = " ".join(eligibility.review_reasons).casefold()
    assert "licence evidence" in reasons
    assert not eligibility.blocking_reasons


def test_pexels_photo_search_hits_the_photo_endpoint():
    raw = json.dumps(PHOTO_FIXTURE).encode()
    seen: list[str] = []

    def fetcher(url: str, key: str) -> bytes:
        seen.append(url)
        return raw

    client = PexelsClient(api_key="test-key", fetcher=fetcher)
    result = client.search("teamwork", limit=1)

    assert "/v1/search" in seen[0]
    assert "per_page=1" in seen[0]
    assert result.result_count == 8000
    assert result.response_hash == sha256_bytes(raw)
    assert len(result.candidates) == 1


def test_pexels_video_search_hits_the_video_endpoint():
    raw = json.dumps(VIDEO_FIXTURE).encode()
    seen: list[str] = []

    def fetcher(url: str, key: str) -> bytes:
        seen.append(url)
        return raw

    client = PexelsClient(api_key="test-key", fetcher=fetcher)
    payload = client.search_with_eligibility(
        "office", context(), media_type=AssetMediaType.VIDEO, limit=1
    )

    assert "/videos/search" in seen[0]
    assert payload["media_type"] == "video"
    assert payload["results"][0]["candidate"]["source"]["media_type"] == "video"


def test_pexels_rejects_audio_media():
    client = PexelsClient(api_key="test-key", fetcher=lambda url, key: b"{}")
    with pytest.raises(ValueError, match="image and video"):
        client.search("teamwork", media_type=AssetMediaType.AUDIO)


def test_pexels_video_without_mp4_is_flagged():
    video = dict(VIDEO_FIXTURE["videos"][0])
    video["video_files"] = []

    candidate = map_pexels_result(
        video, media_type=AssetMediaType.VIDEO, response_hash="d" * 64
    )

    assert candidate.source.direct_asset_url is None
    assert any("mp4" in item.casefold() for item in candidate.rights.rights_uncertainty)

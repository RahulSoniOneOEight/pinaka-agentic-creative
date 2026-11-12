from __future__ import annotations

import json
from decimal import Decimal

import pytest

from pinaka.assembly import Segment, build_plan
from pinaka.enums import AssetMediaType
from pinaka.pexels import PexelsClient, choose_in_point


def video(asset_id: int, duration: float) -> dict:
    return {
        "id": asset_id,
        "width": 1920,
        "height": 1080,
        "url": f"https://www.pexels.com/video/{asset_id}/",
        "duration": duration,
        "user": {"name": "Creator", "url": "https://www.pexels.com/@creator"},
        "video_files": [
            {
                "file_type": "video/mp4",
                "width": 1920,
                "height": 1080,
                "link": f"https://videos.pexels.com/video-files/{asset_id}.mp4",
            }
        ],
    }


def client(payload: dict) -> PexelsClient:
    return PexelsClient(
        api_key="test-key",
        fetcher=lambda url, key: json.dumps(payload).encode("utf-8"),
    )


def test_duration_window_filters_and_prefers():
    payload = {"total_results": 3, "videos": [video(1, 5), video(2, 12), video(3, 40)]}

    result = client(payload).search(
        "office",
        media_type=AssetMediaType.VIDEO,
        min_duration_seconds=8,
        max_duration_seconds=30,
    )
    assert [item.source.provider_asset_id for item in result.candidates] == ["2"]
    assert result.durations == (12.0,)

    preferred = client(payload).search(
        "office",
        media_type=AssetMediaType.VIDEO,
        prefer_duration_seconds=15,
    )
    assert [item.source.provider_asset_id for item in preferred.candidates] == [
        "2",
        "1",
        "3",
    ]


def test_repeated_provider_ids_are_dropped():
    payload = {"total_results": 2, "videos": [video(7, 12), video(7, 12)]}

    result = client(payload).search("office", media_type=AssetMediaType.VIDEO)

    assert len(result.candidates) == 1


def test_bad_duration_window_is_refused():
    with pytest.raises(ValueError, match="min_duration_seconds"):
        client({"videos": []}).search(
            "office",
            media_type=AssetMediaType.VIDEO,
            min_duration_seconds=20,
            max_duration_seconds=10,
        )


def test_in_point_is_deterministic_and_inside_the_clip():
    first = choose_in_point(30.0, 5.0, seed="asset-7")
    second = choose_in_point(30.0, 5.0, seed="asset-7")

    assert first == second
    assert 0.0 <= first <= 25.0
    assert choose_in_point(4.0, 5.0, seed="short") == 0.0


def test_assembly_plan_carries_an_in_point():
    plan = build_plan(
        [Segment(index=0, path="a.mp4", duration=Decimal("2.5"), start=Decimal("4.25"))]
    )

    assert "trim=start=4.25:duration=2.5" in plan.filter_complex

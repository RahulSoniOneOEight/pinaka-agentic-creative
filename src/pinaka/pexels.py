from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .enums import (
    AssetAcquisitionMethod,
    AssetMediaType,
    AssetProvider,
)
from .errors import ConfigurationError
from .models import (
    AssetCandidate,
    AssetEligibilityResult,
    AssetRightsRecord,
    AssetSourceRecord,
    AssetUseContext,
    ProviderComplianceRecord,
)
from .openverse import USER_AGENT
from .sourcing import EVIDENCE_PENDING, REVIEW_DATE, evaluate_asset_candidate
from .utils import sha256_bytes

PEXELS_SEARCH_PHOTO = "https://api.pexels.com/v1/search"
PEXELS_SEARCH_VIDEO = "https://api.pexels.com/videos/search"
PEXELS_LICENSE = "https://www.pexels.com/license/"
PEXELS_TERMS = "https://www.pexels.com/terms-of-service/"
PEXELS_DOCS = "https://www.pexels.com/api/documentation/"
API_KEY_ENV = "PEXELS_API_KEY"
API_KEY_FILE_ENV = "PEXELS_API_KEY_FILE"
DEFAULT_API_KEY_FILE = (
    Path.home() / ".config" / "opencode" / "secrets" / "pexels-api-key"
)

ORIENTATIONS = {"landscape", "portrait", "square"}
SIZES = {"large", "medium", "small"}


@dataclass(frozen=True)
class PexelsSearchResult:
    candidates: tuple[AssetCandidate, ...]
    result_count: int
    page: int
    response_hash: str


def _preview_url(payload: dict[str, Any]) -> str | None:
    src = payload.get("src") or {}
    for key in ("original", "large2x", "large", "medium", "small"):
        value = src.get(key)
        if value:
            return value
    return None


def _best_video_file(payload: dict[str, Any]) -> str | None:
    files = [
        item
        for item in payload.get("video_files") or []
        if item.get("link") and item.get("file_type") == "video/mp4"
    ]
    if not files:
        return None

    def area(item: dict[str, Any]) -> int:
        return int(item.get("width") or 0) * int(item.get("height") or 0)

    return max(files, key=area).get("link")


def _creator(
    payload: dict[str, Any], media_type: AssetMediaType
) -> tuple[str, str | None]:
    if media_type == AssetMediaType.VIDEO:
        user = payload.get("user") or {}
        name = user.get("name") or "Unknown creator"
        return str(name), user.get("url")
    name = payload.get("photographer") or "Unknown creator"
    return str(name), payload.get("photographer_url")


def map_pexels_result(
    payload: dict[str, Any],
    *,
    media_type: AssetMediaType,
    response_hash: str,
) -> AssetCandidate:
    """Map one Pexels photo or video without asserting unverified licence facts."""

    asset_id = payload.get("id")
    if asset_id is None:
        raise ValueError("Pexels result is missing an id")

    creator_name, creator_url = _creator(payload, media_type)
    source_page = payload.get("url")
    if not source_page:
        kind = "videos" if media_type == AssetMediaType.VIDEO else "photo"
        source_page = f"https://www.pexels.com/{kind}/{asset_id}/"

    if media_type == AssetMediaType.VIDEO:
        direct_url = _best_video_file(payload)
    else:
        direct_url = _preview_url(payload)

    noun = "video" if media_type == AssetMediaType.VIDEO else "photo"
    uncertainty = [EVIDENCE_PENDING]
    if direct_url is None:
        uncertainty.append("The API response is missing a usable asset URL.")
    if media_type == AssetMediaType.VIDEO and _best_video_file(payload) is None:
        uncertainty.append("No MP4 video file was present in the API response.")

    obligations = [
        "Include a prominent link to Pexels as required by the API guidelines.",
        f"Retain contributor credit: {creator_name}.",
        "Do not sell unaltered copies or redistribute the asset as a competing stock service.",
    ]

    return AssetCandidate(
        source=AssetSourceRecord(
            provider=AssetProvider.PEXELS,
            provider_asset_id=str(asset_id)[:200],
            media_type=media_type,
            acquisition_method=AssetAcquisitionMethod.OFFICIAL_API,
            source_page_url=source_page,
            creator_name=creator_name[:300],
            creator_url=creator_url,
            direct_asset_url=direct_url,
            api_response_hash=response_hash,
        ),
        rights=AssetRightsRecord(
            license_name="Pexels License",
            license_code="pexels",
            license_url=PEXELS_LICENSE,
            terms_url=PEXELS_TERMS,
            terms_reviewed_at=REVIEW_DATE,
            commercial_use_allowed=True,
            modifications_allowed=True,
            editorial_only=False,
            attribution_required=False,
            attribution_text=f"{noun.capitalize()} by {creator_name} on Pexels",
            license_evidence_ref=None,
            license_evidence_hash=None,
            origin_license_verified=False,
            rights_uncertainty=tuple(uncertainty),
            obligations=tuple(obligations),
        ),
        compliance=ProviderComplianceRecord(
            human_selected=False,
            provider_credit_planned=True,
            creator_credit_planned=True,
        ),
    )


class PexelsClient:
    """Metadata-only Pexels search. It never downloads or imports media."""

    profile_key = "pexels-search"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = PEXELS_SEARCH_PHOTO,
        video_base_url: str = PEXELS_SEARCH_VIDEO,
        fetcher: Callable[[str, str], bytes] | None = None,
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.video_base_url = video_base_url
        self.fetcher = fetcher
        self.timeout = timeout

    def _resolve_key(self) -> str:
        key = (self.api_key or os.environ.get(API_KEY_ENV) or "").strip()
        if not key:
            configured = os.environ.get(API_KEY_FILE_ENV)
            path = Path(configured).expanduser() if configured else DEFAULT_API_KEY_FILE
            if path.is_file():
                key = path.read_text(encoding="utf-8").strip()
        if not key:
            raise ConfigurationError(
                f"Pexels requires an existing API key in {API_KEY_ENV} or a key file; "
                "no key is configured"
            )
        return key

    def _fetch(self, url: str, key: str) -> bytes:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Authorization": key,
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()

    def search(
        self,
        query: str,
        *,
        media_type: AssetMediaType = AssetMediaType.IMAGE,
        limit: int = 10,
        page: int = 1,
        orientation: str | None = None,
        size: str | None = None,
    ) -> PexelsSearchResult:
        if not query.strip():
            raise ValueError("Search query must not be empty")
        if media_type not in {AssetMediaType.IMAGE, AssetMediaType.VIDEO}:
            raise ValueError("Pexels supports image and video search only")
        if orientation is not None and orientation not in ORIENTATIONS:
            raise ValueError(f"orientation must be one of {sorted(ORIENTATIONS)}")
        if size is not None and size not in SIZES:
            raise ValueError(f"size must be one of {sorted(SIZES)}")

        key = self._resolve_key()
        params = {
            "query": query.strip(),
            "per_page": str(max(1, min(limit, 80))),
            "page": str(max(1, page)),
        }
        if orientation:
            params["orientation"] = orientation
        if size:
            params["size"] = size

        base = (
            self.video_base_url if media_type == AssetMediaType.VIDEO else self.base_url
        )
        url = f"{base}?{urllib.parse.urlencode(params)}"
        raw = (self.fetcher or self._fetch)(url, key)
        response_hash = sha256_bytes(raw)
        payload = json.loads(raw.decode("utf-8"))
        results = payload.get("photos") or payload.get("videos") or []
        candidates = tuple(
            map_pexels_result(
                result,
                media_type=media_type,
                response_hash=response_hash,
            )
            for result in results
        )
        total = payload.get("total_results")
        return PexelsSearchResult(
            candidates=candidates,
            result_count=int(total if total is not None else len(candidates)),
            page=max(1, page),
            response_hash=response_hash,
        )

    def search_with_eligibility(
        self,
        query: str,
        context: AssetUseContext,
        *,
        media_type: AssetMediaType = AssetMediaType.IMAGE,
        limit: int = 10,
        page: int = 1,
        orientation: str | None = None,
        size: str | None = None,
    ) -> dict[str, Any]:
        result = self.search(
            query,
            media_type=media_type,
            limit=limit,
            page=page,
            orientation=orientation,
            size=size,
        )
        evaluated = []
        for candidate in result.candidates:
            eligibility: AssetEligibilityResult = evaluate_asset_candidate(
                candidate, context
            )
            evaluated.append(
                {
                    "candidate": candidate.model_dump(mode="json"),
                    "eligibility": eligibility.model_dump(mode="json"),
                }
            )
        return {
            "provider": "pexels",
            "query": query,
            "media_type": media_type.value,
            "page": result.page,
            "result_count": result.result_count,
            "response_hash": result.response_hash,
            "results": evaluated,
        }

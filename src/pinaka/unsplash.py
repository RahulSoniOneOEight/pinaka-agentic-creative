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
from .sourcing import (
    EVIDENCE_PENDING,
    REVIEW_DATE,
    evaluate_asset_candidate,
)
from .utils import sha256_bytes

UNSPLASH_SEARCH = "https://api.unsplash.com/search/photos"
UNSPLASH_LICENSE = "https://unsplash.com/license"
UNSPLASH_GUIDELINES = (
    "https://help.unsplash.com/en/articles/2511245-unsplash-api-guidelines"
)
ACCESS_KEY_ENV = "UNSPLASH_ACCESS_KEY"
ACCESS_KEY_FILE_ENV = "UNSPLASH_ACCESS_KEY_FILE"
DEFAULT_ACCESS_KEY_FILE = (
    Path.home() / ".config" / "opencode" / "secrets" / "unsplash-access-key"
)

ORIENTATIONS = {"landscape", "portrait", "squarish"}
CONTENT_FILTERS = {"low", "high"}
ORDERINGS = {"relevant", "latest"}


@dataclass(frozen=True)
class UnsplashSearchResult:
    candidates: tuple[AssetCandidate, ...]
    result_count: int
    page: int
    response_hash: str


def _is_premium(photo: dict[str, Any]) -> bool:
    return photo.get("premium") is True or photo.get("plus") is True


def map_unsplash_result(
    photo: dict[str, Any],
    *,
    response_hash: str,
    human_selected: bool = False,
) -> AssetCandidate:
    """Map one Unsplash photo to a candidate without asserting unverified rights.

    Unsplash returns no per-item licence field: the Unsplash License applies to every
    free photo, while the API guidelines add contractual credit and download-event
    duties. Membership content (``premium``/``plus``) is not covered by that licence.
    """

    photo_id = str(photo.get("id") or "")
    if not photo_id:
        raise ValueError("Unsplash photo is missing an id")

    user = photo.get("user") or {}
    creator_name = str(user.get("name") or user.get("username") or "Unknown creator")
    creator_url = (user.get("links") or {}).get("html")
    links = photo.get("links") or {}
    source_page = links.get("html") or f"https://unsplash.com/photos/{photo_id}"
    urls = photo.get("urls") or {}
    preview_url = urls.get("regular") or urls.get("raw")

    premium = _is_premium(photo)
    uncertainty: list[str] = [
        EVIDENCE_PENDING,
        "Unsplash supplies no model or property release; check any recognizable person or brand.",
    ]
    obligations: list[str] = [
        "Credit the photographer with a link to their Unsplash profile.",
        "Credit Unsplash with a UTM-tracked link.",
        "Trigger the Unsplash download endpoint when the photo is selected.",
        "Use hotlinked image URLs for previews; never store a preview copy as the master.",
    ]
    if premium:
        uncertainty.append(
            "Unsplash+ membership content is not covered by the free Unsplash License."
        )
    if not links.get("download"):
        uncertainty.append("The API response is missing the download endpoint.")
    if not human_selected:
        uncertainty.append(
            "No operator selection is recorded for this photo; an API result is not a selection."
        )

    attribution_text = f"Photo by {creator_name} on Unsplash"

    return AssetCandidate(
        source=AssetSourceRecord(
            provider=AssetProvider.UNSPLASH,
            provider_asset_id=photo_id[:200],
            media_type=AssetMediaType.IMAGE,
            acquisition_method=AssetAcquisitionMethod.OFFICIAL_API,
            source_page_url=source_page,
            creator_name=creator_name[:300],
            creator_url=creator_url,
            direct_asset_url=preview_url,
            api_response_hash=response_hash,
        ),
        rights=AssetRightsRecord(
            license_name="Unsplash License",
            license_code="unsplash",
            license_url=UNSPLASH_LICENSE,
            terms_url=UNSPLASH_GUIDELINES,
            terms_reviewed_at=REVIEW_DATE,
            commercial_use_allowed=not premium,
            modifications_allowed=True,
            editorial_only=False,
            attribution_required=True,
            attribution_text=attribution_text,
            license_evidence_ref=None,
            license_evidence_hash=None,
            origin_license_verified=False,
            rights_uncertainty=tuple(uncertainty),
            obligations=tuple(obligations),
        ),
        compliance=ProviderComplianceRecord(
            human_selected=human_selected,
            hotlink_preview_used=preview_url is not None,
            download_event_recorded=False,
            provider_credit_planned=True,
            creator_credit_planned=True,
        ),
    )


class UnsplashClient:
    """Metadata-only Unsplash search. It never downloads or imports media."""

    profile_key = "unsplash-search"

    def __init__(
        self,
        *,
        access_key: str | None = None,
        base_url: str = UNSPLASH_SEARCH,
        fetcher: Callable[[str, str], bytes] | None = None,
        timeout: int = 30,
    ):
        self.access_key = access_key
        self.base_url = base_url
        self.fetcher = fetcher
        self.timeout = timeout

    def _resolve_key(self) -> str:
        key = (self.access_key or os.environ.get(ACCESS_KEY_ENV) or "").strip()
        if not key:
            configured = os.environ.get(ACCESS_KEY_FILE_ENV)
            path = (
                Path(configured).expanduser() if configured else DEFAULT_ACCESS_KEY_FILE
            )
            if path.is_file():
                key = path.read_text(encoding="utf-8").strip()
        if not key:
            raise ConfigurationError(
                f"Unsplash requires an existing access key in {ACCESS_KEY_ENV} or a key "
                "file; no key is configured"
            )
        return key

    def _fetch(self, url: str, key: str) -> bytes:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Authorization": f"Client-ID {key}",
                "Accept-Version": "v1",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        page: int = 1,
        orientation: str | None = None,
        content_filter: str | None = None,
        order_by: str | None = None,
        selected_asset_id: str | None = None,
    ) -> UnsplashSearchResult:
        if not query.strip():
            raise ValueError("Search query must not be empty")
        if orientation is not None and orientation not in ORIENTATIONS:
            raise ValueError(f"orientation must be one of {sorted(ORIENTATIONS)}")
        if content_filter is not None and content_filter not in CONTENT_FILTERS:
            raise ValueError(f"content_filter must be one of {sorted(CONTENT_FILTERS)}")
        if order_by is not None and order_by not in ORDERINGS:
            raise ValueError(f"order_by must be one of {sorted(ORDERINGS)}")

        key = self._resolve_key()
        params = {
            "query": query.strip(),
            "per_page": str(max(1, min(limit, 30))),
            "page": str(max(1, page)),
        }
        if orientation:
            params["orientation"] = orientation
        if content_filter:
            params["content_filter"] = content_filter
        if order_by:
            params["order_by"] = order_by
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"
        raw = (self.fetcher or self._fetch)(url, key)
        response_hash = sha256_bytes(raw)
        payload = json.loads(raw.decode("utf-8"))
        photos = payload.get("results") or []
        candidates = tuple(
            map_unsplash_result(
                photo,
                response_hash=response_hash,
                human_selected=selected_asset_id is not None
                and photo.get("id") == selected_asset_id,
            )
            for photo in photos
        )
        return UnsplashSearchResult(
            candidates=candidates,
            result_count=int(payload.get("total") or len(candidates)),
            page=max(1, page),
            response_hash=response_hash,
        )

    def search_with_eligibility(
        self,
        query: str,
        context: AssetUseContext,
        *,
        limit: int = 10,
        page: int = 1,
        orientation: str | None = None,
        content_filter: str | None = None,
        order_by: str | None = None,
        selected_asset_id: str | None = None,
    ) -> dict[str, Any]:
        result = self.search(
            query,
            limit=limit,
            page=page,
            orientation=orientation,
            content_filter=content_filter,
            order_by=order_by,
            selected_asset_id=selected_asset_id,
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
            "provider": "unsplash",
            "query": query,
            "media_type": AssetMediaType.IMAGE.value,
            "page": result.page,
            "result_count": result.result_count,
            "response_hash": result.response_hash,
            "results": evaluated,
        }

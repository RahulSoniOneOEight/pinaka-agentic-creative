from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .enums import (
    AssetAcquisitionMethod,
    AssetMediaType,
    AssetProvider,
)
from .licensing import license_flags
from .models import (
    AssetCandidate,
    AssetEligibilityResult,
    AssetRightsRecord,
    AssetSourceRecord,
    AssetUseContext,
    ProviderComplianceRecord,
)
from .openverse import USER_AGENT
from .sourcing import REVIEW_DATE, evaluate_asset_candidate
from .utils import sha256_bytes

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
WIKIMEDIA_TERMS = "https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use"
WIKIMEDIA_LICENSING = "https://commons.wikimedia.org/wiki/Commons:Licensing"


@dataclass(frozen=True)
class WikimediaSearchResult:
    candidates: tuple[AssetCandidate, ...]
    result_count: int
    offset: int
    response_hash: str


def _html_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = html.unescape(re.sub(r"<[^>]+>", " ", value))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _metadata_value(metadata: dict[str, Any], key: str) -> str | None:
    entry = metadata.get(key)
    if isinstance(entry, dict):
        value = entry.get("value")
        return str(value) if value is not None else None
    return None


def _http_url(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    candidate = value.strip()
    if candidate.startswith("//"):
        return f"https:{candidate}"
    if candidate.startswith(("http://", "https://")):
        return candidate
    return fallback


def map_wikimedia_page(
    page: dict[str, Any],
    *,
    media_type: AssetMediaType,
    response_hash: str,
) -> AssetCandidate:
    imageinfo = (page.get("imageinfo") or [{}])[0]
    metadata = imageinfo.get("extmetadata") or {}
    license_name = (
        _metadata_value(metadata, "LicenseShortName")
        or _metadata_value(metadata, "UsageTerms")
        or "Unknown licence"
    )
    license_url = _http_url(
        _metadata_value(metadata, "LicenseUrl"),
        WIKIMEDIA_LICENSING,
    )
    flags = license_flags(license_name)
    artist = _html_text(_metadata_value(metadata, "Artist")) or "Unknown creator"
    credit = _html_text(_metadata_value(metadata, "Credit"))
    attribution_text = (
        f"{artist} / Wikimedia Commons / {license_name} ({license_url})"
        if flags.attribution
        else None
    )
    uncertainty = [
        (
            "Wikimedia Commons metadata is community-maintained; verify the licence, "
            "authorship, and file page before approval."
        )
    ]
    if not flags.recognized:
        uncertainty.append(
            "The licence name was not recognized by this adapter; treat as unverified."
        )
    restrictions = _metadata_value(metadata, "Restrictions")
    if restrictions:
        uncertainty.append(f"Commons restrictions field: {_html_text(restrictions)}")
    obligations: list[str] = []
    if attribution_text:
        obligations.append(f"Include attribution: {attribution_text}")
    if flags.share_alike:
        obligations.append("Preserve the share-alike licence on derivatives")
    if credit:
        obligations.append(f"Retain source credit: {credit}")
    source_page = imageinfo.get("descriptionurl")
    if not source_page:
        title = urllib.parse.quote(str(page.get("title") or "File:Unknown"))
        source_page = f"https://commons.wikimedia.org/wiki/{title}"
    source_page = _http_url(source_page, WIKIMEDIA_LICENSING)
    return AssetCandidate(
        source=AssetSourceRecord(
            provider=AssetProvider.WIKIMEDIA,
            provider_asset_id=str(page.get("pageid") or page.get("title") or "unknown"),
            media_type=media_type,
            acquisition_method=AssetAcquisitionMethod.OFFICIAL_API,
            source_page_url=source_page,
            creator_name=artist[:300],
            direct_asset_url=_http_url(imageinfo.get("url"), "") or None,
            api_response_hash=response_hash,
        ),
        rights=AssetRightsRecord(
            license_name=license_name[:300],
            license_code=flags.code,
            license_url=license_url,
            terms_url=WIKIMEDIA_TERMS,
            terms_reviewed_at=REVIEW_DATE,
            commercial_use_allowed=flags.commercial,
            modifications_allowed=flags.modifications,
            editorial_only=False,
            attribution_required=flags.attribution,
            attribution_text=attribution_text,
            license_evidence_ref=None,
            license_evidence_hash=None,
            origin_license_verified=False,
            rights_uncertainty=tuple(uncertainty),
            obligations=tuple(obligations),
        ),
        compliance=ProviderComplianceRecord(
            human_selected=True,
            provider_credit_planned=True,
            creator_credit_planned=bool(flags.attribution),
        ),
    )


class WikimediaClient:
    """Metadata-only Wikimedia Commons search. It never downloads or imports media."""

    profile_key = "wikimedia-commons-search"

    def __init__(
        self,
        *,
        base_url: str = WIKIMEDIA_API,
        fetcher: Callable[[str], bytes] | None = None,
        timeout: int = 30,
    ):
        self.base_url = base_url
        self.fetcher = fetcher
        self.timeout = timeout

    def _fetch(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()

    def search(
        self,
        query: str,
        *,
        media_type: AssetMediaType = AssetMediaType.IMAGE,
        limit: int = 10,
        offset: int = 0,
    ) -> WikimediaSearchResult:
        if media_type not in {
            AssetMediaType.IMAGE,
            AssetMediaType.AUDIO,
            AssetMediaType.VIDEO,
        }:
            raise ValueError("Wikimedia Commons search supports image, audio and video")
        if not query.strip():
            raise ValueError("Search query must not be empty")
        page_size = max(1, min(limit, 50))
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "search",
            "gsrsearch": query.strip(),
            "gsrnamespace": "6",
            "gsrlimit": str(page_size),
            "gsroffset": str(max(0, offset)),
            "prop": "imageinfo",
            "iiprop": "url|extmetadata|mime",
        }
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"
        raw = (self.fetcher or self._fetch)(url)
        response_hash = sha256_bytes(raw)
        payload = json.loads(raw.decode("utf-8"))
        pages = (payload.get("query") or {}).get("pages") or []
        candidates = tuple(
            map_wikimedia_page(
                page,
                media_type=media_type,
                response_hash=response_hash,
            )
            for page in pages
        )
        return WikimediaSearchResult(
            candidates=candidates,
            result_count=int(
                (payload.get("query") or {})
                .get("searchinfo", {})
                .get("totalhits", len(candidates))
            ),
            offset=max(0, offset),
            response_hash=response_hash,
        )

    def search_with_eligibility(
        self,
        query: str,
        context: AssetUseContext,
        *,
        media_type: AssetMediaType = AssetMediaType.IMAGE,
        limit: int = 10,
        offset: int = 0,
    ) -> dict[str, Any]:
        result = self.search(
            query,
            media_type=media_type,
            limit=limit,
            offset=offset,
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
            "provider": "wikimedia",
            "query": query,
            "media_type": media_type.value,
            "offset": result.offset,
            "result_count": result.result_count,
            "response_hash": result.response_hash,
            "results": evaluated,
        }

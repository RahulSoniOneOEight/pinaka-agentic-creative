from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .enums import (
    AssetAcquisitionMethod,
    AssetMediaType,
    AssetProvider,
    AssetUseClass,
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
from .sourcing import REVIEW_DATE, evaluate_asset_candidate
from .utils import sha256_bytes

OPENVERSE_TERMS = "https://docs.openverse.org/terms_of_service.html"
OPENVERSE_BASE = "https://api.openverse.org/v1"
USER_AGENT = "pinaka-agentic-creative/0.1 (governed source search)"


@dataclass(frozen=True)
class OpenverseSearchResult:
    candidates: tuple[AssetCandidate, ...]
    result_count: int
    page: int
    response_hash: str


def _license_flags(code: str | None) -> dict[str, bool | None]:
    return license_flags(code).as_dict()


def map_openverse_result(
    result: dict[str, Any],
    *,
    media_type: AssetMediaType,
    response_hash: str,
) -> AssetCandidate:
    license_code = result.get("license") or None
    flags = _license_flags(license_code)
    license_name = result.get("license") or "Unknown licence"
    license_url = result.get("license_url") or "https://creativecommons.org/"
    source_page = (
        result.get("foreign_landing_url")
        or result.get("url")
        or "https://openverse.org/"
    )
    creator = result.get("creator") or "Unknown creator"
    creator_url = result.get("creator_url") or None
    attribution_text = (
        f"{creator} / {result.get('source') or 'Openverse'} / "
        f"{license_name} ({license_url})"
        if flags["attribution"]
        else None
    )
    uncertainty = [
        "Openverse does not verify licence metadata; verify the licence at the origin page."
    ]
    if flags["commercial"] is None:
        uncertainty.append("Licence code is not recognized by this adapter.")
    obligations: list[str] = []
    if flags["attribution"]:
        obligations.append(f"Include attribution: {attribution_text}")
    if flags["share_alike"]:
        obligations.append("Preserve the share-alike licence on derivatives")
    if result.get("source"):
        obligations.append(
            "State that the search used Openverse and is not endorsed by Openverse."
        )
    candidate = AssetCandidate(
        source=AssetSourceRecord(
            provider=AssetProvider.OPENVERSE,
            provider_asset_id=str(
                result.get("id")
                or sha256_bytes(json.dumps(result, sort_keys=True).encode("utf-8"))[:24]
            ),
            media_type=media_type,
            acquisition_method=AssetAcquisitionMethod.OFFICIAL_API,
            source_page_url=source_page,
            creator_name=creator,
            creator_url=creator_url,
            direct_asset_url=result.get("url") or None,
            api_response_hash=response_hash,
        ),
        rights=AssetRightsRecord(
            license_name=license_name,
            license_code=license_code,
            license_url=license_url,
            terms_url=OPENVERSE_TERMS,
            terms_reviewed_at=REVIEW_DATE,
            commercial_use_allowed=flags["commercial"],
            modifications_allowed=flags["modifications"],
            editorial_only=False,
            attribution_required=flags["attribution"],
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
            creator_credit_planned=bool(flags["attribution"]),
            openverse_disclosure_planned=bool(result.get("source")),
        ),
    )
    return candidate


class OpenverseClient:
    """Metadata-only Openverse search. It never downloads or imports media."""

    profile_key = "openverse-search"

    def __init__(
        self,
        *,
        base_url: str = OPENVERSE_BASE,
        fetcher: Callable[[str], bytes] | None = None,
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
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
        page: int = 1,
        license_type: str | None = None,
    ) -> OpenverseSearchResult:
        if media_type not in {AssetMediaType.IMAGE, AssetMediaType.AUDIO}:
            raise ValueError("Openverse supports images and audio only")
        if not query.strip():
            raise ValueError("Search query must not be empty")
        page_size = max(1, min(limit, 20))
        params = {
            "q": query.strip(),
            "page_size": str(page_size),
            "page": str(max(1, page)),
            "mature": "false",
        }
        if license_type:
            params["license_type"] = license_type
        endpoint = f"{self.base_url}/{media_type.value}s/"
        url = f"{endpoint}?{urllib.parse.urlencode(params)}"
        raw = (self.fetcher or self._fetch)(url)
        response_hash = sha256_bytes(raw)
        payload = json.loads(raw.decode("utf-8"))
        results = payload.get("results") or []
        candidates = tuple(
            map_openverse_result(
                result,
                media_type=media_type,
                response_hash=response_hash,
            )
            for result in results
        )
        return OpenverseSearchResult(
            candidates=candidates,
            result_count=int(payload.get("result_count") or len(candidates)),
            page=int(payload.get("page") or page),
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
        license_type: str | None = None,
    ) -> dict[str, Any]:
        result = self.search(
            query,
            media_type=media_type,
            limit=limit,
            page=page,
            license_type=license_type,
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
            "provider": "openverse",
            "query": query,
            "media_type": media_type.value,
            "page": result.page,
            "result_count": result.result_count,
            "response_hash": result.response_hash,
            "results": evaluated,
        }


def default_context(
    *,
    use_class: AssetUseClass = AssetUseClass.COMMERCIAL,
    monetized: bool = False,
    advertising: bool = False,
    modification_planned: bool = True,
    attribution_can_be_delivered: bool = True,
) -> AssetUseContext:
    return AssetUseContext(
        use_class=use_class,
        monetized=monetized,
        advertising=advertising,
        modification_planned=modification_planned,
        attribution_can_be_delivered=attribution_can_be_delivered,
    )

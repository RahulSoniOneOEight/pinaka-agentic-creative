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

FREESOUND_SEARCH = "https://freesound.org/apiv2/search/text/"
FREESOUND_TERMS = "https://freesound.org/help/tos_web/"
FREESOUND_API_DOCS = "https://freesound.org/docs/api/"
TOKEN_ENV = "FREESOUND_API_KEY"
TOKEN_FILE_ENV = "FREESOUND_API_KEY_FILE"
DEFAULT_TOKEN_FILE = (
    Path.home() / ".config" / "opencode" / "secrets" / "freesound-api-key"
)


@dataclass(frozen=True)
class FreesoundSearchResult:
    candidates: tuple[AssetCandidate, ...]
    result_count: int
    page: int
    response_hash: str


def _license_label(code: str | None, recognized: bool) -> str:
    if not recognized:
        return "Unknown licence"
    if code == "cc0":
        return "CC0 1.0"
    if code == "pdm":
        return "Public domain mark"
    if code:
        return f"Creative Commons {code.upper()}"
    return "Unknown licence"


def map_freesound_result(
    result: dict[str, Any], *, response_hash: str
) -> AssetCandidate:
    sound_id = str(result.get("id") or "").strip()
    if not sound_id:
        raise ValueError("Freesound result has no id")
    username = str(result.get("username") or "").strip() or "Unknown creator"
    license_url = str(result.get("license") or "").strip() or FREESOUND_TERMS
    flags = license_flags(license_url)
    license_name = _license_label(flags.code, flags.recognized)
    source_page = (
        str(result.get("url") or "").strip()
        or f"https://freesound.org/people/{urllib.parse.quote(username)}/sounds/{sound_id}/"
    )
    name = str(result.get("name") or "Untitled sound").strip()
    attribution_text = (
        f'"{name}" by {username} / Freesound / {license_name} ({license_url})'
        if flags.attribution
        else None
    )
    uncertainty = [
        (
            "Freesound metadata is uploader-supplied; verify the licence and attribution "
            "on the sound page before approval."
        ),
        "Freesound previews are audition-only; the original requires a governed import.",
    ]
    if not flags.recognized:
        uncertainty.append(
            "The licence URL was not recognized by this adapter; treat as unverified."
        )
    obligations: list[str] = []
    if attribution_text:
        obligations.append(f"Include attribution: {attribution_text}")
    if flags.share_alike:
        obligations.append("Preserve the share-alike licence on derivatives")
    return AssetCandidate(
        source=AssetSourceRecord(
            provider=AssetProvider.FREESOUND,
            provider_asset_id=sound_id[:200],
            media_type=AssetMediaType.AUDIO,
            acquisition_method=AssetAcquisitionMethod.OFFICIAL_API,
            source_page_url=source_page,
            creator_name=username[:300],
            creator_url=f"https://freesound.org/people/{urllib.parse.quote(username)}/",
            direct_asset_url=None,
            api_response_hash=response_hash,
        ),
        rights=AssetRightsRecord(
            license_name=license_name,
            license_code=flags.code,
            license_url=license_url,
            terms_url=FREESOUND_TERMS,
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
            creator_credit_planned=True,
        ),
    )


class FreesoundClient:
    """Metadata-only Freesound search. It never downloads or imports audio."""

    profile_key = "freesound-search"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = FREESOUND_SEARCH,
        fetcher: Callable[[str, str], bytes] | None = None,
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.fetcher = fetcher
        self.timeout = timeout

    def _resolve_key(self) -> str:
        key = (self.api_key or os.environ.get(TOKEN_ENV) or "").strip()
        if not key:
            configured = os.environ.get(TOKEN_FILE_ENV)
            path = Path(configured).expanduser() if configured else DEFAULT_TOKEN_FILE
            if path.is_file():
                key = path.read_text(encoding="utf-8").strip()
        if not key:
            raise ConfigurationError(
                f"Freesound requires an existing API key in {TOKEN_ENV} or a key file; "
                "no key is configured"
            )
        return key

    def _fetch(self, url: str, key: str) -> bytes:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Authorization": f"Token {key}",
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
    ) -> FreesoundSearchResult:
        if not query.strip():
            raise ValueError("Search query must not be empty")
        key = self._resolve_key()
        page_size = max(1, min(limit, 150))
        params = {
            "query": query.strip(),
            "page_size": str(page_size),
            "page": str(max(1, page)),
            "fields": "id,name,license,username,url,previews,description",
        }
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"
        raw = (self.fetcher or self._fetch)(url, key)
        response_hash = sha256_bytes(raw)
        payload = json.loads(raw.decode("utf-8"))
        results = payload.get("results") or []
        candidates = tuple(
            map_freesound_result(result, response_hash=response_hash)
            for result in results
        )
        return FreesoundSearchResult(
            candidates=candidates,
            result_count=int(payload.get("count") or len(candidates)),
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
    ) -> dict[str, Any]:
        result = self.search(query, limit=limit, page=page)
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
            "provider": "freesound",
            "query": query,
            "media_type": "audio",
            "page": result.page,
            "result_count": result.result_count,
            "response_hash": result.response_hash,
            "terms": FREESOUND_API_DOCS,
            "results": evaluated,
        }

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

ARCHIVE_SEARCH = "https://archive.org/advancedsearch.php"
ARCHIVE_TERMS = "https://archive.org/about/terms.php"
ARCHIVE_DETAILS = "https://archive.org/details"
MEDIATYPE_MAP = {
    "image": AssetMediaType.IMAGE,
    "audio": AssetMediaType.AUDIO,
    "movies": AssetMediaType.VIDEO,
    "video": AssetMediaType.VIDEO,
}


@dataclass(frozen=True)
class ArchiveSearchResult:
    candidates: tuple[AssetCandidate, ...]
    result_count: int
    page: int
    response_hash: str
    skipped: int


def _http_url(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    candidate = value.strip()
    if candidate.startswith("//"):
        return f"https:{candidate}"
    if candidate.startswith(("http://", "https://")):
        return candidate
    return fallback


def _creator(value: Any) -> str:
    if isinstance(value, list):
        names = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(names) or "Unknown creator"
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "Unknown creator"


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


def map_archive_doc(
    doc: dict[str, Any],
    *,
    media_type: AssetMediaType,
    response_hash: str,
) -> AssetCandidate | None:
    identifier = str(doc.get("identifier") or "").strip()
    if not identifier:
        return None
    doc_media = MEDIATYPE_MAP.get(str(doc.get("mediatype") or "").casefold())
    raw_media = str(doc.get("mediatype") or "").strip()
    if raw_media and (doc_media is None or doc_media != media_type):
        return None
    license_url = _http_url(doc.get("licenseurl"), ARCHIVE_TERMS)
    flags = license_flags(license_url)
    license_name = _license_label(flags.code, flags.recognized)
    creator = _creator(doc.get("creator"))
    source_page = f"{ARCHIVE_DETAILS}/{urllib.parse.quote(identifier)}"
    attribution_text = (
        f"{creator} / Internet Archive / {license_name} ({license_url})"
        if flags.attribution
        else None
    )
    uncertainty = [
        (
            "Internet Archive search returns item-level metadata; verify the item page, "
            "the selected file, and the uploader's rights before approval."
        )
    ]
    if not flags.recognized:
        uncertainty.append(
            "No recognized item licence was returned by the search index; "
            "commercial use is unverified."
        )
    obligations = [
        "State that the search used the Internet Archive and is not endorsed by it."
    ]
    if attribution_text:
        obligations.append(f"Include attribution: {attribution_text}")
    if flags.share_alike:
        obligations.append("Preserve the share-alike licence on derivatives")
    return AssetCandidate(
        source=AssetSourceRecord(
            provider=AssetProvider.INTERNET_ARCHIVE,
            provider_asset_id=identifier[:200],
            media_type=media_type,
            acquisition_method=AssetAcquisitionMethod.OFFICIAL_API,
            source_page_url=source_page,
            creator_name=creator[:300],
            direct_asset_url=None,
            api_response_hash=response_hash,
        ),
        rights=AssetRightsRecord(
            license_name=license_name,
            license_code=flags.code,
            license_url=license_url,
            terms_url=ARCHIVE_TERMS,
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


class InternetArchiveClient:
    """Metadata-only Internet Archive search. It never downloads or imports media."""

    profile_key = "internet-archive-search"

    def __init__(
        self,
        *,
        base_url: str = ARCHIVE_SEARCH,
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
        page: int = 1,
    ) -> ArchiveSearchResult:
        if media_type not in {
            AssetMediaType.IMAGE,
            AssetMediaType.AUDIO,
            AssetMediaType.VIDEO,
        }:
            raise ValueError("Internet Archive search supports image, audio and video")
        media_type = AssetMediaType(media_type)
        if not query.strip():
            raise ValueError("Search query must not be empty")
        rows = max(1, min(limit, 50))
        current_page = max(1, page)
        media_filter = {
            AssetMediaType.IMAGE: "image",
            AssetMediaType.AUDIO: "audio",
            AssetMediaType.VIDEO: "movies",
        }[media_type]
        params = [
            ("q", f"({query.strip()}) AND mediatype:{media_filter}"),
            ("rows", str(rows)),
            ("page", str(current_page)),
            ("output", "json"),
        ]
        for field in ("identifier", "title", "creator", "licenseurl", "mediatype"):
            params.append(("fl[]", field))
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"
        raw = (self.fetcher or self._fetch)(url)
        response_hash = sha256_bytes(raw)
        payload = json.loads(raw.decode("utf-8"))
        response = payload.get("response") or {}
        docs = response.get("docs") or []
        mapped = [
            map_archive_doc(doc, media_type=media_type, response_hash=response_hash)
            for doc in docs
        ]
        candidates = tuple(item for item in mapped if item is not None)
        return ArchiveSearchResult(
            candidates=candidates,
            result_count=int(response.get("numFound") or len(candidates)),
            page=current_page,
            response_hash=response_hash,
            skipped=len(docs) - len(candidates),
        )

    def search_with_eligibility(
        self,
        query: str,
        context: AssetUseContext,
        *,
        media_type: AssetMediaType = AssetMediaType.IMAGE,
        limit: int = 10,
        page: int = 1,
    ) -> dict[str, Any]:
        result = self.search(
            query,
            media_type=media_type,
            limit=limit,
            page=page,
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
            "provider": "internet_archive",
            "query": query,
            "media_type": media_type.value,
            "page": result.page,
            "result_count": result.result_count,
            "skipped": result.skipped,
            "response_hash": result.response_hash,
            "results": evaluated,
        }

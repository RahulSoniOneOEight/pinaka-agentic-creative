from __future__ import annotations

import urllib.request
from collections.abc import Callable
from typing import Literal

from pydantic import Field, HttpUrl

from .errors import ConfigurationError
from .models import StrictModel
from .openverse import USER_AGENT
from .utils import sha256_bytes, utc_now

MAX_EVIDENCE_BYTES = 4_000_000
TEXT_CONTENT_PREFIXES = ("text/", "application/json", "application/xhtml")

EvidenceKind = Literal["licence_page", "item_page"]


class LicenceEvidence(StrictModel):
    kind: EvidenceKind
    url: HttpUrl
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0)
    content_type: str | None = None
    fetched_at: str


class EvidenceDocument(StrictModel):
    evidence: LicenceEvidence
    content: bytes


Fetcher = Callable[[str], tuple[bytes, str | None]]


def _default_fetch(url: str, timeout: int, max_bytes: int) -> tuple[bytes, str | None]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type")
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ConfigurationError(
            f"Evidence page exceeds {max_bytes} bytes and was not stored: {url}"
        )
    if not content_type:
        raise ConfigurationError(f"Evidence page returned no content type: {url}")
    if not content_type.lower().startswith(TEXT_CONTENT_PREFIXES):
        raise ConfigurationError(
            f"Evidence page is not a text document ({content_type}): {url}"
        )
    return data, content_type


def capture_document(
    kind: EvidenceKind,
    url: str,
    *,
    fetcher: Fetcher | None = None,
    timeout: int = 30,
    max_bytes: int = MAX_EVIDENCE_BYTES,
) -> EvidenceDocument:
    """Fetch and hash a licence or item page for per-asset evidence."""

    if fetcher is not None:
        content, content_type = fetcher(url)
    else:
        content, content_type = _default_fetch(url, timeout, max_bytes)
    if not content:
        raise ConfigurationError(f"Evidence page is empty: {url}")
    return EvidenceDocument(
        evidence=LicenceEvidence(
            kind=kind,
            url=url,
            content_hash=sha256_bytes(content),
            size_bytes=len(content),
            content_type=content_type,
            fetched_at=utc_now(),
        ),
        content=content,
    )

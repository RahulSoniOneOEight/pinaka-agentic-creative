from __future__ import annotations

import re
from dataclasses import dataclass

PUBLIC_DOMAIN_CODES = {
    "cc0",
    "pdm",
    "pd",
    "public-domain",
    "publicdomain",
    "public-domain-mark",
}
CREATIVE_COMMONS_CODES = {
    "by",
    "by-sa",
    "by-nd",
    "by-nc",
    "by-nc-sa",
    "by-nc-nd",
}
UNRECOGNIZED_CODES = {
    "gfdl",
    "copyright",
    "all-rights-reserved",
    "fair-use",
    "unknown",
    "none",
}


@dataclass(frozen=True)
class LicenseFlags:
    code: str | None
    commercial: bool | None
    modifications: bool | None
    attribution: bool | None
    share_alike: bool | None
    recognized: bool

    def as_dict(self) -> dict[str, bool | None]:
        return {
            "commercial": self.commercial,
            "modifications": self.modifications,
            "attribution": self.attribution,
            "share_alike": self.share_alike,
        }


def _normalize(value: str) -> str:
    normalized = value.strip().casefold().replace("_", "-")
    if "/" in normalized:
        parts = [part for part in normalized.split("/") if part]
        if "licenses" in parts:
            normalized = parts[parts.index("licenses") + 1]
        elif "publicdomain" in parts:
            normalized = "cc0" if "zero" in parts else "pdm"
        else:
            normalized = parts[-1]
    normalized = normalized.removeprefix("cc-").removeprefix("cc ")
    normalized = re.sub(r"\s+v?\d+(\.\d+)?$", "", normalized)
    normalized = re.sub(r"\s+", "-", normalized)
    return normalized


def license_flags(value: str | None) -> LicenseFlags:
    if not value:
        return LicenseFlags(None, None, None, None, None, False)
    normalized = _normalize(value)
    if normalized in PUBLIC_DOMAIN_CODES:
        return LicenseFlags(
            code="cc0" if normalized == "cc0" else "pdm",
            commercial=True,
            modifications=True,
            attribution=False,
            share_alike=False,
            recognized=True,
        )
    if normalized in CREATIVE_COMMONS_CODES:
        return LicenseFlags(
            code=normalized,
            commercial="-nc" not in f"-{normalized}",
            modifications="-nd" not in f"-{normalized}",
            attribution=True,
            share_alike="-sa" in f"-{normalized}",
            recognized=True,
        )
    return LicenseFlags(
        code=normalized or None,
        commercial=None,
        modifications=None,
        attribution=None,
        share_alike=None,
        recognized=False,
    )


def is_unrecognized(value: str | None) -> bool:
    if not value:
        return True
    normalized = _normalize(value)
    return (
        normalized not in PUBLIC_DOMAIN_CODES
        and normalized not in CREATIVE_COMMONS_CODES
    )

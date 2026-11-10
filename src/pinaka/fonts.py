from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, HttpUrl

from .models import StrictModel
from .utils import sha256_file, sha256_json, utc_now

FONT_EXTENSIONS = {".ttf", ".otf", ".woff", ".woff2", ".ttc"}
MetadataSource = Literal["fonttools", "filename", "none"]
FontStatus = Literal["eligible", "needs_review", "blocked"]


class FontRecord(StrictModel):
    relative_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=0)
    family: str | None = None
    subfamily: str | None = None
    full_name: str | None = None
    version: str | None = None
    metadata_source: MetadataSource = "none"


class FontInventory(StrictModel):
    schema_version: str = "font-inventory/v1"
    generated_at: str
    root: str
    fonts: tuple[FontRecord, ...]
    issues: tuple[str, ...] = ()
    manifest_hash: str


class FontLicence(StrictModel):
    font_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    license_name: str = Field(min_length=1, max_length=300)
    license_url: HttpUrl | None = None
    embedding_allowed: bool | None = None
    redistribution_allowed: bool | None = None
    attribution_required: bool = False
    evidence_ref: str | None = Field(default=None, max_length=1000)
    notes: str | None = Field(default=None, max_length=2000)


class FontCheck(StrictModel):
    relative_path: str
    sha256: str
    status: FontStatus
    findings: tuple[str, ...] = ()
    licence: FontLicence | None = None


class FontReport(StrictModel):
    schema_version: str = "font-report/v1"
    generated_at: str
    inventory_hash: str
    counts: dict[str, int]
    fonts: tuple[FontCheck, ...]
    manifest_hash: str


def _name(ttfont: Any, name_id: int) -> str | None:
    record = ttfont["name"].getName(name_id, 3, 1, 0x409)
    if record is None:
        record = ttfont["name"].getName(name_id, 1, 0, 0)
    if record is None:
        return None
    try:
        value = record.toUnicode().strip()
    except (AttributeError, UnicodeDecodeError):
        return None
    return value or None


def _metadata(path: Path) -> tuple[dict[str, str | None], str | None]:
    try:
        from fontTools.ttLib import TTFont, TTLibError
    except ImportError:
        return {}, "fontTools is not installed; font names were not read"
    try:
        ttfont = TTFont(path, lazy=True, fontNumber=0)
    except (OSError, TTLibError, ValueError, KeyError, struct.error) as error:
        return {}, f"Font metadata could not be read: {type(error).__name__}"
    try:
        return (
            {
                "family": _name(ttfont, 1),
                "subfamily": _name(ttfont, 2),
                "full_name": _name(ttfont, 4),
                "version": _name(ttfont, 5),
            },
            None,
        )
    except (OSError, TTLibError, ValueError, KeyError, struct.error) as error:
        return {}, f"Font metadata could not be read: {type(error).__name__}"
    finally:
        ttfont.close()


def inventory_fonts(root: Path | str) -> FontInventory:
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise FileNotFoundError(f"Font directory not found: {base}")
    fonts: list[FontRecord] = []
    issues: list[str] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix.casefold() not in FONT_EXTENSIONS:
            continue
        relative = path.relative_to(base).as_posix()
        metadata, issue = _metadata(path)
        if issue:
            issues.append(f"{relative}: {issue}")
        source: MetadataSource = "fonttools" if metadata else "none"
        if not metadata and not issue:
            source = "filename"
        fonts.append(
            FontRecord(
                relative_path=relative,
                sha256=sha256_file(path),
                bytes=path.stat().st_size,
                family=metadata.get("family"),
                subfamily=metadata.get("subfamily"),
                full_name=metadata.get("full_name"),
                version=metadata.get("version"),
                metadata_source=source,
            )
        )
    if not fonts:
        issues.append("No font files were found in the directory.")
    body = {
        "generated_at": utc_now(),
        "root": str(base),
        "fonts": [font.model_dump(mode="json") for font in fonts],
        "issues": issues,
    }
    return FontInventory(**body, manifest_hash=sha256_json(body))


def check_fonts(
    inventory: FontInventory,
    licences: list[FontLicence],
) -> FontReport:
    by_hash = {licence.font_sha256: licence for licence in licences}
    checks: list[FontCheck] = []
    counts = {"eligible": 0, "needs_review": 0, "blocked": 0}
    for font in inventory.fonts:
        licence = by_hash.get(font.sha256)
        findings: list[str] = []
        status: FontStatus
        if licence is None:
            status = "blocked"
            findings.append(
                "No licence record exists for this exact font hash; the font is unreviewed."
            )
        elif licence.embedding_allowed is False:
            status = "blocked"
            findings.append("The licence does not permit embedding the font.")
        elif licence.embedding_allowed is None:
            status = "needs_review"
            findings.append("Embedding permission has not been verified.")
        elif licence.redistribution_allowed is False:
            status = "needs_review"
            findings.append(
                "Redistribution is prohibited; keep the font file out of deliverables."
            )
        elif licence.license_url is None:
            status = "needs_review"
            findings.append("The licence has no evidence URL.")
        elif not licence.evidence_ref:
            status = "needs_review"
            findings.append("The licence has no evidence reference.")
        else:
            status = "eligible"
        counts[status] += 1
        checks.append(
            FontCheck(
                relative_path=font.relative_path,
                sha256=font.sha256,
                status=status,
                findings=tuple(findings),
                licence=licence,
            )
        )
    body = {
        "generated_at": utc_now(),
        "inventory_hash": inventory.manifest_hash,
        "counts": counts,
        "fonts": [check.model_dump(mode="json") for check in checks],
    }
    return FontReport(**body, manifest_hash=sha256_json(body))


def font_report_payload(report: FontReport) -> dict[str, Any]:
    return report.model_dump(mode="json")

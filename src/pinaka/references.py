from __future__ import annotations

from typing import Any

# Provider field names are the documented request keys for the reference roles.
# They must be re-verified against the live API before the first paid call.
PROVIDER_FIELDS: dict[str, dict[str, str]] = {
    "veo": {
        "character": "reference_images",
        "location": "reference_images",
        "style": "reference_images",
        "product": "reference_images",
        "first_frame": "image",
        "last_frame": "last_frame",
    },
    "runway": {
        "character": "reference_images",
        "location": "reference_images",
        "style": "reference_images",
        "product": "reference_images",
        "first_frame": "promptImage",
        "last_frame": "lastFrame",
    },
}

SUPPORTED_PROVIDERS = tuple(PROVIDER_FIELDS)


def provider_reference_payload(
    references: list[dict[str, Any]], provider: str
) -> dict[str, Any]:
    """Map governed reference roles onto one provider's request fields."""

    fields = PROVIDER_FIELDS.get(provider)
    if fields is None:
        raise ValueError(
            f"Unsupported reference provider {provider!r}; "
            f"supported: {', '.join(SUPPORTED_PROVIDERS)}"
        )
    payload: dict[str, Any] = {}
    for reference in references:
        role = reference.get("role")
        artifact = reference.get("artifact") or {}
        path = artifact.get("relative_path")
        content_hash = artifact.get("content_hash")
        if not path or not content_hash:
            continue
        field = fields.get(str(role))
        if field is None:
            continue
        if field in {"reference_images"}:
            payload.setdefault(field, []).append({"path": path, "sha256": content_hash})
        else:
            payload[field] = {"path": path, "sha256": content_hash}
    return payload


def reference_summary(references: list[dict[str, Any]]) -> dict[str, Any]:
    """Compact, auditable description used inside the work packet."""

    return {
        "count": len(references),
        "roles": sorted({str(item.get("role")) for item in references}),
        "shots": sorted({str(item.get("shot_id")) for item in references}),
    }

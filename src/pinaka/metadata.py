"""Deterministic title/description drafting from governed project facts.

No language model is involved: the draft is a template over approved facts, so
it can be reviewed and approved like any other artifact.
"""

from __future__ import annotations

from typing import Any

from .publication import CHANNELS


def _clean(value: str) -> str:
    return " ".join(str(value or "").split())


def _truncate(value: str, limit: int) -> str:
    text = _clean(value)
    if len(text) <= limit:
        return text
    clipped = text[: limit - 1].rsplit(" ", 1)[0] or text[: limit - 1]
    return clipped.rstrip(" ,.;:") + "…"


def _hashtag(value: str) -> str:
    cleaned = "".join(
        character
        for character in _clean(value)
        if character.isalnum() or character == "_"
    )
    return f"#{cleaned}" if cleaned else ""


def draft_metadata(
    *,
    name: str,
    objective: str,
    channel: str,
    claims: tuple[str, ...] = (),
    cta: str | None = None,
    hashtags: tuple[str, ...] = (),
    locale: str = "en",
) -> dict[str, Any]:
    """Draft channel-sized copy from approved project fields."""

    profile = CHANNELS.get(channel)
    if profile is None:
        raise ValueError(
            f"Unknown channel {channel!r}; expected one of {sorted(CHANNELS)}"
        )
    title = _truncate(name, profile.max_title_chars)
    if not title:
        raise ValueError("Metadata drafting needs a project name")

    parts = [_clean(objective)] if _clean(objective) else []
    approved_claims = [_clean(claim) for claim in claims if _clean(claim)]
    if approved_claims:
        parts.append("\n".join(f"• {claim}" for claim in approved_claims))
    if cta and _clean(cta):
        parts.append(_clean(cta))
    description = _truncate("\n\n".join(parts), profile.max_description_chars)

    tags = []
    for value in hashtags:
        tag = _hashtag(value)
        if tag and tag not in tags:
            tags.append(tag)

    return {
        "channel": channel,
        "platform": profile.platform,
        "locale": locale,
        "title": title,
        "description": description,
        "hashtags": tags,
        "limits": {
            "title_chars": profile.max_title_chars,
            "description_chars": profile.max_description_chars,
        },
        "within_limits": (
            len(title) <= profile.max_title_chars
            and len(description) <= profile.max_description_chars
        ),
    }

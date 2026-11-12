from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ScriptAdapter(Protocol):
    def draft(self, *, prompt: str, max_words: int) -> str: ...


@dataclass(frozen=True)
class SuppliedScriptAdapter:
    """Operator/agent-authored copy. No hidden model call or unreserved spending."""

    text: str
    profile_key: str = "supplied-copy"

    def draft(self, *, prompt: str, max_words: int) -> str:
        return self.text.strip()


def script_prompt(
    *,
    objective: str,
    audience: str,
    duration_seconds: float,
    max_words: int,
    supplied_copy: str | None = None,
) -> str:
    lines = [
        f"Write a short, gentle script for {audience}.",
        f"Objective: {objective}",
        f"It must read aloud in about {duration_seconds:g} seconds.",
        f"Use at most {max_words} words, in short lines that scan easily.",
        "Plain words only: no brands, no claims, no prices, no protected characters.",
        "Return the script text only, with no headings or commentary.",
    ]
    if supplied_copy:
        lines.append(f"Keep this supplied copy verbatim where it fits: {supplied_copy}")
    return "\n".join(lines)

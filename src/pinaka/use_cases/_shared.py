from __future__ import annotations

import math
import re
from typing import Any

from ..enums import (
    ApprovalStage,
    ProjectStatus,
)
from ..policy.secrets import SENSITIVE_KEY_NAMES

ALLOWED_TRANSITIONS: dict[ProjectStatus, set[ProjectStatus]] = {
    ProjectStatus.DRAFT: {ProjectStatus.PLANNED, ProjectStatus.CANCELLED},
    ProjectStatus.PLANNED: {
        ProjectStatus.DRAFT,
        ProjectStatus.BRIEF_APPROVED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.BRIEF_APPROVED: {
        ProjectStatus.PLANNED,
        ProjectStatus.FACTS_LOCKED,
        ProjectStatus.GROUNDING_LOCKED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.FACTS_LOCKED: {
        ProjectStatus.BRIEF_APPROVED,
        ProjectStatus.GROUNDING_LOCKED,
        ProjectStatus.SOURCING,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.GROUNDING_LOCKED: {
        ProjectStatus.BRIEF_APPROVED,
        ProjectStatus.FACTS_LOCKED,
        ProjectStatus.SOURCING,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.SOURCING: {
        ProjectStatus.FACTS_LOCKED,
        ProjectStatus.GROUNDING_LOCKED,
        ProjectStatus.ANIMATIC_READY,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.ANIMATIC_READY: {
        ProjectStatus.SOURCING,
        ProjectStatus.ANIMATIC_APPROVED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.ANIMATIC_APPROVED: {
        ProjectStatus.SOURCING,
        ProjectStatus.FINAL_CANDIDATE,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.FINAL_CANDIDATE: {
        ProjectStatus.ANIMATIC_APPROVED,
        ProjectStatus.FINAL_APPROVED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.FINAL_APPROVED: {
        ProjectStatus.FINAL_CANDIDATE,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.ARCHIVED: set(),
    ProjectStatus.CANCELLED: set(),
}

APPROVAL_PREDECESSOR: dict[ApprovalStage, ApprovalStage | None] = {
    ApprovalStage.BRIEF: None,
    ApprovalStage.SOURCING: ApprovalStage.BRIEF,
    ApprovalStage.ANIMATIC: ApprovalStage.SOURCING,
    ApprovalStage.FINAL: ApprovalStage.ANIMATIC,
}

APPROVAL_REQUIRED_STATUS: dict[ApprovalStage, ProjectStatus] = {
    ApprovalStage.BRIEF: ProjectStatus.PLANNED,
    ApprovalStage.SOURCING: ProjectStatus.SOURCING,
    ApprovalStage.ANIMATIC: ProjectStatus.ANIMATIC_READY,
    ApprovalStage.FINAL: ProjectStatus.FINAL_CANDIDATE,
}

APPROVAL_RESULT_STATUS: dict[ApprovalStage, ProjectStatus | None] = {
    ApprovalStage.BRIEF: ProjectStatus.BRIEF_APPROVED,
    ApprovalStage.SOURCING: None,
    ApprovalStage.ANIMATIC: ProjectStatus.ANIMATIC_APPROVED,
    ApprovalStage.FINAL: ProjectStatus.FINAL_APPROVED,
}

SENSITIVE_KEY_PARTS = tuple(sorted(SENSITIVE_KEY_NAMES))

STUDIO_AUTO_SPEND_MINOR = 100

SPEND_CURRENCIES = ("USD", "INR")

DEFAULT_RUN_CAP_MINOR = {"USD": 100, "INR": 8500}

MAX_AUDIO_CUES = 12

BED_GAIN_DB = -20.0

DEFAULT_CREATIVE_TARGET_MINOR = {"USD": 100, "INR": 8500}

GENERATED_SOURCES = frozenset(
    {"generated", "paid_generation", "ai_generated", "ai", "synthesis"}
)

CREATIVE_GENERATION_OPERATIONS = frozenset(
    {"image.generate", "avatar.generate", "video.generate"}
)

STUDIO_CAPABILITY_NAME = "openmontage.studio"

STUDIO_READINESS_TTL_SECONDS = 12 * 60 * 60

MAX_LAYER_BYTES = 1_500_000

MAX_EMBEDDED_BYTES = 4_000_000

IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}

VIDEO_MIME = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}


def _image_mime(path: Any) -> str | None:
    return IMAGE_MIME.get(str(getattr(path, "suffix", "")).lower())


SUBJECT_ROLE_HINTS = ("subject", "character", "prop", "visual", "hero", "presenter")

BACKGROUND_ROLE_HINTS = ("background", "world", "location", "environment", "scene")


def _slot_accepts_element(slot_role: str, element_kind: str) -> bool:
    role = (slot_role or "").lower()
    kind = (element_kind or "").lower()
    if kind in {"character", "prop"}:
        return any(hint in role for hint in SUBJECT_ROLE_HINTS)
    if kind in {"location", "world"}:
        return any(hint in role for hint in BACKGROUND_ROLE_HINTS)
    return False


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


CAPTION_TIME = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def _caption_coverage(srt_text: str, duration: float) -> tuple[float, int, bool]:
    """Seconds covered by cues, cue count, and whether a cue overruns."""

    covered = 0.0
    cues = 0
    overrun = False
    for match in CAPTION_TIME.finditer(srt_text):
        groups = [int(value) for value in match.groups()]
        start = groups[0] * 3600 + groups[1] * 60 + groups[2] + groups[3] / 1000
        end = groups[4] * 3600 + groups[5] * 60 + groups[6] + groups[7] / 1000
        cues += 1
        if duration > 0 and end > duration + 0.05:
            overrun = True
        covered += max(0.0, min(end, duration) - min(start, duration))
    return min(covered, duration), cues, overrun

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from math import gcd
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from .enums import ApprovalStage, ProjectStatus
from .errors import ConflictError, IntegrityError
from .media_probe import probe_ffprobe
from .models import StrictModel
from .utils import sha256_file, sha256_json, utc_now

ChannelKey = Literal[
    "instagram_reels",
    "youtube_shorts",
    "tiktok",
    "linkedin",
    "facebook_reels",
]
CheckStatus = Literal["pass", "warn", "fail"]


class ChannelProfile(StrictModel):
    key: str
    platform: str
    aspect_ratios: tuple[str, ...]
    min_duration_seconds: float
    max_duration_seconds: float
    max_width_px: int
    max_height_px: int
    requires_captions: bool
    requires_commercial_disclosure: bool
    max_title_chars: int = 100
    max_description_chars: int = 2200
    made_for_kids: bool = False
    comments_enabled: bool = True
    personalized_ads: bool = True


CHANNELS: dict[str, ChannelProfile] = {
    "instagram_reels": ChannelProfile(
        key="instagram_reels",
        platform="Instagram Reels",
        aspect_ratios=("9:16",),
        min_duration_seconds=3,
        max_duration_seconds=90,
        max_width_px=1080,
        max_height_px=1920,
        requires_captions=True,
        requires_commercial_disclosure=True,
    ),
    "youtube_shorts": ChannelProfile(
        key="youtube_shorts",
        platform="YouTube Shorts",
        aspect_ratios=("9:16",),
        min_duration_seconds=1,
        max_duration_seconds=180,
        max_width_px=1080,
        max_height_px=1920,
        requires_captions=True,
        requires_commercial_disclosure=True,
        max_title_chars=100,
        max_description_chars=5000,
    ),
    "tiktok": ChannelProfile(
        key="tiktok",
        platform="TikTok",
        aspect_ratios=("9:16",),
        min_duration_seconds=3,
        max_duration_seconds=600,
        max_width_px=1080,
        max_height_px=1920,
        requires_captions=True,
        requires_commercial_disclosure=True,
        max_description_chars=2200,
    ),
    "linkedin": ChannelProfile(
        key="linkedin",
        platform="LinkedIn",
        aspect_ratios=("1:1", "16:9", "4:5", "9:16"),
        min_duration_seconds=3,
        max_duration_seconds=600,
        max_width_px=1920,
        max_height_px=1920,
        requires_captions=True,
        requires_commercial_disclosure=False,
        max_description_chars=3000,
    ),
    "facebook_reels": ChannelProfile(
        key="facebook_reels",
        platform="Facebook Reels",
        aspect_ratios=("9:16",),
        min_duration_seconds=3,
        max_duration_seconds=90,
        max_width_px=1080,
        max_height_px=1920,
        requires_captions=True,
        requires_commercial_disclosure=True,
    ),
    "youtube_kids": ChannelProfile(
        key="youtube_kids",
        platform="YouTube Kids",
        aspect_ratios=("9:16", "16:9"),
        min_duration_seconds=1,
        max_duration_seconds=600,
        max_width_px=1920,
        max_height_px=1920,
        requires_captions=True,
        requires_commercial_disclosure=True,
        max_title_chars=100,
        max_description_chars=5000,
        made_for_kids=True,
        comments_enabled=False,
        personalized_ads=False,
    ),
}


class PublicationAuthorization(StrictModel):
    authorized: bool = False
    authorized_by: str | None = Field(default=None, max_length=200)
    authorized_at: datetime | None = None
    artifact_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    channels: tuple[str, ...] = ()
    reference: str | None = Field(default=None, max_length=500)
    expires_at: datetime | None = None


class PublicationRequest(StrictModel):
    channel: str
    artifact_id: str
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)
    disclosures: tuple[str, ...] = ()
    scheduled_for: datetime | None = None
    caption_artifact_id: str | None = None
    commercial: bool = False


class PublicationCheck(StrictModel):
    rule_id: str
    status: CheckStatus
    detail: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class PublicationPlan(StrictModel):
    schema_version: str = "publication-plan/v1"
    generated_at: str
    project_id: str
    channel: str
    artifact_id: str
    status: Literal["ready", "needs_review", "blocked"]
    authorization_valid: bool
    publishing_performed: bool = False
    checks: tuple[PublicationCheck, ...]
    manifest_hash: str


def _check(
    rule_id: str,
    status: CheckStatus,
    detail: str,
    evidence: dict[str, Any] | None = None,
) -> PublicationCheck:
    return PublicationCheck(
        rule_id=rule_id,
        status=status,
        detail=detail,
        evidence=evidence or {},
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def aspect_ratio(width: int, height: int) -> str:
    divisor = gcd(width, height) or 1
    return f"{width // divisor}:{height // divisor}"


def _authorization_checks(
    *,
    authorization: PublicationAuthorization,
    artifact_hash: str,
    channel: str,
    now: datetime,
) -> list[PublicationCheck]:
    checks: list[PublicationCheck] = []
    if not authorization.authorized:
        return [
            _check(
                "P-AUTH-001",
                "fail",
                "No separate publication authorization is recorded.",
            )
        ]
    checks.append(
        _check(
            "P-AUTH-001",
            "pass",
            "A separate publication authorization is recorded.",
            {"authorized_by": authorization.authorized_by},
        )
    )
    if not authorization.authorized_by:
        checks.append(
            _check("P-AUTH-002", "fail", "The authorization has no authorizing person.")
        )
    else:
        checks.append(
            _check(
                "P-AUTH-002", "pass", "The authorization names its authorizing person."
            )
        )
    if authorization.artifact_hash != artifact_hash:
        checks.append(
            _check(
                "P-AUTH-003",
                "fail",
                "The authorization hash does not match the exact artifact.",
                {
                    "authorized_hash": authorization.artifact_hash,
                    "artifact_hash": artifact_hash,
                },
            )
        )
    else:
        checks.append(
            _check(
                "P-AUTH-003",
                "pass",
                "The authorization hash matches the exact artifact.",
            )
        )
    if channel not in authorization.channels:
        checks.append(
            _check(
                "P-AUTH-004",
                "fail",
                f"The authorization does not include {channel}.",
                {"channels": list(authorization.channels)},
            )
        )
    else:
        checks.append(
            _check("P-AUTH-004", "pass", f"The authorization includes {channel}.")
        )
    expires_at = _aware(authorization.expires_at)
    if expires_at is not None and expires_at < now:
        checks.append(
            _check("P-AUTH-005", "fail", "The publication authorization has expired.")
        )
    else:
        checks.append(
            _check("P-AUTH-005", "pass", "The publication authorization is current.")
        )
    return checks


def evaluate_publication(
    *,
    project_id: str,
    project_status: str,
    request: PublicationRequest,
    authorization: PublicationAuthorization,
    artifact_hash: str,
    final_approval_hash: str | None,
    duration_seconds: float | None,
    width_px: int | None,
    height_px: int | None,
    has_embedded_captions: bool,
    caption_hash: str | None,
    now: datetime | None = None,
) -> PublicationPlan:
    """Evaluate a publication plan. This function never publishes anything."""

    current = _aware(now) or datetime.now(timezone.utc)
    checks: list[PublicationCheck] = []
    try:
        status = ProjectStatus(project_status)
    except ValueError:
        status = None
    checks.append(
        _check(
            "P-PROJECT-001",
            "pass" if status == ProjectStatus.ARCHIVED else "fail",
            "The project is archived."
            if status == ProjectStatus.ARCHIVED
            else "The project must be archived before publication.",
            {"project_status": project_status},
        )
    )
    checks.append(
        _check(
            "P-APPROVAL-001",
            "pass"
            if final_approval_hash and final_approval_hash == artifact_hash
            else "fail",
            "The final approval hash matches the artifact."
            if final_approval_hash and final_approval_hash == artifact_hash
            else "The artifact is not the exact-hash approved final.",
            {
                "final_approval_hash": final_approval_hash,
                "artifact_hash": artifact_hash,
            },
        )
    )
    checks.extend(
        _authorization_checks(
            authorization=authorization,
            artifact_hash=artifact_hash,
            channel=request.channel,
            now=current,
        )
    )

    channel = CHANNELS.get(request.channel)
    if channel is None:
        checks.append(
            _check(
                "P-CHANNEL-001",
                "fail",
                f"Unknown publication channel {request.channel!r}.",
                {"supported": sorted(CHANNELS)},
            )
        )
    else:
        checks.append(
            _check(
                "P-CHANNEL-001",
                "pass",
                f"Channel profile {channel.platform} is known.",
            )
        )
        if duration_seconds is None:
            checks.append(
                _check("P-DURATION-001", "fail", "The artifact duration is unknown.")
            )
        elif not (
            channel.min_duration_seconds
            <= duration_seconds
            <= channel.max_duration_seconds
        ):
            checks.append(
                _check(
                    "P-DURATION-001",
                    "fail",
                    "The artifact duration is outside the channel range.",
                    {
                        "duration_seconds": duration_seconds,
                        "min": channel.min_duration_seconds,
                        "max": channel.max_duration_seconds,
                    },
                )
            )
        else:
            checks.append(
                _check("P-DURATION-001", "pass", "The duration is within range.")
            )
        ratio = (
            aspect_ratio(width_px, height_px)
            if width_px is not None and height_px is not None
            else None
        )
        if ratio is None:
            checks.append(
                _check("P-ASPECT-001", "fail", "The artifact dimensions are unknown.")
            )
        elif ratio not in channel.aspect_ratios:
            checks.append(
                _check(
                    "P-ASPECT-001",
                    "fail",
                    "The aspect ratio is not supported by the channel.",
                    {"ratio": ratio, "supported": list(channel.aspect_ratios)},
                )
            )
        else:
            checks.append(
                _check(
                    "P-ASPECT-001",
                    "pass",
                    f"The {ratio} aspect ratio is supported.",
                )
            )
        if (
            width_px is not None
            and height_px is not None
            and (width_px > channel.max_width_px or height_px > channel.max_height_px)
        ):
            checks.append(
                _check(
                    "P-RESOLUTION-001",
                    "fail",
                    "The resolution exceeds the channel maximum.",
                    {
                        "width_px": width_px,
                        "height_px": height_px,
                        "max_width_px": channel.max_width_px,
                        "max_height_px": channel.max_height_px,
                    },
                )
            )
        else:
            checks.append(
                _check("P-RESOLUTION-001", "pass", "The resolution is within bounds.")
            )
        captions_present = has_embedded_captions or bool(caption_hash)
        if channel.requires_captions and not captions_present:
            checks.append(
                _check(
                    "P-CAPTIONS-001",
                    "fail",
                    "The channel requires captions and none are present.",
                )
            )
        elif not captions_present:
            checks.append(
                _check(
                    "P-CAPTIONS-001",
                    "warn",
                    "No captions are present; accessibility is degraded.",
                )
            )
        else:
            checks.append(_check("P-CAPTIONS-001", "pass", "Captions are present."))
        if (
            channel.requires_commercial_disclosure
            and request.commercial
            and "advertising_disclosure" not in request.disclosures
        ):
            checks.append(
                _check(
                    "P-DISCLOSURE-001",
                    "fail",
                    "Commercial content needs an advertising disclosure.",
                )
            )
        elif request.commercial and not request.disclosures:
            checks.append(
                _check(
                    "P-DISCLOSURE-001",
                    "warn",
                    "Commercial content has no recorded disclosures.",
                )
            )
        else:
            checks.append(
                _check("P-DISCLOSURE-001", "pass", "Disclosures are recorded.")
            )
        if len(request.title) > channel.max_title_chars:
            checks.append(
                _check(
                    "P-METADATA-001",
                    "fail",
                    "The title exceeds the channel limit.",
                )
            )
        elif len(request.description) > channel.max_description_chars:
            checks.append(
                _check(
                    "P-METADATA-001",
                    "fail",
                    "The description exceeds the channel limit.",
                )
            )
        else:
            checks.append(
                _check(
                    "P-METADATA-001", "pass", "The metadata fits the channel limits."
                )
            )

    scheduled_for = _aware(request.scheduled_for)
    if scheduled_for is not None and scheduled_for < current:
        checks.append(
            _check("P-SCHEDULE-001", "warn", "The scheduled time is in the past.")
        )
    else:
        checks.append(
            _check("P-SCHEDULE-001", "pass", "The schedule is not in the past.")
        )

    statuses = {check.status for check in checks}
    overall: Literal["ready", "needs_review", "blocked"]
    if "fail" in statuses:
        overall = "blocked"
    elif "warn" in statuses:
        overall = "needs_review"
    else:
        overall = "ready"
    authorization_valid = not any(
        check.status == "fail" and check.rule_id.startswith("P-AUTH")
        for check in checks
    )
    body = {
        "generated_at": utc_now(),
        "project_id": project_id,
        "channel": request.channel,
        "artifact_id": request.artifact_id,
        "status": overall,
        "authorization_valid": authorization_valid,
        "publishing_performed": False,
        "checks": [check.model_dump(mode="json") for check in checks],
    }
    return PublicationPlan(
        **body,
        manifest_hash=sha256_json(body),
    )


def check_publication(
    controller: Any,
    project_id: str,
    request: PublicationRequest,
    authorization: PublicationAuthorization,
) -> PublicationPlan:
    """Gather controller evidence, then evaluate the plan without publishing."""

    project = controller.repository.get_project(project_id)
    artifact = controller.repository.get_artifact(request.artifact_id)
    final = controller.repository.active_approval(project_id, ApprovalStage.FINAL.value)
    path = controller.artifact_store.resolve(project_id, artifact["relative_path"])
    probe = probe_ffprobe(Path(path))
    caption_hash = None
    if request.caption_artifact_id:
        caption = controller.repository.get_artifact(request.caption_artifact_id)
        caption_hash = caption["content_hash"]
    return evaluate_publication(
        project_id=project_id,
        project_status=project.status.value,
        request=request,
        authorization=authorization,
        artifact_hash=artifact["content_hash"],
        final_approval_hash=final.get("output_hash") if final else None,
        duration_seconds=probe["duration_seconds"],
        width_px=probe["width_px"],
        height_px=probe["height_px"],
        has_embedded_captions=bool(probe["has_subtitles"]),
        caption_hash=caption_hash,
    )


class DispatchFile(StrictModel):
    role: Literal["media", "captions"]
    filename: str
    sha256: str
    bytes: int


class PublicationPackage(StrictModel):
    schema_version: str = "publication-package/v1"
    generated_at: str
    project_id: str
    channel: str
    platform: str
    artifact_id: str
    artifact_hash: str
    title: str
    description: str
    disclosures: tuple[str, ...] = ()
    scheduled_for: datetime | None = None
    commercial: bool = False
    authorization_reference: str | None = None
    status: Literal["ready_for_manual_upload"] = "ready_for_manual_upload"
    publishing_performed: bool = False
    files: tuple[DispatchFile, ...]
    plan_hash: str
    manifest_hash: str


def _approved_hash(plan: PublicationPlan) -> str:
    for check in plan.checks:
        if check.rule_id == "P-APPROVAL-001":
            return str(check.evidence.get("artifact_hash") or "")
    return ""


def write_dispatch_package(
    plan: PublicationPlan,
    request: PublicationRequest,
    authorization: PublicationAuthorization,
    *,
    media_path: Path | str,
    output_dir: Path | str,
    caption_path: Path | str | None = None,
) -> PublicationPackage:
    """Write a self-contained manual-upload package. It never publishes."""

    if plan.status != "ready" or not plan.authorization_valid:
        failed = [
            check.rule_id for check in plan.checks if check.status in {"fail", "warn"}
        ]
        raise ConflictError(
            "Publication plan is not ready; resolve: " + ", ".join(failed)
        )
    if plan.artifact_id != request.artifact_id or plan.channel != request.channel:
        raise ConflictError("Publication plan does not match the request")
    channel = CHANNELS[request.channel]
    media = Path(media_path).expanduser().resolve()
    if not media.is_file():
        raise FileNotFoundError(f"Media file not found: {media}")
    captions = (
        Path(caption_path).expanduser().resolve() if caption_path is not None else None
    )
    if captions is not None and not captions.is_file():
        raise FileNotFoundError(f"Caption file not found: {captions}")
    output = Path(output_dir).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Dispatch directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    media_target = output / "media" / media.name
    media_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(media, media_target)
    media_hash = sha256_file(media_target)
    approved_hash = _approved_hash(plan)
    if approved_hash and media_hash != approved_hash:
        raise IntegrityError(
            "Copied media hash does not match the publication-approved artifact"
        )
    files = [
        DispatchFile(
            role="media",
            filename=media_target.relative_to(output).as_posix(),
            sha256=media_hash,
            bytes=media_target.stat().st_size,
        )
    ]
    if captions is not None:
        caption_target = output / "captions" / captions.name
        caption_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(captions, caption_target)
        files.append(
            DispatchFile(
                role="captions",
                filename=caption_target.relative_to(output).as_posix(),
                sha256=sha256_file(caption_target),
                bytes=caption_target.stat().st_size,
            )
        )
    body = {
        "generated_at": utc_now(),
        "project_id": plan.project_id,
        "channel": request.channel,
        "platform": channel.platform,
        "artifact_id": request.artifact_id,
        "artifact_hash": _approved_hash(plan),
        "title": request.title,
        "description": request.description,
        "disclosures": list(request.disclosures),
        "scheduled_for": request.scheduled_for.isoformat()
        if request.scheduled_for
        else None,
        "commercial": request.commercial,
        "authorization_reference": authorization.reference,
        "status": "ready_for_manual_upload",
        "publishing_performed": False,
        "files": [item.model_dump(mode="json") for item in files],
        "plan_hash": plan.manifest_hash,
    }
    package = PublicationPackage(
        **body,
        manifest_hash=sha256_json(body),
    )
    manifest_path = output / "dispatch.json"
    manifest_path.write_text(package.model_dump_json(indent=2), encoding="utf-8")
    return package


def build_dispatch_package(
    controller: Any,
    project_id: str,
    request: PublicationRequest,
    authorization: PublicationAuthorization,
    output_dir: Path | str,
) -> PublicationPackage:
    """Evaluate the plan, then write a manual-upload package without publishing."""

    plan = check_publication(controller, project_id, request, authorization)
    artifact = controller.repository.get_artifact(request.artifact_id)
    media_path = controller.artifact_store.resolve(
        project_id, artifact["relative_path"]
    )
    caption_path = None
    if request.caption_artifact_id:
        caption = controller.repository.get_artifact(request.caption_artifact_id)
        caption_path = controller.artifact_store.resolve(
            project_id, caption["relative_path"]
        )
    return write_dispatch_package(
        plan,
        request,
        authorization,
        media_path=media_path,
        caption_path=caption_path,
        output_dir=output_dir,
    )

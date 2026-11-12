from __future__ import annotations

from dataclasses import asdict, dataclass

from ..errors import IntegrityError


@dataclass(frozen=True)
class Operation:
    id: str
    routes: tuple[str, ...]
    paid: bool
    preconditions: tuple[str, ...]
    cost_model: str
    receipt: str
    idempotency: str
    one_take: bool = True
    auto_retry: bool = False

    def validate(self, route: str, *, model: str = "") -> None:
        if (
            route not in self.routes
            or "veo" in model.lower()
            or "runway" in model.lower()
        ):
            raise IntegrityError(
                f"Route/model not allowed for {self.id}: {route}/{model}"
            )


_OPERATIONS = {
    item.id: item
    for item in (
        Operation(
            "image.generate",
            ("fal.image",),
            True,
            ("active_project", "funded_item", "provider_allowlist", "one_take"),
            "pinaka.pricing.estimate_fal_image_minor",
            "provider_job",
            "budget_plan_id+item_key",
        ),
        Operation(
            "image.edit",
            ("fal.image",),
            True,
            ("active_project", "funded_item", "source_hash", "rights", "one_take"),
            "funded_plan_item_exact_quote",
            "provider_job",
            "budget_plan_id+item_key",
        ),
        Operation(
            "video.generate",
            ("fal.video",),
            True,
            ("active_project", "funded_item", "shot", "reference_hashes", "one_take"),
            "pinaka.motion.estimate_minor",
            "studio_job",
            "budget_plan_id+item_key",
        ),
        Operation(
            "speech.synthesize",
            ("kokoro",),
            False,
            ("active_project", "script"),
            "zero",
            "local_execution",
            "text+voice+speed",
        ),
        Operation(
            "speech.transcribe",
            ("whisper",),
            False,
            ("input_hash",),
            "zero",
            "local_report",
            "audio_hash+model",
        ),
        Operation(
            "audio.mix",
            ("ffmpeg",),
            False,
            ("active_project", "input_hashes", "audio_rights"),
            "zero",
            "local_execution",
            "video_hash+narration_hash+audio_plan_hash",
        ),
    )
}


def operation(identifier: str) -> Operation:
    try:
        return _OPERATIONS[identifier]
    except KeyError as error:
        raise IntegrityError(f"Unknown catalog operation: {identifier}") from error


def describe_operations() -> list[dict]:
    """Policy capabilities, not a live readiness/balance claim."""

    return [asdict(item) for item in _OPERATIONS.values()]

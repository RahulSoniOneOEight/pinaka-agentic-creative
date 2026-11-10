from __future__ import annotations

from typing import Any

from pydantic import Field

from .enums import AssetUseClass, TreatmentVerdict
from .models import AssetRightsRecord, AssetUseContext, StrictModel
from .utils import sha256_json, utc_now

MODIFYING_VERDICTS = frozenset({TreatmentVerdict.CORRECT, TreatmentVerdict.COMPOSITE})
TERMINAL_VERDICTS = frozenset(
    {TreatmentVerdict.USE_AS_IS, TreatmentVerdict.CORRECT, TreatmentVerdict.COMPOSITE}
)


class AssetCriteria(StrictModel):
    """Observations an evaluator records about one candidate asset.

    Defaults describe an asset that needs no correction. A caller must set the
    observed condition rather than leaving uncertainty implicit.
    """

    satisfies_slot_requirements: bool = True
    framing_ok: bool = True
    resolution_sufficient: bool = True
    contains_conflicting_text: bool = False
    contains_recognizable_person_or_brand: bool = False
    third_party_rights_cleared: bool | None = None
    has_visible_artifacts: bool = False
    motion_suitable: bool = True
    tone_matches: bool = True
    correction_acceptable: bool = True
    notes: tuple[str, ...] = ()


class TreatmentDecision(StrictModel):
    slot_id: str = Field(min_length=1, max_length=120)
    artifact_id: str | None = None
    verdict: TreatmentVerdict
    reasons: tuple[str, ...]
    criteria: AssetCriteria
    rights: AssetRightsRecord | None = None
    decided_by: str = Field(min_length=1, max_length=200)
    decided_at: str
    decision_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def verdict_modifies_bytes(verdict: TreatmentVerdict) -> bool:
    return verdict in MODIFYING_VERDICTS


def verdict_binds_asset(verdict: TreatmentVerdict) -> bool:
    return verdict in TERMINAL_VERDICTS


def evaluate_treatment(
    *,
    slot_id: str,
    criteria: AssetCriteria,
    context: AssetUseContext,
    decided_by: str,
    rights: AssetRightsRecord | None = None,
    artifact_id: str | None = None,
) -> TreatmentDecision:
    """Decide whether to use an asset as-is, correct it, or look elsewhere.

    Rights are checked for the *chosen treatment*, not only at search time: a
    source that may be shown as-is may still forbid modification.
    """

    reasons: list[str] = []
    verdict: TreatmentVerdict

    if not criteria.satisfies_slot_requirements:
        verdict = TreatmentVerdict.REPLACE
        reasons.append("The asset does not satisfy the slot requirements.")
    elif not criteria.tone_matches:
        verdict = TreatmentVerdict.REPLACE
        reasons.append("The asset does not match the approved tone.")
    elif not criteria.motion_suitable:
        verdict = TreatmentVerdict.REPLACE
        reasons.append("The asset motion does not suit the slot.")
    elif (
        criteria.contains_recognizable_person_or_brand
        and criteria.third_party_rights_cleared is not True
    ):
        verdict = TreatmentVerdict.REJECT
        reasons.append(
            "Recognizable person or brand is present and third-party rights are not cleared."
        )
    else:
        needs_correction = (
            not criteria.framing_ok
            or not criteria.resolution_sufficient
            or criteria.contains_conflicting_text
            or criteria.has_visible_artifacts
        )
        attribution_blocked = bool(
            rights
            and rights.attribution_required
            and not context.attribution_can_be_delivered
        )

        if needs_correction:
            if rights is None:
                verdict = TreatmentVerdict.REJECT
                reasons.append(
                    "Correction was requested but no rights record is available."
                )
            elif rights.modifications_allowed is not True:
                verdict = TreatmentVerdict.REPLACE
                reasons.append(
                    "The licence does not verify permission to modify this asset."
                )
            elif rights.editorial_only and context.use_class != AssetUseClass.EDITORIAL:
                verdict = TreatmentVerdict.REJECT
                reasons.append(
                    "Editorial-only media cannot be modified for this use class."
                )
            elif attribution_blocked:
                verdict = TreatmentVerdict.REJECT
                reasons.append("Required attribution cannot be delivered.")
            elif not criteria.correction_acceptable:
                verdict = TreatmentVerdict.REPLACE
                reasons.append("Correction is permitted but not acceptable here.")
            else:
                verdict = TreatmentVerdict.CORRECT
                reasons.append("Correction is permitted by the licence.")
        elif attribution_blocked:
            verdict = TreatmentVerdict.REJECT
            reasons.append("Required attribution cannot be delivered.")
        else:
            verdict = TreatmentVerdict.USE_AS_IS
            reasons.append("The asset satisfies the slot without modification.")

    if verdict_modifies_bytes(verdict):
        reasons.append(
            "A corrected asset must be written as a new derivative with its source as parent."
        )
    if verdict is TreatmentVerdict.REPLACE:
        reasons.append(
            "Source another asset for this slot; keep the rejected one recorded."
        )
    if verdict is TreatmentVerdict.REJECT:
        reasons.append("Do not use this asset for any treatment of this slot.")

    payload: dict[str, Any] = {
        "slot_id": slot_id,
        "artifact_id": artifact_id,
        "verdict": verdict.value,
        "reasons": list(reasons),
        "criteria": criteria.model_dump(mode="json"),
        "rights": rights.model_dump(mode="json") if rights else None,
        "decided_by": decided_by,
    }
    return TreatmentDecision(
        slot_id=slot_id,
        artifact_id=artifact_id,
        verdict=verdict,
        reasons=tuple(reasons),
        criteria=criteria,
        rights=rights,
        decided_by=decided_by,
        decided_at=utc_now(),
        decision_hash=sha256_json(payload),
    )

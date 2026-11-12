from __future__ import annotations

from ..errors import IntegrityError
from ..models import AssetRightsRecord


def require_audio_rights(artifact: dict) -> AssetRightsRecord:
    """Validate the current artifact record, never infer a licence from its filename."""

    payload = artifact.get("operation", {}).get("rights")
    if not payload:
        raise IntegrityError("Audio layers require recorded licence evidence")
    rights = AssetRightsRecord.model_validate(payload)
    if not rights.origin_license_verified:
        raise IntegrityError("Audio origin licence must be verified before mixing")
    if rights.commercial_use_allowed is not True:
        raise IntegrityError("Audio licence does not allow commercial use")
    if rights.modifications_allowed is not True:
        raise IntegrityError("Audio licence does not allow modification")
    if rights.attribution_required is None:
        raise IntegrityError("Audio attribution obligation is unresolved")
    if rights.attribution_required and not rights.attribution_text:
        raise IntegrityError("Audio attribution text is required")
    return rights

"""Prove the stock-video route with a real Wikimedia Commons clip (CM-05).

This is a QC probe, not a delivery: it searches the official API, imports one
CC BY video with licence evidence and attribution, and verifies motion.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.request
from decimal import Decimal
from pathlib import Path

from pinaka.assets import AssetCriteria
from pinaka.contracts.flow import (
    MotionLevelCreate,
    SlotPickCreate,
    SourcingCandidateCreate,
    SourcingDecisionCreate,
)
from pinaka.controller import CreativeController
from pinaka.enums import ArtifactKind, AssetMediaType, SlotAssetRole
from pinaka.models import Money, ProjectCreate, TextBriefRequest
from pinaka.motion_verify import verify_video
from pinaka.openverse import default_context
from pinaka.wikimedia import WikimediaClient

ROOT = Path(r"D:\pinaka-agentic-creative\workspace\stock-route-qc")
if ROOT.exists():
    shutil.rmtree(ROOT)
controller = CreativeController.open(ROOT)
project = controller.create_project(
    ProjectCreate(
        name="Wikimedia stock-video route QC",
        objective="Prove a licensed stock clip can be sourced, imported and verified",
        mode="entertainment",
        audience="reviewers",
        output_format="9:16",
        language="en",
        duration_seconds=Decimal(6),
        max_budget=Money(amount=Decimal("0.01"), currency="USD"),
    )
)

plan = controller.plan_from_text(
    project.id,
    TextBriefRequest(
        text="Show ducks swimming on a pond, calmly.",
        duration_seconds=Decimal(6),
        create_plan=True,
        persist_slots=True,
        production_preset_id="cinematic-story",
        visual_style_id="cinematic-live-action",
    ),
)
controller.plan_production(project.id)
slot = next(
    item for item in controller.repository.list_slots(project.id)
    if item["role"] == "primary_visual"
)
assert slot["media_requirement"] == "motion_clip", slot
assert slot["style_id"] == "cinematic-live-action", slot

client = WikimediaClient()
result = client.search(
    "Ducks at the Duck Pond Sonoma",
    media_type=AssetMediaType.VIDEO,
    limit=10,
)
chosen = None
for candidate in result.candidates:
    page = str(candidate.source.source_page_url or "")
    licence = candidate.rights.license_name
    if "Ducks_at_the_Duck_Pond" in page and "CC BY" in licence:
        chosen = candidate
        break
if chosen is None:
    raise SystemExit("No suitable Wikimedia candidate found")

url = str(chosen.source.direct_asset_url)
local = Path(r"C:\Users\LENOVO\AppData\Local\Temp\pinaka-stock-duck.webm")
request = urllib.request.Request(
    url, headers={"User-Agent": "PinakaQC/1.0 (contact: qc@example.invalid)"}
)
with urllib.request.urlopen(request, timeout=180) as response, local.open("wb") as out:
    while chunk := response.read(1024 * 256):
        out.write(chunk)
raw = local.read_bytes()
digest = hashlib.sha256(raw).hexdigest()
motion = verify_video(str(local), samples=12)
if motion["verdict"] != "pass":
    raise SystemExit(f"Stock clip failed motion verification: {motion}")

rights = chosen.rights.model_dump(mode="json")
rights.update(
    {
        "origin_license_verified": True,
        "license_evidence_ref": str(chosen.source.source_page_url),
        "license_evidence_hash": chosen.source.api_response_hash,
        "rights_uncertainty": [],
    }
)
artifact = controller.import_original(
    project.id,
    local,
    operation={
        "type": "provider_import",
        "provider": "wikimedia_commons",
        "provider_asset_id": chosen.source.provider_asset_id,
        "source_page_url": str(chosen.source.source_page_url),
        "api_response_hash": chosen.source.api_response_hash,
        "rights": rights,
    },
)

controller.record_sourcing_decision(
    project.id,
    SourcingDecisionCreate(
        slot_id=slot["id"],
        decision="stock",
        reason="Wikimedia Commons API search before any generation",
        providers=("wikimedia_commons",),
        candidates_considered=len(result.candidates),
        approved_by="producer",
    ),
)
primary = controller.record_sourcing_candidate(
    project.id,
    SourcingCandidateCreate(
        slot_id=slot["id"],
        provider="wikimedia_commons",
        provider_asset_id=chosen.source.provider_asset_id,
        media_type="video",
        source_url=str(chosen.source.source_page_url),
        creator=chosen.source.creator_name,
        licence=chosen.rights.license_name,
        licence_url=str(chosen.rights.license_url),
        eligibility="eligible",
        treatment="as_is",
        asset_class="footage",
        evidence=(f"api:{chosen.source.api_response_hash}",),
    ),
)
backup_candidate = next(
    item for item in result.candidates if item is not chosen
)
backup = controller.record_sourcing_candidate(
    project.id,
    SourcingCandidateCreate(
        slot_id=slot["id"],
        provider="wikimedia_commons",
        provider_asset_id=backup_candidate.source.provider_asset_id,
        media_type="video",
        source_url=str(backup_candidate.source.source_page_url),
        creator=backup_candidate.source.creator_name,
        licence=backup_candidate.rights.license_name,
        licence_url=str(backup_candidate.rights.license_url),
        eligibility="eligible",
        treatment="as_is",
        asset_class="footage",
        evidence=(f"api:{backup_candidate.source.api_response_hash}",),
    ),
)
controller.pick_slot_assets(
    project.id,
    SlotPickCreate(
        slot_id=slot["id"],
        primary_candidate_id=primary["id"],
        backup_candidate_id=backup["id"],
        decided_by="producer",
        reason="Primary CC BY clip plus an independent backup candidate",
    ),
)
controller.assign_slot_asset(
    project.id,
    slot["id"],
    artifact_id=artifact["id"],
    role=SlotAssetRole.PRIMARY,
    criteria=AssetCriteria(),
    context=default_context(monetized=True),
    decided_by="producer",
)

import cv2  # noqa: E402

capture = cv2.VideoCapture(str(local))
frames = []
while len(frames) < 12:
    ok, frame = capture.read()
    if not ok:
        break
    frames.append(frame)
capture.release()
evidence = controller.write_motion_evidence(
    project.id,
    shot_id=slot["shot_id"],
    level="M2",
    frames=frames,
    source="stock_clip",
)
controller.record_motion_level(
    project.id,
    MotionLevelCreate(
        shot_id=slot["shot_id"],
        level="M2",
        source="stock_clip",
        evidence=(evidence["artifact"]["id"],),
        evidence_artifact_id=evidence["artifact"]["id"],
    ),
)
delivery = controller.write_delivery_metadata(project.id)

print(
    json.dumps(
        {
            "project": project.id,
            "provider": "wikimedia_commons",
            "provider_asset_id": chosen.source.provider_asset_id,
            "source_page": str(chosen.source.source_page_url),
            "licence": chosen.rights.license_name,
            "licence_url": str(chosen.rights.license_url),
            "attribution": chosen.rights.attribution_text,
            "creator": chosen.source.creator_name,
            "sha256": digest,
            "bytes": len(raw),
            "api_response_hash": chosen.source.api_response_hash,
            "style_fit": primary["style_fit"],
            "motion": {
                "verdict": motion["verdict"],
                "residual_ratio": motion["residual_ratio"],
                "components": motion["components"],
            },
            "evidence_artifact": evidence["artifact"]["id"],
            "credits": delivery["credits"],
            "spend": controller.repository.get_project(project.id).spent.model_dump(
                mode="json"
            ),
        },
        indent=2,
    )
)

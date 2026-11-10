from __future__ import annotations

from decimal import Decimal

import pytest

from pinaka.assets import AssetCriteria, evaluate_treatment
from pinaka.enums import (
    ArtifactKind,
    AssetMediaType,
    AssetUseClass,
    SlotAssetRole,
    TreatmentVerdict,
)
from pinaka.errors import ConflictError, QuotaExceededError, RightsEligibilityError
from pinaka.models import AssetRightsRecord, AssetUseContext, ShotCreate, SlotCreate
from pinaka.pexels import map_pexels_result
from pinaka.preflight import run_preflight

from .conftest import create_project

PHOTO = {
    "id": 13802140,
    "width": 4000,
    "height": 6000,
    "url": "https://www.pexels.com/photo/people-in-a-meeting-13802140/",
    "photographer": "Fauxels",
    "photographer_url": "https://www.pexels.com/@fauxels",
    "src": {"original": "https://images.pexels.com/photos/13802140/original.jpg"},
}


def rights(**overrides) -> AssetRightsRecord:
    base = {
        "license_name": "Test Licence",
        "license_url": "https://example.test/licence",
        "terms_url": "https://example.test/terms",
        "terms_reviewed_at": "2026-11-09",
        "commercial_use_allowed": True,
        "modifications_allowed": True,
        "editorial_only": False,
        "attribution_required": False,
        "origin_license_verified": True,
    }
    base.update(overrides)
    return AssetRightsRecord(**base)


def context(**overrides) -> AssetUseContext:
    base = {
        "use_class": AssetUseClass.COMMERCIAL,
        "monetized": True,
        "modification_planned": True,
        "attribution_can_be_delivered": True,
    }
    base.update(overrides)
    return AssetUseContext(**base)


def decide(criteria: AssetCriteria, rights_record: AssetRightsRecord | None = None):
    return evaluate_treatment(
        slot_id="slot_test",
        criteria=criteria,
        context=context(),
        decided_by="reviewer@example.test",
        rights=rights_record,
    )


# --- evaluation -----------------------------------------------------------


def test_clean_asset_is_used_as_is():
    decision = decide(AssetCriteria(), rights())

    assert decision.verdict == TreatmentVerdict.USE_AS_IS
    assert decision.decision_hash


def test_correction_requires_modification_permission():
    decision = decide(AssetCriteria(framing_ok=False), rights())

    assert decision.verdict == TreatmentVerdict.CORRECT
    assert any("new derivative" in reason for reason in decision.reasons)


def test_correction_is_replaced_when_modification_is_forbidden():
    decision = decide(
        AssetCriteria(framing_ok=False), rights(modifications_allowed=False)
    )

    assert decision.verdict == TreatmentVerdict.REPLACE
    assert any("modify" in reason for reason in decision.reasons)


def test_correction_is_replaced_when_modification_is_unknown():
    decision = decide(
        AssetCriteria(resolution_sufficient=False), rights(modifications_allowed=None)
    )

    assert decision.verdict == TreatmentVerdict.REPLACE


def test_correction_without_a_rights_record_is_rejected():
    decision = decide(AssetCriteria(has_visible_artifacts=True), None)

    assert decision.verdict == TreatmentVerdict.REJECT


def test_editorial_only_asset_cannot_be_corrected_for_commercial_use():
    decision = decide(AssetCriteria(framing_ok=False), rights(editorial_only=True))

    assert decision.verdict == TreatmentVerdict.REJECT


def test_uncleared_recognizable_person_is_rejected():
    decision = decide(
        AssetCriteria(
            contains_recognizable_person_or_brand=True,
            third_party_rights_cleared=False,
        ),
        rights(),
    )

    assert decision.verdict == TreatmentVerdict.REJECT


def test_slot_mismatch_replaces_the_asset():
    decision = decide(AssetCriteria(satisfies_slot_requirements=False), rights())

    assert decision.verdict == TreatmentVerdict.REPLACE


def test_impossible_attribution_is_rejected():
    decision = evaluate_treatment(
        slot_id="slot_test",
        criteria=AssetCriteria(),
        context=context(attribution_can_be_delivered=False),
        decided_by="reviewer@example.test",
        rights=rights(attribution_required=True, attribution_text="Credit"),
    )

    assert decision.verdict == TreatmentVerdict.REJECT


# --- evidence, download and import ---------------------------------------


def _candidate():
    return map_pexels_result(
        PHOTO,
        media_type=AssetMediaType.IMAGE,
        response_hash="a" * 64,
    )


def test_evidence_capture_marks_the_licence_verified(controller):
    project = create_project(controller)
    candidate = _candidate()

    result = controller.capture_asset_evidence(
        project.id,
        candidate,
        fetcher=lambda url: (b"<html>terms</html>", "text/html; charset=utf-8"),
    )

    updated = result["candidate"]
    assert updated.rights.origin_license_verified is True
    assert updated.rights.license_evidence_hash
    assert updated.rights.license_evidence_ref
    assert {item["kind"] for item in result["evidence"]} == {
        "item_page",
        "licence_page",
    }

    references = [
        artifact
        for artifact in controller.repository.list_artifacts(project.id)
        if artifact["kind"] == ArtifactKind.REFERENCE.value
    ]
    assert len(references) == 2


def test_download_registers_an_immutable_original(controller):
    project = create_project(controller)
    candidate = _candidate()
    captured = controller.capture_asset_evidence(
        project.id,
        candidate,
        fetcher=lambda url: (b"<html>terms</html>", "text/html; charset=utf-8"),
    )

    def fetcher(url: str):
        if url.endswith("/download"):
            return (b"event", "text/html")
        return (b"\x89PNG\r\n\x1a\nfake-image-bytes", "image/png")

    result = controller.download_stock_asset(
        project.id,
        candidate=captured["candidate"],
        context=context(),
        fetcher=fetcher,
        download_event_url="https://www.pexels.com/photo/13802140/download",
    )

    artifact = result["artifact"]
    assert artifact["kind"] == ArtifactKind.ORIGINAL.value
    assert artifact["immutable"] is True
    assert (
        artifact["operation"]["provider_compliance"]["download_event_recorded"] is True
    )
    assert result["candidate"]["rights"]["origin_license_verified"] is True


def test_download_refuses_a_candidate_without_evidence(controller):
    project = create_project(controller)

    with pytest.raises(RightsEligibilityError, match="evidence"):
        controller.download_stock_asset(
            project.id,
            candidate=_candidate(),
            context=context(),
            fetcher=lambda url: (b"bytes", "image/png"),
        )


# --- slot finalization ----------------------------------------------------


def _project_with_slot(controller):
    project = create_project(controller)
    shot = controller.add_shot(
        project.id,
        ShotCreate(
            stable_key="opening",
            ordinal=1,
            beat="Open on the founder",
            duration_seconds=Decimal(2),
            narrative="A founder reviews cash flow.",
        ),
    )
    slot = controller.add_slot(
        project.id,
        SlotCreate(
            shot_id=shot["id"],
            stable_key="founder-photo",
            role="hero photo",
            identity_requirement="SME founder, no recognizable brand",
            fact_requirement="shows a small business",
            transformation_permission="crop allowed",
            approved_sources=("pexels",),
        ),
    )
    return project, slot


def _imported_asset(controller, project):
    candidate = _candidate()
    captured = controller.capture_asset_evidence(
        project.id,
        candidate,
        fetcher=lambda url: (b"<html>terms</html>", "text/html; charset=utf-8"),
    )
    return controller.download_stock_asset(
        project.id,
        candidate=captured["candidate"],
        context=context(),
        fetcher=lambda url: (b"fake-bytes", "image/jpeg"),
    )["artifact"]


def _spare_artifact(controller, project, name: str):
    return controller.write_artifact(
        project.id,
        kind=ArtifactKind.GENERATED,
        relative_path=f"assets/generated/{name}.png",
        content=f"bytes-{name}".encode(),
        operation={"type": "test_generated"},
    )


def test_primary_and_backup_complete_coverage(controller):
    project, slot = _project_with_slot(controller)
    primary = _imported_asset(controller, project)
    backup = _spare_artifact(controller, project, "backup")

    controller.assign_slot_asset(
        project.id,
        slot["id"],
        artifact_id=primary["id"],
        role=SlotAssetRole.PRIMARY,
        criteria=AssetCriteria(),
        context=context(),
        decided_by="reviewer@example.test",
    )
    controller.assign_slot_asset(
        project.id,
        slot["id"],
        artifact_id=backup["id"],
        role=SlotAssetRole.BACKUP,
        criteria=AssetCriteria(),
        context=context(),
        decided_by="reviewer@example.test",
    )

    coverage = controller.slot_coverage(project.id)
    assert coverage["coverage"] == "complete"
    assert coverage["resolved"] == 1
    assert coverage["slots"][0]["primary_artifact_id"] == primary["id"]
    assert coverage["slots"][0]["backup_artifact_id"] == backup["id"]


def test_primary_without_backup_is_incomplete(controller):
    project, slot = _project_with_slot(controller)
    primary = _imported_asset(controller, project)

    controller.assign_slot_asset(
        project.id,
        slot["id"],
        artifact_id=primary["id"],
        role=SlotAssetRole.PRIMARY,
        criteria=AssetCriteria(),
        context=context(),
        decided_by="reviewer@example.test",
    )

    coverage = controller.slot_coverage(project.id)
    assert coverage["coverage"] == "incomplete"
    assert coverage["unresolved"][0]["problems"] == ["missing backup"]


def test_promote_backup_swaps_the_two_assets(controller):
    project, slot = _project_with_slot(controller)
    primary = _imported_asset(controller, project)
    backup = _spare_artifact(controller, project, "backup")
    for artifact, role in (
        (primary, SlotAssetRole.PRIMARY),
        (backup, SlotAssetRole.BACKUP),
    ):
        controller.assign_slot_asset(
            project.id,
            slot["id"],
            artifact_id=artifact["id"],
            role=role,
            criteria=AssetCriteria(),
            context=context(),
            decided_by="reviewer@example.test",
        )

    result = controller.promote_backup(
        project.id,
        slot["id"],
        decided_by="reviewer@example.test",
        reason="Person preferred the alternate frame",
    )

    assert result["primary_artifact_id"] == backup["id"]
    assert result["backup_artifact_id"] == primary["id"]
    slot_row = controller.slot_coverage(project.id)["slots"][0]
    assert slot_row["primary_artifact_id"] == backup["id"]


def test_ab_variants_must_be_a_complete_pair(controller):
    project, slot = _project_with_slot(controller)
    variant = _spare_artifact(controller, project, "variant-a")

    controller.assign_slot_asset(
        project.id,
        slot["id"],
        artifact_id=variant["id"],
        role=SlotAssetRole.VARIANT_A,
        criteria=AssetCriteria(),
        context=context(),
        decided_by="reviewer@example.test",
    )

    coverage = controller.slot_coverage(project.id)
    assert "incomplete A/B pair" in coverage["unresolved"][0]["problems"]


def test_ab_variants_cannot_reuse_the_same_artifact(controller):
    project, slot = _project_with_slot(controller)
    variant = _spare_artifact(controller, project, "variant-a")
    controller.assign_slot_asset(
        project.id,
        slot["id"],
        artifact_id=variant["id"],
        role=SlotAssetRole.VARIANT_A,
        criteria=AssetCriteria(),
        context=context(),
        decided_by="reviewer@example.test",
    )

    with pytest.raises(ConflictError, match="different artifacts"):
        controller.assign_slot_asset(
            project.id,
            slot["id"],
            artifact_id=variant["id"],
            role=SlotAssetRole.VARIANT_B,
            criteria=AssetCriteria(),
            context=context(),
            decided_by="reviewer@example.test",
        )


def test_unresolved_slot_is_reported(controller):
    project, _slot = _project_with_slot(controller)

    coverage = controller.slot_coverage(project.id)

    assert coverage["coverage"] == "incomplete"
    assert coverage["unresolved"][0]["stable_key"] == "founder-photo"


def test_preflight_warns_on_unresolved_slots(controller):
    project, _slot = _project_with_slot(controller)

    report = run_preflight(controller, project.id)
    rule = next(item for item in report["rules"] if item["rule_id"] == "R-SLOTS-001")

    assert rule["status"] in {"warn", "fail"}
    assert rule["evidence"]["unresolved"] == ["founder-photo"]


def test_preflight_passes_when_no_slots_are_declared(controller):
    project = create_project(controller)

    report = run_preflight(controller, project.id)
    rule = next(item for item in report["rules"] if item["rule_id"] == "R-SLOTS-001")

    assert rule["status"] == "pass"


# --- provider quota -------------------------------------------------------


def test_provider_quota_refuses_requests_beyond_the_window(controller):
    controller.reserve_provider_request(
        "pexels", "search", limit=2, window_seconds=3600
    )
    second = controller.reserve_provider_request(
        "pexels", "search", limit=2, window_seconds=3600
    )
    assert second["used"] == 2

    with pytest.raises(QuotaExceededError, match="quota reached"):
        controller.reserve_provider_request(
            "pexels", "search", limit=2, window_seconds=3600
        )


def test_provider_quota_is_per_provider(controller):
    controller.reserve_provider_request(
        "pexels", "search", limit=1, window_seconds=3600
    )

    reserved = controller.reserve_provider_request(
        "unsplash", "search", limit=1, window_seconds=3600
    )

    assert reserved["provider"] == "unsplash"

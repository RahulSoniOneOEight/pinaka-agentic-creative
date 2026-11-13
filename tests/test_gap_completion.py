from __future__ import annotations

import sqlite3
from decimal import Decimal
from urllib.error import HTTPError

import pytest
import yaml

from pinaka.assets import AssetCriteria
from pinaka.contracts.budget import BudgetPlanCreate, BudgetPlanItemCreate
from pinaka.contracts.effects import EffectCueCreate, EffectsPlanCreate
from pinaka.contracts.flow import FlowStepCreate
from pinaka.database import Database
from pinaka.enums import (
    ArtifactKind,
    AssetAcquisitionMethod,
    AssetMediaType,
    AssetProvider,
    BudgetCategory,
    SlotAssetRole,
)
from pinaka.errors import ApprovalRequiredError, RightsEligibilityError
from pinaka.models import (
    AssetCandidate,
    AssetRightsRecord,
    AssetSourceRecord,
    Money,
    TextBriefRequest,
)
from pinaka.schema import MIGRATIONS, SCHEMA_VERSION
from pinaka.use_cases._shared import _caption_coverage

from .conftest import create_project


def _plan_with_slots(controller, name="Gap completion"):
    project = create_project(controller, name=name)
    plan = controller.plan_from_text(
        project.id,
        TextBriefRequest(
            text="Make a five second rhyme about a duck swimming on a pond",
            persist_slots=True,
            persist_elements=True,
        ),
    )
    return project, plan


def _budget_item(key, ordinal, label=None):
    return BudgetPlanItemCreate(
        stable_key=key,
        ordinal=ordinal,
        category=BudgetCategory.ASSET_GENERATION,
        provider="owned",
        operation="owned_render",
        estimated_cost=Money(amount=Decimal("0.10"), currency="USD"),
        variant_label=label,
    )


def test_budget_items_carry_a_b_labels(controller):
    project = create_project(controller)
    result = controller.plan_creative_budget(
        project.id,
        BudgetPlanCreate(
            creative_summary="A/B budget",
            items=(
                _budget_item("hero-a", 1, "A"),
                _budget_item("hero-b", 2, "B"),
            ),
        ),
    )
    labels = [item["variant_label"] for item in result["plan"]["items"]]
    assert labels == ["A", "B"]
    assert result["summary"]["items"]["variant_a"] == 1
    assert result["summary"]["items"]["variant_b"] == 1


def test_change_request_roundtrip_impact_decision_and_revision(controller):
    project, _ = _plan_with_slots(controller)
    artifact = controller.write_artifact(
        project.id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path="delivery/metadata-test.json",
        content=b'{"title": "old"}',
        operation={"type": "delivery_metadata"},
    )
    generated = controller.change_request(project.id)
    document = yaml.safe_load(generated["yaml"])
    document["change_request"].update(
        {
            "requested_by": "producer",
            "requested_at": "2026-11-13T10:00:00Z",
            "change_type": "metadata",
            "element_or_artifact_id": artifact["id"],
            "current_hash": artifact["content_hash"],
            "requested_change": "Replace the title with the approved rhyme title",
            "reason": "The delivery title was still the working name",
            "approver": "client",
            "version_scope": "both",
        }
    )
    submitted = controller.submit_change_request(
        project.id, text=yaml.safe_dump(document, sort_keys=False)
    )
    request = submitted["request"]
    assert request["status"] == "analysed"
    assert request["classification"] == "safe_in_place"
    assert submitted["impact"]["budget"]["estimated_minor"] >= 0
    assert submitted["impact"]["checks"]["version_scope"] == "both"

    decided = controller.decide_change_request(
        request["id"],
        decision="approve",
        decided_by="client",
        reason="Title-only change approved",
    )
    assert decided["request"]["status"] == "approved"

    with controller.repository.database.transaction() as connection:
        connection.execute(
            "UPDATE projects SET status = 'archived' WHERE id = ?", (project.id,)
        )
    revision = controller.create_revision(
        project.id,
        name="Gap completion revision",
        objective="Apply the approved metadata change",
        change_request_id=request["id"],
        change_list=("replace title",),
        requested_by="client",
    )
    assert revision.parent_project_id == project.id
    assert revision.change_request_id == request["id"]
    status = controller.change_request_status(project.id, request["id"])
    assert status["request"]["status"] == "applied"
    assert status["revisions"] == [revision.id]
    flow = controller.flow_log(revision.id)
    assert any(
        step["step"] == "g0_intake"
        and any("change_request" in item for item in step["evidence"])
        for step in flow
    )


def test_slot_coverage_blocks_until_primary_and_backup_are_bound(controller):
    project, _ = _plan_with_slots(controller)
    with pytest.raises(ApprovalRequiredError):
        controller.require_slot_coverage(project.id)

    for slot in controller.repository.list_slots(project.id):
        for role, name in (
            (SlotAssetRole.PRIMARY, "primary"),
            (SlotAssetRole.BACKUP, "backup"),
        ):
            artifact = controller.write_artifact(
                project.id,
                kind=ArtifactKind.DERIVATIVE,
                relative_path=f"assets/derived/{slot['stable_key']}-{name}.png",
                content=f"{slot['stable_key']}-{name}".encode(),
                operation={"type": "owned_cutout", "slot_id": slot["id"]},
            )
            controller.assign_slot_asset(
                project.id,
                slot["id"],
                artifact_id=artifact["id"],
                role=role,
                criteria=AssetCriteria(),
                decided_by="producer",
            )
    coverage = controller.require_slot_coverage(project.id)
    assert coverage["coverage"] == "complete"
    visual_slots = [
        slot
        for slot in controller.repository.list_slots(project.id)
        if slot["role"]
        not in {"captions", "narration", "music", "cta", "hook", "claim_or_proof"}
    ]
    assert coverage["total"] == len(visual_slots)


def test_asr_captions_are_default_and_pad_to_runtime(controller):
    project, _ = _plan_with_slots(controller)
    narration = controller.write_artifact(
        project.id,
        kind=ArtifactKind.AUDIO,
        relative_path="audio/narration-test.wav",
        content=b"fake-narration",
        operation={"type": "kokoro_narration"},
    )

    class FakeTranscriber:
        def transcribe(self, path):
            return {
                "model_name": "fake-asr",
                "duration_seconds": 4.4,
                "segments": [
                    {
                        "words": [
                            {"start_seconds": 0.1, "end_seconds": 0.5, "text": "Five"},
                            {
                                "start_seconds": 0.6,
                                "end_seconds": 1.0,
                                "text": "little",
                            },
                            {"start_seconds": 1.1, "end_seconds": 1.5, "text": "ducks"},
                            {"start_seconds": 2.0, "end_seconds": 2.5, "text": "swim"},
                            {"start_seconds": 2.6, "end_seconds": 3.0, "text": "today"},
                        ]
                    }
                ],
            }

    exported = controller.export_captions(
        project.id,
        source="auto",
        narration_artifact_id=narration["id"],
        transcriber=FakeTranscriber(),
        pad_to_seconds=5.0,
    )
    assert exported["source"] == "asr"
    assert exported["artifact"]["operation"]["source"] == "asr"
    assert exported["artifact"]["operation"]["audio_artifact_id"] == narration["id"]
    covered, cues, overrun = _caption_coverage(exported["srt"], 5.0)
    assert cues == 1
    assert not overrun
    covered_ratio = covered / 5.0
    assert covered_ratio >= 0.9
    assert exported["artifact"]["operation"]["tail_hold_seconds"] == 2.0


def test_effects_are_authored_and_target_duration_scales(controller):
    project, plan = _plan_with_slots(controller)
    cues = []
    for shot in plan["shots"]:
        cues.extend(
            (
                EffectCueCreate(
                    shot_key=shot["stable_key"],
                    kind="light_glow",
                    start_seconds="0",
                    duration_seconds="1.5",
                    rights="owned:composition",
                ),
                EffectCueCreate(
                    shot_key=shot["stable_key"],
                    kind="particles",
                    start_seconds="0.2",
                    duration_seconds="1.2",
                    rights="owned:composition",
                ),
                EffectCueCreate(
                    shot_key=shot["stable_key"],
                    kind="blur",
                    start_seconds="0.4",
                    duration_seconds="1.0",
                    rights="owned:composition",
                ),
            )
        )
    controller.record_effects_plan(
        project.id, EffectsPlanCreate(cues=tuple(cues), sound_design_notes="pond")
    )
    composition = controller.author_composition(
        project.id, embed_layers=False, target_duration=5.0
    )
    html = composition["artifact"]["relative_path"]
    assert html.endswith("index.html")
    body = controller.artifact_store.resolve(
        project.id, composition["artifact"]["relative_path"]
    ).read_text(encoding="utf-8")
    assert 'data-effect="light_glow"' in body
    assert 'data-effect="particles"' in body
    assert 'data-effect="blur"' in body
    assert 'data-duration="5.0"' in body
    assert composition["artifact"]["operation"]["effects"]
    assert composition["artifact"]["operation"]["effects_plan_hash"]


def test_studio_ownership_is_auto_linked_from_artifact_evidence(controller):
    project = create_project(controller)
    studio_artifact = controller.write_artifact(
        project.id,
        kind=ArtifactKind.RENDER,
        relative_path="artifacts/studio-render.mp4",
        content=b"studio-render",
        operation={
            "type": "openmontage_result",
            "execution_owner": "studio",
            "studio_role": "owner",
        },
    )
    step = controller.record_flow_step(
        project.id,
        FlowStepCreate(
            step="g5_production",
            status="done",
            actor="producer",
            evidence=(studio_artifact["id"],),
        ),
    )
    assert step["execution_owner"] == "studio"
    assert step["studio_role"] == "owner"
    assert step["trade_off"]


def test_pexels_403_is_archived_and_marks_provider_unavailable(controller):
    project = create_project(controller)
    candidate = AssetCandidate(
        source=AssetSourceRecord(
            provider=AssetProvider.PEXELS,
            provider_asset_id="25949846",
            media_type=AssetMediaType.VIDEO,
            acquisition_method=AssetAcquisitionMethod.OFFICIAL_API,
            source_page_url="https://www.pexels.com/video/duck-25949846/",
            creator_name="Pexels Creator",
            direct_asset_url="https://videos.pexels.com/video.mp4",
            api_response_hash="b" * 64,
        ),
        rights=AssetRightsRecord(
            license_name="Pexels License",
            license_code="pexels",
            license_url="https://www.pexels.com/license/",
            terms_url="https://www.pexels.com/terms-of-service/",
            terms_reviewed_at="2026-11-13",
            commercial_use_allowed=True,
            modifications_allowed=True,
            editorial_only=False,
            attribution_required=False,
            origin_license_verified=False,
            rights_uncertainty=(),
        ),
    )

    def blocked(url):
        raise HTTPError(url, 403, "Forbidden", None, None)

    with pytest.raises(RightsEligibilityError):
        controller.capture_asset_evidence(project.id, candidate, fetcher=blocked)

    artifacts = controller.repository.list_artifacts(project.id)
    blocked_artifacts = [
        artifact
        for artifact in artifacts
        if artifact["operation"].get("type") == "provider_blocked"
    ]
    assert blocked_artifacts
    assert blocked_artifacts[0]["operation"]["status"] == 403
    capability = controller.repository.latest_capability("asset-provider:pexels")
    assert capability["status"] == "unavailable"
    assert "HTTP 403" in capability["detail"]


def test_schema_v24_migrates_to_v25(tmp_path):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        for version in range(1, 25):
            connection.executescript(MIGRATIONS[version])
        connection.execute("PRAGMA user_version = 24")
    Database(path).initialize()
    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        budget_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(budget_plan_items)")
        }
        project_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(projects)")
        }
        flow_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(flow_steps)")
        }
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert version == SCHEMA_VERSION == 25
    assert {"variant_label", "ab_experiment_id"} <= budget_columns
    assert "change_request_id" in project_columns
    assert {"execution_owner", "studio_role", "fallback_of"} <= flow_columns
    assert {"change_requests", "change_request_events", "ab_deliveries"} <= tables

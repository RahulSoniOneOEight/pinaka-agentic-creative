from __future__ import annotations

import json
import sqlite3
from decimal import Decimal

import pytest

from pinaka.cli import main
from pinaka.controller import CreativeController
from pinaka.database import Database
from pinaka.enums import (
    ArtifactKind,
    CapabilityStatus,
    ContentMode,
    CreativeElementKind,
    DecisionProvenance,
    DecisionRisk,
    StudioWorkPhase,
    ToolIntegrationMode,
    ToolOperationStatus,
)
from pinaka.errors import (
    ApprovalRequiredError,
    BudgetExceededError,
    IntegrityError,
)
from pinaka.models import (
    BriefDecisionCreate,
    CreativeElementCreate,
    Money,
    ProjectCreate,
    ShotCreate,
    ShotElementBindingCreate,
    SlotCreate,
    StudioResultOutputImport,
    StudioResultPacketImport,
    StudioScope,
    StudioSpendApprovalCreate,
    StudioWorkPacketRequest,
    ToolProfileCreate,
    WorkableBriefCreate,
)
from pinaka.schema import MIGRATIONS, SCHEMA_VERSION
from pinaka.studio import DeterministicStudioAdapter
from pinaka.utils import sha256_bytes

from .conftest import create_project


def _source_artifact(controller, project_id, tmp_path):
    source = tmp_path / "request.txt"
    source.write_text(
        "Make a five-second counting rhyme for children aged four to six.",
        encoding="utf-8",
    )
    return controller.import_original(project_id, source)


def _brief_request(source, *, concept="Three original ducklings count together"):
    return WorkableBriefCreate(
        source_artifact_id=source["id"],
        source_content_hash=source["content_hash"],
        objective="Create a five-second counting rhyme",
        audience="Children aged four to six",
        deliverable="Five-second 9:16 animated review master",
        classification={
            "content_family": "character_story",
            "subject": "kids",
            "production_lane": "deterministic_composition",
        },
        treatment={
            "concept": concept,
            "visual_style": "original paper-cut animation",
            "world": "simple pond",
        },
        beats=(
            {
                "id": "beat-01",
                "start": "0",
                "end": "5",
                "visual": "Three ducklings swim in a row",
            },
        ),
        audio_plan={"voice": "warm synthetic adult", "music": "optional"},
        acceptance={
            "duration_seconds": "5",
            "visible_duck_count": 3,
            "publication_authorized": False,
        },
        decisions=(
            BriefDecisionCreate(
                field="visual.background",
                value="simple stylised pond",
                state=DecisionProvenance.ASSUMED,
                basis="The supplied action is swimming",
                risk=DecisionRisk.LOW,
                reversible=True,
                alternatives_considered=("paper-cut lake",),
                affected_shot_keys=("shot-01",),
            ),
            BriefDecisionCreate(
                field="voice.identity",
                value=None,
                state=DecisionProvenance.BLOCKED,
                basis="No voice identity or consent was supplied",
                risk=DecisionRisk.HIGH,
                reversible=False,
                blocked_work=("narration",),
            ),
        ),
    )


def _studio_profile(controller):
    return controller.register_tool_profile(
        ToolProfileCreate(
            stable_key="openmontage-deterministic",
            name="OpenMontage deterministic boundary fixture",
            integration_mode=ToolIntegrationMode.SPECIALIST_HANDOFF,
            executable_or_endpoint="controller:deterministic-studio",
            supported_operations=("openmontage.execute_work_packet",),
            capability_status=CapabilityStatus.WORKING,
        )
    )


def _planned_project(controller, tmp_path, *, budget="10.00"):
    project = create_project(controller, name="Kids rhyme", budget=budget)
    source = _source_artifact(controller, project.id, tmp_path)
    brief = controller.record_workable_brief(project.id, _brief_request(source))
    shot = controller.add_shot(
        project.id,
        ShotCreate(
            stable_key="shot-01",
            ordinal=1,
            beat="Count three ducklings",
            duration_seconds=Decimal(5),
            narrative="Three original ducklings swim in a row.",
            requirements={"duck_count": 3, "text": "none"},
        ),
    )
    controller.add_slot(
        project.id,
        SlotCreate(
            shot_id=shot["id"],
            stable_key="shot-01.characters",
            role="primary characters",
            identity_requirement="Three original, non-protected ducklings",
            fact_requirement="Exactly three visible ducklings",
            transformation_permission="Deterministic composition and gentle animation",
            approved_sources=("owned", "deterministic_composition"),
        ),
    )
    element_request = CreativeElementCreate(
        stable_key="character.duckling",
        kind=CreativeElementKind.CHARACTER,
        name="Original duckling trio",
        specification={"count": 3, "style": "paper-cut", "protected": False},
        continuity={"body": "yellow", "beak": "orange", "marks": "none"},
    )
    element = controller.record_creative_element(project.id, element_request)
    controller.bind_element_to_shot(
        project.id,
        ShotElementBindingCreate(
            shot_id=shot["id"],
            element_id=element["id"],
            role="primary_characters",
        ),
    )
    controller.record_scene_plan(
        project.id,
        "# Scene plan\n\n- shot-01: Three ducklings swim in a row for 5 seconds.\n",
    )
    return project, source, brief, shot, element, element_request


def test_brief_decisions_and_elements_are_versioned_and_reopen(controller, tmp_path):
    project, source, brief, shot, element, element_request = _planned_project(
        controller, tmp_path
    )
    repeated = controller.record_workable_brief(project.id, _brief_request(source))
    assert repeated["id"] == brief["id"]
    assert brief["version"] == 1
    assert {item["state"] for item in brief["package"]["decisions"]} == {
        "assumed",
        "blocked",
    }

    revised = controller.record_workable_brief(
        project.id,
        _brief_request(source, concept="Three ducklings count with gentle ripples"),
    )
    assert revised["version"] == 2
    assert not controller.repository.get_workable_brief(brief["id"])["is_current"]

    repeated_element = controller.record_creative_element(project.id, element_request)
    assert repeated_element["id"] == element["id"]
    revised_element = controller.record_creative_element(
        project.id,
        element_request.model_copy(
            update={
                "continuity": {
                    "body": "warm yellow",
                    "beak": "orange",
                    "marks": "none",
                }
            }
        ),
    )
    assert revised_element["version"] == 2
    bindings = controller.repository.list_shot_element_bindings(project.id)
    assert len(bindings) == 1
    assert bindings[0]["element_id"] == revised_element["id"]
    assert controller.repository.get_shot(shot["id"])["stable_key"] == "shot-01"

    reopened = CreativeController.open(tmp_path / "runtime")
    assert reopened.workable_brief(project.id)["id"] == revised["id"]
    assert reopened.list_creative_elements(project.id) == [revised_element]


def test_studio_packet_partial_blocking_idempotency_and_fake_return(
    controller, tmp_path
):
    project, _, _, _, element, element_request = _planned_project(controller, tmp_path)
    profile = _studio_profile(controller)
    storyboard_request = StudioWorkPacketRequest(
        tool_profile_id=profile["id"],
        idempotency_key="kids-rhyme-storyboard-v1",
        instructions="Create the governed storyboard and editable composition only.",
        scope=StudioScope(
            phases=(StudioWorkPhase.STORYBOARD, StudioWorkPhase.COMPOSITION),
        ),
        output_declaration={"roles": ["deterministic_result"]},
    )
    prepared = controller.prepare_studio_work_packet(project.id, storyboard_request)
    repeated = controller.prepare_studio_work_packet(project.id, storyboard_request)
    assert repeated["operation"]["id"] == prepared["operation"]["id"]
    assert repeated["handoff"]["id"] == prepared["handoff"]["id"]
    assert prepared["operation"]["limits"] == {
        "allow_publication": False,
        "currency": "USD",
        "max_cost_minor": 100,
        "max_credits": 0,
        "paid": True,
    }
    assert prepared["packet"]["controller_authority"]["max_usd"] == "1"
    assert prepared["packet"]["slots"][0]["stable_key"] == "shot-01.characters"
    assert prepared["packet"]["slot_asset_assignments"] == []
    assert prepared["packet"]["audio_beds"] == []
    assert Decimal(prepared["budget_job"]["reserved_cost"]["amount"]) == Decimal("1.00")
    assert controller.repository.get_project(project.id).reserved.minor == 100

    with pytest.raises(ApprovalRequiredError, match="voice.identity"):
        controller.prepare_studio_work_packet(
            project.id,
            storyboard_request.model_copy(
                update={
                    "idempotency_key": "kids-rhyme-narration-v1",
                    "scope": StudioScope(
                        phases=(StudioWorkPhase.NARRATION,),
                        max_usd=Decimal(0),
                        allow_paid_generation=False,
                    ),
                }
            ),
        )

    result = controller.run_studio_work_packet(
        prepared["operation"]["id"], DeterministicStudioAdapter()
    )
    assert result["receipt"]["status"] == ToolOperationStatus.SUCCEEDED.value
    assert result["receipt"]["actual_cost_minor"] == 0
    assert len(result["artifacts"]) == 1
    assert controller.verify_artifact(result["artifacts"][0]["id"])
    settled_project = controller.repository.get_project(project.id)
    assert settled_project.reserved.minor == 0
    assert settled_project.spent.minor == 0
    repeated_result = controller.run_studio_work_packet(
        prepared["operation"]["id"], DeterministicStudioAdapter()
    )
    assert repeated_result["receipt"]["id"] == result["receipt"]["id"]

    controller.record_creative_element(
        project.id,
        element_request.model_copy(
            update={"specification": {**element_request.specification, "count": 4}}
        ),
    )
    assert (
        controller.repository.get_artifact(prepared["work_packet_artifact"]["id"])[
            "status"
        ]
        == "stale"
    )
    assert controller.repository.get_artifact(result["artifacts"][0]["id"])[
        "status"
    ] == ("stale")
    assert (
        controller.repository.get_creative_element(element["id"])["is_current"] is False
    )


def test_external_studio_result_import_validates_hash_lineage_and_zero_cost(
    controller, tmp_path
):
    project, *_ = _planned_project(controller, tmp_path)
    profile = _studio_profile(controller)
    prepared = controller.prepare_studio_work_packet(
        project.id,
        StudioWorkPacketRequest(
            tool_profile_id=profile["id"],
            idempotency_key="kids-rhyme-board-import-v1",
            instructions="Return one storyboard manifest.",
            scope=StudioScope(
                phases=(StudioWorkPhase.STORYBOARD,),
                max_usd=Decimal(0),
                allow_paid_generation=False,
            ),
            output_declaration={"roles": ["storyboard_manifest"]},
        ),
    )
    assert prepared["budget_job"] is None
    output = tmp_path / "studio-result.json"
    output.write_bytes(b'{"storyboard":"complete","simulated":true}')
    packet = StudioResultPacketImport(
        operation_id=prepared["operation"]["id"],
        status=ToolOperationStatus.SUCCEEDED,
        elapsed_ms=20,
        outputs=(
            StudioResultOutputImport(
                source_path=str(output),
                relative_path="artifacts/openmontage/results/storyboard.json",
                kind=ArtifactKind.DERIVATIVE,
                content_hash=sha256_bytes(output.read_bytes()),
                role="storyboard_manifest",
            ),
        ),
        response_hash="a" * 64,
        actual_cost=Money(amount=Decimal(0), currency="USD"),
        details={"tool_version": "fixture/v1"},
    )
    imported = controller.import_studio_result(packet)
    assert imported["receipt"]["actual_cost_minor"] == 0
    assert imported["handoff"]["result_packet_hash"]
    artifact = imported["artifacts"][0]
    dependency_types = {item["entity_type"] for item in artifact["dependencies"]}
    assert dependency_types == {"artifact", "tool_operation"}
    repeated = controller.import_studio_result(packet)
    assert repeated["receipt"]["id"] == imported["receipt"]["id"]


def test_studio_run_costs_settle_within_reserved_budget(controller, tmp_path):
    project, *_ = _planned_project(controller, tmp_path, budget="5.00")
    profile = _studio_profile(controller)
    prepared = controller.prepare_studio_work_packet(
        project.id,
        StudioWorkPacketRequest(
            tool_profile_id=profile["id"],
            idempotency_key="kids-rhyme-paid-import-v1",
            instructions="Return one storyboard manifest.",
            scope=StudioScope(phases=(StudioWorkPhase.STORYBOARD,)),
            output_declaration={"roles": ["storyboard_manifest"]},
        ),
    )
    output = tmp_path / "paid-result.json"
    output.write_bytes(b'{"storyboard":"complete"}')
    imported = controller.import_studio_result(
        StudioResultPacketImport(
            operation_id=prepared["operation"]["id"],
            status=ToolOperationStatus.SUCCEEDED,
            elapsed_ms=40,
            outputs=(
                StudioResultOutputImport(
                    source_path=str(output),
                    relative_path="artifacts/openmontage/results/paid-storyboard.json",
                    kind=ArtifactKind.DERIVATIVE,
                    content_hash=sha256_bytes(output.read_bytes()),
                    role="storyboard_manifest",
                ),
            ),
            actual_cost=Money(amount=Decimal("0.25"), currency="USD"),
        )
    )
    assert imported["receipt"]["actual_cost_minor"] == 25
    settled = controller.repository.get_project(project.id)
    assert settled.reserved.minor == 0
    assert settled.spent.minor == 25

    over_request = controller.prepare_studio_work_packet(
        project.id,
        StudioWorkPacketRequest(
            tool_profile_id=profile["id"],
            idempotency_key="kids-rhyme-overrun-v1",
            instructions="Return one more storyboard manifest.",
            scope=StudioScope(phases=(StudioWorkPhase.STORYBOARD,)),
            output_declaration={"roles": ["storyboard_manifest"]},
        ),
    )
    with pytest.raises(BudgetExceededError, match="exceeds the controller reservation"):
        controller.import_studio_result(
            StudioResultPacketImport(
                operation_id=over_request["operation"]["id"],
                status=ToolOperationStatus.SUCCEEDED,
                elapsed_ms=40,
                outputs=(
                    StudioResultOutputImport(
                        source_path=str(output),
                        relative_path=(
                            "artifacts/openmontage/results/overrun-storyboard.json"
                        ),
                        kind=ArtifactKind.DERIVATIVE,
                        content_hash=sha256_bytes(output.read_bytes()),
                        role="storyboard_manifest",
                    ),
                ),
                actual_cost=Money(amount=Decimal("2.00"), currency="USD"),
            )
        )


def test_studio_spend_above_one_dollar_requires_matching_approval(controller, tmp_path):
    project, *_ = _planned_project(controller, tmp_path, budget="10.00")
    profile = _studio_profile(controller)
    request = StudioWorkPacketRequest(
        tool_profile_id=profile["id"],
        idempotency_key="kids-rhyme-premium-v1",
        instructions="Return one premium storyboard manifest.",
        scope=StudioScope(
            phases=(StudioWorkPhase.STORYBOARD,),
            max_usd=Decimal("2.50"),
        ),
        output_declaration={"roles": ["storyboard_manifest"]},
    )
    with pytest.raises(ApprovalRequiredError, match="above USD 1.00"):
        controller.prepare_studio_work_packet(project.id, request)

    approval = controller.approve_studio_spend(
        project.id,
        StudioSpendApprovalCreate(
            work_packet=request,
            approved_by="producer",
            reason="Approved storyboard generation for the premium run",
        ),
    )
    assert approval["max_cost_minor"] == 250

    with pytest.raises(ApprovalRequiredError, match="does not match"):
        controller.prepare_studio_work_packet(
            project.id,
            request.model_copy(
                update={
                    "idempotency_key": "kids-rhyme-premium-v2",
                    "instructions": "A different request entirely",
                    "spend_approval_id": approval["id"],
                }
            ),
        )

    prepared = controller.prepare_studio_work_packet(
        project.id,
        request.model_copy(update={"spend_approval_id": approval["id"]}),
    )
    assert Decimal(prepared["budget_job"]["reserved_cost"]["amount"]) == Decimal("2.50")
    assert prepared["handoff"]["spend_approval_id"] == approval["id"]
    assert (
        controller.repository.get_studio_spend_approval(approval["id"])[
            "consumed_operation_id"
        ]
        == prepared["operation"]["id"]
    )
    repeated = controller.prepare_studio_work_packet(
        project.id,
        request.model_copy(update={"spend_approval_id": approval["id"]}),
    )
    assert repeated["operation"]["id"] == prepared["operation"]["id"]

    another = StudioWorkPacketRequest(
        tool_profile_id=profile["id"],
        idempotency_key="kids-rhyme-premium-v3",
        instructions="Another premium request.",
        scope=StudioScope(
            phases=(StudioWorkPhase.STORYBOARD,),
            max_usd=Decimal("2.50"),
        ),
        output_declaration={"roles": ["storyboard_manifest"]},
    )
    with pytest.raises(ApprovalRequiredError, match="does not match"):
        controller.prepare_studio_work_packet(
            project.id,
            another.model_copy(update={"spend_approval_id": approval["id"]}),
        )


def test_studio_spend_requires_usd_budget_and_available_funds(controller, tmp_path):
    rupees = controller.create_project(
        ProjectCreate(
            name="Rupee project",
            objective="Create one governed reel",
            mode=ContentMode.PRODUCT,
            audience="Reviewers",
            output_format="9:16",
            language="en",
            duration_seconds=Decimal(10),
            max_budget=Money(amount=Decimal("500.00"), currency="INR"),
        )
    )
    profile = _studio_profile(controller)
    payload = {
        "tool_profile_id": profile["id"],
        "instructions": "Storyboard only.",
        "scope": StudioScope(phases=(StudioWorkPhase.STORYBOARD,)),
        "output_declaration": {"roles": ["storyboard_manifest"]},
    }
    with pytest.raises(IntegrityError, match="denominated in USD"):
        controller.prepare_studio_work_packet(
            rupees.id,
            StudioWorkPacketRequest(idempotency_key="rupee-v1", **payload),
        )

    small, *_ = _planned_project(controller, tmp_path, budget="0.50")
    with pytest.raises(BudgetExceededError, match="Insufficient budget"):
        controller.prepare_studio_work_packet(
            small.id,
            StudioWorkPacketRequest(idempotency_key="small-v1", **payload),
        )


def test_schema_v8_migrates_to_v10_without_losing_project(tmp_path):
    path = tmp_path / "v8.sqlite"
    with sqlite3.connect(path) as connection:
        for version in range(1, 9):
            connection.executescript(MIGRATIONS[version])
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, f"2026-11-10T00:00:0{version}.000Z"),
            )
        connection.execute(
            """
            INSERT INTO projects(
                id, slug, name, objective, mode, audience, output_format, language,
                duration_seconds, status, max_budget_minor, currency, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "prj_v8",
                "v8",
                "V8 project",
                "Preserve this project",
                "entertainment",
                "Reviewers",
                "9:16",
                "en",
                "5",
                "draft",
                0,
                "USD",
                "2026-11-10T00:00:00.000Z",
                "2026-11-10T00:00:00.000Z",
            ),
        )
        connection.execute("PRAGMA user_version = 8")

    Database(path).initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert (
            connection.execute(
                "SELECT name FROM projects WHERE id = 'prj_v8'"
            ).fetchone()[0]
            == "V8 project"
        )
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "workable_briefs",
            "brief_decisions",
            "creative_elements",
            "shot_element_bindings",
            "studio_spend_approvals",
        } <= tables
        handoff_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(specialist_handoffs)")
        }
        assert {
            "operation_id",
            "work_packet_artifact_id",
            "result_packet_hash",
            "budget_job_id",
            "spend_approval_id",
        } <= handoff_columns


def test_workable_brief_element_and_studio_cli_journey(controller, tmp_path, capsys):
    project, source, *_ = _planned_project(controller, tmp_path)
    profile = _studio_profile(controller)
    brief_file = tmp_path / "brief.json"
    brief_file.write_text(
        json.dumps(_brief_request(source).model_dump(mode="json")), encoding="utf-8"
    )
    request_file = tmp_path / "studio-request.json"
    request_file.write_text(
        json.dumps(
            StudioWorkPacketRequest(
                tool_profile_id=profile["id"],
                idempotency_key="kids-rhyme-cli-v1",
                instructions="Prepare a storyboard-only handoff.",
                scope=StudioScope(phases=(StudioWorkPhase.STORYBOARD,)),
                output_declaration={"roles": ["storyboard_manifest"]},
            ).model_dump(mode="json")
        ),
        encoding="utf-8",
    )
    home = tmp_path / "runtime"
    assert (
        main(
            [
                "--home",
                str(home),
                "record-workable-brief",
                project.id,
                "--file",
                str(brief_file),
            ]
        )
        == 0
    )
    assert main(["--home", str(home), "workable-brief", project.id]) == 0
    assert main(["--home", str(home), "list-creative-elements", project.id]) == 0
    assert (
        main(
            [
                "--home",
                str(home),
                "prepare-studio-work-packet",
                project.id,
                "--file",
                str(request_file),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "pinaka.openmontage.work-packet/v1" in output

from __future__ import annotations

import pytest

from pinaka.enums import (
    ArtifactKind,
    CapabilityStatus,
    CreativeElementKind,
    StudioWorkPhase,
    ToolIntegrationMode,
)
from pinaka.errors import NotFoundError
from pinaka.models import (
    CreativeElementCreate,
    ShotReferenceCreate,
    StudioScope,
    StudioWorkPacketRequest,
    TextBriefRequest,
    ToolProfileCreate,
)
from pinaka.references import provider_reference_payload, reference_summary

from .conftest import create_project, record_studio_ready

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8"/></svg>'


def _project(controller):
    record_studio_ready(controller)
    project = create_project(controller, budget="10.00")
    result = controller.plan_from_text(
        project.id, TextBriefRequest(text="Make a 5 second rhyme for kids")
    )
    return project, result["shots"]


def _art(controller, project_id, name="ref.svg"):
    return controller.write_artifact(
        project_id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path=f"assets/{name}",
        content=SVG,
        operation={"type": "sourced_import"},
    )


def test_reference_binds_an_artifact_to_a_shot_role(controller):
    project, shots = _project(controller)
    art = _art(controller, project.id)
    record = controller.bind_shot_reference(
        project.id,
        ShotReferenceCreate(
            shot_id=shots[0]["id"], role="character", artifact_id=art["id"]
        ),
    )
    assert record["role"] == "character"
    assert record["artifact_id"] == art["id"]
    assert any(
        event["event_type"] == "shot_reference.created"
        for event in controller.repository.events(project.id)
    )


def test_reference_role_is_upserted(controller):
    project, shots = _project(controller)
    first = _art(controller, project.id, "a.svg")
    second = _art(controller, project.id, "b.svg")
    controller.bind_shot_reference(
        project.id,
        ShotReferenceCreate(
            shot_id=shots[0]["id"], role="character", artifact_id=first["id"]
        ),
    )
    updated = controller.bind_shot_reference(
        project.id,
        ShotReferenceCreate(
            shot_id=shots[0]["id"], role="character", artifact_id=second["id"]
        ),
    )
    assert updated["artifact_id"] == second["id"]
    assert len(controller.shot_references(project.id)) == 1


def test_reference_requires_exactly_one_source(controller):
    project, shots = _project(controller)
    with pytest.raises(ValueError):
        ShotReferenceCreate(shot_id=shots[0]["id"], role="character")
    art = _art(controller, project.id)
    element = controller.record_creative_element(
        project.id,
        CreativeElementCreate(
            stable_key="character.duck",
            kind=CreativeElementKind.CHARACTER,
            name="Duck",
            specification={"origin": "original"},
            source_artifact_id=art["id"],
        ),
    )
    with pytest.raises(ValueError):
        ShotReferenceCreate(
            shot_id=shots[0]["id"],
            role="character",
            artifact_id=art["id"],
            element_id=element["id"],
        )


def test_reference_rejects_unknown_artifacts(controller):
    project, shots = _project(controller)
    with pytest.raises(NotFoundError):
        controller.bind_shot_reference(
            project.id,
            ShotReferenceCreate(
                shot_id=shots[0]["id"], role="character", artifact_id="art_missing"
            ),
        )


def test_packet_carries_references_and_provider_hints(controller):
    project, shots = _project(controller)
    art = _art(controller, project.id)
    controller.bind_shot_reference(
        project.id,
        ShotReferenceCreate(
            shot_id=shots[0]["id"], role="character", artifact_id=art["id"]
        ),
    )
    profile = controller.register_tool_profile(
        ToolProfileCreate(
            stable_key="openmontage-deterministic",
            name="Studio fixture",
            integration_mode=ToolIntegrationMode.SPECIALIST_HANDOFF,
            executable_or_endpoint="controller:studio",
            supported_operations=("openmontage.execute_work_packet",),
            capability_status=CapabilityStatus.WORKING,
        )
    )
    prepared = controller.prepare_studio_work_packet(
        project.id,
        StudioWorkPacketRequest(
            tool_profile_id=profile["id"],
            idempotency_key="ref-packet",
            instructions="Storyboard with references.",
            scope=StudioScope(phases=(StudioWorkPhase.STORYBOARD,)),
            output_declaration={"roles": ["storyboard_manifest"]},
        ),
    )
    packet = prepared["packet"]
    assert packet["reference_summary"]["count"] == 1
    assert packet["reference_summary"]["roles"] == ["character"]
    # Generated motion is an exception: no provider hints unless explicitly requested.
    assert packet["provider_reference_hints"] == {}
    assert packet["generated_motion"]["requested"] is False
    input_ids = {item["id"] for item in packet["input_artifacts"]}
    assert art["id"] in input_ids


def test_provider_hints_appear_only_when_generated_motion_is_requested(controller):
    project, shots = _project(controller)
    art = _art(controller, project.id)
    controller.bind_shot_reference(
        project.id,
        ShotReferenceCreate(
            shot_id=shots[0]["id"], role="character", artifact_id=art["id"]
        ),
    )
    profile = controller.register_tool_profile(
        ToolProfileCreate(
            stable_key="openmontage-deterministic",
            name="Studio fixture",
            integration_mode=ToolIntegrationMode.SPECIALIST_HANDOFF,
            executable_or_endpoint="controller:studio",
            supported_operations=("openmontage.execute_work_packet",),
            capability_status=CapabilityStatus.WORKING,
        )
    )
    prepared = controller.prepare_studio_work_packet(
        project.id,
        StudioWorkPacketRequest(
            tool_profile_id=profile["id"],
            idempotency_key="gen-packet",
            instructions="One shot needs photoreal motion.",
            scope=StudioScope(phases=(StudioWorkPhase.STORYBOARD,)),
            output_declaration={"roles": ["storyboard_manifest"]},
            request_generated_motion=True,
            generated_motion_reason="Client asked for live-action water motion in shot 1",
        ),
    )
    packet = prepared["packet"]
    assert packet["generated_motion"]["requested"] is True
    assert packet["generated_motion"]["providers"] == ["veo", "runway"]
    assert (
        packet["provider_reference_hints"]["veo"]["reference_images"][0]["sha256"]
        == art["content_hash"]
    )
    assert packet["controller_authority"]["generated_motion_authorized"] is True


def test_generated_motion_requires_a_stated_reason():
    import pytest as _pytest
    from pydantic import ValidationError

    with _pytest.raises(ValidationError, match="requires a stated reason"):
        StudioWorkPacketRequest(
            tool_profile_id="tool_x",
            idempotency_key="x",
            instructions="x",
            scope=StudioScope(phases=(StudioWorkPhase.STORYBOARD,)),
            output_declaration={"roles": ["a"]},
            request_generated_motion=True,
        )


def test_reference_from_an_element_uses_its_artwork(controller):
    project, shots = _project(controller)
    art = _art(controller, project.id)
    element = controller.record_creative_element(
        project.id,
        CreativeElementCreate(
            stable_key="character.duck",
            kind=CreativeElementKind.CHARACTER,
            name="Duck",
            specification={"origin": "original"},
            source_artifact_id=art["id"],
        ),
    )
    controller.bind_shot_reference(
        project.id,
        ShotReferenceCreate(
            shot_id=shots[0]["id"], role="character", element_id=element["id"]
        ),
    )
    references = controller.shot_references(project.id)
    assert references[0]["element_id"] == element["id"]
    assert references[0]["artifact_id"] is None


def test_provider_mapping_rejects_unknown_providers():
    with pytest.raises(ValueError, match="Unsupported reference provider"):
        provider_reference_payload([], "unknown")


def test_reference_summary_is_compact():
    summary = reference_summary(
        [
            {"role": "character", "shot_id": "s1", "artifact": {}},
            {"role": "location", "shot_id": "s1", "artifact": {}},
        ]
    )
    assert summary == {
        "count": 2,
        "roles": ["character", "location"],
        "shots": ["s1"],
    }

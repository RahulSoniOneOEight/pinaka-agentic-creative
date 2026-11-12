from __future__ import annotations

from pinaka.enums import ArtifactKind, CreativeElementKind, SlotAssetRole
from pinaka.models import (
    CreativeElementCreate,
    ShotElementBindingCreate,
    SlotCreate,
    TextBriefRequest,
)

from .conftest import create_project

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><circle cx="32" cy="32" r="30" fill="#f7d64a"/></svg>'


def _project(controller):
    project = create_project(controller, budget="10.00")
    controller.plan_from_text(
        project.id, TextBriefRequest(text="Make a 5 second rhyme for kids")
    )
    return project, controller.repository.list_shots(project.id)


def _art(controller, project_id, name="duck.svg"):
    return controller.write_artifact(
        project_id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path=f"assets/{name}",
        content=SVG,
        operation={"type": "sourced_import"},
    )


def _element(controller, project_id, kind, key, *, artifact_id):
    return controller.record_creative_element(
        project_id,
        CreativeElementCreate(
            stable_key=key,
            kind=kind,
            name=key,
            specification={"origin": "original"},
            source_artifact_id=artifact_id,
        ),
    )


def _slot(controller, project_id, shot_id, *, role, key):
    return controller.add_slot(
        project_id,
        SlotCreate(
            shot_id=shot_id,
            stable_key=key,
            role=role,
            identity_requirement="Original artwork",
            fact_requirement="Matches the beat",
            transformation_permission="Compose",
            approved_sources=("owned",),
        ),
    )


def test_element_artwork_is_assigned_to_the_matching_slot(controller):
    project, shots = _project(controller)
    art = _art(controller, project.id)
    element = _element(
        controller,
        project.id,
        CreativeElementKind.CHARACTER,
        "character.duck",
        artifact_id=art["id"],
    )
    slot = _slot(
        controller, project.id, shots[0]["id"], role="subject", key="s1.subject"
    )
    controller.bind_element_to_shot(
        project.id,
        ShotElementBindingCreate(
            shot_id=shots[0]["id"], element_id=element["id"], role="character"
        ),
    )

    result = controller.bind_elements_to_slots(project.id)
    assert result["assigned_count"] == 1
    entry = result["assigned"][0]
    assert entry["slot"] == "s1.subject"
    assert entry["artifact_id"] == art["id"]

    assignment = controller.repository.slot_asset_by_role(
        slot["id"], SlotAssetRole.PRIMARY.value
    )
    assert assignment["artifact_id"] == art["id"]


def test_bound_element_appears_in_the_composition(controller):
    project, shots = _project(controller)
    art = _art(controller, project.id)
    element = _element(
        controller,
        project.id,
        CreativeElementKind.CHARACTER,
        "character.duck",
        artifact_id=art["id"],
    )
    _slot(controller, project.id, shots[0]["id"], role="subject", key="s1.subject")
    controller.bind_element_to_shot(
        project.id,
        ShotElementBindingCreate(
            shot_id=shots[0]["id"], element_id=element["id"], role="character"
        ),
    )
    controller.bind_elements_to_slots(project.id)

    authored = controller.author_composition(project.id, width=540, height=960)
    assert authored["layers_embedded"] >= 1
    html = controller.read_artifact_text(authored["artifact"]["id"])
    assert "data:image/svg+xml;base64," in html


def test_location_element_targets_the_background_slot(controller):
    project, shots = _project(controller)
    art = _art(controller, project.id, "pond.svg")
    element = _element(
        controller,
        project.id,
        CreativeElementKind.LOCATION,
        "location.pond",
        artifact_id=art["id"],
    )
    _slot(controller, project.id, shots[0]["id"], role="background", key="s1.bg")
    controller.bind_element_to_shot(
        project.id,
        ShotElementBindingCreate(
            shot_id=shots[0]["id"], element_id=element["id"], role="world"
        ),
    )
    result = controller.bind_elements_to_slots(project.id)
    assert result["assigned_count"] == 1
    assert result["assigned"][0]["role"] == "background"


def test_element_without_artwork_is_skipped(controller):
    project, shots = _project(controller)
    element = _element(
        controller,
        project.id,
        CreativeElementKind.CHARACTER,
        "character.duck",
        artifact_id=None,
    )
    _slot(controller, project.id, shots[0]["id"], role="subject", key="s1.subject")
    controller.bind_element_to_shot(
        project.id,
        ShotElementBindingCreate(
            shot_id=shots[0]["id"], element_id=element["id"], role="character"
        ),
    )
    result = controller.bind_elements_to_slots(project.id)
    assert result["assigned_count"] == 0
    assert any("no artwork" in item["reason"] for item in result["skipped"])


def test_existing_primary_is_not_overwritten(controller):
    project, shots = _project(controller)
    first = _art(controller, project.id, "a.svg")
    second = _art(controller, project.id, "b.svg")
    element = _element(
        controller,
        project.id,
        CreativeElementKind.CHARACTER,
        "character.duck",
        artifact_id=first["id"],
    )
    slot = _slot(
        controller, project.id, shots[0]["id"], role="subject", key="s1.subject"
    )
    from pinaka.assets import AssetCriteria

    controller.assign_slot_asset(
        project.id,
        slot["id"],
        artifact_id=second["id"],
        role=SlotAssetRole.PRIMARY,
        criteria=AssetCriteria(),
        decided_by="reviewer",
    )
    controller.bind_element_to_shot(
        project.id,
        ShotElementBindingCreate(
            shot_id=shots[0]["id"], element_id=element["id"], role="character"
        ),
    )
    result = controller.bind_elements_to_slots(project.id)
    assert result["assigned_count"] == 0
    assert any("already has a primary" in item["reason"] for item in result["skipped"])
    assignment = controller.repository.slot_asset_by_role(
        slot["id"], SlotAssetRole.PRIMARY.value
    )
    assert assignment["artifact_id"] == second["id"]


def test_mismatched_role_is_ignored(controller):
    project, shots = _project(controller)
    art = _art(controller, project.id)
    element = _element(
        controller,
        project.id,
        CreativeElementKind.CHARACTER,
        "character.duck",
        artifact_id=art["id"],
    )
    _slot(controller, project.id, shots[0]["id"], role="background", key="s1.bg")
    controller.bind_element_to_shot(
        project.id,
        ShotElementBindingCreate(
            shot_id=shots[0]["id"], element_id=element["id"], role="character"
        ),
    )
    result = controller.bind_elements_to_slots(project.id)
    assert result["assigned_count"] == 0
    assert result["skipped"] == []


def test_binding_is_idempotent(controller):
    project, shots = _project(controller)
    art = _art(controller, project.id)
    element = _element(
        controller,
        project.id,
        CreativeElementKind.CHARACTER,
        "character.duck",
        artifact_id=art["id"],
    )
    _slot(controller, project.id, shots[0]["id"], role="subject", key="s1.subject")
    controller.bind_element_to_shot(
        project.id,
        ShotElementBindingCreate(
            shot_id=shots[0]["id"], element_id=element["id"], role="character"
        ),
    )
    first = controller.bind_elements_to_slots(project.id)
    second = controller.bind_elements_to_slots(project.id)
    assert first["assigned_count"] == 1
    assert second["assigned_count"] == 0
    assert len(controller.repository.list_slot_assets(project.id)) == 1

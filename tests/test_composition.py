from __future__ import annotations

from decimal import Decimal

import pytest

from pinaka.composition import (
    CompositionError,
    build_composition,
    validate_composition,
)
from pinaka.errors import IntegrityError
from pinaka.models import TextBriefRequest

from .conftest import create_project

SHOTS = [
    {
        "stable_key": "shot-01",
        "duration_seconds": "5",
        "beat": "establish",
        "narrative": "Three ducklings swim in a row",
    },
    {
        "stable_key": "shot-02",
        "duration_seconds": "5",
        "beat": "develop",
        "narrative": "They count together",
    },
]


def test_composition_is_valid_and_offline():
    html = build_composition(
        title="Counting rhyme",
        width=1080,
        height=1920,
        shots=SHOTS,
        elements=[
            {"stable_key": "character.duck", "kind": "character", "name": "Duck"}
        ],
    )
    report = validate_composition(html)
    assert report["valid"] is True
    assert report["shot_count"] == 2
    assert report["shots"] == ["shot-01", "shot-02"]
    assert "http://" not in html and "https://" not in html
    assert 'data-duration="10"' in html
    assert "character.duck" in html


def test_composition_rejects_external_resources():
    html = build_composition(title="x", width=10, height=10, shots=SHOTS).replace(
        "<style>", '<link rel="stylesheet" href="https://cdn.example/x.css"><style>'
    )
    report = validate_composition(html)
    assert report["valid"] is False
    assert any("external" in problem for problem in report["problems"])


def test_composition_detects_duplicate_shots():
    html = build_composition(title="x", width=10, height=10, shots=SHOTS).replace(
        'data-shot="shot-02"', 'data-shot="shot-01"'
    )
    report = validate_composition(html)
    assert report["valid"] is False
    assert any("duplicate" in problem for problem in report["problems"])


def test_composition_requires_shots():
    with pytest.raises(CompositionError):
        build_composition(title="x", width=10, height=10, shots=[])


def test_controller_authors_and_versions_a_composition(controller):
    project = create_project(controller, budget="10.00")
    controller.plan_from_text(
        project.id,
        TextBriefRequest(
            text='Make a 10 second rhyme for kids: "One, two, three little ducks."',
            persist_elements=True,
        ),
    )
    result = controller.author_composition(project.id, width=1080, height=1920)
    artifact = result["artifact"]
    assert artifact["kind"] == "design"
    assert artifact["relative_path"].startswith("scenes/composition-")
    assert artifact["relative_path"].endswith("/index.html")
    assert controller.verify_artifact(artifact["id"])
    assert result["validation"]["valid"] is True
    assert result["shot_count"] >= 1

    dependency_types = {item["entity_type"] for item in artifact["dependencies"]}
    assert {"workable_brief", "shot"} <= dependency_types
    assert "creative_element" in dependency_types

    repeated = controller.author_composition(project.id, width=1080, height=1920)
    assert repeated["artifact"]["id"] == artifact["id"]


def test_author_composition_requires_a_brief(controller):
    project = create_project(controller, budget="10.00")
    with pytest.raises(IntegrityError, match="workable brief"):
        controller.author_composition(project.id)


def test_composition_records_the_deliverable_duration(controller):
    project = create_project(controller, budget="10.00")
    controller.plan_from_text(
        project.id, TextBriefRequest(text="Make a 5 second rhyme for kids")
    )
    result = controller.author_composition(project.id)
    html = controller.read_artifact_text(result["artifact"]["id"])
    assert 'data-duration="5"' in html
    assert Decimal(result["shot_count"]) >= 1


def test_still_camera_move_and_no_baked_text():
    shots = [
        {
            **SHOTS[0],
            "requirements": {"camera": "push_in", "text": "none"},
        }
    ]
    html = build_composition(title="x", width=1080, height=1920, shots=shots)
    assert "@keyframes camera-1" in html
    assert 'class="narrative"' not in html
    assert 'class="beat"' not in html
    assert validate_composition(html)["valid"] is True


def test_baked_text_is_rejected_by_validation():
    html = build_composition(title="x", width=10, height=10, shots=SHOTS)
    tampered = html.replace(
        'class="layers"',
        'class="narrative">Baked caption</p><div class="layers"',
    )
    report = validate_composition(tampered)
    assert report["valid"] is False
    assert any("baked text" in problem for problem in report["problems"])


def test_primary_visual_layer_is_full_bleed():
    import base64

    html = build_composition(
        title="x",
        width=1080,
        height=1920,
        shots=SHOTS,
        layers={
            "shot-01": [
                {
                    "role": "primary visual",
                    "mime": "image/png",
                    "data": base64.b64encode(b"png").decode("ascii"),
                }
            ]
        },
    )
    assert 'class="layer background"' in html
    assert 'data-role="primary visual"' in html

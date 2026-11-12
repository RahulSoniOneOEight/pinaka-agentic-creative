from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

from pinaka import models
from pinaka.contracts.common import Money
from pinaka.controller import CreativeController
from pinaka.enums import ProjectStatus
from pinaka.errors import ConfigurationError, ConflictError, IntegrityError
from pinaka.models import TextBriefRequest
from pinaka.operations import describe_operations, operation
from pinaka.policy.guards import (
    require_active_project,
    require_funded_item,
    require_one_take,
)
from pinaka.repository import Repository

from .conftest import create_project


def test_facades_remain_small_and_methods_have_one_owner():
    for facade, package in [(CreativeController, "use_cases"), (Repository, "store")]:
        assert len(inspect.getsource(facade).splitlines()) < 150
        owners = {}
        for cls in facade.__mro__[1:-1]:
            assert f".{package}." in cls.__module__
            for name, value in cls.__dict__.items():
                if callable(value) or isinstance(value, (staticmethod, classmethod)):
                    assert name not in owners, (name, owners.get(name), cls.__name__)
                    owners[name] = cls.__name__


def test_legacy_model_import_is_the_same_class():
    assert models.Money is Money
    assert models.Money.from_minor(30, "usd").minor == 30
    assert models.QAStatus.PASS.value == "pass"
    assert (
        "use_cases/motion.py"
        in Path(inspect.getsourcefile(CreativeController.plan_shot_motion)).as_posix()
    )


@pytest.mark.parametrize("closed", [ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED])
def test_shared_guard_rejects_closed_projects(controller, closed):
    project = create_project(controller)
    with pytest.raises(ConflictError, match="closed"):
        require_active_project(
            project.model_copy(update={"status": closed}), message="closed"
        )


def test_shared_guards_fail_closed():
    with pytest.raises(IntegrityError, match="funded"):
        require_funded_item(None, "a")
    with pytest.raises(IntegrityError, match="one take"):
        require_one_take({"num_images": 2})
    with pytest.raises(IntegrityError, match="one take"):
        require_one_take({"auto_retry": True})


def test_operation_catalog_is_explicit_and_has_no_implicit_retries():
    entries = describe_operations()
    assert {e["id"] for e in entries} >= {
        "image.generate",
        "image.edit",
        "video.generate",
        "audio.mix",
    }
    assert all(e["auto_retry"] is False for e in entries)
    assert operation("video.generate").receipt == "studio_job"
    assert "funded_item" in operation("image.edit").preconditions
    for route, model in [
        ("google.veo", "veo-3"),
        ("fal.video", "veo-3"),
        ("runway", "minimax/h3"),
    ]:
        with pytest.raises(IntegrityError):
            operation("video.generate").validate(route, model=model)
    with pytest.raises(IntegrityError, match="Unknown"):
        operation("music.generate")


def test_vertex_paths_are_retired():
    assert importlib.util.find_spec("pinaka.vertex") is None
    assert importlib.util.find_spec("pinaka.adapters.google_vertex_image") is None
    assert not hasattr(CreativeController, "synthesize_narration_vertex")
    assert not hasattr(CreativeController, "enhance_still_with_vertex")


def test_script_proposal_is_zero_spend_and_not_silently_truncated(controller):
    project = create_project(controller)
    controller.plan_from_text(
        project.id, TextBriefRequest(text="Make a 10 second rhyme for kids")
    )
    result = controller.draft_script(
        project.id, script="Three little ducks swim in a row.", max_words=24
    )
    assert result["requires_approval"] is True
    assert result["artifact"]["operation"]["provider"] == "supplied-copy"
    assert controller.repository.get_project(project.id).spent.minor == 0
    with pytest.raises(IntegrityError, match="max_words"):
        controller.draft_script(
            project.id, script="one two three four five six seven", max_words=5
        )
    with pytest.raises(IntegrityError, match="duration"):
        controller.draft_script(
            project.id, script="one two three four five", duration_seconds=0
        )


def test_script_requires_copy_or_an_adapter_instead_of_an_implicit_paid_call(
    controller,
):
    project = create_project(controller)
    controller.plan_from_text(
        project.id, TextBriefRequest(text="Make a 10 second rhyme for kids")
    )
    with pytest.raises(ConfigurationError, match="Supply script"):
        controller.draft_script(project.id)

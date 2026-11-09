import pytest

from pinaka.enums import ArtifactKind
from pinaka.errors import IntegrityError, UnsafePathError

from .conftest import create_project


def test_original_import_is_content_addressed_and_idempotent(controller, tmp_path):
    project = create_project(controller)
    source = tmp_path / "product.png"
    source.write_bytes(b"approved-product-bytes")

    first = controller.import_original(project.id, source)
    second = controller.import_original(project.id, source)

    assert first["id"] == second["id"]
    assert first["immutable"] is True
    assert controller.verify_artifact(first["id"])
    assert len(controller.repository.list_artifacts(project.id)) == 1


def test_controller_cannot_overwrite_original_or_escape_project(controller):
    project = create_project(controller)

    with pytest.raises(IntegrityError):
        controller.write_artifact(
            project.id,
            kind=ArtifactKind.ORIGINAL,
            relative_path="assets/originals/forbidden.bin",
            content=b"x",
            operation={"type": "overwrite"},
        )

    with pytest.raises(UnsafePathError):
        controller.write_artifact(
            project.id,
            kind=ArtifactKind.DERIVATIVE,
            relative_path="../outside.bin",
            content=b"x",
            operation={"type": "escape"},
        )

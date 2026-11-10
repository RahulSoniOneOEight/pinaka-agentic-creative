from __future__ import annotations

from decimal import Decimal

import pytest

from pinaka.ai_edit import (
    ComfyUIEditAdapter,
    EditResult,
    build_img2img_workflow,
    build_inpaint_workflow,
    build_upscale_workflow,
)
from pinaka.assets import AssetCriteria
from pinaka.enums import ArtifactKind, TreatmentVerdict
from pinaka.errors import ApprovalRequiredError, ConfigurationError
from pinaka.models import ShotCreate, SlotCreate
from pinaka.utils import sha256_bytes, sha256_json

from .conftest import create_project


def test_img2img_workflow_has_expected_nodes():
    graph = build_img2img_workflow(
        image_name="source.png",
        checkpoint="model.safetensors",
        prompt="warmer light",
        negative_prompt="blurry",
        seed=7,
        steps=12,
        denoise=0.4,
    )

    assert graph["1"]["class_type"] == "CheckpointLoaderSimple"
    assert graph["6"]["inputs"]["seed"] == 7
    assert graph["6"]["inputs"]["denoise"] == 0.4
    assert graph["8"]["class_type"] == "SaveImage"


def test_inpaint_workflow_adds_mask_encoding():
    graph = build_inpaint_workflow(
        image_name="source.png",
        mask_name="mask.png",
        checkpoint="model.safetensors",
        prompt="remove the logo",
        negative_prompt="",
        seed=1,
        steps=10,
        denoise=0.6,
    )

    assert graph["3"]["class_type"] == "VAEEncodeForInpaint"
    assert graph["9"]["class_type"] == "LoadImageMask"
    assert graph["3"]["inputs"]["mask"] == ["9", 0]


def test_upscale_workflow_uses_an_upscale_model():
    graph = build_upscale_workflow(image_name="source.png", model_name="esrgan.pth")

    assert graph["2"]["class_type"] == "UpscaleModelLoader"
    assert graph["3"]["class_type"] == "ImageUpscaleWithModel"


def test_remote_endpoints_are_refused():
    with pytest.raises(ConfigurationError, match="loopback"):
        ComfyUIEditAdapter(
            base_url="https://example.com",
            allowed_checkpoints=("a",),
            allowed_upscale_models=(),
        )


def test_checkpoint_must_be_allowlisted():
    adapter = ComfyUIEditAdapter(
        allowed_checkpoints=("allowed.safetensors",),
        allowed_upscale_models=(),
        transport=lambda method, url, payload: b"{}",
    )

    with pytest.raises(ValueError, match="allowlisted"):
        adapter.edit(b"image", prompt="x", checkpoint="other.safetensors")


def test_upscale_requires_an_allowlisted_model():
    adapter = ComfyUIEditAdapter(
        allowed_checkpoints=("model.safetensors",),
        allowed_upscale_models=(),
        transport=lambda method, url, payload: b"{}",
    )

    with pytest.raises(ConfigurationError, match="upscale model"):
        adapter.upscale(b"image")


def _project_slot_artifact(controller):
    project = create_project(controller)
    shot = controller.add_shot(
        project.id,
        ShotCreate(
            stable_key="hero",
            ordinal=1,
            beat="Hero frame",
            duration_seconds=Decimal(2),
            narrative="Hero frame",
        ),
    )
    slot = controller.add_slot(
        project.id,
        SlotCreate(
            shot_id=shot["id"],
            stable_key="hero-image",
            role="hero image",
            identity_requirement="no recognizable brand",
            fact_requirement="shows the product",
            transformation_permission="crop, scale and retouch",
        ),
    )
    artifact = controller.write_artifact(
        project.id,
        kind=ArtifactKind.PREVIEW,
        relative_path="previews/hero.png",
        content=b"source-bytes",
        operation={"type": "test_source"},
    )
    return project, slot, artifact


class _FakeEditAdapter:
    def __init__(self, content: bytes, kind: str):
        self.content = content
        self.kind = kind

    def edit(self, source, **kwargs):
        return self._result()

    def upscale(self, source, **kwargs):
        return self._result()

    def _result(self):
        return EditResult(
            content=self.content,
            content_hash=sha256_bytes(self.content),
            workflow_hash="d" * 64,
            operation={"type": self.kind, "workflow_hash": "d" * 64},
        )


def _record_correct_decision(controller, project, slot, artifact):
    controller.repository.register_treatment_decision(
        project.id,
        {
            "slot_id": slot["id"],
            "artifact_id": artifact["id"],
            "verdict": TreatmentVerdict.CORRECT.value,
            "criteria": AssetCriteria().model_dump(mode="json"),
            "rights": None,
            "reasons": ["correction permitted"],
        },
        decision_hash=sha256_json({"slot": slot["id"]}),
        artifact_id=artifact["id"],
        decided_by="reviewer@example.test",
        created_at="2026-11-11T00:00:00.000Z",
    )


def test_ai_edit_requires_a_recorded_decision(controller):
    project, slot, artifact = _project_slot_artifact(controller)

    with pytest.raises(ApprovalRequiredError, match="correct"):
        controller.ai_edit_asset(
            project.id,
            slot_id=slot["id"],
            artifact_id=artifact["id"],
            prompt="warmer light",
            adapter=_FakeEditAdapter(b"edited", "ai_edit"),
        )


def test_ai_edit_writes_a_derivative_with_provenance(controller):
    project, slot, artifact = _project_slot_artifact(controller)
    _record_correct_decision(controller, project, slot, artifact)

    result = controller.ai_edit_asset(
        project.id,
        slot_id=slot["id"],
        artifact_id=artifact["id"],
        prompt="warmer light",
        adapter=_FakeEditAdapter(b"edited", "ai_edit"),
    )

    derivative = result["artifact"]
    assert derivative["kind"] == ArtifactKind.DERIVATIVE.value
    assert derivative["parent_artifact_id"] == artifact["id"]
    assert derivative["operation"]["type"] == "ai_edit"


def test_upscale_writes_a_derivative_with_provenance(controller):
    project, slot, artifact = _project_slot_artifact(controller)
    _record_correct_decision(controller, project, slot, artifact)

    result = controller.generative_upscale_asset(
        project.id,
        slot_id=slot["id"],
        artifact_id=artifact["id"],
        adapter=_FakeEditAdapter(b"upscaled", "generative_upscale"),
    )

    assert result["artifact"]["kind"] == ArtifactKind.DERIVATIVE.value
    assert result["operation"] == "generative_upscale"

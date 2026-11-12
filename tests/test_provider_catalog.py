from __future__ import annotations

from decimal import Decimal

import pytest

from pinaka import pricing
from pinaka.adapters.base import PollResult, Submission
from pinaka.adapters.fal_image import FalImageAdapter
from pinaka.enums import ArtifactKind, BudgetCategory, JobStatus
from pinaka.errors import IntegrityError
from pinaka.fal import FalClient
from pinaka.models import BudgetPlanCreate, BudgetPlanItemCreate, JobRequest, Money

from .conftest import create_project


class FakeFal:
    provider_name = "fal"
    calls = 0

    def submit(self, request):
        self.calls += 1
        return Submission(provider_job_id="fake_fal_1", raw={"simulated": True})

    def poll(self, provider_job_id, request):
        return PollResult(
            status=JobStatus.SUCCEEDED,
            raw={"simulated": True},
            output_bytes=b"edited-image",
            actual_cost=Money.from_minor(2, "USD"),
        )


def setup_edit(controller):
    project = create_project(controller, budget="1.00")
    source = controller.write_artifact(
        project.id,
        kind=ArtifactKind.DERIVATIVE,
        relative_path="artifacts/source.png",
        content=b"source",
        operation={"type": "working_copy"},
    )
    plan = controller.plan_creative_budget(
        project.id,
        BudgetPlanCreate(
            creative_summary="Internal edit",
            items=(
                BudgetPlanItemCreate(
                    stable_key="edit",
                    ordinal=1,
                    category=BudgetCategory.ASSET_GENERATION,
                    provider="fal",
                    operation="image.edit",
                    estimated_cost=Money.from_minor(3, "USD"),
                ),
            ),
        ),
    )["plan"]
    return project, source, plan


def test_funded_fal_edit_preserves_source_and_settles_once(controller):
    project, source, plan = setup_edit(controller)
    adapter = FakeFal()
    kwargs = {
        "source_artifact_id": source["id"],
        "prompt": "Keep the duck, simplify the background",
        "model": "fal-ai/flux-2/flash/edit",
        "plan_id": plan["id"],
        "item_key": "edit",
        "adapter": adapter,
    }
    result = controller.enhance_still(project.id, **kwargs)
    artifact = result["artifact"]
    assert artifact["parent_artifact_id"] == source["id"]
    assert artifact["kind"] == "derivative"
    assert controller.verify_artifact(source["id"])
    assert controller.verify_artifact(artifact["id"])
    assert controller.repository.get_project(project.id).spent.minor == 2
    assert controller.asset_costs(project.id)["count"] == 1
    assert any(d["entity_id"] == source["id"] for d in artifact["dependencies"])
    repeated = controller.enhance_still(project.id, **kwargs)
    assert repeated["resumed"] is True
    assert repeated["artifact"]["id"] == artifact["id"]
    assert adapter.calls == 1


def test_edit_cannot_change_the_request_after_spending(controller):
    project, source, plan = setup_edit(controller)
    args = {
        "source_artifact_id": source["id"],
        "prompt": "first",
        "model": "fal-ai/flux-2/flash/edit",
        "plan_id": plan["id"],
        "item_key": "edit",
        "adapter": FakeFal(),
    }
    controller.enhance_still(project.id, **args)
    with pytest.raises(IntegrityError, match="different request"):
        controller.enhance_still(project.id, **{**args, "prompt": "second"})


@pytest.mark.parametrize(
    "op, message",
    [("image.generate", "funded plan"), ("unregistered.image", "declared catalog")],
)
def test_fal_requires_a_funded_item_before_calling_the_adapter(controller, op, message):
    project = create_project(controller)
    adapter = FakeFal()
    request = JobRequest(
        provider="fal",
        endpoint="fal-ai/flux/schnell",
        operation=op,
        idempotency_key="unfunded",
        estimated_cost=Money.from_minor(1, "USD"),
    )
    with pytest.raises(IntegrityError, match=message):
        controller.run_adapter_once(
            project.id, request, adapter, artifact_path="artifacts/no.png"
        )
    assert adapter.calls == 0


def test_fal_adapter_rejects_batches_and_wrong_currency_before_network():
    adapter = FalImageAdapter(FalClient())
    for currency, parameters in [("USD", {"num_images": 2}), ("INR", {})]:
        request = JobRequest(
            provider="fal",
            endpoint="fal-ai/flux/schnell",
            operation="image.generate",
            idempotency_key="bad",
            estimated_cost=Money.from_minor(1, currency),
            parameters=parameters,
        )
        with pytest.raises(IntegrityError):
            adapter.submit(request)


def test_fal_pricing_preview():
    assert pricing.estimate_fal_image_minor("fal-ai/flux/schnell") == 1
    assert pricing.estimate_fal_image_minor("fal-ai/sana") == 1
    assert (
        pricing.estimate_fal_video_minor(
            "fal-ai/ltx-2-19b/distilled/image-to-video", seconds=5
        )
        == 9
    )
    preview = pricing.fal_budget_preview(images=100, clips=10)
    assert preview["estimated_total_minor"] == 190
    with pytest.raises(ValueError, match="Unknown fal image endpoint"):
        pricing.estimate_fal_image_minor("nope/model")
    with pytest.raises(ValueError, match="Unsupported resolution"):
        pricing.estimate_fal_video_minor(
            "minimax/h3-max-turbo/image-to-video", seconds=5, resolution="2160p"
        )


def test_budget_preview_fits_the_one_dollar_rule():
    preview = pricing.fal_budget_preview(images=20, clips=8, clip_seconds=5)
    assert preview["estimated_total_minor"] <= 100
    assert Decimal(preview["estimated_total_minor"]) / 100 <= Decimal("1.00")

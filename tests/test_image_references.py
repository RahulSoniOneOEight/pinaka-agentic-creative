from __future__ import annotations

from decimal import Decimal

from pinaka.adapters.fal_image import FalImageAdapter
from pinaka.models import JobRequest, Money


class FakeClient:
    def __init__(self) -> None:
        self.payload: dict | None = None

    def run(self, model: str, payload: dict) -> dict:
        self.payload = payload
        return {"images": [{"url": "https://cdn.example/out.png"}]}

    def download(self, url: str) -> bytes:
        return b"generated"


def request(**parameters) -> JobRequest:
    return JobRequest(
        provider="fal",
        endpoint="fal-ai/flux/edit",
        operation="image.edit",
        idempotency_key="refs-1",
        estimated_cost=Money(amount=Decimal("0.04"), currency="USD"),
        parameters={
            "prompt": "same character, new pose",
            "num_images": 1,
            **parameters,
        },
    )


def test_source_and_references_become_ordered_image_urls():
    client = FakeClient()
    adapter = FalImageAdapter(
        client,
        model="fal-ai/flux/edit",
        source_image=b"source",
        source_mime="image/png",
        reference_images=(b"character", b"product"),
        reference_mimes=("image/jpeg", "image/png"),
    )

    adapter.submit(request())

    urls = client.payload["image_urls"]
    assert len(urls) == 3
    assert urls[0].startswith("data:image/png;base64,")
    assert urls[1].startswith("data:image/jpeg;base64,")
    assert urls[2].startswith("data:image/png;base64,")


def test_references_are_optional():
    client = FakeClient()
    adapter = FalImageAdapter(client, model="fal-ai/flux/edit")

    adapter.submit(request())

    assert "image_urls" not in client.payload

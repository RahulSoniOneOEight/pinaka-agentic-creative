from __future__ import annotations

import pytest

from pinaka.contracts.openmontage import (
    CapabilityDiscoveryCreate,
    CapabilityEntry,
)
from pinaka.errors import NotFoundError

from .conftest import create_project


def _capability() -> CapabilityEntry:
    return CapabilityEntry(
        capability_id="cinematic_shots",
        name="Cinematic shots",
        category="make",
        planning_use="10-second generated shot",
        production_method="image_to_video",
        cost_hint="a few dollars per shot",
        account_requirement="Studio credits or provider keys",
        fallback="owned still + local compositing",
    )


def test_capability_route_falls_back_without_studio_account(controller):
    project = create_project(controller)
    controller.record_capability_discovery(
        project.id,
        CapabilityDiscoveryCreate(
            source="manual snapshot",
            account_ready=False,
            credits=0,
            capabilities=(_capability(),),
        ),
    )
    route = controller.route_capability(project.id, "cinematic_shots")
    assert route.ready is False
    assert route.action == "fallback"
    assert "not ready" in route.reason
    assert route.fallback == "owned still + local compositing"


def test_capability_route_uses_studio_when_ready(controller):
    project = create_project(controller)
    controller.record_capability_discovery(
        project.id,
        CapabilityDiscoveryCreate(
            source="manual snapshot",
            account_ready=True,
            credits=10,
            capabilities=(_capability(),),
        ),
    )
    route = controller.require_capability_ready(project.id, "cinematic_shots")
    assert route.ready is True
    assert route.action == "studio"


def test_unknown_capability_is_not_found(controller):
    project = create_project(controller)
    controller.record_capability_discovery(
        project.id,
        CapabilityDiscoveryCreate(
            source="manual snapshot",
            capabilities=(_capability(),),
        ),
    )
    with pytest.raises(NotFoundError):
        controller.route_capability(project.id, "not-a-capability")

from __future__ import annotations

from decimal import Decimal

import pytest

from pinaka.controller import CreativeController
from pinaka.enums import ApprovalStage, ContentMode, ProjectStatus
from pinaka.models import ApprovalRequest, FactSetCreate, Money, ProjectCreate


@pytest.fixture
def controller(tmp_path):
    return CreativeController.open(tmp_path / "runtime")


def create_project(
    controller: CreativeController,
    *,
    name: str = "Pilot Product",
    budget: str = "10.00",
):
    return controller.create_project(
        ProjectCreate(
            name=name,
            objective="Create one governed product-video pilot",
            mode=ContentMode.PRODUCT,
            audience="Pilot reviewers",
            output_format="9:16",
            language="en",
            duration_seconds=Decimal(25),
            max_budget=Money(amount=Decimal(budget), currency="USD"),
        )
    )


def advance_to_sourcing(controller: CreativeController, *, name: str = "Pilot Product"):
    project = create_project(controller, name=name)
    controller.transition_project(
        project.id, ProjectStatus.PLANNED, reason="Plan ready"
    )
    controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.BRIEF,
            approved_by="reviewer@example.test",
            scope={"script": "v1", "budget": "10 USD"},
        ),
    )
    fact_set = controller.add_fact_set(
        project.id,
        FactSetCreate(
            source_ref="approved-fact-sheet-v1",
            values={"sku": "SKU-001", "price": "9.99", "currency": "USD"},
            evidence_refs=("test://facts/v1",),
        ),
    )
    controller.transition_project(
        project.id, ProjectStatus.SOURCING, reason="Begin sourcing"
    )
    sourcing_approval = controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.SOURCING,
            approved_by="reviewer@example.test",
            scope={"selected_sources": ["own-library"], "still_allowance": "2 USD"},
        ),
    )
    return project, fact_set, sourcing_approval

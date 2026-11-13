from __future__ import annotations

from decimal import Decimal

from pinaka.models import TextBriefRequest

from .conftest import create_project


def test_generate_production_plan_from_the_shot_plan(controller):
    project = create_project(controller)
    controller.plan_from_text(
        project.id,
        TextBriefRequest(
            text="Five little ducks swim on the pond and count one to five.",
            duration_seconds=Decimal(10),
            persist_slots=True,
            persist_elements=True,
        ),
    )
    generated = controller.generate_production_plan(project.id)
    plan = generated["plan"]
    assert plan["schema"] == "production-plan/v1"
    assert plan["production_format"] == "product_showcase"
    assert plan["delivery_format"] == "vertical_reel"
    assert len(plan["shot_directions"]) == len(
        controller.repository.list_shots(project.id)
    )
    assert plan["rough_cut"]["duration_seconds"] > 0
    status = controller.production_plan_status(project.id)
    assert status["ready"] is True
    assert status["issues"] == []
    assert status["artifact"]["operation"]["type"] == "production_plan"

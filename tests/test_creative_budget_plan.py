from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

from pinaka.enums import (
    BudgetCategory,
    BudgetItemStatus,
    BudgetPlanStatus,
    ContentMode,
    JobStatus,
    ProjectStatus,
)
from pinaka.errors import (
    ApprovalRequiredError,
    BudgetExceededError,
    ConflictError,
    IntegrityError,
)
from pinaka.models import (
    BudgetPlanApprovalCreate,
    BudgetPlanCreate,
    BudgetPlanItemCreate,
    JobRequest,
    Money,
    ProjectCreate,
)
from pinaka.schema import MIGRATIONS, SCHEMA_VERSION

from .conftest import create_project


def _amount(payload: dict) -> Decimal:
    return Decimal(str(payload["amount"]))


def _item(key, ordinal, cost, *, provider="fal", operation="image_generate"):
    return BudgetPlanItemCreate(
        stable_key=key,
        ordinal=ordinal,
        category=BudgetCategory.ASSET_GENERATION,
        provider=provider,
        operation=operation,
        estimated_cost=Money(amount=Decimal(cost), currency="USD"),
    )


def _within_plan():
    return BudgetPlanCreate(
        creative_summary="Five-second counting rhyme",
        items=(
            _item("hero-image", 1, "0.40"),
            _item(
                "narration",
                2,
                "0.30",
                provider="elevenlabs",
                operation="speech",
            ),
        ),
    )


def _over_plan():
    return BudgetPlanCreate(
        creative_summary="Premium three-shot rhyme",
        items=(
            _item("hero-image", 1, "0.80"),
            _item("second-image", 2, "0.80"),
        ),
    )


def _job(key="hero-run", cost="0.40"):
    return JobRequest(
        provider="fal",
        endpoint="https://fal.run/image",
        operation="image_generate",
        idempotency_key=key,
        estimated_cost=Money(amount=Decimal(cost), currency="USD"),
    )


def test_plan_within_one_dollar_is_authorized_immediately(controller):
    project = create_project(controller, budget="10.00")
    result = controller.plan_creative_budget(project.id, _within_plan())
    assert result["within_target"] is True
    assert result["requires_approval"] is False
    assert result["plan"]["status"] == BudgetPlanStatus.WITHIN_TARGET.value
    assert _amount(result["planned_total"]) == Decimal("0.70")
    assert _amount(result["overage"]) == Decimal(0)
    assert result["summary"]["items"]["total"] == 2
    assert _amount(result["summary"]["execution"]["settled_actual"]) == Decimal(0)

    reserved = controller.reserve_budget_item(
        project.id, result["plan"]["id"], "hero-image", _job()
    )
    assert reserved["item"]["status"] == BudgetItemStatus.RESERVED.value
    assert reserved["item"]["job_id"] == reserved["job"]["id"]
    assert controller.repository.get_project(project.id).reserved.minor == 40


def test_plan_above_one_dollar_requires_approval_before_execution(controller):
    project = create_project(controller, budget="10.00")
    result = controller.plan_creative_budget(project.id, _over_plan())
    assert result["requires_approval"] is True
    assert result["plan"]["status"] == BudgetPlanStatus.APPROVAL_REQUIRED.value
    assert _amount(result["overage"]) == Decimal("0.60")

    with pytest.raises(ApprovalRequiredError, match="requires human approval"):
        controller.reserve_budget_item(
            project.id, result["plan"]["id"], "hero-image", _job(cost="0.80")
        )

    approved = controller.approve_budget_plan(
        project.id,
        BudgetPlanApprovalCreate(
            plan_id=result["plan"]["id"],
            approved_by="producer",
            reason="Two generated frames are required for this creative",
        ),
    )
    assert approved["plan"]["status"] == BudgetPlanStatus.APPROVED.value
    assert _amount(approved["approval"]["approved_total"]) == Decimal("1.60")

    reserved = controller.reserve_budget_item(
        project.id, result["plan"]["id"], "hero-image", _job(cost="0.80")
    )
    assert reserved["item"]["status"] == BudgetItemStatus.RESERVED.value


def test_budget_item_rejects_estimates_that_differ_from_the_plan(controller):
    project = create_project(controller, budget="10.00")
    plan = controller.plan_creative_budget(project.id, _within_plan())["plan"]
    with pytest.raises(IntegrityError, match="must match the approved budget plan"):
        controller.reserve_budget_item(
            project.id, plan["id"], "hero-image", _job(cost="0.50")
        )
    with pytest.raises(IntegrityError, match="provider and operation"):
        controller.reserve_budget_item(
            project.id,
            plan["id"],
            "hero-image",
            _job().model_copy(update={"operation": "video_generate"}),
        )


def test_slight_overrun_is_tolerated_and_reported(controller):
    project = create_project(controller, budget="10.00")
    plan = controller.plan_creative_budget(project.id, _within_plan())["plan"]
    reserved = controller.reserve_budget_item(
        project.id, plan["id"], "hero-image", _job()
    )
    job_id = reserved["job"]["id"]
    settled = controller.settle_job(
        job_id,
        target=JobStatus.SUCCEEDED,
        actual_cost=Money(amount=Decimal("0.44"), currency="USD"),
    )
    assert settled.actual_cost.amount == Decimal("0.44")

    item = next(
        entry
        for entry in controller.budget_plan(project.id)["items"]
        if entry["stable_key"] == "hero-image"
    )
    assert item["status"] == BudgetItemStatus.SETTLED.value
    assert item["actual_minor"] == 44
    assert item["variance_minor"] == 4

    summary = controller.budget_summary(project.id)
    assert _amount(summary["execution"]["settled_actual"]) == Decimal("0.44")
    assert _amount(summary["execution"]["variance"]) == Decimal("0.04")
    assert summary["execution"]["within_tolerance"] is True
    assert summary["requires_further_approval"] is False
    assert summary["items"]["outstanding_required"] == 1
    assert controller.repository.get_project(project.id).spent.minor == 44


def test_material_overrun_requires_further_approval(controller):
    project = create_project(controller, budget="10.00")
    plan = controller.plan_creative_budget(project.id, _within_plan())["plan"]
    reserved = controller.reserve_budget_item(
        project.id, plan["id"], "hero-image", _job()
    )
    with pytest.raises(BudgetExceededError, match="variance tolerance"):
        controller.settle_job(
            reserved["job"]["id"],
            target=JobStatus.SUCCEEDED,
            actual_cost=Money(amount=Decimal("0.60"), currency="USD"),
        )
    job = controller.repository.get_job(reserved["job"]["id"])
    assert job.status == JobStatus.BUDGET_RESERVED
    assert controller.repository.get_project(project.id).reserved.minor == 40


def test_overrun_cannot_exceed_the_project_budget(controller):
    project = create_project(controller, budget="0.45")
    plan = controller.plan_creative_budget(project.id, _within_plan())["plan"]
    reserved = controller.reserve_budget_item(
        project.id, plan["id"], "hero-image", _job()
    )
    with pytest.raises(BudgetExceededError, match="project budget"):
        controller.settle_job(
            reserved["job"]["id"],
            target=JobStatus.SUCCEEDED,
            actual_cost=Money(amount=Decimal("0.49"), currency="USD"),
        )


def test_budget_plan_versions_and_archived_projects(controller):
    project = create_project(controller, budget="10.00")
    first = controller.plan_creative_budget(project.id, _within_plan())["plan"]
    second = controller.plan_creative_budget(project.id, _over_plan())["plan"]
    assert second["version"] == 2
    assert not controller.repository.get_budget_plan(first["id"])["is_current"]
    with pytest.raises(ConflictError, match="superseded"):
        controller.reserve_budget_item(project.id, first["id"], "hero-image", _job())
    with pytest.raises(ConflictError, match="superseded"):
        controller.approve_budget_plan(
            project.id,
            BudgetPlanApprovalCreate(
                plan_id=first["id"], approved_by="producer", reason="stale plan"
            ),
        )


def test_budget_plan_requires_usd_project_currency(controller):
    project = controller.create_project(
        ProjectCreate(
            name="Rupee creative",
            objective="Create one reel",
            mode=ContentMode.PRODUCT,
            audience="Reviewers",
            output_format="9:16",
            language="en",
            duration_seconds=Decimal(10),
            max_budget=Money(amount=Decimal("500.00"), currency="INR"),
        )
    )
    with pytest.raises(IntegrityError, match="denominated in USD"):
        controller.plan_creative_budget(project.id, _within_plan())


def test_budget_plan_migration_adds_tables(tmp_path):
    path = tmp_path / "v10.sqlite"
    with sqlite3.connect(path) as connection:
        for version in range(1, 11):
            connection.executescript(MIGRATIONS[version])
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, f"2026-11-11T00:00:{version:02d}.000Z"),
            )
        connection.execute(
            """
            INSERT INTO projects(
                id, slug, name, objective, mode, audience, output_format, language,
                duration_seconds, status, max_budget_minor, currency, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "prj_v10",
                "v10",
                "V10 project",
                "Preserve this project",
                "product",
                "Reviewers",
                "9:16",
                "en",
                "10",
                "draft",
                1000,
                "USD",
                "2026-11-11T00:00:00.000Z",
                "2026-11-11T00:00:00.000Z",
            ),
        )
        connection.execute("PRAGMA user_version = 10")

    from pinaka.database import Database

    Database(path).initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"budget_plans", "budget_plan_items", "budget_plan_approvals"} <= tables
        assert (
            connection.execute(
                "SELECT status FROM projects WHERE id = 'prj_v10'"
            ).fetchone()[0]
            == ProjectStatus.DRAFT.value
        )

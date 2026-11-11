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
    VarianceDecisionCreate,
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
            actual_cost=Money(amount=Decimal("0.70"), currency="USD"),
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


def test_budget_plan_supports_inr_projects(controller):
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
    plan_request = BudgetPlanCreate(
        creative_summary="Rupee creative",
        items=(
            BudgetPlanItemCreate(
                stable_key="hero-image",
                ordinal=1,
                category=BudgetCategory.ASSET_GENERATION,
                provider="fal",
                operation="image_generate",
                estimated_cost=Money(amount=Decimal("40.00"), currency="INR"),
            ),
        ),
    )
    result = controller.plan_creative_budget(project.id, plan_request)
    assert result["plan"]["currency"] == "INR"
    assert _amount(result["target"]) == Decimal("85.00")
    assert result["within_target"] is True

    with pytest.raises(IntegrityError, match="must match the project budget currency"):
        controller.plan_creative_budget(
            project.id,
            BudgetPlanCreate(
                creative_summary="Wrong currency",
                items=(
                    BudgetPlanItemCreate(
                        stable_key="hero-image",
                        ordinal=1,
                        category=BudgetCategory.ASSET_GENERATION,
                        provider="fal",
                        operation="image_generate",
                        estimated_cost=Money(amount=Decimal("0.40"), currency="USD"),
                    ),
                ),
            ),
        )


def test_overrun_creates_a_pending_approval_request(controller):
    project = create_project(controller, budget="10.00")
    plan = controller.plan_creative_budget(project.id, _within_plan())["plan"]
    reserved = controller.reserve_budget_item(
        project.id, plan["id"], "hero-image", _job()
    )
    job_id = reserved["job"]["id"]

    result = controller.settle_budget_item(
        project.id,
        job_id,
        actual_cost=Money(amount=Decimal("0.70"), currency="USD"),
        requested_by="planner",
        reason="Provider charged for a higher resolution",
    )
    assert result["status"] == "approval_required"
    request = result["request"]
    assert request["status"] == "pending"
    assert request["estimated_minor"] == 40
    assert request["actual_minor"] == 70
    assert request["allowed_minor"] == 60
    assert request["overage_minor"] == 10

    assert controller.repository.get_project(project.id).reserved.minor == 40
    assert controller.repository.get_project(project.id).spent.minor == 0
    assert controller.repository.get_job(job_id).status == JobStatus.BUDGET_RESERVED

    summary = controller.budget_summary(project.id)
    assert summary["requires_further_approval"] is True
    assert len(summary["pending_variances"]) == 1

    repeated = controller.settle_budget_item(
        project.id, job_id, actual_cost=Money(amount=Decimal("0.70"), currency="USD")
    )
    assert repeated["request"]["id"] == request["id"]
    assert len(controller.variance_requests(project.id)) == 1


def test_approved_variance_settles_at_the_actual_cost(controller):
    project = create_project(controller, budget="10.00")
    plan = controller.plan_creative_budget(project.id, _within_plan())["plan"]
    reserved = controller.reserve_budget_item(
        project.id, plan["id"], "hero-image", _job()
    )
    pending = controller.settle_budget_item(
        project.id,
        reserved["job"]["id"],
        actual_cost=Money(amount=Decimal("0.70"), currency="USD"),
    )["request"]

    approved = controller.decide_variance(
        project.id,
        VarianceDecisionCreate(
            request_id=pending["id"],
            decision="approve",
            decided_by="producer",
            reason="Higher resolution was worth the extra spend",
        ),
    )
    assert approved["decision"]["status"] == "approved"
    assert approved["job"]["status"] == JobStatus.SUCCEEDED.value
    assert _amount(approved["job"]["actual_cost"]) == Decimal("0.70")

    settled_project = controller.repository.get_project(project.id)
    assert settled_project.reserved.minor == 0
    assert settled_project.spent.minor == 70
    item = next(
        entry
        for entry in controller.budget_plan(project.id)["items"]
        if entry["stable_key"] == "hero-image"
    )
    assert item["status"] == BudgetItemStatus.SETTLED.value
    assert item["variance_minor"] == 30

    summary = controller.budget_summary(project.id)
    assert summary["pending_variances"] == []
    assert summary["execution"]["variance_minor"] == 30

    with pytest.raises(ConflictError, match="already approved"):
        controller.decide_variance(
            project.id,
            VarianceDecisionCreate(
                request_id=pending["id"],
                decision="approve",
                decided_by="producer",
                reason="Duplicate",
            ),
        )


def test_rejected_variance_releases_the_reservation_without_a_charge(controller):
    project = create_project(controller, budget="10.00")
    plan = controller.plan_creative_budget(project.id, _within_plan())["plan"]
    reserved = controller.reserve_budget_item(
        project.id, plan["id"], "hero-image", _job()
    )
    pending = controller.settle_budget_item(
        project.id,
        reserved["job"]["id"],
        actual_cost=Money(amount=Decimal("0.70"), currency="USD"),
    )["request"]

    rejected = controller.decide_variance(
        project.id,
        VarianceDecisionCreate(
            request_id=pending["id"],
            decision="reject",
            decided_by="producer",
            reason="Over budget for this creative",
        ),
    )
    assert rejected["decision"]["status"] == "rejected"
    assert rejected["job"]["status"] == JobStatus.CANCELLED.value
    assert _amount(rejected["reported_actual"]) == Decimal("0.70")

    settled_project = controller.repository.get_project(project.id)
    assert settled_project.reserved.minor == 0
    assert settled_project.spent.minor == 0
    item = next(
        entry
        for entry in controller.budget_plan(project.id)["items"]
        if entry["stable_key"] == "hero-image"
    )
    assert item["status"] == BudgetItemStatus.SKIPPED.value
    assert controller.budget_summary(project.id)["requires_further_approval"] is False


def test_approving_a_variance_cannot_exceed_the_project_budget(controller):
    project = create_project(controller, budget="0.45")
    plan = controller.plan_creative_budget(project.id, _within_plan())["plan"]
    reserved = controller.reserve_budget_item(
        project.id, plan["id"], "hero-image", _job()
    )
    pending = controller.settle_budget_item(
        project.id,
        reserved["job"]["id"],
        actual_cost=Money(amount=Decimal("0.70"), currency="USD"),
    )["request"]

    with pytest.raises(BudgetExceededError, match="project budget"):
        controller.decide_variance(
            project.id,
            VarianceDecisionCreate(
                request_id=pending["id"],
                decision="approve",
                decided_by="producer",
                reason="Approve anyway",
            ),
        )
    assert (
        controller.repository.get_variance_request(pending["id"])["status"] == "pending"
    )
    assert controller.repository.get_project(project.id).reserved.minor == 40


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


def test_schema_v11_migrates_budget_tables_to_multi_currency(tmp_path):
    path = tmp_path / "v11.sqlite"
    with sqlite3.connect(path) as connection:
        for version in range(1, 12):
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
                "prj_v11",
                "v11",
                "V11 project",
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
        connection.execute(
            """
            INSERT INTO budget_plans(
                id, project_id, version, creative_summary, target_minor,
                planned_total_minor, currency, tolerance_bps, tolerance_floor_minor,
                status, content_hash, is_current, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "budgetplan_legacy",
                "prj_v11",
                1,
                "Legacy plan",
                100,
                80,
                "USD",
                1000,
                10,
                "within_target",
                "a" * 64,
                1,
                "2026-11-11T00:00:00.000Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO budget_plan_items(
                id, plan_id, project_id, stable_key, ordinal, category, provider,
                operation, estimated_minor, priority, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "budgetitem_legacy",
                "budgetplan_legacy",
                "prj_v11",
                "hero-image",
                1,
                "asset_generation",
                "fal",
                "image_generate",
                80,
                "required",
                "planned",
                "2026-11-11T00:00:00.000Z",
                "2026-11-11T00:00:00.000Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO budget_plan_approvals(
                id, project_id, plan_id, plan_hash, approved_total_minor,
                currency, approved_by, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "budgetapproval_legacy",
                "prj_v11",
                "budgetplan_legacy",
                "a" * 64,
                80,
                "USD",
                "producer",
                "Legacy approval",
                "2026-11-11T00:00:00.000Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO studio_spend_approvals(
                id, project_id, scope_hash, max_cost_minor, currency, approved_by,
                reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "spendapproval_legacy",
                "prj_v11",
                "b" * 64,
                250,
                "USD",
                "producer",
                "Legacy studio approval",
                "2026-11-11T00:00:00.000Z",
            ),
        )
        connection.execute("PRAGMA user_version = 11")

    from pinaka.database import Database

    Database(path).initialize()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert (
            connection.execute(
                "SELECT creative_summary FROM budget_plans WHERE id = ?",
                ("budgetplan_legacy",),
            ).fetchone()[0]
            == "Legacy plan"
        )
        assert (
            connection.execute(
                "SELECT estimated_minor FROM budget_plan_items WHERE id = ?",
                ("budgetitem_legacy",),
            ).fetchone()[0]
            == 80
        )
        assert (
            connection.execute(
                "SELECT approved_by FROM budget_plan_approvals WHERE id = ?",
                ("budgetapproval_legacy",),
            ).fetchone()[0]
            == "producer"
        )
        assert (
            connection.execute(
                "SELECT max_cost_minor FROM studio_spend_approvals WHERE id = ?",
                ("spendapproval_legacy",),
            ).fetchone()[0]
            == 250
        )
        connection.execute(
            """
            INSERT INTO budget_plans(
                id, project_id, version, creative_summary, target_minor,
                planned_total_minor, currency, tolerance_bps, tolerance_floor_minor,
                status, content_hash, is_current, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "budgetplan_inr",
                "prj_v11",
                2,
                "INR plan",
                8500,
                4000,
                "INR",
                5000,
                10,
                "within_target",
                "c" * 64,
                0,
                "2026-11-11T00:00:00.000Z",
            ),
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

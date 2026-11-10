from __future__ import annotations

from pinaka.preflight import run_preflight

from .conftest import create_project


def test_preflight_fresh_project_reports_governance_blocks(controller):
    project = create_project(controller)

    report = run_preflight(controller, project.id)

    by_id = {rule["rule_id"]: rule for rule in report["rules"]}
    assert report["overall"] == "fail"
    assert by_id["R-BUDGET-001"]["status"] == "pass"
    assert by_id["R-GROUNDING-001"]["status"] == "fail"
    assert by_id["R-DURATION-001"]["status"] == "fail"
    assert by_id["R-ARTIFACT-001"]["status"] == "pass"
    assert by_id["R-RECOVERY-001"]["status"] == "pass"
    assert by_id["R-PUBLISH-001"]["status"] == "pass"


def test_preflight_budget_rule_rejects_overcommitment(controller):
    project = create_project(controller, budget="0.00")

    report = run_preflight(controller, project.id)

    by_id = {rule["rule_id"]: rule for rule in report["rules"]}
    assert by_id["R-BUDGET-001"]["status"] == "pass"
    assert report["project_status"] == "draft"

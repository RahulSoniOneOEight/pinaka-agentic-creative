from __future__ import annotations

from decimal import Decimal

import pytest

from pinaka.enums import (
    CapabilityStatus,
    ProjectStatus,
    StudioWorkPhase,
    ToolIntegrationMode,
)
from pinaka.errors import ApprovalRequiredError
from pinaka.models import (
    StudioScope,
    StudioWorkPacketRequest,
    TextBriefRequest,
    ToolProfileCreate,
    VarianceDecisionCreate,
)
from pinaka.studio_gateway import DeterministicStudioTransport, SpoolStudioTransport

from .conftest import create_project, record_studio_ready


def _prepared_run(controller, tmp_path, *, key="run-v1"):
    record_studio_ready(controller)
    project = create_project(controller, budget="10.00")
    controller.plan_from_text(
        project.id, TextBriefRequest(text="Make a 5 second rhyme for kids")
    )
    profile = controller.register_tool_profile(
        ToolProfileCreate(
            stable_key="openmontage-deterministic",
            name="Studio gateway fixture",
            integration_mode=ToolIntegrationMode.SPECIALIST_HANDOFF,
            executable_or_endpoint="controller:studio-gateway",
            supported_operations=("openmontage.execute_work_packet",),
            capability_status=CapabilityStatus.WORKING,
        )
    )
    prepared = controller.prepare_studio_work_packet(
        project.id,
        StudioWorkPacketRequest(
            tool_profile_id=profile["id"],
            idempotency_key=key,
            instructions="Produce the storyboard.",
            scope=StudioScope(phases=(StudioWorkPhase.STORYBOARD,)),
            output_declaration={"roles": ["deterministic_result"]},
        ),
    )
    transport = DeterministicStudioTransport(workdir=tmp_path / "studio")
    return project, prepared, transport


def test_gateway_runs_a_production_to_completion(controller, tmp_path):
    project, prepared, transport = _prepared_run(controller, tmp_path)
    transport.cost_minor = 25

    result = controller.run_studio_production(
        project.id, prepared["operation"]["id"], transport
    )
    assert result["status"] == "completed"
    production = result["production"]
    assert production["status"] == "completed"
    assert production["external_production_id"] == "fake-prod-1"
    assert production["budget_minor"] == 100
    assert production["currency"] == "USD"

    jobs = controller.repository.studio_jobs(production["id"])
    assert len(jobs) == 1
    assert jobs[0]["status"] == "completed"

    assert result["receipt"]["status"] == "succeeded"
    assert result["receipt"]["actual_cost_minor"] == 25
    assert result["artifacts"]

    settled = controller.repository.get_project(project.id)
    assert settled.reserved.minor == 0
    assert settled.spent.minor == 25
    assert any(
        event["event_type"] == "studio_production.opened"
        for event in controller.repository.events(project.id)
    )


def test_gateway_resumes_without_resubmitting(controller, tmp_path):
    project, prepared, transport = _prepared_run(controller, tmp_path)
    first = controller.run_studio_production(
        project.id, prepared["operation"]["id"], transport
    )
    assert first["status"] == "completed"
    second = controller.run_studio_production(
        project.id, prepared["operation"]["id"], transport
    )
    assert second["status"] == "settled"
    assert transport.submit_calls == 1
    assert transport.open_calls == 1


def test_gateway_waits_for_a_host_response(controller, tmp_path):
    project, prepared, _ = _prepared_run(controller, tmp_path)
    spool = SpoolStudioTransport(tmp_path / "spool")
    result = controller.run_studio_production(
        project.id, prepared["operation"]["id"], spool
    )
    assert result["status"] == "pending"
    assert result["step"] == "open"
    assert result["production"] is None
    assert list((tmp_path / "spool").glob("*.open.request.json"))
    assert controller.repository.get_project(project.id).reserved.minor == 100


def test_gateway_requires_fresh_readiness(controller, tmp_path):
    project, prepared, transport = _prepared_run(controller, tmp_path)
    with controller.repository.database.transaction() as connection:
        connection.execute("DELETE FROM capability_checks")
    with pytest.raises(ApprovalRequiredError, match="readiness"):
        controller.run_studio_production(
            project.id, prepared["operation"]["id"], transport
        )


def test_gateway_overrun_asks_for_approval_then_resumes(controller, tmp_path):
    project, prepared, transport = _prepared_run(controller, tmp_path)
    transport.cost_minor = 150

    first = controller.run_studio_production(
        project.id, prepared["operation"]["id"], transport
    )
    assert first["status"] == "approval_required"
    request = first["request"]
    assert request["kind"] == "studio_run"
    assert request["actual_minor"] == 150
    assert request["allowed_minor"] == 100
    assert first["production"]["status"] == "awaiting_approval"
    assert controller.repository.get_project(project.id).spent.minor == 0

    controller.decide_variance(
        project.id,
        VarianceDecisionCreate(
            request_id=request["id"],
            decision="approve",
            decided_by="producer",
            reason="Accepted the overrun",
        ),
    )
    second = controller.run_studio_production(
        project.id, prepared["operation"]["id"], transport
    )
    assert second["status"] == "completed"
    assert second["receipt"]["actual_cost_minor"] == 150
    settled = controller.repository.get_project(project.id)
    assert settled.spent.minor == 150
    assert settled.reserved.minor == 0
    assert transport.submit_calls == 1


def test_gateway_failure_settles_at_zero(controller, tmp_path):
    project, prepared, transport = _prepared_run(controller, tmp_path)
    transport.outcome = "failed"
    result = controller.run_studio_production(
        project.id, prepared["operation"]["id"], transport
    )
    assert result["status"] == "failed"
    assert result["receipt"]["status"] == "failed"
    assert result["receipt"]["actual_cost_minor"] == 0
    settled = controller.repository.get_project(project.id)
    assert settled.reserved.minor == 0
    assert settled.spent.minor == 0
    assert result["production"]["status"] == "failed"


def test_gateway_polls_until_terminal(controller, tmp_path):
    project, prepared, transport = _prepared_run(controller, tmp_path)
    transport.polls_before_terminal = 2
    pending = controller.run_studio_production(
        project.id, prepared["operation"]["id"], transport, max_polls=1
    )
    assert pending["status"] == "pending"
    assert pending["step"] == "poll"
    completed = controller.run_studio_production(
        project.id, prepared["operation"]["id"], transport, max_polls=3
    )
    assert completed["status"] == "completed"
    assert transport.submit_calls == 1


def test_spool_transport_completes_a_full_round_trip(controller, tmp_path):
    import json

    project, prepared, _ = _prepared_run(controller, tmp_path)
    spool = tmp_path / "spool"
    transport = SpoolStudioTransport(spool)

    def respond(step, payload):
        requests = sorted(spool.glob(f"*.{step}.request.json"))
        assert requests, f"no request written for {step}"
        request = requests[-1]
        request.with_name(
            request.name.replace(".request.json", ".response.json")
        ).write_text(json.dumps(payload), encoding="utf-8")

    first = controller.run_studio_production(
        project.id, prepared["operation"]["id"], transport
    )
    assert first["step"] == "open"
    respond("open", {"production_id": "spool-prod-1", "status": "opened"})

    second = controller.run_studio_production(
        project.id, prepared["operation"]["id"], transport
    )
    assert second["step"] == "submit"
    respond("submit", {"job_id": "spool-job-1", "status": "accepted"})

    third = controller.run_studio_production(
        project.id, prepared["operation"]["id"], transport
    )
    assert third["step"] == "poll-spool-job-1"
    respond("poll-spool-job-1", {"status": "completed"})

    fourth = controller.run_studio_production(
        project.id, prepared["operation"]["id"], transport
    )
    assert fourth["step"] == "collect-spool-job-1"
    output = tmp_path / "spool-result.json"
    output.write_bytes(b'{"storyboard":"ready"}')
    from pinaka.utils import sha256_bytes

    respond(
        "collect-spool-job-1",
        {
            "outputs": [
                {
                    "source_path": str(output),
                    "relative_path": "artifacts/openmontage/results/spool.json",
                    "kind": "derivative",
                    "role": "deterministic_result",
                    "content_hash": sha256_bytes(output.read_bytes()),
                }
            ],
            "actual_cost_minor": 25,
        },
    )

    final = controller.run_studio_production(
        project.id, prepared["operation"]["id"], transport
    )
    assert final["status"] == "completed"
    assert final["production"]["status"] == "completed"
    assert final["receipt"]["actual_cost_minor"] == 25
    settled = controller.repository.get_project(project.id)
    assert settled.spent.minor == 25
    assert settled.reserved.minor == 0
    assert final["artifacts"][0]["operation"]["type"] == "openmontage_result"


def test_gateway_refuses_a_production_without_a_budget_job(controller, tmp_path):
    record_studio_ready(controller)
    project = create_project(controller, budget="10.00")
    controller.plan_from_text(
        project.id, TextBriefRequest(text="Make a 5 second rhyme for kids")
    )
    profile = controller.register_tool_profile(
        ToolProfileCreate(
            stable_key="openmontage-deterministic",
            name="Studio gateway fixture",
            integration_mode=ToolIntegrationMode.SPECIALIST_HANDOFF,
            executable_or_endpoint="controller:studio-gateway",
            supported_operations=("openmontage.execute_work_packet",),
        )
    )
    prepared = controller.prepare_studio_work_packet(
        project.id,
        StudioWorkPacketRequest(
            tool_profile_id=profile["id"],
            idempotency_key="zero-cost-run",
            instructions="Storyboard without spend.",
            scope=StudioScope(
                phases=(StudioWorkPhase.STORYBOARD,),
                max_amount=Decimal(0),
                max_currency="USD",
                allow_paid_generation=False,
            ),
            output_declaration={"roles": ["deterministic_result"]},
        ),
    )
    transport = DeterministicStudioTransport(workdir=tmp_path / "studio")
    with pytest.raises(Exception, match="reserved budget job"):
        controller.run_studio_production(
            project.id, prepared["operation"]["id"], transport
        )
    assert controller.repository.get_project(project.id).status == ProjectStatus.DRAFT
    assert transport.open_calls == 0

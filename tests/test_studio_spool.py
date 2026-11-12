from __future__ import annotations

import json
import os
import time

import pytest

from pinaka.errors import ConfigurationError, IntegrityError
from pinaka.studio_gateway import (
    SpoolStudioTransport,
    StudioOpenRequest,
    StudioPending,
    StudioStale,
    StudioSubmission,
)


def open_request(packet_hash: str = "a" * 64) -> StudioOpenRequest:
    return StudioOpenRequest(
        project_id="project-1",
        title="Review",
        intent="one still",
        autonomy="autopilot",
        max_cost_minor=100,
        currency="USD",
        packet_hash=packet_hash,
    )


def test_open_response_without_a_production_id_is_refused(tmp_path):
    transport = SpoolStudioTransport(tmp_path)
    with pytest.raises(StudioPending):
        transport.open_production(open_request())
    response = tmp_path / f"{'a' * 16}.open.response.json"
    response.write_text(json.dumps({"status": "opened"}), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="production_id"):
        transport.open_production(open_request())


def test_unknown_remote_status_is_refused(tmp_path):
    transport = SpoolStudioTransport(tmp_path)
    with pytest.raises(StudioPending):
        transport.open_production(open_request())
    response = tmp_path / f"{'a' * 16}.open.response.json"
    response.write_text(
        json.dumps({"production_id": "p-1", "status": "banana"}), encoding="utf-8"
    )

    with pytest.raises(ConfigurationError, match="unknown status"):
        transport.open_production(open_request())


def test_collect_rejects_a_bad_hash(tmp_path):
    transport = SpoolStudioTransport(tmp_path)
    production = {"packet_hash": "b" * 64, "external_production_id": "prod-1"}
    submission = StudioSubmission(external_job_id="job-1", status="completed")
    with pytest.raises(StudioPending):
        transport.collect(production, submission)
    response = tmp_path / f"{'b' * 16}.collect-job-1.response.json"
    response.write_text(
        json.dumps(
            {
                "outputs": [
                    {
                        "source_path": "result.json",
                        "relative_path": "artifacts/openmontage/results/r.json",
                        "role": "storyboard_manifest",
                        "content_hash": "deadbeef",
                    }
                ],
                "actual_cost_minor": 3,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(IntegrityError, match="sha256"):
        transport.collect(production, submission)


def test_collect_rejects_a_negative_cost(tmp_path):
    transport = SpoolStudioTransport(tmp_path)
    production = {"packet_hash": "b" * 64, "external_production_id": "prod-1"}
    submission = StudioSubmission(external_job_id="job-1", status="completed")
    with pytest.raises(StudioPending):
        transport.collect(production, submission)
    response = tmp_path / f"{'b' * 16}.collect-job-1.response.json"
    response.write_text(
        json.dumps(
            {
                "outputs": [],
                "actual_cost_minor": -5,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="actual_cost_minor"):
        transport.collect(production, submission)


def test_collect_rejects_a_path_outside_artifacts(tmp_path):
    transport = SpoolStudioTransport(tmp_path)
    production = {"packet_hash": "b" * 64, "external_production_id": "prod-1"}
    submission = StudioSubmission(external_job_id="job-1", status="completed")
    with pytest.raises(StudioPending):
        transport.collect(production, submission)
    response = tmp_path / f"{'b' * 16}.collect-job-1.response.json"
    response.write_text(
        json.dumps(
            {
                "outputs": [
                    {
                        "source_path": "result.json",
                        "relative_path": "../escape.json",
                        "role": "storyboard_manifest",
                        "content_hash": "0" * 64,
                    }
                ],
                "actual_cost_minor": 3,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(IntegrityError, match="artifacts/"):
        transport.collect(production, submission)


def test_poll_requires_a_status(tmp_path):
    transport = SpoolStudioTransport(tmp_path)
    production = {"packet_hash": "c" * 64, "external_production_id": "prod-1"}
    submission = StudioSubmission(external_job_id="job-2", status="accepted")
    with pytest.raises(StudioPending):
        transport.poll(production, submission)
    response = tmp_path / f"{'c' * 16}.poll-job-2.response.json"
    response.write_text("{}", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="status"):
        transport.poll(production, submission)


def test_stale_request_is_reported(tmp_path):
    transport = SpoolStudioTransport(tmp_path, stale_after_seconds=60)
    with pytest.raises(StudioPending):
        transport.open_production(open_request())
    request = next(tmp_path.glob("*.open.request.json"))
    old = time.time() - 3600
    os.utime(request, (old, old))

    with pytest.raises(StudioStale) as error:
        transport.open_production(open_request())

    assert error.value.step == "open"
    assert "void" in str(error.value)


def test_existing_request_is_never_overwritten(tmp_path):
    transport = SpoolStudioTransport(tmp_path)
    with pytest.raises(StudioPending):
        transport.open_production(open_request())
    request = next(tmp_path.glob("*.open.request.json"))
    original = request.read_text(encoding="utf-8")
    request.write_text('{"sentinel": true}', encoding="utf-8")

    with pytest.raises(StudioPending):
        transport.open_production(open_request())

    assert request.read_text(encoding="utf-8") == '{"sentinel": true}'
    assert original != request.read_text(encoding="utf-8")

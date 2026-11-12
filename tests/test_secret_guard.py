from __future__ import annotations

from decimal import Decimal

import pytest

from pinaka.errors import IntegrityError
from pinaka.models import JobRequest, Money
from pinaka.policy.secrets import reject_sensitive_payload
from tests.conftest import create_project

FAL_KEY = "fal_sk_" + "0123456789abcdef" * 2


@pytest.mark.parametrize(
    "field",
    [
        "api_key",
        "fal_key",
        "token",
        "access_token",
        "private_key",
        "bearer",
        "authorization",
        "password",
        "client_secret",
        "credential",
        "Cookies",
    ],
)
def test_named_credential_fields_are_refused(field):
    with pytest.raises(IntegrityError):
        reject_sensitive_payload({field: "placeholder"})


def test_innocent_field_names_are_not_refused():
    reject_sensitive_payload(
        {
            "item_key": "still-1",
            "stable_key": "shot-1",
            "max_tokens": 512,
            "fraud_key": "campaign-7",
            "token_count": 12,
        }
    )


@pytest.mark.parametrize(
    "value",
    [
        FAL_KEY,
        "sk-proj-" + "a" * 24,
        "AKIA" + "A" * 16,
        "ghp_" + "a" * 24,
        (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        ),
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_credential_values_are_refused_under_innocent_names(value):
    with pytest.raises(IntegrityError):
        reject_sensitive_payload({"prompt": f"please use {value} for this run"})


def test_error_names_the_location_without_repeating_the_value():
    with pytest.raises(IntegrityError) as error:
        reject_sensitive_payload({"prompt": FAL_KEY}, "job.parameters")

    message = str(error.value)
    assert FAL_KEY not in message
    assert "job.parameters.prompt" in message


def test_nested_payloads_are_scanned():
    with pytest.raises(IntegrityError):
        reject_sensitive_payload(
            {"items": [{"nested": {"api_key": "placeholder"}}]}, "plan"
        )
    with pytest.raises(IntegrityError):
        reject_sensitive_payload({"items": ["token " + FAL_KEY]}, "plan")


def test_controller_refuses_a_credential_in_job_parameters(controller):
    project = create_project(controller)
    request = JobRequest(
        provider="mock",
        endpoint="local/test",
        operation="generate_still",
        idempotency_key="secret-guard-1",
        estimated_cost=Money(amount=Decimal("0.10"), currency="USD"),
        parameters={"prompt": "still", "fal_key": FAL_KEY},
    )

    with pytest.raises(IntegrityError):
        controller.reserve_job(project.id, request)

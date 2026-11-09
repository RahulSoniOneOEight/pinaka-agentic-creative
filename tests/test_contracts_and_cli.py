import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from pinaka.cli import main
from pinaka.enums import AgentStatus
from pinaka.models import AgentResult, Money


def test_agent_output_contract_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        AgentResult(
            status=AgentStatus.COMPLETED,
            next_action="Human review",
            secret_side_effect=True,
        )


def test_money_uses_exact_minor_units():
    assert Money(amount=Decimal("12.34"), currency="inr").minor == 1234


def test_cli_demo_smoke(tmp_path, capsys):
    exit_code = main(
        [
            "--home",
            str(tmp_path / "cli-runtime"),
            "demo",
            "--name",
            "CLI Foundation Demo",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert captured.err == ""
    assert payload["message"] == "Demo completed without external or paid calls"
    assert payload["job"]["status"] == "succeeded"

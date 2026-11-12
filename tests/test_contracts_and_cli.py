import json
from decimal import Decimal
from pathlib import Path

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


def test_cli_pilot_contract_new_plan_and_next(tmp_path, capsys):
    contract = (
        Path(__file__).parents[1] / "config" / "pilots" / "pinaka-virtual-cfo-10s.json"
    )
    home = tmp_path / "pilot-runtime"

    assert (
        main(["--home", str(home), "pilot-validate", "--contract", str(contract)]) == 0
    )
    validated = json.loads(capsys.readouterr().out)
    assert validated["status"] == "valid"
    assert validated["provider_mode"] == "disabled"

    assert main(["--home", str(home), "pilot-new", "--contract", str(contract)]) == 0
    created = json.loads(capsys.readouterr().out)
    project_id = created["project"]["id"]
    assert created["next_action"]["action"] == "plan"

    assert (
        main(
            [
                "--home",
                str(home),
                "pilot-plan",
                project_id,
                "--contract",
                str(contract),
            ]
        )
        == 0
    )
    planned = json.loads(capsys.readouterr().out)
    assert planned["project"]["status"] == "planned"
    assert len(planned["segments"]) == 4

    assert main(["--home", str(home), "next", project_id]) == 0
    next_action = json.loads(capsys.readouterr().out)
    assert next_action["action"] == "approve_brief"


def test_cli_json_output_is_ascii_safe():
    from pinaka.cli import _json

    text = _json({"text": "\ufeffcafé"})

    assert "\ufeff" not in text
    assert json.loads(text)["text"] == "\ufeffcafé"

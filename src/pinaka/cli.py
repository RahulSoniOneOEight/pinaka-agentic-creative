from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .adapters.mock import MockGenerationAdapter
from .controller import CreativeController
from .enums import ApprovalStage, ContentMode, ProjectStatus
from .errors import PinakaError
from .models import (
    ApprovalRequest,
    FactSetCreate,
    JobRequest,
    Money,
    ProjectCreate,
)


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _default_home() -> Path:
    configured = os.environ.get("PINAKA_HOME")
    return Path(configured) if configured else Path.cwd() / "workspace"


def _controller(args: argparse.Namespace) -> CreativeController:
    return CreativeController.open(args.home)


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_init(args: argparse.Namespace) -> dict[str, Any]:
    controller = _controller(args)
    return {
        "status": "initialized",
        "home": str(Path(args.home).expanduser().resolve()),
        "database": str(controller.database.path),
    }


def cmd_new_project(args: argparse.Namespace) -> dict[str, Any]:
    controller = _controller(args)
    project = controller.create_project(
        ProjectCreate(
            name=args.name,
            objective=args.objective,
            mode=ContentMode(args.mode),
            audience=args.audience,
            output_format=args.output_format,
            language=args.language,
            duration_seconds=Decimal(args.duration),
            max_budget=Money(amount=Decimal(args.budget), currency=args.currency),
        )
    )
    return project.model_dump(mode="json")


def cmd_list_projects(args: argparse.Namespace) -> list[dict[str, Any]]:
    controller = _controller(args)
    return [
        item.model_dump(mode="json") for item in controller.repository.list_projects()
    ]


def cmd_transition(args: argparse.Namespace) -> dict[str, Any]:
    controller = _controller(args)
    project = controller.transition_project(
        args.project_id,
        ProjectStatus(args.target),
        reason=args.reason,
    )
    return project.model_dump(mode="json")


def cmd_add_facts(args: argparse.Namespace) -> dict[str, Any]:
    controller = _controller(args)
    values = _load_json(args.values)
    if not isinstance(values, dict):
        raise TypeError("Fact values file must contain a JSON object")
    return controller.add_fact_set(
        args.project_id,
        FactSetCreate(
            source_ref=args.source,
            effective_from=args.effective_from,
            values=values,
            evidence_refs=tuple(args.evidence),
        ),
    )


def cmd_approve(args: argparse.Namespace) -> dict[str, Any]:
    controller = _controller(args)
    scope = json.loads(args.scope)
    if not isinstance(scope, dict):
        raise TypeError("Approval scope must be a JSON object")
    return controller.approve(
        args.project_id,
        ApprovalRequest(
            stage=ApprovalStage(args.stage),
            approved_by=args.approved_by,
            scope=scope,
            output_hash=args.output_hash,
        ),
    )


def cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).status_snapshot(args.project_id)


def cmd_capabilities(args: argparse.Namespace) -> list[dict[str, Any]]:
    return _controller(args).check_capabilities()


def cmd_events(args: argparse.Namespace) -> list[dict[str, Any]]:
    return _controller(args).repository.events(args.project_id)


def cmd_demo(args: argparse.Namespace) -> dict[str, Any]:
    """Run a paid-call-free vertical slice through the controller."""
    controller = _controller(args)
    project = controller.create_project(
        ProjectCreate(
            name=args.name,
            objective="Demonstrate controller policy without external provider calls",
            mode=ContentMode.PRODUCT,
            audience="Internal pilot reviewers",
            output_format="9:16",
            language="en",
            duration_seconds=Decimal(25),
            max_budget=Money(amount=Decimal("10.00"), currency="USD"),
        )
    )
    controller.transition_project(
        project.id, ProjectStatus.PLANNED, reason="Demo plan recorded"
    )
    controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.BRIEF,
            approved_by="demo-human",
            scope={"demo": True, "paid_calls": False},
        ),
    )
    controller.add_fact_set(
        project.id,
        FactSetCreate(
            source_ref="demo-approved-fact-sheet-v1",
            values={"sku": "DEMO-001", "price": "9.99", "currency": "USD"},
            evidence_refs=("demo://fact-sheet-v1",),
        ),
    )
    controller.transition_project(
        project.id, ProjectStatus.SOURCING, reason="Demo facts locked"
    )
    controller.approve(
        project.id,
        ApprovalRequest(
            stage=ApprovalStage.SOURCING,
            approved_by="demo-human",
            scope={"provider": "mock", "still_generation_allowance": "0.50 USD"},
        ),
    )
    job, artifact = controller.run_adapter_once(
        project.id,
        JobRequest(
            provider="mock",
            endpoint="local/deterministic-v1",
            operation="generate_still",
            idempotency_key="demo-still-001",
            estimated_cost=Money(amount=Decimal("0.50"), currency="USD"),
            parameters={"prompt": "A deterministic non-image test payload"},
            required_approval_stage=ApprovalStage.SOURCING,
        ),
        MockGenerationAdapter(),
        artifact_path="assets/generated/demo-still-001.json",
    )
    return {
        "message": "Demo completed without external or paid calls",
        "project_id": project.id,
        "job": job.model_dump(mode="json"),
        "artifact": artifact,
        "snapshot": controller.status_snapshot(project.id),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pinaka",
        description="Controller-first agentic creative production foundation",
    )
    parser.add_argument(
        "--home",
        default=str(_default_home()),
        help="Runtime home for SQLite and project artifacts (default: ./workspace)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init", help="Initialize the runtime database and folders"
    )
    init_parser.set_defaults(handler=cmd_init)

    new_parser = subparsers.add_parser(
        "new-project", help="Create a governed creative project"
    )
    new_parser.add_argument("--name", required=True)
    new_parser.add_argument("--objective", required=True)
    new_parser.add_argument("--audience", required=True)
    new_parser.add_argument(
        "--mode", choices=[item.value for item in ContentMode], default="product"
    )
    new_parser.add_argument("--output-format", default="9:16")
    new_parser.add_argument("--language", default="en")
    new_parser.add_argument("--duration", default="25")
    new_parser.add_argument("--budget", required=True)
    new_parser.add_argument("--currency", required=True)
    new_parser.set_defaults(handler=cmd_new_project)

    list_parser = subparsers.add_parser("list-projects", help="List projects")
    list_parser.set_defaults(handler=cmd_list_projects)

    transition_parser = subparsers.add_parser(
        "transition", help="Apply a valid project transition"
    )
    transition_parser.add_argument("project_id")
    transition_parser.add_argument(
        "target", choices=[item.value for item in ProjectStatus]
    )
    transition_parser.add_argument("--reason", required=True)
    transition_parser.set_defaults(handler=cmd_transition)

    facts_parser = subparsers.add_parser("add-facts", help="Add a versioned fact set")
    facts_parser.add_argument("project_id")
    facts_parser.add_argument("--source", required=True)
    facts_parser.add_argument("--values", required=True, help="UTF-8 JSON object file")
    facts_parser.add_argument("--effective-from")
    facts_parser.add_argument("--evidence", action="append", default=[])
    facts_parser.set_defaults(handler=cmd_add_facts)

    approve_parser = subparsers.add_parser("approve", help="Record a human approval")
    approve_parser.add_argument("project_id")
    approve_parser.add_argument("stage", choices=[item.value for item in ApprovalStage])
    approve_parser.add_argument("--approved-by", required=True)
    approve_parser.add_argument("--scope", default="{}", help="JSON object")
    approve_parser.add_argument("--output-hash")
    approve_parser.set_defaults(handler=cmd_approve)

    status_parser = subparsers.add_parser(
        "status", help="Show project state, costs, and recovery work"
    )
    status_parser.add_argument("project_id")
    status_parser.set_defaults(handler=cmd_status)

    capability_parser = subparsers.add_parser(
        "capabilities", help="Probe local tools without exposing credentials"
    )
    capability_parser.set_defaults(handler=cmd_capabilities)

    event_parser = subparsers.add_parser(
        "events", help="Show the append-only audit event stream"
    )
    event_parser.add_argument("--project-id")
    event_parser.set_defaults(handler=cmd_events)

    demo_parser = subparsers.add_parser(
        "demo", help="Run a deterministic no-network vertical slice"
    )
    demo_parser.add_argument("--name", default="Foundation Demo")
    demo_parser.set_defaults(handler=cmd_demo)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.handler(args)
    except (
        PinakaError,
        ValidationError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        print(
            _json({"error": type(error).__name__, "message": str(error)}),
            file=sys.stderr,
        )
        return 2
    print(_json(result))
    return 0

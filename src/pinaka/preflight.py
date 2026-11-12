from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from .controller import CreativeController
from .enums import ApprovalStage, ProjectStatus
from .errors import IntegrityError
from .policy.audio import require_audio_rights
from .reconciliation import reconcile_project


def _rule(
    rule_id: str,
    status: str,
    detail: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "status": status,
        "detail": detail,
        "evidence": evidence or {},
    }


def _overall(rules: list[dict[str, Any]]) -> str:
    statuses = {rule["status"] for rule in rules}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def run_preflight(controller: CreativeController, project_id: str) -> dict[str, Any]:
    """Read-only production preflight.

    This is the deterministic governance rule framework. The creative CR-01..CR-15 catalog and
    slot policies remain a separately versioned rule set; these rules cover authority, budget,
    grounding, duration, approvals, artifacts, QA, captions, providers and recovery.
    """
    project = controller.repository.get_project(project_id)
    approvals = {
        stage.value: controller.repository.active_approval(project_id, stage.value)
        for stage in ApprovalStage
    }
    bundles = {
        bundle["kind"]
        for bundle in controller.repository.current_grounding_bundles(project_id)
    }
    deliverables = controller.repository.list_deliverables(project_id)
    segments = controller.repository.list_segments(project_id)
    artifacts = controller.repository.list_artifacts(project_id)
    qa_results = controller.repository.list_qa_results(project_id)
    reconciliation = reconcile_project(controller, project_id)
    rules: list[dict[str, Any]] = []

    budget_ok = project.spent.minor + project.reserved.minor <= project.max_budget.minor
    rules.append(
        _rule(
            "R-BUDGET-001",
            "pass" if budget_ok else "fail",
            "Spend plus reservations are within the approved budget."
            if budget_ok
            else "Spend plus reservations exceed the approved budget.",
            {
                "max_budget_minor": project.max_budget.minor,
                "reserved_minor": project.reserved.minor,
                "spent_minor": project.spent.minor,
                "currency": project.max_budget.currency,
            },
        )
    )

    required_grounding = {"brand", "claims", "sources"}
    missing_grounding = sorted(required_grounding - bundles)
    rules.append(
        _rule(
            "R-GROUNDING-001",
            "pass" if not missing_grounding else "fail",
            "Brand, claims and source grounding are current."
            if not missing_grounding
            else "Required grounding is missing.",
            {"grounding_kinds": sorted(bundles), "missing": missing_grounding},
        )
    )

    duration_ok = False
    duration_evidence: dict[str, Any] = {
        "deliverables": len(deliverables),
        "segments": len(segments),
    }
    if len(deliverables) == 1 and deliverables[0].duration_seconds is not None:
        total_decimal = sum(
            (Decimal(str(segment["duration_seconds"])) for segment in segments),
            start=Decimal(0),
        )
        duration_ok = total_decimal == deliverables[0].duration_seconds
        duration_evidence.update(
            {
                "deliverable_seconds": str(deliverables[0].duration_seconds),
                "segment_total_seconds": str(total_decimal),
            }
        )
    rules.append(
        _rule(
            "R-DURATION-001",
            "pass" if duration_ok else "fail",
            "Segment durations equal the deliverable duration."
            if duration_ok
            else "Segment durations do not match one deliverable.",
            duration_evidence,
        )
    )

    required_stages: list[str] = []
    if project.status not in {ProjectStatus.DRAFT, ProjectStatus.PLANNED}:
        required_stages.append(ApprovalStage.BRIEF.value)
    if project.status in {
        ProjectStatus.SOURCING,
        ProjectStatus.ANIMATIC_READY,
        ProjectStatus.ANIMATIC_APPROVED,
        ProjectStatus.FINAL_CANDIDATE,
        ProjectStatus.FINAL_APPROVED,
        ProjectStatus.ARCHIVED,
    }:
        required_stages.append(ApprovalStage.SOURCING.value)
    if project.status in {
        ProjectStatus.ANIMATIC_APPROVED,
        ProjectStatus.FINAL_CANDIDATE,
        ProjectStatus.FINAL_APPROVED,
        ProjectStatus.ARCHIVED,
    }:
        required_stages.append(ApprovalStage.ANIMATIC.value)
    if project.status in {ProjectStatus.FINAL_APPROVED, ProjectStatus.ARCHIVED}:
        required_stages.append(ApprovalStage.FINAL.value)
    missing_approvals = [stage for stage in required_stages if not approvals.get(stage)]
    rules.append(
        _rule(
            "R-APPROVAL-001",
            "pass" if not missing_approvals else "fail",
            "All approvals required for the current state are active."
            if not missing_approvals
            else "Required approvals are missing.",
            {
                "required": required_stages,
                "missing": missing_approvals,
                "active": [stage for stage, value in approvals.items() if value],
            },
        )
    )

    artifact_issues = bool(
        reconciliation["missing_artifacts"]
        or reconciliation["hash_mismatches"]
        or reconciliation["orphaned_files"]
    )
    rules.append(
        _rule(
            "R-ARTIFACT-001",
            "pass" if not artifact_issues else "fail",
            "Artifact registry, files and hashes reconcile."
            if not artifact_issues
            else "Artifact registry, files or hashes do not reconcile.",
            reconciliation["counts"],
        )
    )

    slots = controller.repository.list_slots(project_id)
    coverage = controller.slot_coverage(project_id)
    if not slots:
        slots_status = "pass"
        slots_detail = "No asset slots are declared for this project."
    elif coverage["coverage"] == "complete":
        slots_status = "pass"
        slots_detail = "Every declared slot has an approved treatment."
    else:
        slots_status = (
            "fail"
            if project.status
            in {
                ProjectStatus.FINAL_CANDIDATE,
                ProjectStatus.FINAL_APPROVED,
                ProjectStatus.ARCHIVED,
            }
            else "warn"
        )
        slots_detail = "Some declared slots have no approved treatment."
    rules.append(
        _rule(
            "R-SLOTS-001",
            slots_status,
            slots_detail,
            {
                "total": coverage["total"],
                "resolved": coverage["resolved"],
                "unresolved": [item["stable_key"] for item in coverage["unresolved"]],
            },
        )
    )

    recovery_issues = bool(
        reconciliation["recoverable_jobs"] or reconciliation["unresolved_operations"]
    )
    rules.append(
        _rule(
            "R-RECOVERY-001",
            "pass" if not recovery_issues else "fail",
            "No recoverable jobs or unresolved operations."
            if not recovery_issues
            else "Recoverable jobs or unresolved operations require attention.",
            {
                "recoverable_jobs": len(reconciliation["recoverable_jobs"]),
                "unresolved_operations": len(reconciliation["unresolved_operations"]),
            },
        )
    )

    final_approval = approvals.get(ApprovalStage.FINAL.value)
    if final_approval and final_approval.get("output_hash"):
        matching = [
            artifact
            for artifact in artifacts
            if artifact["content_hash"] == final_approval["output_hash"]
        ]
        final_ok = bool(matching)
        rules.append(
            _rule(
                "R-FINAL-001",
                "pass" if final_ok else "fail",
                "The final approval hash matches a registered artifact."
                if final_ok
                else "The final approval hash has no registered artifact.",
                {
                    "output_hash": final_approval["output_hash"],
                    "matching_artifacts": [item["id"] for item in matching],
                },
            )
        )
        if matching:
            final_artifact_id = matching[0]["id"]
            final_qa = [
                result
                for result in qa_results
                if result["artifact_id"] == final_artifact_id
            ]
            qa_failures = [result for result in final_qa if result["status"] == "fail"]
            qa_ok = bool(final_qa) and not qa_failures
            rules.append(
                _rule(
                    "R-QA-001",
                    "pass" if qa_ok else "fail",
                    "QA exists for the final artifact and has no failures."
                    if qa_ok
                    else "QA is missing or has failures for the final artifact.",
                    {
                        "final_artifact_id": final_artifact_id,
                        "qa_checks": len(final_qa),
                        "failures": len(qa_failures),
                    },
                )
            )
    else:
        rules.append(
            _rule(
                "R-FINAL-001",
                "warn",
                "No exact-hash final approval exists yet.",
                {"project_status": project.status.value},
            )
        )

    caption_qa = [
        result for result in qa_results if result["check_name"] == "captions.coverage"
    ]
    captions_ok = any(result["status"] == "pass" for result in caption_qa)
    rules.append(
        _rule(
            "R-CAPTIONS-001",
            "pass" if captions_ok else "warn",
            "Captions coverage QA passed."
            if captions_ok
            else "No passing captions coverage QA was found.",
            {"checks": len(caption_qa)},
        )
    )

    enabled_providers = [
        profile
        for profile in controller.repository.list_provider_profiles()
        if profile["enabled"]
    ]
    paid_provider_with_zero_budget = bool(
        enabled_providers and project.max_budget.minor == 0
    )
    rules.append(
        _rule(
            "R-PROVIDER-001",
            "fail" if paid_provider_with_zero_budget else "pass",
            "Enabled providers are compatible with the project budget."
            if not paid_provider_with_zero_budget
            else "A provider is enabled while the project budget is zero.",
            {
                "enabled_profiles": [
                    profile["stable_key"] for profile in enabled_providers
                ],
                "max_budget_minor": project.max_budget.minor,
            },
        )
    )

    rules.append(
        _rule(
            "R-PUBLISH-001",
            "pass",
            "Publication is outside this preflight and requires a separate approval.",
            {"publishing_authorized": False},
        )
    )

    beds = controller.repository.list_audio_beds(project_id)
    cues = controller.repository.list_audio_cues(project_id)
    audio_problems: list[str] = []
    for bed in beds:
        artifact = controller.repository.get_artifact(bed["artifact_id"])
        decision = artifact["operation"].get("rights") or {}
        try:
            require_audio_rights(artifact)
        except (IntegrityError, ValueError) as error:
            audio_problems.append(f"bed {bed['role']}: {error}")
        if not decision.get("commercial_use_allowed"):
            audio_problems.append(f"bed {bed['role']}: commercial use not allowed")
        if not decision.get("modifications_allowed"):
            audio_problems.append(f"bed {bed['role']}: modification not allowed")
        if decision.get("attribution_required") and not decision.get(
            "attribution_text"
        ):
            audio_problems.append(f"bed {bed['role']}: attribution missing")
    for cue in cues:
        try:
            require_audio_rights(controller.repository.get_artifact(cue["artifact_id"]))
        except (IntegrityError, ValueError) as error:
            audio_problems.append(f"cue {cue['cue_key']}: {error}")
        decision = json.loads(cue["decision_json"])
        if not decision.get("commercial_use_allowed"):
            audio_problems.append(f"cue {cue['cue_key']}: commercial use not allowed")
        if not decision.get("modifications_allowed"):
            audio_problems.append(f"cue {cue['cue_key']}: modification not allowed")
        if decision.get("attribution_required") and not decision.get(
            "attribution_text"
        ):
            audio_problems.append(f"cue {cue['cue_key']}: attribution missing")
    roles = {bed["role"] for bed in beds}
    if beds and "primary" not in roles:
        audio_problems.append("a backup bed exists without a primary bed")
    rules.append(
        _rule(
            "R-AUDIO-001",
            "fail" if audio_problems else "pass",
            "Audio layers are licence-clean and credited."
            if not audio_problems
            else "; ".join(audio_problems),
            {
                "beds": len(beds),
                "cues": len(cues),
                "cue_roles": sorted({cue["role"] for cue in cues}),
                "primary_bed": "primary" in roles,
                "backup_bed": "backup" in roles,
            },
        )
    )

    return {
        "schema_version": 1,
        "project_id": project_id,
        "project_status": project.status.value,
        "overall": _overall(rules),
        "rules": rules,
    }

"""One-time, AST-checked source relocation for the bca8ca0 architecture bundle.

Run only on the old layout. Method bodies and decorators are copied verbatim;
source ownership is explicit, imports are subsequently minimized with Ruff.
This is a repository codemod, not runtime reflection or a plugin system.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "pinaka"


def source(text, node):
    start = min([node.lineno, *[d.lineno for d in getattr(node, "decorator_list", [])]])
    return "\n".join(text.splitlines()[start - 1 : node.end_lineno])


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf8")


def imports(text, tree):
    return (
        "\n".join(
            source(text, n).replace("from .", "from ..", 1)
            if isinstance(n, ast.ImportFrom) and n.level
            else source(text, n)
            for n in tree.body
            if isinstance(n, (ast.Import, ast.ImportFrom))
        )
        + "\n"
    )


def split_class(filename, clsname, package, groups, base_names):
    path = ROOT / filename
    text = path.read_text(encoding="utf8")
    tree = ast.parse(text)
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == clsname
    )
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    owners = {}
    for group, names in groups.items():
        for name in names.split():
            if name in owners:
                raise ValueError(f"Duplicate method owner: {name}")
            if name not in methods:
                raise ValueError(f"Missing method: {name}")
            owners[name] = group
    unknown = set(methods) - set(owners) - set(base_names.split())
    if unknown:
        raise ValueError(f"Unassigned methods: {sorted(unknown)}")
    header = imports(text, tree)
    top = [
        n
        for n in tree.body
        if not isinstance(n, (ast.Import, ast.ImportFrom, ast.ClassDef))
    ]
    shared = "\n\n".join(source(text, n) for n in top)
    shared_names = []
    for n in top:
        if isinstance(n, ast.FunctionDef):
            shared_names.append(n.name)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            shared_names.extend(t.id for t in targets if isinstance(t, ast.Name))
    if shared:
        put(ROOT / package / "_shared.py", header + "\n" + shared)
        header += "from ._shared import (" + ", ".join(shared_names) + ")\n"
    bases = []
    for group in groups:
        group_class = (
            "".join(w.title() for w in group.split("_")) + "UseCases"
            if package == "use_cases"
            else "".join(w.title() for w in group.split("_")) + "Store"
        )
        bodies = [
            source(text, n)
            for n in cls.body
            if isinstance(n, ast.FunctionDef) and owners.get(n.name) == group
        ]
        put(
            ROOT / package / f"{group}.py",
            header + f"\n\nclass {group_class}:\n" + "\n\n".join(bodies),
        )
        bases.append((group, group_class))
    facade_imports = "from __future__ import annotations\n\n"
    for n in tree.body:
        if isinstance(n, (ast.Import, ast.ImportFrom)) and not (
            isinstance(n, ast.ImportFrom) and n.module == "__future__"
        ):
            facade_imports += source(text, n) + "\n"
    for group, name in bases:
        facade_imports += f"from .{package}.{group} import {name}\n"
    # Re-export old public constants/helpers for callers using controller imports.
    if shared:
        facade_imports += (
            "from ."
            + package
            + "._shared import ("
            + ", ".join(f"{name} as {name}" for name in shared_names)
            + ")\n"
        )
    kept = [source(text, methods[n]) for n in base_names.split()]
    put(
        path,
        facade_imports
        + "\n\nclass "
        + clsname
        + "("
        + ", ".join(name for _, name in bases)
        + "):\n"
        + f'    """Compatible facade; feature behavior lives in {package}/."""\n\n'
        + "\n\n".join(kept),
    )
    put(
        ROOT / package / "__init__.py",
        f'"""Feature-local {clsname} implementations. Import the public facade."""',
    )
    # Every relocated method must be byte-for-byte identical before formatting.
    moved = {}
    for module in (ROOT / package).glob("*.py"):
        for node in ast.parse(module.read_text(encoding="utf8")).body:
            if isinstance(node, ast.ClassDef):
                moved.update(
                    {
                        n.name: source(module.read_text(encoding="utf8"), n)
                        for n in node.body
                        if isinstance(n, ast.FunctionDef)
                    }
                )
    for name in owners:
        assert moved[name] == source(text, methods[name]), name


CONTROLLER = {
    "projects": "create_project create_revision revision_lineage create_project_from_contract apply_pilot_plan ground_pilot_from_contract add_deliverable add_segment add_grounding_bundle transition_project add_fact_set add_shot add_slot approve next_action status_snapshot",
    "planning": "record_scene_plan scene_plan record_workable_brief workable_brief plan_from_text draft_script",
    "elements": "record_creative_element list_creative_elements bind_element_to_shot bind_shot_reference shot_references bind_elements_to_slots",
    "sourcing": "evaluate_asset_candidate import_sourced_original capture_asset_evidence import_stock_asset download_stock_asset finalize_asset",
    "assets": "assign_slot_asset promote_backup slot_coverage asset_use_context set_asset_use_context _enforce_context analyze_asset correct_asset compose_assets _require_correction_decision _read_artifact_bytes ai_edit_asset generative_upscale_asset judge_asset",
    "audio": "assign_audio_bed audio_coverage assign_audio_cue audio_credits _audio_layer_metadata _audio_layer_plan mux_voiced_animatic mux_narration",
    "artifacts": "_deduplicate_dependencies import_original write_artifact verify_artifact read_artifact_text",
    "tools": "register_provider_profile register_tool_profile reserve_tool_operation record_execution_receipt run_tool_once check_capabilities",
    "budget": "reserve_provider_spend provider_spend_summary reserve_provider_request _auto_run_cap_minor _resolve_run_cap plan_creative_budget approve_budget_plan budget_plan budget_summary _enforce_generation_routing reserve_budget_item",
    "jobs": "reserve_job _reject_sensitive_payload mark_job_submitting record_job_submitted mark_job_running mark_submission_unknown settle_job run_adapter_once",
    "settlement": "settle_budget_item variance_requests raise_project_budget reconcile_overage asset_costs decide_variance",
    "studio_packets": "_studio_spend_scope_hash record_studio_readiness studio_status approve_studio_spend _blocked_studio_decisions prepare_studio_work_packet studio_work_packet",
    "studio_execution": "import_studio_result run_studio_work_packet studio_production run_studio_production",
    "composition": "_composition_layers author_composition render_composition _latest_composition",
    "motion": "assemble_film plan_motion_takes plan_shot_motion prepare_shot_motion import_shot_motion attach_shot_clip _motion_shot _motion_frame",
    "captions": "export_captions burn_captions",
    "qa": "qa_render qa_animatic qa_voiced_animatic _validate_mux_result",
    "animatic": "_animatic_operation _pilot_timeline_context _latest_animatic_bundle produce_animatic",
    "frames": "capture_animatic_frames encode_silent_animatic",
    "narration": "synthesize_narration",
    "publication": "authorize_publication publication_authorization stored_publication_authorization archive_project",
}

STORE = {
    "projects": "create_project get_project list_projects list_child_projects _deliverable_from_row create_deliverable list_deliverables _segment_dict create_segment list_segments set_project_status",
    "planning": "record_scene_plan latest_scene_plan _brief_decision_dict _workable_brief_dict record_workable_brief latest_workable_brief get_workable_brief",
    "grounding": "_fact_dict current_fact_set _grounding_dict current_grounding_bundles add_grounding_bundle add_fact_set _rollback_status_after_fact_change _rollback_status_after_grounding_change _invalidate_dependency_graph",
    "shots": "_shot_dict add_shot get_shot list_shots add_slot get_slot list_slots",
    "elements": "_creative_element_dict record_creative_element get_creative_element list_creative_elements _shot_element_binding_dict bind_element_to_shot list_shot_element_bindings _shot_reference_dict upsert_shot_reference list_shot_references",
    "approvals": "active_approval _approval_dict create_approval _publication_authorization_dict record_publication_authorization current_publication_authorization",
    "artifacts": "register_artifact _artifact_dict get_artifact artifact_by_path list_artifacts discard_artifact artifact_by_content_hash register_archive get_archive",
    "assets": "register_treatment_decision list_treatment_decisions latest_treatment_for_slot _slot_asset_dict upsert_slot_asset list_slot_assets slot_asset_by_role swap_slot_asset_roles get_asset_use_context set_asset_use_context",
    "audio": "upsert_audio_bed list_audio_beds upsert_audio_cue list_audio_cues",
    "providers": "record_provider_spend provider_spend_total_cents list_provider_spend record_provider_request count_provider_requests _provider_profile_dict register_provider_profile list_provider_profiles record_capability latest_capability",
    "tools": "_tool_profile_dict register_tool_profile list_tool_profiles get_tool_profile _tool_operation_dict reserve_tool_operation get_tool_operation tool_operation_by_idempotency mark_tool_operation_running record_execution_receipt get_execution_receipt list_tool_operations",
    "studio_reservations": "_studio_spend_approval_dict record_studio_spend_approval get_studio_spend_approval _specialist_handoff_dict reserve_studio_work record_specialist_handoff specialist_handoff_for_operation",
    "studio_jobs": "_studio_production_dict open_studio_production get_studio_production update_studio_production _studio_job_dict record_studio_job update_studio_job studio_jobs complete_specialist_handoff",
    "studio_results": "record_specialist_result",
    "qa": "_qa_dict record_qa_result list_qa_results",
    "jobs": "_ledger reserve_job get_job update_job_status settle_job recoverable_jobs budget_ledger",
    "budget": "_budget_item_dict _budget_plan_dict record_budget_plan get_budget_plan current_budget_plan record_budget_plan_approval _budget_approval_dict budget_plan_approval link_budget_item_job settle_budget_item_for_job skip_budget_item raise_project_budget record_overage_reconciliation",
    "costs": "_variance_request_dict record_variance_request get_variance_request _asset_cost_dict _record_asset_cost_tx record_asset_cost list_asset_costs approved_variance_for_job pending_variance_requests decide_variance_request",
}


def contract_group(name):
    groups = {
        "common": "StrictModel Money",
        "projects": "ProjectCreate ProjectRecord DeliverableCreate DeliverableRecord SegmentCreate GroundingBundleCreate FactSetCreate ApprovalRequest PublicationAuthorizationCreate",
        "assets": "AssetSourceRecord AssetRightsRecord ProviderComplianceRecord AssetCandidate AssetUseContext ProviderProfile AssetEligibilityResult",
        "planning": "ShotCreate SlotCreate BriefDecisionCreate WorkableBriefCreate CreativeElementCreate ShotElementBindingCreate ShotReferenceCreate TextBriefRequest",
        "production": "JobRequest JobRecord AgentResult CapabilityResult ProviderModelProfileCreate ToolProfileCreate ToolOperationRequest ExecutionReceiptCreate ShotMotionRequest AudioCueRequest",
        "artifacts": "DependencyRef ArtifactCreate QACheck QAReport",
        "studio": "StudioScope StudioWorkPacketRequest StudioSpendApprovalCreate StudioResultOutputImport StudioResultPacketImport StudioReadinessReport",
        "budget": "BudgetPlanItemCreate BudgetPlanCreate BudgetPlanApprovalCreate VarianceDecisionCreate BudgetRaiseCreate OverageReconcileCreate",
        "pilot": "ClientProfile PilotDesignProfile PilotScript PilotProviderPolicy PilotSegmentPlan PilotDeliverablePlan PilotContract",
    }
    return next(g for g, names in groups.items() if name in names.split())


def split_contracts():
    path = ROOT / "models.py"
    text = path.read_text(encoding="utf8")
    tree = ast.parse(text)
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    owners = {name: contract_group(name) for name in classes}
    header = imports(text, tree)
    groups = defaultdict(list)
    for name, node in classes.items():
        groups[owners[name]].append(node)
    exports = []
    for group, nodes in groups.items():
        dependencies = sorted(
            {
                n.id
                for cls in nodes
                for n in ast.walk(cls)
                if isinstance(n, ast.Name) and n.id in classes and owners[n.id] != group
            }
        )
        dependency_imports = "\n".join(
            f"from .{owners[name]} import {name}" for name in dependencies
        )
        put(
            ROOT / "contracts" / f"{group}.py",
            header
            + dependency_imports
            + "\n\n"
            + "\n\n".join(source(text, n) for n in nodes),
        )
        exports.extend(f"from .{group} import {n.name} as {n.name}" for n in nodes)
    put(
        ROOT / "contracts" / "__init__.py",
        '"""Aggregate-local strict request and record contracts."""\n\n'
        + "\n".join(exports),
    )
    # Keep the old import surface, including enums historically exported here.
    facade = "\n".join(f"from .contracts import {name} as {name}" for name in classes)
    for n in tree.body:
        if isinstance(n, ast.ImportFrom) and n.module == "enums":
            facade += "\n" + "\n".join(
                f"from .enums import {a.name} as {a.name}" for a in n.names
            )
    put(
        path,
        '"""Compatibility exports. New contracts belong in contracts/<aggregate>.py."""\n\n'
        + facade,
    )


def retire_vertex():
    """Remove only the explicitly retired provider entry points, not historic records."""

    removed = {
        "synthesize_narration_vertex",
        "register_vertex_profile",
        "verify_vertex_models",
        "enhance_still_with_vertex",
        "cmd_synthesize_narration_vertex",
        "cmd_register_vertex_profile",
        "cmd_vertex_check",
        "cmd_enhance_still_vertex",
        "cmd_generate_vertex_image",
    }
    for filename in ("controller.py", "cli.py"):
        path = ROOT / filename
        text = path.read_text(encoding="utf8")
        tree = ast.parse(text)
        ranges = []
        for n in ast.walk(tree):
            if (
                isinstance(n, ast.FunctionDef)
                and n.name in removed
                or isinstance(n, ast.ImportFrom)
                and n.module in {"vertex", "adapters.google_vertex_image"}
            ):
                ranges.append((n.lineno, n.end_lineno))
        if filename == "cli.py":
            for function in tree.body:
                if not isinstance(function, ast.FunctionDef):
                    continue
                active = False
                for statement in function.body:
                    if isinstance(statement, ast.Assign) and isinstance(
                        statement.value, ast.Call
                    ):
                        args = statement.value.args
                        if (
                            args
                            and isinstance(args[0], ast.Constant)
                            and args[0].value
                            in {
                                "vertex-check",
                                "register-vertex-profile",
                                "enhance-still-vertex",
                                "generate-vertex-image",
                                "synthesize-narration-vertex",
                            }
                        ):
                            active = True
                    if active:
                        ranges.append((statement.lineno, statement.end_lineno))
                        if (
                            isinstance(statement, ast.Expr)
                            and isinstance(statement.value, ast.Call)
                            and isinstance(statement.value.func, ast.Attribute)
                            and statement.value.func.attr == "set_defaults"
                        ):
                            active = False
        lines = text.splitlines()
        deleted = {i for start, end in ranges for i in range(start - 1, end)}
        put(path, "\n".join(line for i, line in enumerate(lines) if i not in deleted))


def shared_guards():
    path = ROOT / "controller.py"
    text = path.read_text(encoding="utf8")
    tree = ast.parse(text)
    replacements = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.If) or len(n.body) != 1 or n.orelse:
            continue
        condition = ast.get_source_segment(text, n.test) or ""
        raised = n.body[0]
        if (
            condition
            != "project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}"
        ):
            continue
        if (
            not isinstance(raised, ast.Raise)
            or not isinstance(raised.exc, ast.Call)
            or getattr(raised.exc.func, "id", "") != "ConflictError"
        ):
            continue
        message = ast.get_source_segment(text, raised.exc.args[0])
        replacements.append(
            (
                n.lineno,
                n.end_lineno,
                " " * n.col_offset
                + f"require_active_project(project, message={message})",
            )
        )
    lines = text.splitlines()
    for start, end, value in sorted(replacements, reverse=True):
        lines[start - 1 : end] = [value]
    put(path, "\n".join(lines))
    print(f"Centralized {len(replacements)} active-project guards")


def main():
    if (ROOT / "use_cases" / "motion.py").exists():
        raise SystemExit("Already relocated; refusing to rerun the one-time codemod")
    retire_vertex()
    shared_guards()
    split_contracts()
    split_class(
        "repository.py",
        "Repository",
        "store",
        STORE,
        "__init__ _event _project_from_row _job_from_row _require_project _require_job events",
    )
    split_class(
        "controller.py", "CreativeController", "use_cases", CONTROLLER, "__init__ open"
    )
    print("Relocated contracts, store and use cases; method text/decorators verified")


if __name__ == "__main__":
    main()

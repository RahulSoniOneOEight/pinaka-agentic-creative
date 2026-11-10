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

from .adapters.captions import CaptionMuxAdapter
from .adapters.comfyui import ComfyUIImageAdapter
from .adapters.google_image import GeminiImageAdapter
from .adapters.google_vertex_image import (
    GoogleVertexImageAdapter,
    estimate_cost_minor,
)
from .adapters.mock import MockGenerationAdapter
from .adapters.transcribe import (
    WhisperTranscriber,
    transcription_payload,
    write_transcript,
)
from .analytics import (
    ExperimentDesign,
    VariantMetrics,
    evaluate_experiment,
    ingest_metrics_csv,
)
from .backup import (
    create_backup,
    drill_restore,
    mirror_backup,
    restore_backup,
    verify_backup,
)
from .controller import CreativeController
from .creative_rules import CreativeProfile, evaluate_profile
from .delivery import build_delivery_packet
from .enums import (
    ApprovalStage,
    ArtifactKind,
    AssetMediaType,
    AssetUseClass,
    ContentMode,
    ProjectStatus,
)
from .errors import ConflictError, NotFoundError, PinakaError
from .external_qa import run_external_qa
from .external_spend import (
    ExternalQuote,
    ExternalReceipt,
    SpendEnvelope,
    SpendReservation,
    reconcile_spend,
)
from .fonts import FontInventory, FontLicence, check_fonts, inventory_fonts
from .formats import FormatDerivationAdapter, format_output_name
from .freesound import FreesoundClient
from .internet_archive import InternetArchiveClient
from .localization import plan_localization
from .models import (
    ApprovalRequest,
    DeliverableCreate,
    ExecutionReceiptCreate,
    FactSetCreate,
    GroundingBundleCreate,
    JobRequest,
    Money,
    PilotContract,
    ProjectCreate,
    ProviderModelProfileCreate,
    SegmentCreate,
    ToolOperationRequest,
    ToolProfileCreate,
)
from .openverse import OpenverseClient, default_context
from .preflight import run_preflight
from .publication import (
    PublicationAuthorization,
    PublicationRequest,
    build_dispatch_package,
    check_publication,
)
from .recipes import build_plan
from .reconciliation import reconcile_project
from .retention import RetentionItem, RetentionPolicy, plan_retention
from .review import load_spec, review_video, write_report
from .routing import RouteRequest, route_profiles
from .utils import sha256_file, sha256_json
from .wikimedia import WikimediaClient


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _default_home() -> Path:
    configured = os.environ.get("PINAKA_HOME")
    return Path(configured) if configured else Path.cwd() / "workspace"


def _controller(args: argparse.Namespace) -> CreativeController:
    return CreativeController.open(args.home)


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _resolve_tool_profile(controller: CreativeController, value: str) -> dict[str, Any]:
    for profile in controller.repository.list_tool_profiles():
        if value in {profile["id"], profile["stable_key"]}:
            return profile
    raise NotFoundError(f"Tool profile not found: {value}")


def _load_contract(path: str) -> PilotContract:
    return PilotContract.model_validate(_load_json(path))


def _write_json(path: str | None, payload: Any) -> Any:
    if path:
        output = Path(path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    return payload


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


def cmd_pilot_validate(args: argparse.Namespace) -> dict[str, Any]:
    contract = _load_contract(args.contract)
    payload = contract.model_dump(mode="json")
    return {
        "status": "valid",
        "contract_hash": sha256_json(payload),
        "project": payload["project"],
        "provider_mode": (
            payload["provider_policy"]["mode"]
            if payload.get("provider_policy")
            else None
        ),
    }


def cmd_pilot_new(args: argparse.Namespace) -> dict[str, Any]:
    controller = _controller(args)
    project, artifact = controller.create_project_from_contract(
        _load_contract(args.contract)
    )
    return {
        "project": project.model_dump(mode="json"),
        "contract_artifact": artifact,
        "next_action": controller.next_action(project.id),
    }


def cmd_pilot_plan(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).apply_pilot_plan(
        args.project_id, _load_contract(args.contract)
    )


def cmd_pilot_ground(args: argparse.Namespace) -> dict[str, Any]:
    controller = _controller(args)
    bundles = controller.ground_pilot_from_contract(
        args.project_id, _load_contract(args.contract)
    )
    return {
        "grounding_bundles": bundles,
        "next_action": controller.next_action(args.project_id),
    }


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


def cmd_add_deliverable(args: argparse.Namespace) -> dict[str, Any]:
    request = DeliverableCreate.model_validate(_load_json(args.file))
    return (
        _controller(args)
        .add_deliverable(args.project_id, request)
        .model_dump(mode="json")
    )


def cmd_add_segment(args: argparse.Namespace) -> dict[str, Any]:
    request = SegmentCreate.model_validate(_load_json(args.file))
    return _controller(args).add_segment(args.project_id, request)


def cmd_add_grounding(args: argparse.Namespace) -> dict[str, Any]:
    request = GroundingBundleCreate.model_validate(_load_json(args.file))
    return _controller(args).add_grounding_bundle(args.project_id, request)


def cmd_register_provider_profile(args: argparse.Namespace) -> dict[str, Any]:
    request = ProviderModelProfileCreate.model_validate(_load_json(args.file))
    return _controller(args).register_provider_profile(request)


def cmd_register_tool_profile(args: argparse.Namespace) -> dict[str, Any]:
    request = ToolProfileCreate.model_validate(_load_json(args.file))
    return _controller(args).register_tool_profile(request)


def cmd_authorize_operation(args: argparse.Namespace) -> dict[str, Any]:
    request = ToolOperationRequest.model_validate(_load_json(args.file))
    return _controller(args).reserve_tool_operation(args.project_id, request)


def cmd_record_receipt(args: argparse.Namespace) -> dict[str, Any]:
    request = ExecutionReceiptCreate.model_validate(_load_json(args.file))
    return _controller(args).record_execution_receipt(args.operation_id, request)


def cmd_approve(args: argparse.Namespace) -> dict[str, Any]:
    controller = _controller(args)
    scope = _load_json(args.scope_file) if args.scope_file else json.loads(args.scope)
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


def cmd_next(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).next_action(args.project_id)


def cmd_archive(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).archive_project(args.project_id)


def cmd_reconcile(args: argparse.Namespace) -> dict[str, Any]:
    return reconcile_project(_controller(args), args.project_id)


def cmd_produce_animatic(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).produce_animatic(args.project_id)


def cmd_qa_animatic(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).qa_animatic(args.project_id)


def cmd_capture_frames(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).capture_animatic_frames(
        args.project_id, frame_count=args.frames, fps=args.fps
    )


def cmd_encode_animatic(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).encode_silent_animatic(args.project_id)


def cmd_synthesize_narration(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).synthesize_narration(
        args.project_id, voice=args.voice, speed=args.speed
    )


def cmd_mux_voiced(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).mux_voiced_animatic(args.project_id)


def cmd_qa_voiced(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).qa_voiced_animatic(args.project_id)


def cmd_review_video(args: argparse.Namespace) -> dict[str, Any]:
    spec = load_spec(args.spec)
    report = review_video(args.video, spec)
    write_report(report, args.output)
    return report


def cmd_caption_video(args: argparse.Namespace) -> dict[str, Any]:
    adapter = CaptionMuxAdapter()
    result = adapter.mux(
        video_path=Path(args.video),
        srt_path=Path(args.srt),
        output_path=Path(args.output),
    )
    manifest = {
        "schema_version": 1,
        "output": str(Path(args.output).expanduser().resolve()),
        "output_hash": result.content_hash,
        "source_video": str(Path(args.video).expanduser().resolve()),
        "source_video_hash": result.source_video_hash,
        "subtitle": str(Path(args.srt).expanduser().resolve()),
        "subtitle_hash": result.subtitle_hash,
        "metadata": result.metadata,
        "command_hash": result.command_hash,
        "encoder_version": result.encoder_version,
        "note": (
            "Captioned review derivative. It does not replace the archived master "
            "without a new exact-hash approval."
        ),
    }
    manifest_path = (
        Path(args.manifest).expanduser().resolve()
        if args.manifest
        else Path(args.output).with_suffix(".manifest.json").resolve()
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {
        "output": manifest["output"],
        "output_hash": result.content_hash,
        "manifest": str(manifest_path),
        "metadata": result.metadata,
    }


def cmd_transcribe_audio(args: argparse.Namespace) -> dict[str, Any]:
    download_root = (
        Path(args.download_root).expanduser().resolve()
        if args.download_root
        else Path.cwd() / "workspace" / "tools" / "whisper"
    )
    transcriber = WhisperTranscriber(model_size=args.model, download_root=download_root)
    result = transcriber.transcribe(args.audio)
    expected_text = (
        Path(args.expected_file).read_text(encoding="utf-8").strip()
        if args.expected_file
        else None
    )
    payload = transcription_payload(result, expected_text=expected_text)
    write_transcript(payload, args.output)
    return payload


def cmd_derive_formats(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.video).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if output_dir.exists():
        raise FileExistsError(f"Format output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    adapter = FormatDerivationAdapter()
    results = adapter.derive(source)
    formats = []
    for result in results:
        name = format_output_name(source.stem, result.key)
        path = output_dir / name
        path.write_bytes(result.content)
        formats.append(
            {
                "key": result.key,
                "width_px": result.width_px,
                "height_px": result.height_px,
                "output": name,
                "sha256": result.content_hash,
                "bytes": len(result.content),
                "metadata": result.metadata,
                "command_hash": result.command_hash,
            }
        )
    manifest = {
        "schema_version": 1,
        "source": str(source),
        "source_sha256": sha256_file(source),
        "formats": formats,
    }
    manifest_path = (
        Path(args.manifest).expanduser().resolve()
        if args.manifest
        else output_dir / "formats-manifest.json"
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return {
        "source": str(source),
        "manifest": str(manifest_path),
        "formats": formats,
    }


def cmd_delivery_packet(args: argparse.Namespace) -> dict[str, Any]:
    return build_delivery_packet(
        master=args.master,
        captioned=args.captioned,
        captions=args.captions,
        formats_manifest=args.formats_manifest,
        metadata=args.metadata,
        output_dir=args.output_dir,
        formats_dir=args.formats_dir,
    )


def cmd_preflight(args: argparse.Namespace) -> dict[str, Any]:
    return run_preflight(_controller(args), args.project_id)


def cmd_evaluate_profile(args: argparse.Namespace) -> dict[str, Any]:
    profile = CreativeProfile.model_validate(_load_json(args.file))
    decision = evaluate_profile(profile)
    payload = decision.model_dump(mode="json")
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return payload


def cmd_creative_plan(args: argparse.Namespace) -> dict[str, Any]:
    profile = CreativeProfile.model_validate(_load_json(args.file))
    plan = build_plan(profile)
    payload = plan.model_dump(mode="json")
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return payload


def cmd_record_creative_plan(args: argparse.Namespace) -> dict[str, Any]:
    controller = _controller(args)
    project = controller.repository.get_project(args.project_id)
    if project.status in {ProjectStatus.ARCHIVED, ProjectStatus.CANCELLED}:
        raise ConflictError(
            f"Cannot record a creative plan for a {project.status.value} project"
        )
    profile = CreativeProfile.model_validate(_load_json(args.file))
    plan = build_plan(profile)
    payload = json.dumps(
        plan.model_dump(mode="json"), indent=2, ensure_ascii=False
    ).encode("utf-8")
    artifact = controller.write_artifact(
        args.project_id,
        kind=ArtifactKind.CONTRACT,
        relative_path=f"artifacts/creative-plan-{plan.profile_hash[:12]}.json",
        content=payload,
        operation={
            "type": "creative_plan",
            "guide_version": plan.guide_version,
            "recipe_id": plan.recipe_selection.recipe.id,
            "recipe_version": plan.recipe_selection.recipe.version,
            "decision_risk": plan.decision.risk,
            "treatment_ceiling": plan.decision.treatment_ceiling,
        },
    )
    return {
        "project_id": args.project_id,
        "artifact_id": artifact["id"],
        "content_hash": artifact["content_hash"],
        "profile_hash": plan.profile_hash,
        "recipe": plan.recipe_selection.recipe.id,
    }


def cmd_backup(args: argparse.Namespace) -> dict[str, Any]:
    manifest = create_backup(args.home, args.output)
    return {
        "backup": manifest["manifest"],
        "items": len(manifest["items"]),
        "source_home": manifest["source_home"],
    }


def cmd_verify_backup(args: argparse.Namespace) -> dict[str, Any]:
    return verify_backup(args.backup)


def cmd_restore_backup(args: argparse.Namespace) -> dict[str, Any]:
    return restore_backup(args.backup, args.target)


def cmd_drill_restore(args: argparse.Namespace) -> dict[str, Any]:
    return drill_restore(args.backup, workdir=args.workdir)


def cmd_mirror_backup(args: argparse.Namespace) -> dict[str, Any]:
    return mirror_backup(args.backup, args.destination)


def cmd_source_search(args: argparse.Namespace) -> dict[str, Any]:
    context = default_context(
        use_class=AssetUseClass(args.use_class),
        monetized=args.monetized,
        advertising=args.advertising,
        modification_planned=not args.no_modification,
        attribution_can_be_delivered=not args.no_attribution,
    )
    media_type = AssetMediaType(args.media)
    if args.provider == "openverse":
        payload = OpenverseClient().search_with_eligibility(
            args.query,
            context,
            media_type=media_type,
            limit=args.limit,
            page=args.page,
        )
    elif args.provider == "wikimedia":
        payload = WikimediaClient().search_with_eligibility(
            args.query,
            context,
            media_type=media_type,
            limit=args.limit,
            offset=(args.page - 1) * args.limit,
        )
    elif args.provider == "freesound":
        payload = FreesoundClient().search_with_eligibility(
            args.query,
            context,
            limit=args.limit,
            page=args.page,
        )
    else:
        payload = InternetArchiveClient().search_with_eligibility(
            args.query,
            context,
            media_type=media_type,
            limit=args.limit,
            page=args.page,
        )
    return _write_json(args.output, payload)


def cmd_route_provider(args: argparse.Namespace) -> dict[str, Any]:
    controller = _controller(args)
    request = RouteRequest.model_validate(_load_json(args.file))
    decision = route_profiles(controller.repository.list_provider_profiles(), request)
    return _write_json(args.output, decision.model_dump(mode="json"))


def cmd_spend_reconcile(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    report = reconcile_spend(
        [ExternalQuote.model_validate(item) for item in payload.get("quotes", [])],
        [
            SpendReservation.model_validate(item)
            for item in payload.get("reservations", [])
        ],
        [ExternalReceipt.model_validate(item) for item in payload.get("receipts", [])],
        SpendEnvelope.model_validate(payload["envelope"]),
    )
    return _write_json(args.output, report.model_dump(mode="json"))


def cmd_retention_plan(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    policy = (
        RetentionPolicy.model_validate(payload["policy"])
        if payload.get("policy")
        else None
    )
    report = plan_retention(
        [RetentionItem.model_validate(item) for item in payload.get("items", [])],
        policy=policy,
    )
    return _write_json(args.output, report.model_dump(mode="json"))


def cmd_publication_check(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    request = PublicationRequest.model_validate(payload["request"])
    authorization = PublicationAuthorization.model_validate(
        payload.get("authorization", {})
    )
    plan = check_publication(_controller(args), args.project_id, request, authorization)
    return _write_json(args.output, plan.model_dump(mode="json"))


def cmd_publication_package(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    request = PublicationRequest.model_validate(payload["request"])
    authorization = PublicationAuthorization.model_validate(
        payload.get("authorization", {})
    )
    package = build_dispatch_package(
        _controller(args),
        args.project_id,
        request,
        authorization,
        args.output_dir,
    )
    return _write_json(args.output, package.model_dump(mode="json"))


def cmd_localization_plan(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.srt).expanduser().resolve()
    targets = tuple(
        target.strip() for target in args.targets.split(",") if target.strip()
    )
    plan = plan_localization(
        source.read_text(encoding="utf-8-sig"),
        targets=targets,
        source_locale=args.source_locale,
    )
    return _write_json(args.output, plan.model_dump(mode="json"))


def cmd_font_inventory(args: argparse.Namespace) -> dict[str, Any]:
    inventory = inventory_fonts(args.root)
    return _write_json(args.output, inventory.model_dump(mode="json"))


def cmd_font_check(args: argparse.Namespace) -> dict[str, Any]:
    inventory = FontInventory.model_validate(_load_json(args.inventory))
    licences = [
        FontLicence.model_validate(item)
        for item in _load_json(args.licences).get("licences", [])
    ]
    report = check_fonts(inventory, licences)
    return _write_json(args.output, report.model_dump(mode="json"))


def cmd_experiment_report(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    design_payload = payload.get("design") if isinstance(payload, dict) else None
    if design_payload is None:
        raise ValueError("Experiment file must contain a 'design' object")
    design = ExperimentDesign.model_validate(design_payload)
    if args.metrics_csv:
        metrics = list(
            ingest_metrics_csv(Path(args.metrics_csv).read_text(encoding="utf-8-sig"))
        )
    else:
        metrics = [
            VariantMetrics.model_validate(item) for item in payload.get("metrics", [])
        ]
    report = evaluate_experiment(design, metrics)
    return _write_json(args.output, report.model_dump(mode="json"))


def cmd_generate_image(args: argparse.Namespace) -> dict[str, Any]:
    import shutil

    from .utils import sha256_json

    prompt = (args.prompt or "").strip()
    if not prompt:
        raise ValueError("--prompt is required")
    parameters: dict[str, Any] = {
        "prompt": prompt,
        "billing_mode": "free_tier",
    }
    if args.aspect_ratio:
        parameters["aspect_ratio"] = args.aspect_ratio
    if args.image_size:
        parameters["image_size"] = args.image_size
    digest = sha256_json(
        {"prompt": prompt, "aspect_ratio": args.aspect_ratio, "model": args.model}
    )[:16]
    controller = _controller(args)
    profile = _resolve_tool_profile(controller, args.profile_id)
    request = ToolOperationRequest(
        tool_profile_id=profile["id"],
        operation="image.generate",
        idempotency_key=f"nano-banana-{digest}",
        parameters=parameters,
        limits={"max_cost_minor": 0},
        output_declaration={"media_type": "image", "model": args.model},
        required_approval_stage=ApprovalStage.BRIEF,
    )
    adapter = GeminiImageAdapter(model=args.model)
    adapter.ensure_configured()
    operation, receipt, artifact = controller.run_tool_once(
        args.project_id,
        request,
        adapter,
        artifact_path=f"artifacts/nano-banana-{digest}.png",
    )
    result: dict[str, Any] = {
        "operation_id": operation["id"],
        "operation_status": operation["status"],
        "receipt_status": receipt["status"],
        "artifact_id": artifact["id"] if artifact else None,
        "content_hash": artifact["content_hash"] if artifact else None,
    }
    if artifact and args.copy_to:
        source = controller.artifact_store.resolve(
            args.project_id, artifact["relative_path"]
        )
        destination = Path(args.copy_to).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        result["copied_to"] = str(destination)
    return result


def cmd_generate_local_image(args: argparse.Namespace) -> dict[str, Any]:
    import shutil

    from .utils import sha256_json

    prompt = (args.prompt or "").strip()
    if not prompt:
        raise ValueError("--prompt is required")
    parameters: dict[str, Any] = {
        "prompt": prompt,
        "negative_prompt": args.negative_prompt,
        "checkpoint": args.checkpoint,
        "width": args.width,
        "height": args.height,
        "steps": args.steps,
        "seed": args.seed,
        "filename_prefix": "pinaka",
    }
    digest = sha256_json(
        {
            "prompt": prompt,
            "checkpoint": args.checkpoint,
            "width": args.width,
            "height": args.height,
            "steps": args.steps,
            "seed": args.seed,
        }
    )[:16]
    controller = _controller(args)
    adapter = ComfyUIImageAdapter(allowed_checkpoints=(args.checkpoint,))
    adapter.ensure_available()
    profile = _resolve_tool_profile(controller, args.profile_id)
    request = ToolOperationRequest(
        tool_profile_id=profile["id"],
        operation="image.generate",
        idempotency_key=f"comfyui-{digest}",
        parameters=parameters,
        limits={"max_cost_minor": 0},
        output_declaration={"media_type": "image", "device": "cpu"},
        required_approval_stage=ApprovalStage.BRIEF,
    )
    operation, receipt, artifact = controller.run_tool_once(
        args.project_id,
        request,
        adapter,
        artifact_path=f"artifacts/comfyui-{digest}.png",
    )
    result: dict[str, Any] = {
        "operation_id": operation["id"],
        "operation_status": operation["status"],
        "receipt_status": receipt["status"],
        "artifact_id": artifact["id"] if artifact else None,
        "content_hash": artifact["content_hash"] if artifact else None,
    }
    if artifact and args.copy_to:
        source = controller.artifact_store.resolve(
            args.project_id, artifact["relative_path"]
        )
        destination = Path(args.copy_to).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        result["copied_to"] = str(destination)
    return result


def cmd_generate_vertex_image(args: argparse.Namespace) -> dict[str, Any]:
    import shutil

    from .utils import sha256_json

    prompt = (args.prompt or "").strip()
    if not prompt:
        raise ValueError("--prompt is required")
    adapter = GoogleVertexImageAdapter(
        project=args.project,
        location=args.location,
        model=args.model,
    )
    adapter.ensure_configured()
    estimated = estimate_cost_minor(args.model, args.image_size)
    if args.max_cost_cents < estimated:
        raise ValueError(
            f"--max-cost-cents must be at least {estimated} for {args.model} "
            f"at {args.image_size}"
        )
    digest = sha256_json(
        {
            "prompt": prompt,
            "model": args.model,
            "image_size": args.image_size,
            "aspect_ratio": args.aspect_ratio,
        }
    )[:16]
    controller = _controller(args)
    profile = _resolve_tool_profile(controller, args.profile_id)
    request = ToolOperationRequest(
        tool_profile_id=profile["id"],
        operation="image.generate",
        idempotency_key=f"vertex-image-{digest}",
        parameters={
            "prompt": prompt,
            "model": args.model,
            "image_size": args.image_size,
            "aspect_ratio": args.aspect_ratio,
        },
        limits={"max_cost_minor": args.max_cost_cents},
        output_declaration={
            "media_type": "image",
            "model": args.model,
            "project": adapter.project,
            "billing": "gcp_trial_credit",
        },
        required_approval_stage=ApprovalStage.BRIEF,
    )
    operation, receipt, artifact = controller.run_tool_once(
        args.project_id,
        request,
        adapter,
        artifact_path=f"artifacts/vertex-image-{digest}.png",
    )
    result: dict[str, Any] = {
        "operation_id": operation["id"],
        "operation_status": operation["status"],
        "receipt_status": receipt["status"],
        "artifact_id": artifact["id"] if artifact else None,
        "content_hash": artifact["content_hash"] if artifact else None,
        "estimated_cost_minor": estimated,
        "actual_cost_minor": receipt.get("details", {}).get("actual_cost_minor"),
        "billing": "gcp_trial_credit",
    }
    if (
        artifact
        and receipt["status"] == "succeeded"
        and receipt.get("details", {}).get("actual_cost_minor")
    ):
        spend_payload = {
            "schema_version": "external-trial-credit/v1",
            "provider": "google-vertex",
            "project": adapter.project,
            "location": adapter.location,
            "model": args.model,
            "operation_id": operation["id"],
            "output_artifact_id": artifact["id"],
            "output_content_hash": artifact["content_hash"],
            "actual_cost_minor": receipt["details"]["actual_cost_minor"],
            "currency": "USD",
            "billing": "gcp_trial_credit",
            "prompt_sha256": receipt["details"].get("prompt_sha256"),
            "created_at": operation.get("created_at"),
        }
        spend_artifact = controller.write_artifact(
            args.project_id,
            kind=ArtifactKind.EXTERNAL_SPEND,
            relative_path=f"artifacts/vertex-spend-{digest}.json",
            content=json.dumps(spend_payload, indent=2, ensure_ascii=False).encode(
                "utf-8"
            ),
            operation={
                "type": "external_trial_credit",
                "model": args.model,
                "actual_cost_minor": receipt["details"]["actual_cost_minor"],
            },
        )
        result["spend_artifact_id"] = spend_artifact["id"]
        result["spend_content_hash"] = spend_artifact["content_hash"]
    if artifact and args.copy_to:
        source = controller.artifact_store.resolve(
            args.project_id, artifact["relative_path"]
        )
        destination = Path(args.copy_to).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        result["copied_to"] = str(destination)
    return result


def cmd_external_qa(args: argparse.Namespace) -> dict[str, Any]:
    report = run_external_qa(
        args.video,
        args.output_dir,
        qcli=args.qcli,
        mediaconch=args.mediaconch,
    )
    return _write_json(args.output, report.model_dump(mode="json"))


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

    validate_parser = subparsers.add_parser(
        "pilot-validate", help="Validate a pilot contract without changing state"
    )
    validate_parser.add_argument("--contract", required=True)
    validate_parser.set_defaults(handler=cmd_pilot_validate)

    pilot_new_parser = subparsers.add_parser(
        "pilot-new", help="Create a project from a validated pilot contract"
    )
    pilot_new_parser.add_argument("--contract", required=True)
    pilot_new_parser.set_defaults(handler=cmd_pilot_new)

    pilot_plan_parser = subparsers.add_parser(
        "pilot-plan", help="Persist the deliverable/segment plan from a pilot contract"
    )
    pilot_plan_parser.add_argument("project_id")
    pilot_plan_parser.add_argument("--contract", required=True)
    pilot_plan_parser.set_defaults(handler=cmd_pilot_plan)

    pilot_ground_parser = subparsers.add_parser(
        "pilot-ground", help="Persist brand, claim and source grounding after approval"
    )
    pilot_ground_parser.add_argument("project_id")
    pilot_ground_parser.add_argument("--contract", required=True)
    pilot_ground_parser.set_defaults(handler=cmd_pilot_ground)

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

    deliverable_parser = subparsers.add_parser(
        "add-deliverable", help="Add a typed deliverable from a JSON contract"
    )
    deliverable_parser.add_argument("project_id")
    deliverable_parser.add_argument("--file", required=True)
    deliverable_parser.set_defaults(handler=cmd_add_deliverable)

    segment_parser = subparsers.add_parser(
        "add-segment", help="Add a typed segment from a JSON contract"
    )
    segment_parser.add_argument("project_id")
    segment_parser.add_argument("--file", required=True)
    segment_parser.set_defaults(handler=cmd_add_segment)

    grounding_parser = subparsers.add_parser(
        "add-grounding", help="Add a versioned grounding bundle"
    )
    grounding_parser.add_argument("project_id")
    grounding_parser.add_argument("--file", required=True)
    grounding_parser.set_defaults(handler=cmd_add_grounding)

    provider_profile_parser = subparsers.add_parser(
        "register-provider-profile",
        help="Register a secret-free managed provider profile",
    )
    provider_profile_parser.add_argument("--file", required=True)
    provider_profile_parser.set_defaults(handler=cmd_register_provider_profile)

    tool_profile_parser = subparsers.add_parser(
        "register-tool-profile", help="Register a governed local or remote tool profile"
    )
    tool_profile_parser.add_argument("--file", required=True)
    tool_profile_parser.set_defaults(handler=cmd_register_tool_profile)

    operation_parser = subparsers.add_parser(
        "authorize-operation", help="Authorize an idempotent tool operation"
    )
    operation_parser.add_argument("project_id")
    operation_parser.add_argument("--file", required=True)
    operation_parser.set_defaults(handler=cmd_authorize_operation)

    receipt_parser = subparsers.add_parser(
        "record-receipt", help="Record one terminal execution receipt"
    )
    receipt_parser.add_argument("operation_id")
    receipt_parser.add_argument("--file", required=True)
    receipt_parser.set_defaults(handler=cmd_record_receipt)

    approve_parser = subparsers.add_parser("approve", help="Record a human approval")
    approve_parser.add_argument("project_id")
    approve_parser.add_argument("stage", choices=[item.value for item in ApprovalStage])
    approve_parser.add_argument("--approved-by", required=True)
    approve_parser.add_argument("--scope", default="{}", help="JSON object")
    approve_parser.add_argument(
        "--scope-file", help="UTF-8 JSON object file; overrides --scope"
    )
    approve_parser.add_argument("--output-hash")
    approve_parser.set_defaults(handler=cmd_approve)

    status_parser = subparsers.add_parser(
        "status", help="Show project state, costs, and recovery work"
    )
    status_parser.add_argument("project_id")
    status_parser.set_defaults(handler=cmd_status)

    next_parser = subparsers.add_parser(
        "next", help="Show the next valid governed project action"
    )
    next_parser.add_argument("project_id")
    next_parser.set_defaults(handler=cmd_next)

    archive_parser = subparsers.add_parser(
        "archive", help="Archive a project after exact-hash final approval"
    )
    archive_parser.add_argument("project_id")
    archive_parser.set_defaults(handler=cmd_archive)

    reconcile_parser = subparsers.add_parser(
        "reconcile",
        help="Reconcile filesystem artifacts, hashes and unresolved operations",
    )
    reconcile_parser.add_argument("project_id")
    reconcile_parser.set_defaults(handler=cmd_reconcile)

    animatic_parser = subparsers.add_parser(
        "produce-animatic",
        help="Render the deterministic animatic, timeline and draft captions",
    )
    animatic_parser.add_argument("project_id")
    animatic_parser.set_defaults(handler=cmd_produce_animatic)

    qa_animatic_parser = subparsers.add_parser(
        "qa-animatic",
        help="Run deterministic baseline QA on the animatic artifacts",
    )
    qa_animatic_parser.add_argument("project_id")
    qa_animatic_parser.set_defaults(handler=cmd_qa_animatic)

    frames_parser = subparsers.add_parser(
        "capture-frames",
        help="Capture a deterministic PNG frame sequence from the animatic",
    )
    frames_parser.add_argument("project_id")
    frames_parser.add_argument("--frames", type=int, default=300)
    frames_parser.add_argument("--fps", type=int, default=30)
    frames_parser.set_defaults(handler=cmd_capture_frames)

    encode_parser = subparsers.add_parser(
        "encode-animatic",
        help="Encode a silent H.264 master from the captured frame sequence",
    )
    encode_parser.add_argument("project_id")
    encode_parser.set_defaults(handler=cmd_encode_animatic)

    narration_parser = subparsers.add_parser(
        "synthesize-narration",
        help="Synthesize the approved voiceover with local Kokoro TTS",
    )
    narration_parser.add_argument("project_id")
    narration_parser.add_argument("--voice", default="af_heart")
    narration_parser.add_argument("--speed", type=float, default=1.0)
    narration_parser.set_defaults(handler=cmd_synthesize_narration)

    mux_parser = subparsers.add_parser(
        "mux-voiced",
        help="Mux the narration into the silent master with loudness normalization",
    )
    mux_parser.add_argument("project_id")
    mux_parser.set_defaults(handler=cmd_mux_voiced)

    voiced_qa_parser = subparsers.add_parser(
        "qa-voiced",
        help="Run technical and narration QA on the voiced master",
    )
    voiced_qa_parser.add_argument("project_id")
    voiced_qa_parser.set_defaults(handler=cmd_qa_voiced)

    capability_parser = subparsers.add_parser(
        "capabilities", help="Probe local tools without exposing credentials"
    )
    capability_parser.set_defaults(handler=cmd_capabilities)

    review_parser = subparsers.add_parser(
        "review-video",
        help="Run independent technical and OCR review on a finished video",
    )
    review_parser.add_argument("--video", required=True)
    review_parser.add_argument("--spec", required=True)
    review_parser.add_argument("--output", required=True)
    review_parser.set_defaults(handler=cmd_review_video)

    caption_parser = subparsers.add_parser(
        "caption-video",
        help="Mux an SRT sidecar as a soft subtitle track without re-encoding A/V",
    )
    caption_parser.add_argument("--video", required=True)
    caption_parser.add_argument("--srt", required=True)
    caption_parser.add_argument("--output", required=True)
    caption_parser.add_argument("--manifest")
    caption_parser.set_defaults(handler=cmd_caption_video)

    transcribe_parser = subparsers.add_parser(
        "transcribe-audio",
        help="Transcribe a local audio/video file with faster-whisper",
    )
    transcribe_parser.add_argument("--audio", required=True)
    transcribe_parser.add_argument("--output", required=True)
    transcribe_parser.add_argument("--model", default="tiny.en")
    transcribe_parser.add_argument("--download-root")
    transcribe_parser.add_argument(
        "--expected-file", help="Optional approved script for comparison"
    )
    transcribe_parser.set_defaults(handler=cmd_transcribe_audio)

    formats_parser = subparsers.add_parser(
        "derive-formats",
        help="Create 1:1, 16:9 and 4:5 derivatives from the 9:16 master",
    )
    formats_parser.add_argument("--video", required=True)
    formats_parser.add_argument("--output-dir", required=True)
    formats_parser.add_argument("--manifest")
    formats_parser.set_defaults(handler=cmd_derive_formats)

    delivery_parser = subparsers.add_parser(
        "delivery-packet",
        help="Assemble a publication-ready packet without publishing",
    )
    delivery_parser.add_argument("--master", required=True)
    delivery_parser.add_argument("--captioned", required=True)
    delivery_parser.add_argument("--captions", required=True)
    delivery_parser.add_argument("--formats-manifest", required=True)
    delivery_parser.add_argument("--metadata", required=True)
    delivery_parser.add_argument("--output-dir", required=True)
    delivery_parser.add_argument(
        "--formats-dir",
        help="Directory of 1:1/16:9/4:5 MP4 derivatives to include",
    )
    delivery_parser.set_defaults(handler=cmd_delivery_packet)

    preflight_parser = subparsers.add_parser(
        "preflight",
        help="Run read-only budget, grounding, approval, artifact, QA and recovery rules",
    )
    preflight_parser.add_argument("project_id")
    preflight_parser.set_defaults(handler=cmd_preflight)

    profile_parser = subparsers.add_parser(
        "evaluate-profile",
        help="Evaluate CR-01 through CR-15 for a creative profile",
    )
    profile_parser.add_argument("--file", required=True)
    profile_parser.add_argument("--output")
    profile_parser.set_defaults(handler=cmd_evaluate_profile)

    plan_parser = subparsers.add_parser(
        "creative-plan",
        help="Select a recipe and derive slot policies for a creative profile",
    )
    plan_parser.add_argument("--file", required=True)
    plan_parser.add_argument("--output")
    plan_parser.set_defaults(handler=cmd_creative_plan)

    record_plan_parser = subparsers.add_parser(
        "record-creative-plan",
        help="Record a versioned creative plan as a project artifact",
    )
    record_plan_parser.add_argument("project_id")
    record_plan_parser.add_argument("--file", required=True)
    record_plan_parser.set_defaults(handler=cmd_record_creative_plan)

    backup_parser = subparsers.add_parser(
        "backup",
        help="Create a hashed runtime backup of SQLite and project artifacts",
    )
    backup_parser.add_argument("--output", required=True)
    backup_parser.set_defaults(handler=cmd_backup)

    verify_backup_parser = subparsers.add_parser(
        "verify-backup",
        help="Verify backup hashes and SQLite integrity",
    )
    verify_backup_parser.add_argument("--backup", required=True)
    verify_backup_parser.set_defaults(handler=cmd_verify_backup)

    restore_parser = subparsers.add_parser(
        "restore-backup",
        help="Restore a verified backup into an empty runtime home",
    )
    restore_parser.add_argument("--backup", required=True)
    restore_parser.add_argument("--target", required=True)
    restore_parser.set_defaults(handler=cmd_restore_backup)

    drill_parser = subparsers.add_parser(
        "restore-drill",
        help="Restore a backup into a throwaway directory and verify it",
    )
    drill_parser.add_argument("--backup", required=True)
    drill_parser.add_argument("--workdir")
    drill_parser.set_defaults(handler=cmd_drill_restore)

    mirror_parser = subparsers.add_parser(
        "mirror-backup",
        help="Copy a verified backup to a second location and verify the copy",
    )
    mirror_parser.add_argument("--backup", required=True)
    mirror_parser.add_argument("--destination", required=True)
    mirror_parser.set_defaults(handler=cmd_mirror_backup)

    source_parser = subparsers.add_parser(
        "source-search",
        help="Search provider metadata and evaluate rights before import",
    )
    source_parser.add_argument(
        "--provider",
        choices=["openverse", "wikimedia", "internet_archive", "freesound"],
        default="openverse",
    )
    source_parser.add_argument("--query", required=True)
    source_parser.add_argument(
        "--media", choices=["image", "audio", "video"], default="image"
    )
    source_parser.add_argument("--limit", type=int, default=10)
    source_parser.add_argument("--page", type=int, default=1)
    source_parser.add_argument(
        "--use-class",
        choices=[item.value for item in AssetUseClass],
        default=AssetUseClass.COMMERCIAL.value,
    )
    source_parser.add_argument("--monetized", action="store_true")
    source_parser.add_argument("--advertising", action="store_true")
    source_parser.add_argument("--no-modification", action="store_true")
    source_parser.add_argument("--no-attribution", action="store_true")
    source_parser.add_argument("--output")
    source_parser.set_defaults(handler=cmd_source_search)

    route_parser = subparsers.add_parser(
        "route-provider",
        help="Select an approved provider profile without submitting paid work",
    )
    route_parser.add_argument("--file", required=True)
    route_parser.add_argument("--output")
    route_parser.set_defaults(handler=cmd_route_provider)

    spend_parser = subparsers.add_parser(
        "spend-reconcile",
        help="Reconcile external quotes, reservations and receipts against a ceiling",
    )
    spend_parser.add_argument("--file", required=True)
    spend_parser.add_argument("--output")
    spend_parser.set_defaults(handler=cmd_spend_reconcile)

    retention_parser = subparsers.add_parser(
        "retention-plan",
        help="Build a non-destructive retention plan",
    )
    retention_parser.add_argument("--file", required=True)
    retention_parser.add_argument("--output")
    retention_parser.set_defaults(handler=cmd_retention_plan)

    publication_parser = subparsers.add_parser(
        "publication-check",
        help="Evaluate a publication plan; never publishes",
    )
    publication_parser.add_argument("project_id")
    publication_parser.add_argument("--file", required=True)
    publication_parser.add_argument("--output")
    publication_parser.set_defaults(handler=cmd_publication_check)

    publication_package_parser = subparsers.add_parser(
        "publication-package",
        help="Write a manual-upload dispatch package; never publishes",
    )
    publication_package_parser.add_argument("project_id")
    publication_package_parser.add_argument("--file", required=True)
    publication_package_parser.add_argument("--output-dir", required=True)
    publication_package_parser.add_argument("--output")
    publication_package_parser.set_defaults(handler=cmd_publication_package)

    localization_parser = subparsers.add_parser(
        "localization-plan",
        help="Check caption readability and plan localisation targets",
    )
    localization_parser.add_argument("--srt", required=True)
    localization_parser.add_argument("--targets", default="hi,de")
    localization_parser.add_argument("--source-locale", default="en")
    localization_parser.add_argument("--output")
    localization_parser.set_defaults(handler=cmd_localization_plan)

    font_inventory_parser = subparsers.add_parser(
        "font-inventory",
        help="Hash and name every font file in a directory",
    )
    font_inventory_parser.add_argument("--root", required=True)
    font_inventory_parser.add_argument("--output")
    font_inventory_parser.set_defaults(handler=cmd_font_inventory)

    font_check_parser = subparsers.add_parser(
        "font-check",
        help="Check font files against exact-hash licence records",
    )
    font_check_parser.add_argument("--inventory", required=True)
    font_check_parser.add_argument("--licences", required=True)
    font_check_parser.add_argument("--output")
    font_check_parser.set_defaults(handler=cmd_font_check)

    external_qa_parser = subparsers.add_parser(
        "external-qa",
        help="Run QCTools and MediaConch on a finished video",
    )
    external_qa_parser.add_argument("--video", required=True)
    external_qa_parser.add_argument("--output-dir", required=True)
    external_qa_parser.add_argument("--qcli")
    external_qa_parser.add_argument("--mediaconch")
    external_qa_parser.add_argument("--output")
    external_qa_parser.set_defaults(handler=cmd_external_qa)

    experiment_parser = subparsers.add_parser(
        "experiment-report",
        help="Evaluate an A/B experiment with Wilson intervals and a z-test",
    )
    experiment_parser.add_argument("--file", required=True)
    experiment_parser.add_argument(
        "--metrics-csv", help="CSV export with one row per variant"
    )
    experiment_parser.add_argument("--output")
    experiment_parser.set_defaults(handler=cmd_experiment_report)

    image_parser = subparsers.add_parser(
        "generate-image",
        help="Generate one zero-cost Nano Banana image through the controller",
    )
    image_parser.add_argument("project_id")
    image_parser.add_argument("--profile-id", default="google-nano-banana-image")
    image_parser.add_argument("--prompt", required=True)
    image_parser.add_argument("--aspect-ratio", default="9:16")
    image_parser.add_argument("--image-size", choices=["1K", "2K", "4K"])
    image_parser.add_argument("--model", default="gemini-2.5-flash-image")
    image_parser.add_argument("--copy-to")
    image_parser.set_defaults(handler=cmd_generate_image)

    local_image_parser = subparsers.add_parser(
        "generate-local-image",
        help="Generate one zero-cost ComfyUI image through the controller",
    )
    local_image_parser.add_argument("project_id")
    local_image_parser.add_argument("--profile-id", default="comfyui-local-image")
    local_image_parser.add_argument("--prompt", required=True)
    local_image_parser.add_argument(
        "--negative-prompt",
        default="text, watermark, logo, letters, blurry, low quality",
    )
    local_image_parser.add_argument(
        "--checkpoint", default="v1-5-pruned-emaonly-fp16.safetensors"
    )
    local_image_parser.add_argument("--width", type=int, default=512)
    local_image_parser.add_argument("--height", type=int, default=512)
    local_image_parser.add_argument("--steps", type=int, default=12)
    local_image_parser.add_argument("--seed", type=int, default=0)
    local_image_parser.add_argument("--copy-to")
    local_image_parser.set_defaults(handler=cmd_generate_local_image)

    vertex_image_parser = subparsers.add_parser(
        "generate-vertex-image",
        help="Generate one ADC-authenticated Vertex Nano Banana image",
    )
    vertex_image_parser.add_argument("project_id")
    vertex_image_parser.add_argument(
        "--profile-id", default="google-vertex-nano-banana"
    )
    vertex_image_parser.add_argument("--prompt", required=True)
    vertex_image_parser.add_argument("--project")
    vertex_image_parser.add_argument("--location")
    vertex_image_parser.add_argument("--model", default="gemini-3.1-flash-image")
    vertex_image_parser.add_argument(
        "--image-size", choices=["512", "1K", "2K", "4K"], default="1K"
    )
    vertex_image_parser.add_argument("--aspect-ratio", default="16:9")
    vertex_image_parser.add_argument("--max-cost-cents", type=int, default=15)
    vertex_image_parser.add_argument("--copy-to")
    vertex_image_parser.set_defaults(handler=cmd_generate_vertex_image)

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

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
from .adapters.fal_avatar import AVATAR_MODELS, OMNIHUMAN_MODEL
from .adapters.google_image import GeminiImageAdapter
from .adapters.mock import MockGenerationAdapter
from .adapters.transcribe import (
    WhisperTranscriber,
    transcription_payload,
    write_transcript,
)
from .ai_edit import ComfyUIEditAdapter
from .analytics import (
    ExperimentDesign,
    VariantMetrics,
    evaluate_experiment,
    ingest_metrics_csv,
)
from .assets import AssetCriteria
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
    SlotAssetRole,
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
from .fal import FalClient, FalEditAdapter, FalVisionJudge
from .fonts import FontInventory, FontLicence, check_fonts, inventory_fonts
from .formats import FormatDerivationAdapter, format_output_name
from .freesound import FreesoundClient
from .internet_archive import InternetArchiveClient
from .localization import plan_localization
from .models import (
    ABExperimentCreate,
    ABVariantBind,
    ApprovalRequest,
    AssetCandidate,
    AssetUseContext,
    AudioCueRequest,
    BudgetPlanApprovalCreate,
    BudgetPlanCreate,
    BudgetRaiseCreate,
    CapabilityDiscoveryCreate,
    CreativeElementCreate,
    CreativeReviewCreate,
    DeliverableCreate,
    EffectsPlanCreate,
    ExecutionReceiptCreate,
    FactSetCreate,
    FlowStepCreate,
    GroundingBundleCreate,
    IntentMatrixCreate,
    JobRequest,
    Money,
    MotionLevelCreate,
    OverageReconcileCreate,
    PilotContract,
    ProductionPlanCreate,
    ProjectCreate,
    ProviderModelProfileCreate,
    PublicationAuthorizationCreate,
    QualityContractCreate,
    ScriptApproval,
    ScriptCreate,
    SegmentCreate,
    ShotElementBindingCreate,
    ShotMotionRequest,
    ShotReferenceCreate,
    SlotCreate,
    SlotPickCreate,
    SourcingCandidateCreate,
    SourcingDecisionCreate,
    StageCritiqueCreate,
    StudioReadinessReport,
    StudioResultPacketImport,
    StudioSpendApprovalCreate,
    StudioWorkPacketRequest,
    TextBriefRequest,
    ToolOperationRequest,
    ToolProfileCreate,
    VarianceDecisionCreate,
    WorkableBriefCreate,
)
from .openverse import OpenverseClient, default_context
from .pexels import PexelsClient
from .preference import (
    PreferencePolicy,
    RouteCandidate,
    policy_summary,
    select_route,
)
from .preflight import run_preflight
from .production import ShotProductionCreate
from .publication import (
    CHANNELS,
    PublicationAuthorization,
    PublicationRequest,
    build_dispatch_package,
    check_publication,
)
from .recipes import build_plan
from .reconciliation import reconcile_project
from .retention import RetentionItem, RetentionPolicy, plan_retention
from .review import load_spec, review_video, write_report
from .review_policy import CampaignRisk, ReviewPolicy
from .routing import RouteRequest, route_profiles
from .sourcing import build_owned_candidate
from .unsplash import UnsplashClient
from .utils import sha256_file, sha256_json
from .wikimedia import WikimediaClient


def _json(value: Any) -> str:
    # ASCII-escaped output is portable across Windows consoles; the value is
    # still valid JSON, so consumers decode it normally.
    return json.dumps(value, indent=2, ensure_ascii=True, default=str)


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
        args.project_id,
        voice=args.voice,
        speed=args.speed,
        script=Path(args.script_file).read_text(encoding="utf-8-sig")
        if args.script_file
        else None,
        route=args.route,
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
    from .captions import build_srt_from_words

    download_root = (
        Path(args.download_root).expanduser().resolve()
        if args.download_root
        else Path.cwd() / "workspace" / "tools" / "whisper"
    )
    transcriber = WhisperTranscriber(
        model_size=args.model,
        download_root=download_root,
        word_timestamps=bool(args.srt),
    )
    result = transcriber.transcribe(args.audio)
    expected_text = (
        Path(args.expected_file).read_text(encoding="utf-8").strip()
        if args.expected_file
        else None
    )
    payload = transcription_payload(result, expected_text=expected_text)
    write_transcript(payload, args.output)
    if args.srt:
        words = [
            word for segment in payload["segments"] for word in segment.get("words", ())
        ]
        if not words:
            raise ValueError(
                "No word timings were produced; captions need word_timestamps"
            )
        srt = build_srt_from_words(words, locale=args.locale or result.language)
        srt_path = Path(args.srt).expanduser().resolve()
        srt_path.parent.mkdir(parents=True, exist_ok=True)
        srt_path.write_text(srt, encoding="utf-8")
        payload["srt"] = {
            "path": str(srt_path),
            "cues": len([block for block in srt.split("\n\n") if block.strip()]),
        }
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
        credits=getattr(args, "credits", None),
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
    if args.orientation and args.provider not in {"unsplash", "pexels"}:
        raise ValueError("--orientation applies to Unsplash and Pexels only")
    unsplash_only = bool(args.content_filter or args.order_by or args.selected_asset_id)
    if unsplash_only and args.provider != "unsplash":
        raise ValueError(
            "--content-filter, --order-by and --selected-asset-id apply to Unsplash only"
        )
    if args.size and args.provider != "pexels":
        raise ValueError("--size applies to Pexels only")
    quota = PROVIDER_QUOTAS.get(args.provider)
    if quota:
        _controller(args).reserve_provider_request(
            args.provider, "search", limit=quota[0], window_seconds=quota[1]
        )
    if args.provider == "pexels":
        payload = PexelsClient().search_with_eligibility(
            args.query,
            context,
            media_type=media_type,
            limit=args.limit,
            page=args.page,
            orientation=args.orientation,
            size=args.size,
        )
    elif args.provider == "unsplash":
        if media_type != AssetMediaType.IMAGE:
            raise ValueError("Unsplash supports image search only")
        payload = UnsplashClient().search_with_eligibility(
            args.query,
            context,
            limit=args.limit,
            page=args.page,
            orientation=args.orientation,
            content_filter=args.content_filter,
            order_by=args.order_by,
            selected_asset_id=args.selected_asset_id,
        )
    elif args.provider == "openverse":
        payload = OpenverseClient().search_with_eligibility(
            args.query,
            context,
            media_type=media_type,
            limit=args.limit,
            page=args.page,
            license_type=args.license_type,
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


PROVIDER_QUOTAS: dict[str, tuple[int, int]] = {
    "pexels": (200, 3600),
    "unsplash": (50, 3600),
}


def _asset_context(payload: dict[str, Any]) -> AssetUseContext:
    return AssetUseContext(
        use_class=AssetUseClass(
            payload.get("use_class", AssetUseClass.COMMERCIAL.value)
        ),
        monetized=bool(payload.get("monetized", False)),
        advertising=bool(payload.get("advertising", False)),
        modification_planned=bool(payload.get("modification_planned", True)),
        attribution_can_be_delivered=bool(
            payload.get("attribution_can_be_delivered", True)
        ),
    )


def _candidate_from(payload: dict[str, Any]) -> AssetCandidate:
    inner = payload.get("candidate", payload)
    return AssetCandidate.model_validate(inner)


def cmd_finalize_asset(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    context = (
        _asset_context(payload["use_context"]) if "use_context" in payload else None
    )
    criteria = AssetCriteria.model_validate(payload.get("criteria", {}))
    decided_by = payload.get("decided_by") or args.decided_by
    return _controller(args).finalize_asset(
        args.project_id,
        payload["slot_id"],
        artifact_id=payload["artifact_id"],
        criteria=criteria,
        context=context,
        decided_by=decided_by,
    )


def cmd_slot_coverage(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).slot_coverage(args.project_id)


def cmd_capture_evidence(args: argparse.Namespace) -> dict[str, Any]:
    candidate = _candidate_from(_load_json(args.candidate))
    result = _controller(args).capture_asset_evidence(args.project_id, candidate)
    result["candidate"] = result["candidate"].model_dump(mode="json")
    return _write_json(args.output, result)


def cmd_download_asset(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.candidate)
    candidate = _candidate_from(payload)
    context = (
        _asset_context(payload["use_context"]) if "use_context" in payload else None
    )
    result = _controller(args).download_stock_asset(
        args.project_id,
        candidate=candidate,
        context=context,
        download_event_url=payload.get("download_event_url"),
        filename=payload.get("filename"),
    )
    return _write_json(args.output, result)


def _comfy_adapter(payload: dict[str, Any]) -> ComfyUIEditAdapter:
    return ComfyUIEditAdapter(
        base_url=str(payload.get("base_url", "http://127.0.0.1:8188")),
        allowed_checkpoints=tuple(payload.get("allowed_checkpoints", ())),
        allowed_upscale_models=tuple(payload.get("allowed_upscale_models", ())),
    )


def _edit_adapter(payload: dict[str, Any]) -> Any:
    """Choose the execution surface: fal direct first, local ComfyUI second."""

    if payload.get("provider", "fal") == "comfy":
        return _comfy_adapter(payload)
    models = {key: tuple(value) for key, value in (payload.get("models") or {}).items()}
    return FalEditAdapter(FalClient(), allowed_models=models)


def cmd_judge_asset(args: argparse.Namespace) -> dict[str, Any]:
    policy = (
        ReviewPolicy.model_validate(_load_json(args.policy)) if args.policy else None
    )
    judge = (
        FalVisionJudge(FalClient(), model=args.model)
        if args.provider == "fal"
        else None
    )
    risk = CampaignRisk(
        monetized=args.monetized,
        advertising=args.advertising,
        has_recognizable_people=args.people,
        makes_factual_claims=args.claims,
    )
    return _write_json(
        args.output,
        _controller(args).judge_asset(
            args.project_id,
            args.artifact_id,
            model=args.model,
            intent=args.intent,
            policy=policy,
            technical_ok=not args.technical_failed,
            risk=risk,
            judge=judge,
        ),
    )


def cmd_create_revision(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    budget = (
        Money(
            amount=Decimal(str(payload["max_budget"])),
            currency=payload.get("currency", "USD"),
        )
        if payload.get("max_budget") is not None
        else None
    )
    duration = (
        Decimal(str(payload["duration_seconds"]))
        if payload.get("duration_seconds") is not None
        else None
    )
    revision = _controller(args).create_revision(
        args.parent_id,
        name=payload["name"],
        objective=payload["objective"],
        audience=payload.get("audience"),
        output_format=payload.get("output_format"),
        language=payload.get("language"),
        duration_seconds=duration,
        max_budget=budget,
        change_request_id=payload.get("change_request_id"),
        change_list=tuple(payload.get("change_list", ())),
        requested_by=payload.get("requested_by", "system"),
    )
    return revision.model_dump(mode="json")


def cmd_revision_lineage(args: argparse.Namespace) -> dict[str, Any]:
    return {"lineage": _controller(args).revision_lineage(args.project_id)}


def cmd_record_scene_plan(args: argparse.Namespace) -> dict[str, Any]:
    markdown = Path(args.markdown_file).read_text(encoding="utf-8")
    return _controller(args).record_scene_plan(args.project_id, markdown)


def cmd_scene_plan(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).scene_plan(args.project_id) or {"plan": None}


def cmd_record_workable_brief(args: argparse.Namespace) -> dict[str, Any]:
    request = WorkableBriefCreate.model_validate(_load_json(args.file))
    return _controller(args).record_workable_brief(args.project_id, request)


def cmd_workable_brief(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).workable_brief(args.project_id) or {"brief": None}


def cmd_record_creative_element(args: argparse.Namespace) -> dict[str, Any]:
    request = CreativeElementCreate.model_validate(_load_json(args.file))
    return _controller(args).record_creative_element(args.project_id, request)


def cmd_list_creative_elements(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "elements": _controller(args).list_creative_elements(
            args.project_id, current_only=not args.include_history
        )
    }


def cmd_bind_creative_element(args: argparse.Namespace) -> dict[str, Any]:
    request = ShotElementBindingCreate.model_validate(_load_json(args.file))
    return _controller(args).bind_element_to_shot(args.project_id, request)


def cmd_bind_elements(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).bind_elements_to_slots(args.project_id)


def cmd_derive_element_slots(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).derive_element_slots(args.project_id)


def cmd_bind_reference(args: argparse.Namespace) -> dict[str, Any]:
    request = ShotReferenceCreate.model_validate(_load_json(args.file))
    return _controller(args).bind_shot_reference(args.project_id, request)


def cmd_list_references(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "references": _controller(args).shot_references(
            args.project_id, shot_id=args.shot_id
        )
    }


def cmd_prepare_studio_work_packet(args: argparse.Namespace) -> dict[str, Any]:
    request = StudioWorkPacketRequest.model_validate(_load_json(args.file))
    if args.spend_approval:
        request = request.model_copy(update={"spend_approval_id": args.spend_approval})
    return _controller(args).prepare_studio_work_packet(args.project_id, request)


def cmd_approve_studio_spend(args: argparse.Namespace) -> dict[str, Any]:
    request = StudioSpendApprovalCreate.model_validate(_load_json(args.file))
    return _controller(args).approve_studio_spend(args.project_id, request)


def cmd_plan_budget(args: argparse.Namespace) -> dict[str, Any]:
    request = BudgetPlanCreate.model_validate(_load_json(args.file))
    return _controller(args).plan_creative_budget(args.project_id, request)


def cmd_budget_plan(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).budget_plan(args.project_id) or {"plan": None}


def cmd_approve_budget_plan(args: argparse.Namespace) -> dict[str, Any]:
    request = BudgetPlanApprovalCreate.model_validate(_load_json(args.file))
    return _controller(args).approve_budget_plan(args.project_id, request)


def cmd_budget_summary(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).budget_summary(args.project_id)


def cmd_settle_budget_item(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).settle_budget_item(
        args.project_id,
        args.job_id,
        actual_cost=Money(amount=Decimal(args.amount), currency=args.currency),
        requested_by=args.requested_by,
        reason=args.reason,
    )


def cmd_approve_variance(args: argparse.Namespace) -> dict[str, Any]:
    request = VarianceDecisionCreate.model_validate(_load_json(args.file))
    return _controller(args).decide_variance(args.project_id, request)


def cmd_pending_variances(args: argparse.Namespace) -> dict[str, Any]:
    return {"requests": _controller(args).variance_requests(args.project_id)}


def cmd_asset_costs(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).asset_costs(args.project_id)


def cmd_plan_from_text(args: argparse.Namespace) -> dict[str, Any]:
    request = TextBriefRequest.model_validate(_load_json(args.file))
    if getattr(args, "persist_slots", False):
        request = request.model_copy(update={"persist_slots": True})
    return _controller(args).plan_from_text(args.project_id, request)


def cmd_studio_status(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).studio_status()


def cmd_studio_check(args: argparse.Namespace) -> dict[str, Any]:
    report = StudioReadinessReport.model_validate(_load_json(args.file))
    return _controller(args).record_studio_readiness(report)


def cmd_studio_run(args: argparse.Namespace) -> dict[str, Any]:
    from .studio_gateway import DeterministicStudioTransport, SpoolStudioTransport

    if args.transport == "deterministic":
        workdir = Path(args.spool or (Path(args.home) / "studio-work"))
        transport = DeterministicStudioTransport(workdir=workdir)
    else:
        if not args.spool:
            raise ValueError("--spool is required for the spool transport")
        transport = SpoolStudioTransport(args.spool)
    return _controller(args).run_studio_production(
        args.project_id,
        args.operation_id,
        transport,
        max_polls=args.max_polls,
        requested_by=args.requested_by,
    )


def cmd_caption_drift(args: argparse.Namespace) -> dict[str, Any]:
    from .captions import caption_drift, parse_srt

    plan = parse_srt(Path(args.plan).expanduser().read_text(encoding="utf-8"))
    spoken = parse_srt(Path(args.spoken).expanduser().read_text(encoding="utf-8"))
    if not plan or not spoken:
        raise ValueError("Both SRT files need at least one cue")
    return caption_drift(plan, spoken, tolerance_seconds=args.tolerance) | {
        "plan_cues": len(plan),
        "spoken_cues": len(spoken),
    }


def cmd_draft_metadata(args: argparse.Namespace) -> dict[str, Any]:
    from .metadata import draft_metadata

    project = _controller(args).repository.get_project(args.project_id)
    draft = draft_metadata(
        name=project.name,
        objective=project.objective,
        channel=args.channel,
        claims=tuple(args.claim or ()),
        cta=args.cta,
        hashtags=tuple(args.hashtag or ()),
        locale=project.language,
    )
    if args.output:
        path = Path(args.output).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        draft = {**draft, "output": str(path)}
    return draft


def cmd_void_job(args: argparse.Namespace) -> dict[str, Any]:
    job = _controller(args).void_job(args.job_id, reason=args.reason)
    return {"job": job.model_dump(mode="json")}


def cmd_studio_production(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).studio_production(args.operation_id) or {
        "production": None
    }


def cmd_mux_narration(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).mux_narration(
        args.project_id,
        video_artifact_id=args.video,
        audio_artifact_id=args.audio,
    )


def cmd_export_captions(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).export_captions(
        args.project_id,
        locale=args.locale,
        source=args.source,
        narration_artifact_id=args.narration,
        pad_to_seconds=args.pad_to,
        max_cue_seconds=args.max_cue,
    )


def cmd_qa_render(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).qa_render(
        args.project_id,
        artifact_id=args.artifact_id,
        expected_duration=args.duration,
        expected_width=args.width,
        expected_height=args.height,
        expected_fps=args.fps,
        expect_audio=not args.no_audio,
        captions_artifact_id=args.captions,
        max_true_peak_dbtp=args.max_true_peak,
    )


def cmd_burn_captions(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).burn_captions(
        args.project_id,
        video_artifact_id=args.video,
        captions_artifact_id=args.captions,
        mode="hard" if args.hard else "soft",
    )


def cmd_render_composition(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).render_composition(
        args.project_id,
        composition_artifact_id=args.composition,
        fps=args.fps,
        width=args.width,
        height=args.height,
        renderer=args.renderer,
    )


def cmd_author_composition(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).author_composition(
        args.project_id,
        width=args.width,
        height=args.height,
        title=args.title,
        embed_layers=getattr(args, "embed_layers", True),
    )


def cmd_assemble_film(args: argparse.Namespace) -> dict[str, Any]:
    beat_grid = None
    if args.beat_grid:
        payload = _load_json(args.beat_grid)
        beats = payload.get("beats") if isinstance(payload, dict) else payload
        if not isinstance(beats, list) or not beats:
            raise ValueError("A beat grid file needs a non-empty 'beats' list")
        beat_grid = tuple(float(beat) for beat in beats)
    return _controller(args).assemble_film(
        args.project_id,
        transition=args.transition,
        dissolve_seconds=Decimal(args.dissolve),
        fps=args.fps,
        width=args.width,
        height=args.height,
        beat_grid=beat_grid,
        beat_tolerance=args.beat_tolerance,
    )


def cmd_detect_beats(args: argparse.Namespace) -> dict[str, Any]:
    from .beats import detect_beats

    result = detect_beats(args.audio)
    if args.output:
        path = Path(args.output).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        result = {**result, "output": str(path)}
    return result


def cmd_motion_takes(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).plan_motion_takes(
        args.project_id,
        family=args.model,
        seconds=args.seconds,
        resolution=args.resolution,
        max_takes=args.max_takes,
    )


def cmd_draft_script(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).draft_script(
        args.project_id,
        duration_seconds=args.duration,
        max_words=args.max_words,
        model=args.model,
        script=Path(args.script_file).read_text(encoding="utf-8-sig")
        if args.script_file
        else None,
    )


def cmd_plan_shot_motion(args: argparse.Namespace) -> dict[str, Any]:
    request = ShotMotionRequest.model_validate(_load_json(args.file))
    return _controller(args).plan_shot_motion(args.project_id, request)


def cmd_prepare_shot_motion(args: argparse.Namespace) -> dict[str, Any]:
    request = ShotMotionRequest.model_validate(_load_json(args.file))
    return _controller(args).prepare_shot_motion(args.project_id, request)


def cmd_import_shot_motion(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).import_shot_motion(
        args.project_id,
        job_id=payload["job_id"],
        source_path=payload["source_path"],
        content_hash=payload["content_hash"],
        actual_cost_minor=payload.get("actual_cost_minor"),
        output_relative_path=payload.get("output_relative_path"),
        shot_id=payload.get("shot_id"),
    )


def cmd_raise_budget(args: argparse.Namespace) -> dict[str, Any]:
    request = BudgetRaiseCreate.model_validate(_load_json(args.file))
    return _controller(args).raise_project_budget(args.project_id, request)


def cmd_reconcile_overage(args: argparse.Namespace) -> dict[str, Any]:
    request = OverageReconcileCreate.model_validate(_load_json(args.file))
    return _controller(args).reconcile_overage(args.project_id, request)


def cmd_generate_asset(args: argparse.Namespace) -> dict[str, Any]:
    from .adapters.fal_image import (
        DEFAULT_IMAGE_MODEL,
        FalImageAdapter,
        dry_run_payload,
    )
    from .adapters.mock import MockGenerationAdapter
    from .fal import FalClient
    from .operations import operation

    controller = _controller(args)
    if args.dry_run:
        return dry_run_payload(
            args.prompt or "",
            aspect_ratio=args.aspect_ratio,
            model=args.model or DEFAULT_IMAGE_MODEL,
        )
    plan = controller.repository.get_budget_plan(args.plan)
    item = next(
        (entry for entry in plan["items"] if entry["stable_key"] == args.item), None
    )
    if not item:
        raise NotFoundError(f"Budget item not found in plan: {args.item}")
    if args.adapter == "fal":
        operation("image.generate").validate(
            "fal.image", model=args.model or DEFAULT_IMAGE_MODEL
        )
        if (
            item["provider"] != "fal"
            or item["operation"] != "image.generate"
            or plan["currency"] != "USD"
        ):
            raise ValueError(
                "fal image generation requires a fal/image.generate USD budget item"
            )
    cost = Money.from_minor(item["estimated_minor"], plan["currency"])
    request = JobRequest(
        provider=item["provider"],
        endpoint=args.endpoint or f"{item['provider']}://{item['operation']}",
        operation=item["operation"],
        idempotency_key=args.key or f"plan:{args.plan}:{args.item}",
        estimated_cost=cost,
        parameters={
            "prompt": args.prompt,
            "aspect_ratio": args.aspect_ratio,
            "model": args.model,
        },
    )
    if args.adapter == "mock":
        adapter = MockGenerationAdapter()
    else:
        adapter = FalImageAdapter(
            FalClient(),
            model=args.model or DEFAULT_IMAGE_MODEL,
        )
    suffix = "png" if args.adapter == "fal" else "json"
    job, artifact = controller.run_adapter_once(
        args.project_id,
        request,
        adapter,
        artifact_path=f"artifacts/assets/{args.item}.{suffix}",
        budget_plan_item=(args.plan, args.item),
    )
    return {
        "job": job.model_dump(mode="json"),
        "artifact": artifact,
        "asset_costs": controller.asset_costs(args.project_id),
    }


def cmd_enhance_still(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).enhance_still(args.project_id, **payload)


def cmd_generate_avatar(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).generate_avatar(
        args.project_id,
        source_artifact_id=args.source,
        audio_artifact_id=args.audio,
        plan_id=args.plan,
        item_key=args.item,
        model=args.model,
        likeness_consent=args.consent,
        resolution=args.resolution,
        turbo_mode=not args.no_turbo,
        prompt=args.prompt,
        sync_mode=args.sync_mode,
    )


def cmd_fal_check(args: argparse.Namespace) -> dict[str, Any]:
    from .fal import FalClient

    return FalClient().probe_credentials()


def cmd_operations(args: argparse.Namespace) -> list[dict[str, Any]]:
    from .operations import describe_operations

    return describe_operations()


def cmd_studio_work_packet(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).studio_work_packet(args.operation_id)


def cmd_import_studio_result(args: argparse.Namespace) -> dict[str, Any]:
    request = StudioResultPacketImport.model_validate(_load_json(args.file))
    return _controller(args).import_studio_result(request)


def cmd_ai_edit_asset(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    mask = Path(payload["mask_path"]).read_bytes() if payload.get("mask_path") else None
    return _write_json(
        args.output,
        _controller(args).ai_edit_asset(
            args.project_id,
            slot_id=payload["slot_id"],
            artifact_id=payload["artifact_id"],
            prompt=payload["prompt"],
            mask=mask,
            checkpoint=payload.get("model") or payload.get("checkpoint"),
            seed=int(payload.get("seed", 0)),
            steps=int(payload.get("steps", 20)),
            denoise=float(payload.get("denoise", 0.55)),
            adapter=_edit_adapter(payload),
        ),
    )


def cmd_upscale_asset(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _write_json(
        args.output,
        _controller(args).generative_upscale_asset(
            args.project_id,
            slot_id=payload["slot_id"],
            artifact_id=payload["artifact_id"],
            model_name=payload.get("model_name") or payload.get("model"),
            adapter=_edit_adapter(payload),
        ),
    )


def cmd_import_owned_asset(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    controller = _controller(args)
    candidate = build_owned_candidate(
        asset_id=payload["asset_id"],
        media_type=AssetMediaType(payload.get("media_type", "image")),
        creator_name=payload["creator_name"],
        authorisation_url=payload["authorisation_url"],
    )
    captured = controller.capture_asset_evidence(args.project_id, candidate)
    context = (
        _asset_context(payload["use_context"])
        if "use_context" in payload
        else controller.asset_use_context(args.project_id)
    )
    artifact = controller.import_sourced_original(
        args.project_id,
        args.source,
        candidate=captured["candidate"],
        context=context,
    )
    return _write_json(
        args.output,
        {
            "artifact": artifact,
            "candidate": captured["candidate"].model_dump(mode="json"),
            "evidence": captured["evidence"],
            "evidence_hash": captured["evidence_hash"],
        },
    )


def cmd_assign_slot_asset(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    context = (
        _asset_context(payload["use_context"]) if "use_context" in payload else None
    )
    criteria = AssetCriteria.model_validate(payload.get("criteria", {}))
    decided_by = payload.get("decided_by") or args.decided_by
    return _controller(args).assign_slot_asset(
        args.project_id,
        payload["slot_id"],
        artifact_id=payload["artifact_id"],
        role=SlotAssetRole(payload["role"]),
        criteria=criteria,
        context=context,
        decided_by=decided_by,
    )


def cmd_assign_audio_bed(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    context = (
        _asset_context(payload["use_context"]) if "use_context" in payload else None
    )
    criteria = AssetCriteria.model_validate(payload.get("criteria", {}))
    return _controller(args).assign_audio_bed(
        args.project_id,
        artifact_id=payload["artifact_id"],
        role=SlotAssetRole(payload.get("role", "primary")),
        criteria=criteria,
        context=context,
        decided_by=payload.get("decided_by") or args.decided_by,
    )


def cmd_audio_coverage(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).audio_coverage(args.project_id)


def cmd_assign_audio_cue(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    request = AudioCueRequest.model_validate(payload)
    return _controller(args).assign_audio_cue(args.project_id, request)


def cmd_audio_credits(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).audio_credits(args.project_id)


def cmd_promote_backup(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).promote_backup(
        args.project_id,
        args.slot_id,
        decided_by=args.decided_by,
        reason=args.reason,
    )


def cmd_add_slot(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).add_slot(
        args.project_id, SlotCreate.model_validate(payload)
    )


def cmd_record_sourcing_decision(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).record_sourcing_decision(
        args.project_id, SourcingDecisionCreate.model_validate(payload)
    )


def cmd_record_sourcing_candidate(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).record_sourcing_candidate(
        args.project_id, SourcingCandidateCreate.model_validate(payload)
    )


def cmd_record_effects_plan(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).record_effects_plan(
        args.project_id, EffectsPlanCreate.model_validate(payload)
    )


def cmd_effects_plan(args: argparse.Namespace) -> dict[str, Any]:
    return {"plan": _controller(args).current_effects_plan(args.project_id)}


def cmd_plan_production(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).plan_production(args.project_id)


def cmd_record_shot_production(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).record_shot_production(
        args.project_id, ShotProductionCreate.model_validate(payload)
    )


def cmd_record_script(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).record_script(
        args.project_id, ScriptCreate.model_validate(payload)
    )


def cmd_approve_script(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).approve_script(
        args.project_id,
        args.script_id,
        ScriptApproval(approved_by=args.approved_by),
    )


def cmd_current_script(args: argparse.Namespace) -> dict[str, Any]:
    return {"script": _controller(args).current_script(args.project_id)}


def cmd_delivery_metadata(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).write_delivery_metadata(args.project_id)


def cmd_style_query_plan(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).write_style_query_plan(args.project_id, args.slot_id)


def cmd_quality_bar(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).quality_bar(args.project_id, artifact_id=args.artifact_id)


def cmd_production_record(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).production_record(args.project_id)


def cmd_change_request(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).change_request(args.project_id, output_dir=args.output)


def cmd_submit_change_request(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).submit_change_request(
        args.project_id, path=args.file, submitted_by=args.submitted_by
    )


def cmd_decide_change_request(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).decide_change_request(
        args.request_id,
        decision=args.decision,
        decided_by=args.by,
        reason=args.reason,
    )


def cmd_change_request_status(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).change_request_status(args.project_id, args.request)


def cmd_auto_critique(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).auto_critique(
        args.project_id, args.stage, artifact_id=args.artifact
    )


def cmd_record_critique(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).record_stage_critique(
        args.project_id, StageCritiqueCreate.model_validate(payload)
    )


def cmd_close_corrective_action(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).close_corrective_action(
        args.action_id,
        closed_by=args.by,
        evidence=tuple(args.evidence),
        result=args.result,
    )


def cmd_critique_status(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).critique_summary(
        args.project_id, artifact_id=args.artifact
    )


def cmd_record_production_plan(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).record_production_plan(
        args.project_id, ProductionPlanCreate.model_validate(payload)
    )


def cmd_generate_production_plan(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).generate_production_plan(args.project_id)


def cmd_production_plan(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).current_execution_plan(args.project_id)


def cmd_production_plan_status(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).production_plan_status(args.project_id)


def cmd_quality_contract(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).record_quality_contract(
        args.project_id, QualityContractCreate.model_validate(payload)
    )


def cmd_intent_matrix(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).record_intent_matrix(
        args.project_id, IntentMatrixCreate.model_validate(payload)
    )


def cmd_creative_review(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).record_creative_review(
        args.project_id, CreativeReviewCreate.model_validate(payload)
    )


def cmd_quality_status(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).quality_status(args.project_id, artifact_id=args.artifact)


def cmd_review_packet(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).build_review_packet(
        args.project_id, artifact_id=args.artifact
    )


def cmd_record_capabilities(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).record_capability_discovery(
        args.project_id, CapabilityDiscoveryCreate.model_validate(payload)
    )


def cmd_openmontage_capabilities(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).current_capability_discovery(args.project_id)


def cmd_route_capability(args: argparse.Namespace) -> dict[str, Any]:
    return (
        _controller(args)
        .route_capability(args.project_id, args.capability_id)
        .model_dump(mode="json")
    )


def cmd_declare_ab(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).create_ab_experiment(
        args.project_id, ABExperimentCreate.model_validate(payload)
    )


def cmd_bind_ab_variant(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).bind_ab_variant(
        args.project_id, ABVariantBind.model_validate(payload)
    )


def cmd_ab_status(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).ab_status(args.project_id)


def cmd_ab_delivery(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).build_ab_delivery_package(
        args.project_id, args.output, experiment_id=args.experiment
    )


def cmd_record_motion_level(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).record_motion_level(
        args.project_id, MotionLevelCreate.model_validate(payload)
    )


def cmd_motion_levels(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).motion_levels(args.project_id)


def cmd_sourcing_manifest(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).write_sourcing_manifest(args.project_id)


def cmd_sourcing_candidates(args: argparse.Namespace) -> dict[str, Any]:
    controller = _controller(args)
    return {
        "candidates": controller.sourcing_candidates(args.project_id, args.slot_id),
        "picks": controller.slot_pick_summary(args.project_id),
    }


def cmd_pick_slot_assets(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).pick_slot_assets(
        args.project_id, SlotPickCreate.model_validate(payload)
    )


def cmd_sourcing_decisions(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "decisions": _controller(args).sourcing_decisions(args.project_id, args.slot_id)
    }


def cmd_record_flow_step(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _controller(args).record_flow_step(
        args.project_id, FlowStepCreate.model_validate(payload)
    )


def cmd_flow_log(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "completeness": _controller(args).flow_completeness(args.project_id),
        "steps": _controller(args).flow_log(args.project_id, args.step),
    }


def cmd_set_use_context(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    context = _asset_context(payload)
    return _controller(args).set_asset_use_context(args.project_id, context)


def cmd_analyze_asset(args: argparse.Namespace) -> dict[str, Any]:
    return _write_json(
        args.output,
        _controller(args).analyze_asset(
            args.project_id,
            args.artifact_id,
            required_width=args.width,
            required_height=args.height,
            required_duration_seconds=args.duration,
        ),
    )


def cmd_grade_asset(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).grade_asset(
        args.project_id,
        args.artifact_id,
        lut_path=args.lut,
        brightness=args.brightness,
        saturation=args.saturation,
        contrast=args.contrast,
        gamma=args.gamma,
    )


def cmd_key_asset(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).key_asset(
        args.project_id,
        background_artifact_id=args.background,
        foreground_artifact_id=args.foreground,
        key_color=args.key_color,
        similarity=args.similarity,
        blend=args.blend,
    )


def cmd_change_speed(args: argparse.Namespace) -> dict[str, Any]:
    return _controller(args).change_asset_speed(
        args.project_id, args.artifact_id, speed=args.speed
    )


def cmd_correct_asset(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _write_json(
        args.output,
        _controller(args).correct_asset(
            args.project_id,
            slot_id=payload["slot_id"],
            artifact_id=payload["artifact_id"],
            operations=list(payload["operations"]),
        ),
    )


def cmd_compose_assets(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load_json(args.file)
    return _write_json(
        args.output,
        _controller(args).compose_assets(
            args.project_id,
            background_id=payload["background_id"],
            overlay_id=payload["overlay_id"],
            x=int(payload.get("x", 0)),
            y=int(payload.get("y", 0)),
            width=payload.get("width"),
            height=payload.get("height"),
            opacity=float(payload.get("opacity", 1.0)),
        ),
    )


def cmd_reserve_spend(args: argparse.Namespace) -> dict[str, Any]:
    result = _controller(args).reserve_provider_spend(
        args.provider, amount_usd=args.amount_usd, purpose=args.purpose
    )
    for message in result["notifications"]:
        print(f"NOTICE: {message}", file=sys.stderr)
    return _write_json(args.output, result)


def cmd_spend_summary(args: argparse.Namespace) -> dict[str, Any]:
    return _write_json(
        args.output, _controller(args).provider_spend_summary(args.provider)
    )


def cmd_select_route(args: argparse.Namespace) -> dict[str, Any]:
    policy = (
        PreferencePolicy.load(args.policy) if args.policy else PreferencePolicy.load()
    )
    if args.show_policy:
        return policy_summary(policy)
    payload = _load_json(args.candidates)
    raw = payload.get("candidates", payload) if isinstance(payload, dict) else payload
    candidates = [RouteCandidate.model_validate(item) for item in raw]
    remaining = Decimal(args.budget_remaining) if args.budget_remaining else None
    selection = select_route(
        args.capability, candidates, policy, budget_usd_remaining=remaining
    )
    return _write_json(args.output, selection.model_dump(mode="json"))


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


def _publication_authorization(
    controller: Any, project_id: str, payload: dict[str, Any], channel: str
) -> PublicationAuthorization:
    if payload.get("authorization"):
        return PublicationAuthorization.model_validate(payload["authorization"])
    return controller.stored_publication_authorization(project_id, channel)


def cmd_authorize_publication(args: argparse.Namespace) -> dict[str, Any]:
    request = PublicationAuthorizationCreate.model_validate(_load_json(args.file))
    return _controller(args).authorize_publication(args.project_id, request)


def cmd_publication_check(args: argparse.Namespace) -> dict[str, Any]:
    controller = _controller(args)
    payload = _load_json(args.file)
    request = PublicationRequest.model_validate(payload["request"])
    authorization = _publication_authorization(
        controller, args.project_id, payload, request.channel
    )
    plan = check_publication(controller, args.project_id, request, authorization)
    return _write_json(args.output, plan.model_dump(mode="json"))


def cmd_publication_package(args: argparse.Namespace) -> dict[str, Any]:
    controller = _controller(args)
    payload = _load_json(args.file)
    request = PublicationRequest.model_validate(payload["request"])
    authorization = _publication_authorization(
        controller, args.project_id, payload, request.channel
    )
    package = build_dispatch_package(
        controller,
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
    narration_parser.add_argument(
        "--script-file", help="Local TTS preview of supplied copy"
    )
    narration_parser.add_argument("--route", choices=("kokoro",), default="kokoro")
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
    transcribe_parser.add_argument(
        "--srt", help="Also write speech-timed SRT captions to this path"
    )
    transcribe_parser.add_argument(
        "--locale", help="Locale profile for caption wrapping (defaults to detected)"
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
        "--credits",
        help="audio-credits JSON to include as credits.json",
    )
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
        choices=[
            "openverse",
            "wikimedia",
            "internet_archive",
            "freesound",
            "unsplash",
            "pexels",
        ],
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
    source_parser.add_argument(
        "--orientation",
        choices=["landscape", "portrait", "squarish", "square"],
        help="Unsplash and Pexels only; use squarish for Unsplash and square for Pexels",
    )
    source_parser.add_argument(
        "--license-type",
        choices=["all", "all-cc", "commercial", "modification"],
        help="Openverse only: filter by licence capability",
    )
    source_parser.add_argument(
        "--size",
        choices=["large", "medium", "small"],
        help="Pexels only: minimum asset size filter",
    )
    source_parser.add_argument("--content-filter", choices=["low", "high"])
    source_parser.add_argument("--order-by", choices=["relevant", "latest"])
    source_parser.add_argument(
        "--selected-asset-id",
        help=(
            "Unsplash only: the photo the operator selected from the results. "
            "API results are not treated as human selections without it."
        ),
    )
    source_parser.add_argument("--output")
    source_parser.set_defaults(handler=cmd_source_search)

    finalize_parser = subparsers.add_parser(
        "finalize-asset",
        help="Evaluate one asset for a slot and record the treatment decision",
    )
    finalize_parser.add_argument("project_id")
    finalize_parser.add_argument("--file", required=True)
    finalize_parser.add_argument("--decided-by", required=True)
    finalize_parser.set_defaults(handler=cmd_finalize_asset)

    coverage_parser = subparsers.add_parser(
        "slot-coverage",
        help="Report which slots have an approved treatment",
    )
    coverage_parser.add_argument("project_id")
    coverage_parser.set_defaults(handler=cmd_slot_coverage)

    evidence_parser = subparsers.add_parser(
        "capture-evidence",
        help="Archive the item and licence pages for a sourced candidate",
    )
    evidence_parser.add_argument("project_id")
    evidence_parser.add_argument("--candidate", required=True)
    evidence_parser.add_argument("--output")
    evidence_parser.set_defaults(handler=cmd_capture_evidence)

    download_parser = subparsers.add_parser(
        "download-asset",
        help="Download and register a verified stock asset",
    )
    download_parser.add_argument("project_id")
    download_parser.add_argument("--candidate", required=True)
    download_parser.add_argument("--output")
    download_parser.set_defaults(handler=cmd_download_asset)

    owned_parser = subparsers.add_parser(
        "import-owned-asset",
        help="Register client-owned media with its signed authorisation",
    )
    owned_parser.add_argument("project_id")
    owned_parser.add_argument("--file", required=True)
    owned_parser.add_argument("--source", required=True)
    owned_parser.add_argument("--output")
    owned_parser.set_defaults(handler=cmd_import_owned_asset)

    revision_parser = subparsers.add_parser(
        "create-revision",
        help="Create a child revision whose parent archive stays intact",
    )
    revision_parser.add_argument("parent_id")
    revision_parser.add_argument("--file", required=True)
    revision_parser.set_defaults(handler=cmd_create_revision)

    lineage_parser = subparsers.add_parser(
        "revision-lineage", help="Show the revision chain up to the original"
    )
    lineage_parser.add_argument("project_id")
    lineage_parser.set_defaults(handler=cmd_revision_lineage)

    plan_parser = subparsers.add_parser(
        "record-scene-plan", help="Store the SCENE_PLAN markdown and its hash"
    )
    plan_parser.add_argument("project_id")
    plan_parser.add_argument("--markdown-file", required=True)
    plan_parser.set_defaults(handler=cmd_record_scene_plan)

    plan_show_parser = subparsers.add_parser(
        "scene-plan", help="Show the latest recorded scene plan"
    )
    plan_show_parser.add_argument("project_id")
    plan_show_parser.set_defaults(handler=cmd_scene_plan)

    brief_record_parser = subparsers.add_parser(
        "record-workable-brief",
        help="Record a versioned workable brief with decision provenance",
    )
    brief_record_parser.add_argument("project_id")
    brief_record_parser.add_argument("--file", required=True)
    brief_record_parser.set_defaults(handler=cmd_record_workable_brief)

    brief_show_parser = subparsers.add_parser(
        "workable-brief", help="Show the current workable brief"
    )
    brief_show_parser.add_argument("project_id")
    brief_show_parser.set_defaults(handler=cmd_workable_brief)

    element_record_parser = subparsers.add_parser(
        "record-creative-element",
        help="Record a versioned reusable character, prop, location or style element",
    )
    element_record_parser.add_argument("project_id")
    element_record_parser.add_argument("--file", required=True)
    element_record_parser.set_defaults(handler=cmd_record_creative_element)

    element_list_parser = subparsers.add_parser(
        "list-creative-elements", help="List reusable creative elements"
    )
    element_list_parser.add_argument("project_id")
    element_list_parser.add_argument("--include-history", action="store_true")
    element_list_parser.set_defaults(handler=cmd_list_creative_elements)

    element_bind_parser = subparsers.add_parser(
        "bind-creative-element", help="Bind a current creative element to a shot role"
    )
    element_bind_parser.add_argument("project_id")
    element_bind_parser.add_argument("--file", required=True)
    element_bind_parser.set_defaults(handler=cmd_bind_creative_element)

    element_slots_parser = subparsers.add_parser(
        "derive-element-slots",
        help="Create visual slots for every element bound to a shot",
    )
    element_slots_parser.add_argument("project_id")
    element_slots_parser.set_defaults(handler=cmd_derive_element_slots)

    element_apply_parser = subparsers.add_parser(
        "bind-elements",
        help="Assign element artwork to matching slots across bound shots",
    )
    element_apply_parser.add_argument("project_id")
    element_apply_parser.set_defaults(handler=cmd_bind_elements)

    reference_bind_parser = subparsers.add_parser(
        "bind-reference",
        help="Attach a governed reference image to a shot role",
    )
    reference_bind_parser.add_argument("project_id")
    reference_bind_parser.add_argument("--file", required=True)
    reference_bind_parser.set_defaults(handler=cmd_bind_reference)

    reference_list_parser = subparsers.add_parser(
        "list-references", help="List shot references"
    )
    reference_list_parser.add_argument("project_id")
    reference_list_parser.add_argument("--shot-id")
    reference_list_parser.set_defaults(handler=cmd_list_references)

    studio_prepare_parser = subparsers.add_parser(
        "prepare-studio-work-packet",
        help="Authorize a governed OpenMontage work packet",
    )
    studio_prepare_parser.add_argument("project_id")
    studio_prepare_parser.add_argument("--file", required=True)
    studio_prepare_parser.add_argument(
        "--spend-approval",
        help="Human spend approval ID required above USD 1.00",
    )
    studio_prepare_parser.set_defaults(handler=cmd_prepare_studio_work_packet)

    studio_spend_parser = subparsers.add_parser(
        "approve-studio-spend",
        help="Approve one Studio work request above the USD 1.00 automatic limit",
    )
    studio_spend_parser.add_argument("project_id")
    studio_spend_parser.add_argument("--file", required=True)
    studio_spend_parser.set_defaults(handler=cmd_approve_studio_spend)

    budget_plan_parser = subparsers.add_parser(
        "plan-budget",
        help="Plan one creative's spend and decide whether approval is needed",
    )
    budget_plan_parser.add_argument("project_id")
    budget_plan_parser.add_argument("--file", required=True)
    budget_plan_parser.set_defaults(handler=cmd_plan_budget)

    budget_show_parser = subparsers.add_parser(
        "budget-plan", help="Show the current creative budget plan"
    )
    budget_show_parser.add_argument("project_id")
    budget_show_parser.set_defaults(handler=cmd_budget_plan)

    budget_approve_parser = subparsers.add_parser(
        "approve-budget-plan",
        help="Approve a creative budget plan above the automatic target",
    )
    budget_approve_parser.add_argument("project_id")
    budget_approve_parser.add_argument("--file", required=True)
    budget_approve_parser.set_defaults(handler=cmd_approve_budget_plan)

    budget_summary_parser = subparsers.add_parser(
        "budget-summary",
        help="Show planned versus actual spend and any approval needed",
    )
    budget_summary_parser.add_argument("project_id")
    budget_summary_parser.set_defaults(handler=cmd_budget_summary)

    settle_item_parser = subparsers.add_parser(
        "settle-budget-item",
        help="Settle one planned job and raise approval if it exceeds tolerance",
    )
    settle_item_parser.add_argument("project_id")
    settle_item_parser.add_argument("job_id")
    settle_item_parser.add_argument("--amount", required=True)
    settle_item_parser.add_argument("--currency", required=True)
    settle_item_parser.add_argument("--requested-by")
    settle_item_parser.add_argument("--reason")
    settle_item_parser.set_defaults(handler=cmd_settle_budget_item)

    variance_approve_parser = subparsers.add_parser(
        "approve-variance",
        help="Approve or reject a pending budget variance request",
    )
    variance_approve_parser.add_argument("project_id")
    variance_approve_parser.add_argument("--file", required=True)
    variance_approve_parser.set_defaults(handler=cmd_approve_variance)

    variance_list_parser = subparsers.add_parser(
        "pending-variances", help="List variance requests awaiting a decision"
    )
    variance_list_parser.add_argument("project_id")
    variance_list_parser.set_defaults(handler=cmd_pending_variances)

    asset_cost_parser = subparsers.add_parser(
        "asset-costs",
        help="Show per-asset generation cost, variance and provider totals",
    )
    asset_cost_parser.add_argument("project_id")
    asset_cost_parser.set_defaults(handler=cmd_asset_costs)

    text_plan_parser = subparsers.add_parser(
        "plan-from-text",
        help="Derive a governed workable brief, shots and scene plan from raw text",
    )
    text_plan_parser.add_argument("project_id")
    text_plan_parser.add_argument("--file", required=True)
    text_plan_parser.add_argument(
        "--persist-slots",
        action="store_true",
        help="Create shots and slots now so sourcing can precede spend",
    )
    text_plan_parser.set_defaults(handler=cmd_plan_from_text)

    studio_status_parser = subparsers.add_parser(
        "studio-status",
        help="Show whether Studio readiness has been verified recently",
    )
    studio_status_parser.set_defaults(handler=cmd_studio_status)

    studio_check_parser = subparsers.add_parser(
        "studio-check",
        help="Record one live Studio readiness probe for this session",
    )
    studio_check_parser.add_argument("--file", required=True)
    studio_check_parser.set_defaults(handler=cmd_studio_check)

    studio_run_parser = subparsers.add_parser(
        "studio-run",
        help="Drive one authorized Studio production through a transport",
    )
    studio_run_parser.add_argument("project_id")
    studio_run_parser.add_argument("operation_id")
    studio_run_parser.add_argument(
        "--transport", choices=["deterministic", "spool"], default="deterministic"
    )
    studio_run_parser.add_argument("--spool")
    studio_run_parser.add_argument("--max-polls", type=int, default=3)
    studio_run_parser.add_argument("--requested-by")
    studio_run_parser.set_defaults(handler=cmd_studio_run)

    void_job_parser = subparsers.add_parser(
        "void-job",
        help="Release a reservation that never reached a provider (BUDGET_RESERVED only)",
    )
    void_job_parser.add_argument("job_id")
    void_job_parser.add_argument("--reason", required=True)
    void_job_parser.set_defaults(handler=cmd_void_job)

    metadata_parser = subparsers.add_parser(
        "draft-metadata",
        help="Draft channel-sized title/description from approved project facts",
    )
    metadata_parser.add_argument("project_id")
    metadata_parser.add_argument("--channel", required=True, choices=sorted(CHANNELS))
    metadata_parser.add_argument("--claim", action="append", default=[])
    metadata_parser.add_argument("--hashtag", action="append", default=[])
    metadata_parser.add_argument("--cta")
    metadata_parser.add_argument("--output")
    metadata_parser.set_defaults(handler=cmd_draft_metadata)

    drift_parser = subparsers.add_parser(
        "caption-drift",
        help="Compare plan-timed captions with speech-timed captions",
    )
    drift_parser.add_argument("--plan", required=True)
    drift_parser.add_argument("--spoken", required=True)
    drift_parser.add_argument("--tolerance", type=float, default=0.75)
    drift_parser.set_defaults(handler=cmd_caption_drift)

    studio_production_parser = subparsers.add_parser(
        "studio-production", help="Show a Studio production and its jobs"
    )
    studio_production_parser.add_argument("operation_id")
    studio_production_parser.set_defaults(handler=cmd_studio_production)

    generate_asset_parser = subparsers.add_parser(
        "generate-asset",
        help="Run governed asset generation for one budget-plan item",
    )
    generate_asset_parser.add_argument("project_id")
    generate_asset_parser.add_argument("--plan", required=True)
    generate_asset_parser.add_argument("--item", required=True)
    generate_asset_parser.add_argument("--prompt")
    generate_asset_parser.add_argument("--aspect-ratio")
    generate_asset_parser.add_argument("--model")
    generate_asset_parser.add_argument("--endpoint")
    generate_asset_parser.add_argument("--key")
    generate_asset_parser.add_argument(
        "--adapter", choices=["mock", "fal"], default="mock"
    )
    generate_asset_parser.add_argument("--dry-run", action="store_true")
    generate_asset_parser.set_defaults(handler=cmd_generate_asset)

    raise_budget_parser = subparsers.add_parser(
        "raise-budget", help="Increase the project budget ceiling with a reason"
    )
    raise_budget_parser.add_argument("project_id")
    raise_budget_parser.add_argument("--file", required=True)
    raise_budget_parser.set_defaults(handler=cmd_raise_budget)

    reconcile_overage_parser = subparsers.add_parser(
        "reconcile-overage",
        help="Record a human-accepted provider overrun and settle the job",
    )
    reconcile_overage_parser.add_argument("project_id")
    reconcile_overage_parser.add_argument("--file", required=True)
    reconcile_overage_parser.set_defaults(handler=cmd_reconcile_overage)

    motion_plan_parser = subparsers.add_parser(
        "plan-motion",
        help="Estimate one per-shot Studio video generation without spending",
    )
    motion_plan_parser.add_argument("project_id")
    motion_plan_parser.add_argument("--file", required=True)
    motion_plan_parser.set_defaults(handler=cmd_plan_shot_motion)

    motion_prepare_parser = subparsers.add_parser(
        "prepare-motion",
        help="Reserve budget and write a per-shot motion packet",
    )
    motion_prepare_parser.add_argument("project_id")
    motion_prepare_parser.add_argument("--file", required=True)
    motion_prepare_parser.set_defaults(handler=cmd_prepare_shot_motion)

    motion_import_parser = subparsers.add_parser(
        "import-motion",
        help="Register a generated shot clip and settle its reservation",
    )
    motion_import_parser.add_argument("project_id")
    motion_import_parser.add_argument("--file", required=True)
    motion_import_parser.set_defaults(handler=cmd_import_shot_motion)

    assembly_parser = subparsers.add_parser(
        "assemble-film",
        help="Trim every shot clip to its duration and join the film",
    )
    assembly_parser.add_argument("project_id")
    assembly_parser.add_argument(
        "--transition", choices=["cut", "dissolve"], default="cut"
    )
    assembly_parser.add_argument("--dissolve", default="0.4")
    assembly_parser.add_argument("--fps", type=int, default=24)
    assembly_parser.add_argument("--width", type=int, default=1080)
    assembly_parser.add_argument("--height", type=int, default=1920)
    assembly_parser.add_argument(
        "--beat-grid", help="detect-beats JSON; snap cuts to this beat grid"
    )
    assembly_parser.add_argument("--beat-tolerance", type=float, default=0.08)
    assembly_parser.set_defaults(handler=cmd_assemble_film)

    detect_beats_parser = subparsers.add_parser(
        "detect-beats",
        help="Detect tempo and a beat grid from a local audio file (no network)",
    )
    detect_beats_parser.add_argument("--audio", required=True)
    detect_beats_parser.add_argument("--output")
    detect_beats_parser.set_defaults(handler=cmd_detect_beats)

    takes_parser = subparsers.add_parser(
        "motion-takes",
        help="Plan how many takes fit the remaining budget",
    )
    takes_parser.add_argument("project_id")
    takes_parser.add_argument("--model", default="minimax/h3")
    takes_parser.add_argument("--seconds", type=float, default=5.0)
    takes_parser.add_argument("--resolution", default="768p")
    takes_parser.add_argument("--max-takes", type=int, default=3)
    takes_parser.set_defaults(handler=cmd_motion_takes)

    script_parser = subparsers.add_parser(
        "draft-script",
        help="Record supplied/agent-authored script as an unapproved proposal (no hosted call)",
    )
    script_parser.add_argument("project_id")
    script_parser.add_argument("--duration", type=float)
    script_parser.add_argument("--max-words", type=int)
    script_parser.add_argument("--model")
    script_parser.add_argument("--script-file")
    script_parser.set_defaults(handler=cmd_draft_script)

    image_edit_parser = subparsers.add_parser(
        "enhance-still", help="One funded fal image edit; provider-neutral command"
    )
    image_edit_parser.add_argument("project_id")
    image_edit_parser.add_argument("--file", required=True)
    image_edit_parser.set_defaults(handler=cmd_enhance_still)

    avatar_parser = subparsers.add_parser(
        "avatar-generate",
        help="One funded fal avatar/lip-sync take with recorded likeness consent",
    )
    avatar_parser.add_argument("project_id")
    avatar_parser.add_argument("--source", required=True)
    avatar_parser.add_argument("--audio", required=True)
    avatar_parser.add_argument("--plan", required=True)
    avatar_parser.add_argument("--item", required=True)
    avatar_parser.add_argument("--consent", required=True)
    avatar_parser.add_argument(
        "--model",
        default=OMNIHUMAN_MODEL,
        choices=list(AVATAR_MODELS),
    )
    avatar_parser.add_argument(
        "--resolution", choices=["720p", "1080p"], default="720p"
    )
    avatar_parser.add_argument("--no-turbo", action="store_true")
    avatar_parser.add_argument("--prompt")
    avatar_parser.add_argument(
        "--sync-mode",
        choices=["cut_off", "loop", "bounce", "silence", "remap"],
        default="cut_off",
    )
    avatar_parser.set_defaults(handler=cmd_generate_avatar)

    operations_parser = subparsers.add_parser(
        "operations", help="Describe supported operation policy (not live readiness)"
    )
    operations_parser.set_defaults(handler=cmd_operations)

    fal_check_parser = subparsers.add_parser(
        "fal-check",
        help="Test the fal credential with a zero-cost, read-only model-catalog call",
    )
    fal_check_parser.set_defaults(handler=cmd_fal_check)

    composition_parser = subparsers.add_parser(
        "author-composition",
        help="Author an editable HyperFrames composition from the shot plan",
    )
    composition_parser.add_argument("project_id")
    composition_parser.add_argument("--width", type=int, default=1080)
    composition_parser.add_argument("--height", type=int, default=1920)
    composition_parser.add_argument("--title")
    composition_parser.add_argument(
        "--no-embed-layers",
        dest="embed_layers",
        action="store_false",
        help="Author the scaffold without embedding assigned image layers",
    )
    composition_parser.set_defaults(handler=cmd_author_composition, embed_layers=True)

    captions_export_parser = subparsers.add_parser(
        "export-captions",
        help="Write an SRT from narration ASR when present, else the shot plan",
    )
    captions_export_parser.add_argument("project_id")
    captions_export_parser.add_argument(
        "--source", choices=("auto", "asr", "plan"), default="auto"
    )
    captions_export_parser.add_argument("--locale")
    captions_export_parser.add_argument("--narration")
    captions_export_parser.add_argument(
        "--pad-to",
        type=float,
        help="Extend the final cue to this runtime so coverage passes",
    )
    captions_export_parser.add_argument(
        "--max-cue-seconds",
        type=float,
        default=6.0,
        help="Maximum duration of one caption cue (kids preset uses 3.5)",
    )
    captions_export_parser.set_defaults(handler=cmd_export_captions)

    captions_burn_parser = subparsers.add_parser(
        "burn-captions", help="Attach the SRT as a soft subtitle track"
    )
    captions_burn_parser.add_argument("project_id")
    captions_burn_parser.add_argument("--video", required=True)
    captions_burn_parser.add_argument("--captions", required=True)
    captions_burn_parser.add_argument(
        "--hard", action="store_true", help="Burn captions into the picture"
    )
    captions_burn_parser.set_defaults(handler=cmd_burn_captions)

    qa_render_parser = subparsers.add_parser(
        "qa-render",
        help="Technical QA on a rendered deliverable, recorded per check",
    )
    qa_render_parser.add_argument("project_id")
    qa_render_parser.add_argument("artifact_id")
    qa_render_parser.add_argument("--duration", type=float)
    qa_render_parser.add_argument("--width", type=int)
    qa_render_parser.add_argument("--height", type=int)
    qa_render_parser.add_argument("--fps", type=float)
    qa_render_parser.add_argument("--no-audio", action="store_true")
    qa_render_parser.add_argument(
        "--captions", help="Caption artifact to score for coverage and timing"
    )
    qa_render_parser.add_argument(
        "--max-true-peak",
        type=float,
        default=None,
        help="True-peak ceiling in dBTP; default is -1.0, or -2.0 for kids profiles",
    )
    qa_render_parser.set_defaults(handler=cmd_qa_render)

    mux_parser = subparsers.add_parser(
        "mux-narration",
        help="Mux narration over a silent composition render",
    )
    mux_parser.add_argument("project_id")
    mux_parser.add_argument("--video", required=True)
    mux_parser.add_argument("--audio", required=True)
    mux_parser.set_defaults(handler=cmd_mux_narration)

    render_parser = subparsers.add_parser(
        "render-composition",
        help="Render an authored composition to a silent H.264 preview",
    )
    render_parser.add_argument("project_id")
    render_parser.add_argument("--composition")
    render_parser.add_argument("--fps", type=int, default=24)
    render_parser.add_argument("--width", type=int, default=1080)
    render_parser.add_argument("--height", type=int, default=1920)
    render_parser.add_argument(
        "--renderer",
        choices=("legacy", "hyperframes"),
        default=None,
        help="Override PINAKA_RENDERER for this render",
    )
    render_parser.set_defaults(handler=cmd_render_composition)

    studio_show_parser = subparsers.add_parser(
        "studio-work-packet", help="Show a governed OpenMontage work packet"
    )
    studio_show_parser.add_argument("operation_id")
    studio_show_parser.set_defaults(handler=cmd_studio_work_packet)

    studio_import_parser = subparsers.add_parser(
        "import-studio-result",
        help="Import a governed OpenMontage result and terminal receipt",
    )
    studio_import_parser.add_argument("--file", required=True)
    studio_import_parser.set_defaults(handler=cmd_import_studio_result)

    judge_parser = subparsers.add_parser(
        "judge-asset",
        help="Judge tone, brand, composition, legibility and safety with a vision model",
    )
    judge_parser.add_argument("project_id")
    judge_parser.add_argument("artifact_id")
    judge_parser.add_argument("--model", required=True)
    judge_parser.add_argument("--provider", choices=["fal", "managed"], default="fal")
    judge_parser.add_argument("--intent", required=True)
    judge_parser.add_argument("--monetized", action="store_true")
    judge_parser.add_argument("--advertising", action="store_true")
    judge_parser.add_argument("--people", action="store_true")
    judge_parser.add_argument("--claims", action="store_true")
    judge_parser.add_argument("--policy", help="Review policy JSON file")
    judge_parser.add_argument("--technical-failed", action="store_true")
    judge_parser.add_argument("--output")
    judge_parser.set_defaults(handler=cmd_judge_asset)

    edit_parser = subparsers.add_parser(
        "ai-edit-asset",
        help="Inpaint or restyle an asset through local ComfyUI",
    )
    edit_parser.add_argument("project_id")
    edit_parser.add_argument("--file", required=True)
    edit_parser.add_argument("--output")
    edit_parser.set_defaults(handler=cmd_ai_edit_asset)

    upscale_parser = subparsers.add_parser(
        "upscale-asset",
        help="Generative upscale an asset through local ComfyUI",
    )
    upscale_parser.add_argument("project_id")
    upscale_parser.add_argument("--file", required=True)
    upscale_parser.add_argument("--output")
    upscale_parser.set_defaults(handler=cmd_upscale_asset)

    add_slot_parser = subparsers.add_parser(
        "add-slot",
        help="Create a slot before sourcing, generation or spend",
    )
    add_slot_parser.add_argument("project_id")
    add_slot_parser.add_argument("--file", required=True)
    add_slot_parser.set_defaults(handler=cmd_add_slot)

    sourcing_parser = subparsers.add_parser(
        "record-sourcing-decision",
        help="Record owned/stock/manual/deterministic/generated per slot",
    )
    sourcing_parser.add_argument("project_id")
    sourcing_parser.add_argument("--file", required=True)
    sourcing_parser.set_defaults(handler=cmd_record_sourcing_decision)

    candidate_parser = subparsers.add_parser(
        "record-sourcing-candidate",
        help="Add one searched candidate to a slot's candidate ledger",
    )
    candidate_parser.add_argument("project_id")
    candidate_parser.add_argument("--file", required=True)
    candidate_parser.set_defaults(handler=cmd_record_sourcing_candidate)

    manifest_parser = subparsers.add_parser(
        "sourcing-manifest",
        help="Write and hash the project sourcing manifest",
    )
    manifest_parser.add_argument("project_id")
    manifest_parser.set_defaults(handler=cmd_sourcing_manifest)

    candidates_parser = subparsers.add_parser(
        "sourcing-candidates",
        help="List the candidate ledger and the two-pick summary",
    )
    candidates_parser.add_argument("project_id")
    candidates_parser.add_argument("--slot")
    candidates_parser.set_defaults(handler=cmd_sourcing_candidates)

    pick_parser = subparsers.add_parser(
        "pick-slot-assets",
        help="Record the primary and independent backup for a slot",
    )
    pick_parser.add_argument("project_id")
    pick_parser.add_argument("--file", required=True)
    pick_parser.set_defaults(handler=cmd_pick_slot_assets)

    sourcing_list_parser = subparsers.add_parser(
        "sourcing-decisions",
        help="List the sourcing decisions recorded for a project",
    )
    sourcing_list_parser.add_argument("project_id")
    sourcing_list_parser.add_argument("--slot")
    sourcing_list_parser.set_defaults(handler=cmd_sourcing_decisions)

    flow_parser = subparsers.add_parser(
        "record-flow-step",
        help="Append one G0-G7 step record with status and evidence",
    )
    flow_parser.add_argument("project_id")
    flow_parser.add_argument("--file", required=True)
    flow_parser.set_defaults(handler=cmd_record_flow_step)

    effects_parser = subparsers.add_parser(
        "record-effects-plan",
        help="Record camera, transition, light, particle and sound cues with rights",
    )
    effects_parser.add_argument("project_id")
    effects_parser.add_argument("--file", required=True)
    effects_parser.set_defaults(handler=cmd_record_effects_plan)

    effects_show_parser = subparsers.add_parser(
        "effects-plan", help="Show the current effects and sound-design plan"
    )
    effects_show_parser.add_argument("project_id")
    effects_show_parser.set_defaults(handler=cmd_effects_plan)

    plan_production_parser = subparsers.add_parser(
        "plan-production",
        help="Create shot production contracts and layer plans from the creative plan",
    )
    plan_production_parser.add_argument("project_id")
    plan_production_parser.set_defaults(handler=cmd_plan_production)

    shot_prod_parser = subparsers.add_parser(
        "record-shot-production",
        help="Record one shot's production contract",
    )
    shot_prod_parser.add_argument("project_id")
    shot_prod_parser.add_argument("--file", required=True)
    shot_prod_parser.set_defaults(handler=cmd_record_shot_production)

    script_parser = subparsers.add_parser(
        "record-script", help="Record the current narration script"
    )
    script_parser.add_argument("project_id")
    script_parser.add_argument("--file", required=True)
    script_parser.set_defaults(handler=cmd_record_script)

    approve_script_parser = subparsers.add_parser(
        "approve-script", help="Approve the current narration script"
    )
    approve_script_parser.add_argument("project_id")
    approve_script_parser.add_argument("script_id")
    approve_script_parser.add_argument("--by", required=True, dest="approved_by")
    approve_script_parser.set_defaults(handler=cmd_approve_script)

    current_script_parser = subparsers.add_parser(
        "current-script", help="Show the current narration script"
    )
    current_script_parser.add_argument("project_id")
    current_script_parser.set_defaults(handler=cmd_current_script)

    delivery_meta_parser = subparsers.add_parser(
        "delivery-metadata", help="Write CREDITS.txt and title/description metadata"
    )
    delivery_meta_parser.add_argument("project_id")
    delivery_meta_parser.set_defaults(handler=cmd_delivery_metadata)

    query_plan_parser = subparsers.add_parser(
        "style-query-plan", help="Write the style-aware sourcing query plan for a slot"
    )
    query_plan_parser.add_argument("project_id")
    query_plan_parser.add_argument("slot_id")
    query_plan_parser.set_defaults(handler=cmd_style_query_plan)

    quality_parser = subparsers.add_parser(
        "quality-bar",
        help="Check brief, sourcing, two-pick, motion, flow, A/B and delivery gates",
    )
    quality_parser.add_argument("project_id")
    quality_parser.add_argument("--artifact-id")
    quality_parser.set_defaults(handler=cmd_quality_bar)

    record_parser = subparsers.add_parser(
        "production-record",
        help="Export the production record as hashed JSON and Markdown",
    )
    record_parser.add_argument("project_id")
    record_parser.set_defaults(handler=cmd_production_record)

    change_request_parser = subparsers.add_parser(
        "change-request",
        help="Generate CHANGE-REQUEST.md and YAML for every element and hash",
    )
    change_request_parser.add_argument("project_id")
    change_request_parser.add_argument("--output")
    change_request_parser.set_defaults(handler=cmd_change_request)

    change_submit_parser = subparsers.add_parser(
        "submit-change-request",
        help="Parse, validate and score a filled change-request template",
    )
    change_submit_parser.add_argument("project_id")
    change_submit_parser.add_argument("--file", required=True)
    change_submit_parser.add_argument("--submitted-by")
    change_submit_parser.set_defaults(handler=cmd_submit_change_request)

    change_decide_parser = subparsers.add_parser(
        "decide-change-request",
        help="Approve or reject an analysed change request",
    )
    change_decide_parser.add_argument("request_id")
    change_decide_parser.add_argument(
        "--decision", choices=("approve", "reject"), required=True
    )
    change_decide_parser.add_argument("--by", required=True)
    change_decide_parser.add_argument("--reason", required=True)
    change_decide_parser.set_defaults(handler=cmd_decide_change_request)

    change_status_parser = subparsers.add_parser(
        "change-request-status",
        help="Show one request with its events and revisions, or list requests",
    )
    change_status_parser.add_argument("project_id")
    change_status_parser.add_argument("--request")
    change_status_parser.set_defaults(handler=cmd_change_request_status)

    auto_critique_parser = subparsers.add_parser(
        "auto-critique",
        help="Run the stage critic and record findings plus corrective actions",
    )
    auto_critique_parser.add_argument("project_id")
    auto_critique_parser.add_argument(
        "stage",
        choices=(
            "script",
            "shots",
            "style",
            "assets",
            "motion",
            "visual",
            "captions",
            "audio",
            "delivery",
        ),
    )
    auto_critique_parser.add_argument("--artifact")
    auto_critique_parser.set_defaults(handler=cmd_auto_critique)

    record_critique_parser = subparsers.add_parser(
        "record-critique",
        help="Record a human/model stage critique from JSON",
    )
    record_critique_parser.add_argument("project_id")
    record_critique_parser.add_argument("--file", required=True)
    record_critique_parser.set_defaults(handler=cmd_record_critique)

    close_action_parser = subparsers.add_parser(
        "close-corrective-action",
        help="Close one corrective action with verification evidence",
    )
    close_action_parser.add_argument("action_id")
    close_action_parser.add_argument("--by", required=True)
    close_action_parser.add_argument("--result", required=True)
    close_action_parser.add_argument("--evidence", action="append", default=[])
    close_action_parser.set_defaults(handler=cmd_close_corrective_action)

    critique_status_parser = subparsers.add_parser(
        "critique-status",
        help="Show every stage critique and its corrective actions",
    )
    critique_status_parser.add_argument("project_id")
    critique_status_parser.add_argument("--artifact")
    critique_status_parser.set_defaults(handler=cmd_critique_status)

    record_plan_parser = subparsers.add_parser(
        "record-production-plan",
        help="Freeze the executable production plan (format, beats, shot directions, assets, rough cut)",
    )
    record_plan_parser.add_argument("project_id")
    record_plan_parser.add_argument("--file", required=True)
    record_plan_parser.set_defaults(handler=cmd_record_production_plan)

    generate_plan_parser = subparsers.add_parser(
        "generate-production-plan",
        help="Generate the executable plan from the approved brief and shot plan",
    )
    generate_plan_parser.add_argument("project_id")
    generate_plan_parser.set_defaults(handler=cmd_generate_production_plan)

    show_plan_parser = subparsers.add_parser(
        "production-plan", help="Show the current production plan"
    )
    show_plan_parser.add_argument("project_id")
    show_plan_parser.set_defaults(handler=cmd_production_plan)

    plan_status_parser = subparsers.add_parser(
        "production-plan-status",
        help="Check whether the production plan is ready to execute",
    )
    plan_status_parser.add_argument("project_id")
    plan_status_parser.set_defaults(handler=cmd_production_plan_status)

    quality_contract_parser = subparsers.add_parser(
        "quality-contract",
        help="Freeze the project quality contract (purpose, minimums, must-have beats)",
    )
    quality_contract_parser.add_argument("project_id")
    quality_contract_parser.add_argument("--file", required=True)
    quality_contract_parser.set_defaults(handler=cmd_quality_contract)

    intent_matrix_parser = subparsers.add_parser(
        "intent-matrix",
        help="Record delivered evidence for every must-have beat",
    )
    intent_matrix_parser.add_argument("project_id")
    intent_matrix_parser.add_argument("--file", required=True)
    intent_matrix_parser.set_defaults(handler=cmd_intent_matrix)

    creative_review_parser = subparsers.add_parser(
        "creative-review",
        help="Record the 1-5 creative review for the exact final hash",
    )
    creative_review_parser.add_argument("project_id")
    creative_review_parser.add_argument("--file", required=True)
    creative_review_parser.set_defaults(handler=cmd_creative_review)

    quality_status_parser = subparsers.add_parser(
        "quality-status",
        help="Show the quality contract, intent matrix and readiness",
    )
    quality_status_parser.add_argument("project_id")
    quality_status_parser.add_argument("--artifact")
    quality_status_parser.set_defaults(handler=cmd_quality_status)

    review_packet_parser = subparsers.add_parser(
        "review-packet",
        help="Build a contact sheet and evidence packet for the final artifact",
    )
    review_packet_parser.add_argument("project_id")
    review_packet_parser.add_argument("--artifact", required=True)
    review_packet_parser.set_defaults(handler=cmd_review_packet)

    record_capabilities_parser = subparsers.add_parser(
        "record-capabilities",
        help="Record an OpenMontage capability/account snapshot",
    )
    record_capabilities_parser.add_argument("project_id")
    record_capabilities_parser.add_argument("--file", required=True)
    record_capabilities_parser.set_defaults(handler=cmd_record_capabilities)

    capabilities_parser = subparsers.add_parser(
        "capabilities", help="Show the current OpenMontage capability snapshot"
    )
    capabilities_parser.add_argument("project_id")
    capabilities_parser.set_defaults(handler=cmd_openmontage_capabilities)

    route_capability_parser = subparsers.add_parser(
        "route-capability",
        help="Choose Studio or the recorded fallback for one capability",
    )
    route_capability_parser.add_argument("project_id")
    route_capability_parser.add_argument("capability_id")
    route_capability_parser.set_defaults(handler=cmd_route_capability)

    ab_declare_parser = subparsers.add_parser(
        "declare-ab",
        help="Declare the A/B axis, hypothesis, metric, split and sample plan",
    )
    ab_declare_parser.add_argument("project_id")
    ab_declare_parser.add_argument("--file", required=True)
    ab_declare_parser.set_defaults(handler=cmd_declare_ab)

    ab_bind_parser = subparsers.add_parser(
        "bind-ab-variant",
        help="Bind an A or B render to the declared experiment",
    )
    ab_bind_parser.add_argument("project_id")
    ab_bind_parser.add_argument("--file", required=True)
    ab_bind_parser.set_defaults(handler=cmd_bind_ab_variant)

    ab_status_parser = subparsers.add_parser(
        "ab-status", help="Show the A/B experiment and variant coverage"
    )
    ab_status_parser.add_argument("project_id")
    ab_status_parser.set_defaults(handler=cmd_ab_status)

    ab_delivery_parser = subparsers.add_parser(
        "ab-delivery",
        help="Package A/, B/, experiment.json and metrics-plan.json",
    )
    ab_delivery_parser.add_argument("project_id")
    ab_delivery_parser.add_argument("--output", required=True)
    ab_delivery_parser.add_argument("--experiment")
    ab_delivery_parser.set_defaults(handler=cmd_ab_delivery)

    motion_parser = subparsers.add_parser(
        "record-motion-level",
        help="Record the motion level (M0-M5) for one shot",
    )
    motion_parser.add_argument("project_id")
    motion_parser.add_argument("--file", required=True)
    motion_parser.set_defaults(handler=cmd_record_motion_level)

    motion_list_parser = subparsers.add_parser(
        "motion-levels",
        help="Show motion-level coverage and the M1 character minimum",
    )
    motion_list_parser.add_argument("project_id")
    motion_list_parser.set_defaults(handler=cmd_motion_levels)

    flow_log_parser = subparsers.add_parser(
        "flow-log",
        help="Show the flow ledger and its completeness",
    )
    flow_log_parser.add_argument("project_id")
    flow_log_parser.add_argument("--step")
    flow_log_parser.set_defaults(handler=cmd_flow_log)

    assign_parser = subparsers.add_parser(
        "assign-slot-asset",
        help="Bind a primary, backup or A/B variant to a slot",
    )
    assign_parser.add_argument("project_id")
    assign_parser.add_argument("--file", required=True)
    assign_parser.add_argument("--decided-by", required=True)
    assign_parser.set_defaults(handler=cmd_assign_slot_asset)

    promote_parser = subparsers.add_parser(
        "promote-backup",
        help="Swap the backup asset into use for a slot",
    )
    promote_parser.add_argument("project_id")
    promote_parser.add_argument("slot_id")
    promote_parser.add_argument("--decided-by", required=True)
    promote_parser.add_argument("--reason", required=True)
    promote_parser.set_defaults(handler=cmd_promote_backup)

    audio_parser = subparsers.add_parser(
        "assign-audio-bed",
        help="Bind a sourced track as the primary or backup music bed",
    )
    audio_parser.add_argument("project_id")
    audio_parser.add_argument("--file", required=True)
    audio_parser.add_argument("--decided-by", required=True)
    audio_parser.set_defaults(handler=cmd_assign_audio_bed)

    audio_cov_parser = subparsers.add_parser(
        "audio-coverage", help="Report primary and backup music coverage"
    )
    audio_cov_parser.add_argument("project_id")
    audio_cov_parser.set_defaults(handler=cmd_audio_coverage)

    cue_parser = subparsers.add_parser(
        "assign-audio-cue",
        help="Place an open-source SFX, ambience or stinger cue on the timeline",
    )
    cue_parser.add_argument("project_id")
    cue_parser.add_argument("--file", required=True)
    cue_parser.set_defaults(handler=cmd_assign_audio_cue)

    credits_parser = subparsers.add_parser(
        "audio-credits",
        help="Write the attribution block for every music bed and cue",
    )
    credits_parser.add_argument("project_id")
    credits_parser.set_defaults(handler=cmd_audio_credits)

    context_parser = subparsers.add_parser(
        "set-use-context",
        help="Record the licence use context that sourcing must satisfy",
    )
    context_parser.add_argument("project_id")
    context_parser.add_argument("--file", required=True)
    context_parser.set_defaults(handler=cmd_set_use_context)

    analyze_parser = subparsers.add_parser(
        "analyze-asset",
        help="Measure an asset and propose treatment criteria",
    )
    analyze_parser.add_argument("project_id")
    analyze_parser.add_argument("artifact_id")
    analyze_parser.add_argument("--width", type=int)
    analyze_parser.add_argument("--height", type=int)
    analyze_parser.add_argument("--duration", type=float)
    analyze_parser.add_argument("--output")
    analyze_parser.set_defaults(handler=cmd_analyze_asset)

    correct_parser = subparsers.add_parser(
        "correct-asset",
        help="Apply crop, scale or pad to an asset approved for correction",
    )
    correct_parser.add_argument("project_id")
    correct_parser.add_argument("--file", required=True)
    correct_parser.add_argument("--output")
    correct_parser.set_defaults(handler=cmd_correct_asset)

    grade_parser = subparsers.add_parser(
        "grade-asset",
        help="Apply a .cube LUT and/or EQ grade to a finished clip",
    )
    grade_parser.add_argument("project_id")
    grade_parser.add_argument("artifact_id")
    grade_parser.add_argument("--lut")
    grade_parser.add_argument("--brightness", type=float, default=0.0)
    grade_parser.add_argument("--saturation", type=float, default=1.0)
    grade_parser.add_argument("--contrast", type=float, default=1.0)
    grade_parser.add_argument("--gamma", type=float, default=1.0)
    grade_parser.set_defaults(handler=cmd_grade_asset)

    key_parser = subparsers.add_parser(
        "key-asset",
        help="Key a green screen and composite it over a background clip",
    )
    key_parser.add_argument("project_id")
    key_parser.add_argument("--background", required=True)
    key_parser.add_argument("--foreground", required=True)
    key_parser.add_argument("--key-color", default="0x00FF00")
    key_parser.add_argument("--similarity", type=float, default=0.1)
    key_parser.add_argument("--blend", type=float, default=0.1)
    key_parser.set_defaults(handler=cmd_key_asset)

    speed_parser = subparsers.add_parser(
        "change-speed",
        help="Slow down or speed up a clip with pitch-correct audio",
    )
    speed_parser.add_argument("project_id")
    speed_parser.add_argument("artifact_id")
    speed_parser.add_argument("--speed", type=float, required=True)
    speed_parser.set_defaults(handler=cmd_change_speed)

    compose_parser = subparsers.add_parser(
        "compose-assets",
        help="Composite an overlay onto a background artifact",
    )
    compose_parser.add_argument("project_id")
    compose_parser.add_argument("--file", required=True)
    compose_parser.add_argument("--output")
    compose_parser.set_defaults(handler=cmd_compose_assets)

    spend_parser = subparsers.add_parser(
        "reserve-spend",
        help="Charge a paid call to the primary or bonus provider budget",
    )
    spend_parser.add_argument("--provider", default="fal")
    spend_parser.add_argument("--amount-usd", required=True)
    spend_parser.add_argument("--purpose", required=True)
    spend_parser.add_argument("--output")
    spend_parser.set_defaults(handler=cmd_reserve_spend)

    summary_parser = subparsers.add_parser(
        "spend-summary", help="Show provider spend against the tiered budget"
    )
    summary_parser.add_argument("--provider", default="fal")
    summary_parser.add_argument("--output")
    summary_parser.set_defaults(handler=cmd_spend_summary)

    preference_parser = subparsers.add_parser(
        "select-route",
        help="Choose an execution route under the provider preference policy",
    )
    preference_parser.add_argument("--capability", required=True)
    preference_parser.add_argument("--candidates", help="JSON file of route candidates")
    preference_parser.add_argument(
        "--policy", help="Alternative preference policy JSON"
    )
    preference_parser.add_argument("--budget-remaining")
    preference_parser.add_argument("--show-policy", action="store_true")
    preference_parser.add_argument("--output")
    preference_parser.set_defaults(handler=cmd_select_route)

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

    publication_auth_parser = subparsers.add_parser(
        "authorize-publication",
        help="Record a human publication authorization for the approved hash",
    )
    publication_auth_parser.add_argument("project_id")
    publication_auth_parser.add_argument("--file", required=True)
    publication_auth_parser.set_defaults(handler=cmd_authorize_publication)

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

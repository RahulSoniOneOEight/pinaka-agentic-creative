from __future__ import annotations

from typing import Any

from ..ai_edit import ComfyUIEditAdapter
from ..analysis import AssetAnalyzer
from ..assets import (
    AssetCriteria,
    evaluate_treatment,
    verdict_binds_asset,
)
from ..correction import FFmpegCompositeAdapter, FFmpegCorrectionAdapter
from ..enums import (
    ArtifactKind,
    AssetUseClass,
    SlotAssetRole,
    TreatmentVerdict,
)
from ..errors import (
    ApprovalRequiredError,
    ConfigurationError,
    ConflictError,
    RightsEligibilityError,
)
from ..finishing import FFmpegFinishAdapter
from ..judge import OpenAICompatibleJudge
from ..models import (
    AssetRightsRecord,
    AssetUseContext,
    DependencyRef,
)
from ..review_policy import CampaignRisk, ReviewPolicy, decide_review
from ..utils import canonical_json, sha256_bytes, sha256_file


class AssetsUseCases:
    def assign_slot_asset(
        self,
        project_id: str,
        slot_id: str,
        *,
        artifact_id: str,
        role: SlotAssetRole,
        criteria: AssetCriteria,
        decided_by: str,
        context: AssetUseContext | None = None,
    ) -> dict[str, Any]:
        """Bind one artifact to a slot as primary, backup, or an A/B variant."""

        context = self._enforce_context(project_id, context)
        slot = self.repository.get_slot(slot_id)
        if slot["project_id"] != project_id:
            raise ConflictError("Slot belongs to a different project")
        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")

        rights_payload = artifact["operation"].get("rights")
        rights = (
            AssetRightsRecord.model_validate(rights_payload) if rights_payload else None
        )
        decision = evaluate_treatment(
            slot_id=slot_id,
            criteria=criteria,
            context=context,
            decided_by=decided_by,
            rights=rights,
            artifact_id=artifact_id,
        )
        if not verdict_binds_asset(decision.verdict):
            raise RightsEligibilityError(
                f"Verdict {decision.verdict.value} cannot be assigned to a slot: "
                + "; ".join(decision.reasons)
            )

        if role in {SlotAssetRole.VARIANT_A, SlotAssetRole.VARIANT_B}:
            other = (
                SlotAssetRole.VARIANT_B
                if role == SlotAssetRole.VARIANT_A
                else SlotAssetRole.VARIANT_A
            )
            existing_variant = self.repository.slot_asset_by_role(slot_id, other.value)
            if existing_variant and existing_variant["artifact_id"] == artifact_id:
                raise ConflictError("A and B variants must be different artifacts")

        recorded = self.repository.register_treatment_decision(
            project_id,
            decision.model_dump(mode="json"),
            decision_hash=decision.decision_hash,
            artifact_id=artifact_id,
            decided_by=decided_by,
            created_at=decision.decided_at,
        )
        assignment = self.repository.upsert_slot_asset(
            project_id,
            slot_id,
            artifact_id,
            role.value,
            treatment_decision_id=recorded["id"],
        )
        return {
            "assignment": assignment,
            "decision": recorded,
            "verdict": decision.verdict.value,
            "role": role.value,
        }

    def promote_backup(
        self,
        project_id: str,
        slot_id: str,
        *,
        decided_by: str,
        reason: str,
    ) -> dict[str, Any]:
        """Swap the backup asset into use when the person asks for it."""

        slot = self.repository.get_slot(slot_id)
        if slot["project_id"] != project_id:
            raise ConflictError("Slot belongs to a different project")
        result = self.repository.swap_slot_asset_roles(
            project_id, slot_id, reason=reason
        )
        return {**result, "decided_by": decided_by, "reason": reason}

    def slot_coverage(self, project_id: str) -> dict[str, Any]:
        """Report primary, backup and A/B coverage for every declared slot."""

        slots = self.repository.list_slots(project_id)
        assignments = self.repository.list_slot_assets(project_id)
        by_slot: dict[str, dict[str, dict[str, Any]]] = {}
        for assignment in assignments:
            by_slot.setdefault(assignment["slot_id"], {})[assignment["role"]] = (
                assignment
            )

        rows = []
        resolved = 0
        for slot in slots:
            roles = by_slot.get(slot["id"], {})
            primary = roles.get(SlotAssetRole.PRIMARY.value)
            backup = roles.get(SlotAssetRole.BACKUP.value)
            variant_a = roles.get(SlotAssetRole.VARIANT_A.value)
            variant_b = roles.get(SlotAssetRole.VARIANT_B.value)

            problems: list[str] = []
            if not primary:
                problems.append("missing primary")
            if not backup:
                problems.append("missing backup")
            if bool(variant_a) != bool(variant_b):
                problems.append("incomplete A/B pair")
            if (
                variant_a
                and variant_b
                and variant_a["artifact_id"] == variant_b["artifact_id"]
            ):
                problems.append("A and B are the same artifact")

            is_resolved = not problems
            if is_resolved:
                resolved += 1
            rows.append(
                {
                    "slot_id": slot["id"],
                    "stable_key": slot["stable_key"],
                    "role": slot["role"],
                    "primary_artifact_id": (
                        primary["artifact_id"] if primary else None
                    ),
                    "backup_artifact_id": backup["artifact_id"] if backup else None,
                    "variant_a_artifact_id": (
                        variant_a["artifact_id"] if variant_a else None
                    ),
                    "variant_b_artifact_id": (
                        variant_b["artifact_id"] if variant_b else None
                    ),
                    "problems": problems,
                    "resolved": is_resolved,
                }
            )
        return {
            "project_id": project_id,
            "slots": rows,
            "total": len(rows),
            "resolved": resolved,
            "unresolved": [row for row in rows if not row["resolved"]],
            "coverage": "complete" if resolved == len(rows) else "incomplete",
        }

    def asset_use_context(self, project_id: str) -> AssetUseContext:
        """Return the recorded use context, or a conservative default for the project."""

        project = self.repository.get_project(project_id)
        stored = self.repository.get_asset_use_context(project_id)
        if stored:
            return AssetUseContext(
                use_class=AssetUseClass(stored["use_class"]),
                monetized=stored["monetized"],
                advertising=stored["advertising"],
                modification_planned=stored["modification_planned"],
                attribution_can_be_delivered=stored["attribution_can_be_delivered"],
            )
        return AssetUseContext(
            use_class=AssetUseClass.COMMERCIAL,
            monetized=project.max_budget.minor > 0,
            advertising=False,
            modification_planned=True,
            attribution_can_be_delivered=True,
        )

    def set_asset_use_context(
        self, project_id: str, context: AssetUseContext
    ) -> dict[str, Any]:
        self.repository.get_project(project_id)
        return self.repository.set_asset_use_context(
            project_id,
            {
                "use_class": context.use_class.value,
                "monetized": context.monetized,
                "advertising": context.advertising,
                "modification_planned": context.modification_planned,
                "attribution_can_be_delivered": context.attribution_can_be_delivered,
            },
        )

    def _enforce_context(
        self, project_id: str, context: AssetUseContext | None
    ) -> AssetUseContext:
        baseline = self.asset_use_context(project_id)
        if context is None:
            return baseline
        weaker: list[str] = []
        if baseline.use_class == AssetUseClass.COMMERCIAL and (
            context.use_class != AssetUseClass.COMMERCIAL
        ):
            weaker.append("use class")
        if baseline.monetized and not context.monetized:
            weaker.append("monetized")
        if baseline.advertising and not context.advertising:
            weaker.append("advertising")
        if baseline.modification_planned and not context.modification_planned:
            weaker.append("modification planned")
        if (
            not baseline.attribution_can_be_delivered
            and context.attribution_can_be_delivered
        ):
            weaker.append("attribution deliverability")
        if weaker:
            raise ConflictError(
                "The supplied use context is weaker than the project's recorded "
                f"context: {', '.join(weaker)}"
            )
        return context

    def analyze_asset(
        self,
        project_id: str,
        artifact_id: str,
        *,
        required_width: int | None = None,
        required_height: int | None = None,
        required_duration_seconds: float | None = None,
        analyzer: AssetAnalyzer | None = None,
    ) -> dict[str, Any]:
        """Measure an asset and propose treatment criteria from observations."""

        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")
        path = self.artifact_store.resolve(project_id, artifact["relative_path"])
        observation = (analyzer or AssetAnalyzer()).analyze(
            path,
            required_width=required_width,
            required_height=required_height,
            required_duration_seconds=required_duration_seconds,
        )
        criteria = observation.proposed_criteria()
        payload = {
            "schema_version": "asset-analysis/v1",
            "artifact_id": artifact_id,
            "artifact_hash": artifact["content_hash"],
            "observation": observation.model_dump(mode="json"),
            "criteria": criteria.model_dump(mode="json"),
        }
        content = canonical_json(payload).encode("utf-8")
        self.write_artifact(
            project_id,
            kind=ArtifactKind.QA,
            relative_path=(
                f"qa/asset-analysis-{artifact_id}-{sha256_bytes(content)[:12]}.json"
            ),
            content=content,
            operation={"type": "asset_analysis", "artifact_id": artifact_id},
        )
        return payload

    def correct_asset(
        self,
        project_id: str,
        *,
        slot_id: str,
        artifact_id: str,
        operations: list[dict[str, Any]],
        adapter: FFmpegCorrectionAdapter | None = None,
    ) -> dict[str, Any]:
        """Produce a corrected derivative after a recorded 'correct' decision."""

        decision = self.repository.latest_treatment_for_slot(slot_id)
        if (
            not decision
            or decision["verdict"] != TreatmentVerdict.CORRECT.value
            or decision["artifact_id"] != artifact_id
        ):
            raise ApprovalRequiredError(
                "A recorded 'correct' treatment decision for this artifact is "
                "required before correcting it"
            )
        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")
        path = self.artifact_store.resolve(project_id, artifact["relative_path"])
        result = (adapter or FFmpegCorrectionAdapter()).correct(path, operations)
        derivative = self.write_artifact(
            project_id,
            kind=ArtifactKind.DERIVATIVE,
            relative_path=(f"assets/derived/{result.content_hash[:12]}-corrected.png"),
            content=result.content,
            operation={
                "type": "correction",
                "operations": list(operations),
                "filter_graph": result.filter_graph,
                "command_hash": result.command_hash,
                "encoder_version": result.encoder_version,
            },
            parent_artifact_id=artifact_id,
        )
        return {
            "artifact": derivative,
            "parent_artifact_id": artifact_id,
            "command_hash": result.command_hash,
            "filter_graph": result.filter_graph,
        }

    def compose_assets(
        self,
        project_id: str,
        *,
        background_id: str,
        overlay_id: str,
        x: int = 0,
        y: int = 0,
        width: int | None = None,
        height: int | None = None,
        opacity: float = 1.0,
        adapter: FFmpegCompositeAdapter | None = None,
    ) -> dict[str, Any]:
        """Build a composite derivative from a background and an overlay."""

        background = self.repository.get_artifact(background_id)
        overlay = self.repository.get_artifact(overlay_id)
        for artifact in (background, overlay):
            if artifact["project_id"] != project_id:
                raise ConflictError("Artifact belongs to a different project")
        result = (adapter or FFmpegCompositeAdapter()).compose(
            self.artifact_store.resolve(project_id, background["relative_path"]),
            self.artifact_store.resolve(project_id, overlay["relative_path"]),
            x=x,
            y=y,
            width=width,
            height=height,
            opacity=opacity,
        )
        derivative = self.write_artifact(
            project_id,
            kind=ArtifactKind.DERIVATIVE,
            relative_path=(f"assets/derived/{result.content_hash[:12]}-composite.png"),
            content=result.content,
            operation={
                "type": "composite",
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "opacity": opacity,
                "filter_graph": result.filter_graph,
                "command_hash": result.command_hash,
                "encoder_version": result.encoder_version,
                "overlay_artifact_id": overlay_id,
            },
            parent_artifact_id=background_id,
            dependencies=(DependencyRef(entity_type="artifact", entity_id=overlay_id),),
        )
        return {
            "artifact": derivative,
            "background_artifact_id": background_id,
            "overlay_artifact_id": overlay_id,
            "command_hash": result.command_hash,
        }

    def _require_correction_decision(
        self, slot_id: str, artifact_id: str
    ) -> dict[str, Any]:
        decision = self.repository.latest_treatment_for_slot(slot_id)
        if (
            not decision
            or decision["verdict"] != TreatmentVerdict.CORRECT.value
            or decision["artifact_id"] != artifact_id
        ):
            raise ApprovalRequiredError(
                "A recorded 'correct' treatment decision for this artifact is "
                "required before changing its pixels"
            )
        return decision

    def _read_artifact_bytes(self, project_id: str, artifact_id: str) -> bytes:
        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")
        return self.artifact_store.resolve(
            project_id, artifact["relative_path"]
        ).read_bytes()

    def ai_edit_asset(
        self,
        project_id: str,
        *,
        slot_id: str,
        artifact_id: str,
        prompt: str,
        mask: bytes | None = None,
        checkpoint: str | None = None,
        seed: int = 0,
        steps: int = 20,
        denoise: float = 0.55,
        adapter: ComfyUIEditAdapter | None = None,
    ) -> dict[str, Any]:
        """Inpaint or restyle an asset through the local ComfyUI route."""

        self._require_correction_decision(slot_id, artifact_id)
        content = self._read_artifact_bytes(project_id, artifact_id)
        if adapter is None:
            raise ConfigurationError(
                "A ComfyUI edit adapter with an allowlisted checkpoint is required"
            )
        result = adapter.edit(
            content,
            prompt=prompt,
            checkpoint=checkpoint,
            mask=mask,
            seed=seed,
            steps=steps,
            denoise=denoise,
        )
        derivative = self.write_artifact(
            project_id,
            kind=ArtifactKind.DERIVATIVE,
            relative_path=f"assets/derived/{result.content_hash[:12]}-ai-edit.png",
            content=result.content,
            operation=result.operation,
            parent_artifact_id=artifact_id,
        )
        return {
            "artifact": derivative,
            "parent_artifact_id": artifact_id,
            "workflow_hash": result.workflow_hash,
            "operation": result.operation["type"],
        }

    def generative_upscale_asset(
        self,
        project_id: str,
        *,
        slot_id: str,
        artifact_id: str,
        model_name: str | None = None,
        adapter: ComfyUIEditAdapter | None = None,
    ) -> dict[str, Any]:
        """Upscale an asset with an allowlisted ESRGAN-class model."""

        self._require_correction_decision(slot_id, artifact_id)
        content = self._read_artifact_bytes(project_id, artifact_id)
        if adapter is None:
            raise ConfigurationError(
                "A ComfyUI adapter with an allowlisted upscale model is required"
            )
        result = adapter.upscale(content, model_name=model_name)
        derivative = self.write_artifact(
            project_id,
            kind=ArtifactKind.DERIVATIVE,
            relative_path=(f"assets/derived/{result.content_hash[:12]}-upscaled.png"),
            content=result.content,
            operation=result.operation,
            parent_artifact_id=artifact_id,
        )
        return {
            "artifact": derivative,
            "parent_artifact_id": artifact_id,
            "workflow_hash": result.workflow_hash,
            "operation": result.operation["type"],
        }

    def grade_asset(
        self,
        project_id: str,
        artifact_id: str,
        *,
        lut_path: Any | None = None,
        brightness: float = 0.0,
        saturation: float = 1.0,
        contrast: float = 1.0,
        gamma: float = 1.0,
        adapter: Any | None = None,
    ) -> dict[str, Any]:
        """Apply a LUT and/or EQ grade to a finished clip."""

        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")
        source = self.artifact_store.resolve(project_id, artifact["relative_path"])
        result = (adapter or FFmpegFinishAdapter()).grade(
            source,
            lut_path=lut_path,
            brightness=brightness,
            saturation=saturation,
            contrast=contrast,
            gamma=gamma,
        )
        derivative = self.write_artifact(
            project_id,
            kind=ArtifactKind.DERIVATIVE,
            relative_path=f"assets/derived/{result.content_hash[:12]}-graded.mp4",
            content=result.content,
            operation={
                "type": "grade",
                "lut_sha256": sha256_file(lut_path) if lut_path else None,
                "brightness": brightness,
                "saturation": saturation,
                "contrast": contrast,
                "gamma": gamma,
                "filter_graph": result.filter_graph,
            },
            parent_artifact_id=artifact_id,
        )
        return {
            "artifact": derivative,
            "parent_artifact_id": artifact_id,
            "filter_graph": result.filter_graph,
            "command_hash": result.command_hash,
        }

    def key_asset(
        self,
        project_id: str,
        *,
        background_artifact_id: str,
        foreground_artifact_id: str,
        key_color: str = "0x00FF00",
        similarity: float = 0.1,
        blend: float = 0.1,
        adapter: Any | None = None,
    ) -> dict[str, Any]:
        """Key a green screen and composite it over another clip."""

        background = self.repository.get_artifact(background_artifact_id)
        foreground = self.repository.get_artifact(foreground_artifact_id)
        for artifact in (background, foreground):
            if artifact["project_id"] != project_id:
                raise ConflictError("Artifact belongs to a different project")
        background_path = self.artifact_store.resolve(
            project_id, background["relative_path"]
        )
        foreground_path = self.artifact_store.resolve(
            project_id, foreground["relative_path"]
        )
        result = (adapter or FFmpegFinishAdapter()).key_composite(
            background=background_path,
            foreground=foreground_path,
            key_color=key_color,
            similarity=similarity,
            blend=blend,
        )
        derivative = self.write_artifact(
            project_id,
            kind=ArtifactKind.DERIVATIVE,
            relative_path=f"assets/derived/{result.content_hash[:12]}-keyed.mp4",
            content=result.content,
            operation={
                "type": "key_composite",
                "key_color": key_color,
                "similarity": similarity,
                "blend": blend,
                "filter_graph": result.filter_graph,
            },
            parent_artifact_id=foreground_artifact_id,
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=background["id"],
                    entity_hash=background["content_hash"],
                ),
            ),
        )
        return {
            "artifact": derivative,
            "parent_artifact_id": foreground_artifact_id,
            "background_artifact_id": background_artifact_id,
            "filter_graph": result.filter_graph,
            "command_hash": result.command_hash,
        }

    def change_asset_speed(
        self,
        project_id: str,
        artifact_id: str,
        *,
        speed: float,
        adapter: Any | None = None,
    ) -> dict[str, Any]:
        """Slow down or speed up a clip, keeping audio in sync via atempo."""

        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")
        source = self.artifact_store.resolve(project_id, artifact["relative_path"])
        result = (adapter or FFmpegFinishAdapter()).change_speed(source, speed=speed)
        derivative = self.write_artifact(
            project_id,
            kind=ArtifactKind.DERIVATIVE,
            relative_path=(
                f"assets/derived/{result.content_hash[:12]}-speed-{speed:g}.mp4"
            ),
            content=result.content,
            operation={
                "type": "speed_change",
                "speed": speed,
                "filter_graph": result.filter_graph,
            },
            parent_artifact_id=artifact_id,
        )
        return {
            "artifact": derivative,
            "parent_artifact_id": artifact_id,
            "filter_graph": result.filter_graph,
            "command_hash": result.command_hash,
        }

    def judge_asset(
        self,
        project_id: str,
        artifact_id: str,
        *,
        model: str,
        intent: str,
        policy: ReviewPolicy | None = None,
        technical_ok: bool = True,
        risk: CampaignRisk | None = None,
        judge: OpenAICompatibleJudge | None = None,
    ) -> dict[str, Any]:
        """Judge tone, brand, composition, legibility and safety with a vision model."""

        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")
        path = self.artifact_store.resolve(project_id, artifact["relative_path"])
        content = path.read_bytes()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(path.suffix.lower(), "image/png")
        client = judge or OpenAICompatibleJudge(model=model)
        judgement = client.judge(content, intent=intent, mime=mime)
        policy = policy or ReviewPolicy()
        review = decide_review(judgement, policy, technical_ok=technical_ok, risk=risk)
        payload = {
            "schema_version": "visual-judgement/v1",
            "artifact_id": artifact_id,
            "artifact_hash": artifact["content_hash"],
            "intent": intent,
            "judgement": judgement.model_dump(mode="json"),
            "review": review,
        }
        encoded = canonical_json(payload).encode("utf-8")
        report = self.write_artifact(
            project_id,
            kind=ArtifactKind.QA,
            relative_path=(
                f"qa/visual-judge-{artifact_id}-{sha256_bytes(encoded)[:12]}.json"
            ),
            content=encoded,
            operation={
                "type": "visual_judgement",
                "model": model,
                "artifact_id": artifact_id,
            },
        )
        return {**payload, "report_artifact_id": report["id"]}

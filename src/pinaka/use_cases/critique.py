from __future__ import annotations

from typing import Any

from ..contracts.critique import (
    CritiqueFinding,
    StageCritiqueCreate,
)
from ..errors import (
    ApprovalRequiredError,
    ConflictError,
    IntegrityError,
)
from ..visual_qc import analyze_asset, analyze_video, caption_metrics

REQUIRED_CRITIQUE_STAGES: tuple[str, ...] = (
    "script",
    "shots",
    "style",
    "assets",
    "motion",
    "visual",
    "captions",
    "audio",
    "delivery",
)
VISUAL_SLOT_ROLES = {
    "background",
    "primary_visual",
    "hero_visual",
    "character",
    "prop",
    "foreground",
}
HIGH_SEVERITIES = {"blocker", "high"}


class CritiqueUseCases:
    """Per-stage creative and visual QC with mandatory corrective actions."""

    def record_stage_critique(
        self, project_id: str, request: StageCritiqueCreate
    ) -> dict[str, Any]:
        self.repository.get_project(project_id)
        subject_hash = request.subject_hash
        if request.subject_type == "artifact":
            artifact = self.repository.get_artifact(request.subject_id)
            if artifact["project_id"] != project_id:
                raise ConflictError("Critique artifact belongs to another project")
            if subject_hash and subject_hash != artifact["content_hash"]:
                raise ConflictError("Critique subject hash is stale")
            subject_hash = artifact["content_hash"]
        elif request.subject_type == "shot":
            shot = self.repository.get_shot(request.subject_id)
            if shot["project_id"] != project_id:
                raise ConflictError("Critique shot belongs to another project")
        elif request.subject_type == "slot":
            slot = self.repository.get_slot(request.subject_id)
            if slot["project_id"] != project_id:
                raise ConflictError("Critique slot belongs to another project")
        elif request.subject_type == "script":
            script = self.repository.current_script(project_id)
            if script and subject_hash and subject_hash != script["content_hash"]:
                raise ConflictError("Critique script hash is stale")
            if script:
                subject_hash = script["content_hash"]
        elif request.subject_id != project_id:
            raise ConflictError("Project critique names another project")
        if subject_hash and request.subject_hash is None:
            request = request.model_copy(update={"subject_hash": subject_hash})
        return self.repository.record_stage_critique(project_id, request)

    def auto_critique(
        self,
        project_id: str,
        stage: str,
        *,
        artifact_id: str | None = None,
        reviewer: str = "pinaka.visual_qc",
    ) -> dict[str, Any]:
        if stage not in REQUIRED_CRITIQUE_STAGES:
            raise IntegrityError(f"Unknown critique stage: {stage}")
        findings = self._critique_findings(project_id, stage, artifact_id)
        if any(item.severity == "blocker" for item in findings):
            verdict = "block"
        elif any(item.severity in {"high", "medium"} for item in findings):
            verdict = "revise"
        else:
            verdict = "pass"
        subject_type, subject_id, subject_hash = self._critique_subject(
            project_id, stage, artifact_id
        )
        request = StageCritiqueCreate(
            stage=stage,
            subject_type=subject_type,
            subject_id=subject_id,
            subject_hash=subject_hash,
            reviewer=reviewer,
            reviewer_kind="automated",
            findings=tuple(findings),
            verdict=verdict,
            notes=f"Automated {stage} critique",
        )
        return self.record_stage_critique(project_id, request)

    def close_corrective_action(
        self,
        action_id: str,
        *,
        closed_by: str,
        evidence: tuple[str, ...],
        result: str,
    ) -> dict[str, Any]:
        if not evidence:
            raise IntegrityError("Closing a corrective action requires evidence")
        return self.repository.close_corrective_action(
            action_id,
            closed_by=closed_by,
            evidence=evidence,
            result=result,
        )

    def stage_critique_gate(
        self,
        project_id: str,
        stage: str,
        *,
        artifact_id: str | None = None,
    ) -> dict[str, Any]:
        latest = self.repository.latest_stage_critique(
            project_id, stage, subject_id=artifact_id
        )
        if latest is None and artifact_id is not None:
            latest = self.repository.latest_stage_critique(project_id, stage)
        if latest is None:
            raise ApprovalRequiredError(f"Creative QC is missing a {stage} critique")
        if artifact_id is not None:
            artifact = self.repository.get_artifact(artifact_id)
            if latest.get("subject_hash") != artifact["content_hash"]:
                raise ApprovalRequiredError(
                    f"The {stage} critique is stale for the final artifact hash"
                )
        if latest["verdict"] == "block":
            raise ApprovalRequiredError(
                f"The {stage} critique blocks delivery: "
                f"{latest.get('notes') or 'see findings'}"
            )
        actions = self.repository.list_corrective_actions(
            project_id, critique_id=latest["id"], status="open"
        )
        blocking = [
            action for action in actions if action["severity"] in HIGH_SEVERITIES
        ]
        if blocking:
            raise ApprovalRequiredError(
                f"The {stage} critique has open corrective actions: "
                + "; ".join(action["action"] for action in blocking)
            )
        return {
            "critique": latest,
            "open_actions": actions,
            "blocking_actions": blocking,
        }

    def require_creative_qc(
        self, project_id: str, *, artifact_id: str | None = None
    ) -> dict[str, Any]:
        failures: list[str] = []
        for stage in REQUIRED_CRITIQUE_STAGES:
            try:
                target = (
                    artifact_id
                    if stage in {"visual", "motion", "audio"} and artifact_id
                    else None
                )
                self.stage_critique_gate(project_id, stage, artifact_id=target)
            except ApprovalRequiredError as error:
                failures.append(f"{stage}: {error}")
        if failures:
            raise ApprovalRequiredError(
                "Creative QC is not complete: " + " | ".join(failures)
            )
        return {
            "passed": True,
            "stages": list(REQUIRED_CRITIQUE_STAGES),
        }

    def critique_summary(
        self, project_id: str, *, artifact_id: str | None = None
    ) -> dict[str, Any]:
        rows = []
        for stage in REQUIRED_CRITIQUE_STAGES:
            latest = self.repository.latest_stage_critique(
                project_id,
                stage,
                subject_id=artifact_id
                if stage in {"visual", "motion", "audio"}
                else None,
            )
            actions = (
                self.repository.list_corrective_actions(
                    project_id, critique_id=latest["id"]
                )
                if latest
                else []
            )
            rows.append(
                {
                    "stage": stage,
                    "critique": latest,
                    "actions": actions,
                    "open_blocking": [
                        action
                        for action in actions
                        if action["status"] == "open"
                        and action["severity"] in HIGH_SEVERITIES
                    ],
                }
            )
        return {
            "project_id": project_id,
            "stages": rows,
            "complete": all(
                row["critique"] is not None and not row["open_blocking"] for row in rows
            ),
        }

    def _critique_subject(
        self, project_id: str, stage: str, artifact_id: str | None
    ) -> tuple[str, str, str | None]:
        if artifact_id:
            artifact = self.repository.get_artifact(artifact_id)
            return "artifact", artifact["id"], artifact["content_hash"]
        if stage == "script":
            script = self.repository.current_script(project_id)
            return (
                "script",
                script["id"] if script else project_id,
                script["content_hash"] if script else None,
            )
        if stage == "captions":
            captions = self._latest_captions(project_id)
            if captions:
                return "artifact", captions["id"], captions["content_hash"]
            return "project", project_id, None
        if stage in {"shots", "style", "assets", "delivery"}:
            return "project", project_id, None
        raise IntegrityError(f"A {stage} critique needs an artifact_id")

    def _critique_findings(
        self, project_id: str, stage: str, artifact_id: str | None
    ) -> list[CritiqueFinding]:
        if stage == "script":
            return self._script_findings(project_id)
        if stage == "shots":
            return self._shot_findings(project_id)
        if stage == "style":
            return self._style_findings(project_id)
        if stage == "assets":
            return self._asset_findings(project_id)
        if stage == "motion":
            return self._motion_findings(project_id, artifact_id)
        if stage == "visual":
            return self._visual_findings(project_id, artifact_id)
        if stage == "captions":
            return self._caption_findings(project_id, artifact_id)
        if stage == "audio":
            return self._audio_findings(project_id, artifact_id)
        if stage == "delivery":
            return self._delivery_findings(project_id)
        raise IntegrityError(f"Unknown critique stage: {stage}")

    @staticmethod
    def _finding(
        dimension: str,
        severity: str,
        summary: str,
        *,
        evidence: tuple[str, ...] = (),
        metric: str | None = None,
        threshold: str | None = None,
        action: str | None = None,
    ) -> CritiqueFinding:
        if severity in HIGH_SEVERITIES and not action:
            action = f"Correct and re-run {dimension} QC: {summary}"
        return CritiqueFinding(
            dimension=dimension,
            severity=severity,
            summary=summary[:2000],
            evidence=evidence,
            metric=str(metric)[:200] if metric is not None else None,
            threshold=str(threshold)[:200] if threshold is not None else None,
            corrective_action=action[:2000] if action else None,
        )

    def _script_findings(self, project_id: str) -> list[CritiqueFinding]:
        findings = []
        script = self.repository.current_script(project_id)
        if not script:
            findings.append(
                self._finding(
                    "script",
                    "blocker",
                    "No narration script is recorded",
                    action="Record and approve the narration script",
                )
            )
            return findings
        if not script.get("approved_by"):
            findings.append(
                self._finding(
                    "script",
                    "high",
                    "The script is not approved",
                    evidence=(script["id"],),
                )
            )
        words = str(script.get("text", "")).split()
        if len(words) < 8:
            findings.append(
                self._finding(
                    "script",
                    "high",
                    f"The script is only {len(words)} words",
                    evidence=(script["id"],),
                    metric=str(len(words)),
                    threshold=">= 8",
                )
            )
        return findings

    def _shot_findings(self, project_id: str) -> list[CritiqueFinding]:
        findings = []
        shots = self.repository.list_shots(project_id)
        if not shots:
            return [
                self._finding(
                    "shots",
                    "blocker",
                    "No shots are planned",
                    action="Create a shot plan before production",
                )
            ]
        total = sum(float(shot["duration_seconds"]) for shot in shots)
        if total <= 0:
            findings.append(
                self._finding("shots", "blocker", "Shot durations sum to zero")
            )
        if len(shots) < 2:
            findings.append(
                self._finding(
                    "shots",
                    "medium",
                    "The production has a single shot; no editorial variety",
                    evidence=tuple(shot["stable_key"] for shot in shots),
                )
            )
        subjects = []
        for shot in shots:
            requirements = shot.get("requirements") or {}
            subject = str(requirements.get("subject") or "")
            motion = requirements.get("motion") or {}
            if not subject and not motion:
                findings.append(
                    self._finding(
                        "shots",
                        "high",
                        f"{shot['stable_key']} has no subject or motion requirement",
                        evidence=(shot["id"],),
                    )
                )
            if subject:
                subjects.append(subject)
            if not requirements.get("camera"):
                findings.append(
                    self._finding(
                        "shots",
                        "low",
                        f"{shot['stable_key']} has no camera direction",
                        evidence=(shot["id"],),
                    )
                )
        if subjects and len(set(subjects)) == 1 and len(subjects) > 2:
            findings.append(
                self._finding(
                    "shots",
                    "medium",
                    "Every shot uses the same subject action; no shot variety",
                    evidence=tuple(subjects),
                )
            )
        return findings

    def _style_findings(self, project_id: str) -> list[CritiqueFinding]:
        findings = []
        productions = self.repository.list_shot_productions(project_id)
        if not productions:
            return [
                self._finding(
                    "style",
                    "high",
                    "No shot productions carry a preset or style",
                    action="Plan shot productions with preset and style IDs",
                )
            ]
        for production in productions:
            if not production.get("preset_id") or not production.get("style_id"):
                findings.append(
                    self._finding(
                        "style",
                        "high",
                        f"{production['shot_id']} is missing preset/style",
                        evidence=(production["id"],),
                    )
                )
        picks = self.slot_pick_summary(project_id)
        candidates = {
            item["id"]: item
            for item in self.repository.list_sourcing_candidates(project_id)
        }
        for pick in picks["slots"]:
            for role in ("primary", "backup"):
                selected = pick.get(role)
                if not selected:
                    continue
                style_fit = candidates.get(selected["id"], {}).get("style_fit", 0)
                if style_fit <= 0:
                    findings.append(
                        self._finding(
                            "style",
                            "high",
                            f"{pick['stable_key']} {role} has no style-fit score",
                            evidence=(selected["id"],),
                            metric=str(style_fit),
                            threshold="> 0",
                        )
                    )
        return findings

    def _asset_findings(self, project_id: str) -> list[CritiqueFinding]:
        findings = []
        slots = [
            slot
            for slot in self.repository.list_slots(project_id)
            if slot["role"] in VISUAL_SLOT_ROLES
        ]
        assignments: dict[str, dict[str, dict[str, Any]]] = {}
        for assignment in self.repository.list_slot_assets(project_id):
            assignments.setdefault(assignment["slot_id"], {})[assignment["role"]] = (
                assignment
            )
        for slot in slots:
            roles = assignments.get(slot["id"], {})
            for role in ("primary", "backup"):
                assignment = roles.get(role)
                if not assignment:
                    findings.append(
                        self._finding(
                            "assets",
                            "high",
                            f"{slot['stable_key']} has no {role} artifact",
                            evidence=(slot["id"],),
                        )
                    )
                    continue
                artifact = self.repository.get_artifact(assignment["artifact_id"])
                path = self.artifact_store.resolve(
                    project_id, artifact["relative_path"]
                )
                if not path.is_file():
                    findings.append(
                        self._finding(
                            "assets",
                            "blocker",
                            f"{slot['stable_key']} {role} file is missing",
                            evidence=(artifact["id"],),
                        )
                    )
                    continue
                probe = analyze_asset(path)
                if probe["width"] < 360 or probe["height"] < 640:
                    findings.append(
                        self._finding(
                            "assets",
                            "high",
                            f"{slot['stable_key']} {role} is only "
                            f"{probe['width']}x{probe['height']}",
                            evidence=(artifact["id"],),
                            metric=f"{probe['width']}x{probe['height']}",
                            threshold=">= 360x640",
                        )
                    )
                if slot.get("role") != "background" and probe.get("sharpness", 0) < 8:
                    findings.append(
                        self._finding(
                            "assets",
                            "high",
                            f"{slot['stable_key']} {role} is soft "
                            f"(sharpness {probe.get('sharpness')})",
                            evidence=(artifact["id"],),
                            metric=str(probe.get("sharpness")),
                            threshold=">= 8",
                        )
                    )
                if probe.get("dark_ratio", 0) > 0.6:
                    findings.append(
                        self._finding(
                            "assets",
                            "high",
                            f"{slot['stable_key']} {role} is mostly dark",
                            evidence=(artifact["id"],),
                            metric=str(probe.get("dark_ratio")),
                            threshold="<= 0.6",
                        )
                    )
                if slot.get("media_requirement") == "cutout" and not probe.get(
                    "has_alpha"
                ):
                    findings.append(
                        self._finding(
                            "assets",
                            "high",
                            f"{slot['stable_key']} is a cutout slot but the "
                            "asset has no alpha channel",
                            evidence=(artifact["id"], artifact["relative_path"]),
                            action=(
                                "Replace the full-frame asset with an alpha "
                                "cutout or change the slot media requirement"
                            ),
                        )
                    )
        return findings

    def _motion_findings(
        self, project_id: str, artifact_id: str | None
    ) -> list[CritiqueFinding]:
        findings = []
        levels = self.motion_levels(project_id)
        if not levels["complete"]:
            findings.append(
                self._finding(
                    "motion",
                    "high",
                    "Motion levels are incomplete: "
                    f"missing {levels['missing']}, "
                    f"below minimum {levels['below_minimum']}, "
                    f"no evidence {levels['missing_evidence']}",
                )
            )
        if artifact_id:
            final = self.final_motion_report(project_id, artifact_id)
            if not final["passed"]:
                findings.append(
                    self._finding(
                        "motion",
                        "blocker",
                        "The final render fails motion verification",
                        evidence=(artifact_id,),
                    )
                )
            continuity = self.motion_continuity_report(project_id, artifact_id)
            if not continuity["passed"]:
                findings.append(
                    self._finding(
                        "motion",
                        "high",
                        "Motion continuity failed",
                        evidence=(artifact_id,),
                        metric=str(continuity["checks"]),
                    )
                )
            for shot in final.get("shots", ()):
                if shot.get("residual_ratio", 0) < 0.002:
                    findings.append(
                        self._finding(
                            "motion",
                            "high",
                            f"{shot['stable_key']} has almost no measured subject motion",
                            evidence=(artifact_id,),
                            metric=str(shot.get("residual_ratio")),
                            threshold=">= 0.002",
                        )
                    )
                motion_area = shot.get("subject_area_ratio") or shot.get(
                    "motion_area_ratio", 0
                )
                if motion_area < 0.005:
                    findings.append(
                        self._finding(
                            "motion",
                            "high",
                            f"{shot['stable_key']} motion covers too little of the frame",
                            evidence=(artifact_id,),
                            metric=str(motion_area),
                            threshold=">= 0.005",
                        )
                    )
        return findings

    def _visual_findings(
        self, project_id: str, artifact_id: str | None
    ) -> list[CritiqueFinding]:
        if not artifact_id:
            return [
                self._finding(
                    "visual",
                    "blocker",
                    "No final artifact was supplied for visual QC",
                    action="Render the final artifact before visual critique",
                )
            ]
        artifact = self.repository.get_artifact(artifact_id)
        path = self.artifact_store.resolve(project_id, artifact["relative_path"])
        if not path.is_file():
            return [
                self._finding(
                    "visual",
                    "blocker",
                    "The final artifact file is missing",
                    evidence=(artifact_id,),
                )
            ]
        probe = analyze_video(path)
        findings = []
        if probe["width"] < 1080 or probe["height"] < 1920:
            findings.append(
                self._finding(
                    "visual",
                    "high",
                    f"Delivery resolution is {probe['width']}x{probe['height']}",
                    evidence=(artifact_id,),
                    metric=f"{probe['width']}x{probe['height']}",
                    threshold=">= 1080x1920",
                )
            )
        if probe["fps"] < 24:
            findings.append(
                self._finding(
                    "visual",
                    "high",
                    f"Delivery frame rate is {probe['fps']} fps",
                    evidence=(artifact_id,),
                    metric=str(probe["fps"]),
                    threshold=">= 24",
                )
            )
        for run in probe["dark_runs"]:
            if run["frames"] >= 2:
                findings.append(
                    self._finding(
                        "visual",
                        "blocker",
                        f"Near-black gap {run['start_seconds']}–"
                        f"{run['end_seconds']}s ({run['frames']} frames)",
                        evidence=(artifact_id,),
                        metric=str(run["min_luma"]),
                        threshold=">= 40 mean luma",
                        action=(
                            "Replace the stacked fades with a true crossfade "
                            "or a short cut, then re-render"
                        ),
                    )
                )
                break
        if probe["dead_frames"]:
            findings.append(
                self._finding(
                    "visual",
                    "blocker",
                    f"{probe['dead_frames']} dead frames detected",
                    evidence=(artifact_id,),
                )
            )
        if probe["sharpness"] < 12:
            findings.append(
                self._finding(
                    "visual",
                    "high",
                    f"Final render is soft (sharpness {probe['sharpness']})",
                    evidence=(artifact_id,),
                    metric=str(probe["sharpness"]),
                    threshold=">= 12",
                )
            )
        if probe["top_bar_ratio"] > 0.9 or probe["bottom_bar_ratio"] > 0.9:
            findings.append(
                self._finding(
                    "visual",
                    "high",
                    "Letterbox bars are present",
                    evidence=(artifact_id,),
                )
            )
        if probe["luma_mean"] < 30 or probe["luma_mean"] > 225:
            findings.append(
                self._finding(
                    "visual",
                    "high",
                    f"Mean luma is {probe['luma_mean']}",
                    evidence=(artifact_id,),
                    threshold="30–225",
                )
            )
        return findings

    def _caption_findings(
        self, project_id: str, artifact_id: str | None
    ) -> list[CritiqueFinding]:
        captions = (
            self.repository.get_artifact(artifact_id)
            if artifact_id
            else self._latest_captions(project_id)
        )
        if not captions:
            return [
                self._finding(
                    "captions",
                    "high",
                    "No caption artifact exists",
                    action="Export ASR captions and burn them",
                )
            ]
        srt = self.artifact_store.resolve(
            project_id, captions["relative_path"]
        ).read_text(encoding="utf-8")
        duration = self._video_duration(project_id, artifact_id) or 8.0
        tail_hold = float(
            (captions.get("operation") or {}).get("tail_hold_seconds") or 0.0
        )
        metrics = caption_metrics(srt, duration, tail_hold_seconds=tail_hold)
        findings = []
        if metrics["cue_count"] == 0:
            findings.append(self._finding("captions", "blocker", "The SRT has no cues"))
        if metrics["coverage_ratio"] < 0.9:
            findings.append(
                self._finding(
                    "captions",
                    "high",
                    f"Caption coverage is {metrics['coverage_ratio']:.0%}",
                    evidence=(captions["id"],),
                    metric=str(metrics["coverage_ratio"]),
                    threshold=">= 0.9",
                )
            )
        if metrics["max_cue_seconds"] > 4.0:
            findings.append(
                self._finding(
                    "captions",
                    "high",
                    f"A caption cue runs {metrics['max_cue_seconds']}s",
                    evidence=(captions["id"],),
                    metric=str(metrics["max_cue_seconds"]),
                    threshold="<= 4.0s",
                )
            )
        if metrics["max_chars_per_second"] > 15:
            findings.append(
                self._finding(
                    "captions",
                    "high",
                    f"Caption speed is {metrics['max_chars_per_second']} CPS",
                    evidence=(captions["id"],),
                    metric=str(metrics["max_chars_per_second"]),
                    threshold="<= 15",
                )
            )
        if metrics["max_lines"] > 2:
            findings.append(
                self._finding(
                    "captions",
                    "high",
                    f"A caption cue has {metrics['max_lines']} lines",
                    evidence=(captions["id"],),
                    threshold="<= 2",
                )
            )
        return findings

    def _audio_findings(
        self, project_id: str, artifact_id: str | None
    ) -> list[CritiqueFinding]:
        if not artifact_id:
            return [
                self._finding(
                    "audio",
                    "high",
                    "No final artifact was supplied for audio QC",
                )
            ]
        results = [
            result
            for result in self.repository.list_qa_results(project_id)
            if result["artifact_id"] == artifact_id
            and result["check_name"]
            in {"audio.loudness", "audio.true_peak", "technical.audio"}
        ]
        if not results:
            return [
                self._finding(
                    "audio",
                    "high",
                    "No audio QA results exist for the final artifact",
                    evidence=(artifact_id,),
                )
            ]
        findings = []
        for result in results:
            if result["status"] == "fail":
                findings.append(
                    self._finding(
                        "audio",
                        "high",
                        f"{result['check_name']} failed: {result['findings']}",
                        evidence=(result["id"],),
                    )
                )
            elif result["status"] == "warn":
                findings.append(
                    self._finding(
                        "audio",
                        "medium",
                        f"{result['check_name']} warned: {result['findings']}",
                        evidence=(result["id"],),
                    )
                )
        return findings

    def _delivery_findings(self, project_id: str) -> list[CritiqueFinding]:
        findings = []
        ab = self.ab_status(project_id)
        if ab["declared"] and not ab["complete"]:
            findings.append(
                self._finding(
                    "delivery",
                    "high",
                    "A/B variants are incomplete",
                    action="Bind both A and B variants",
                )
            )
        if ab["declared"] and not self.repository.latest_ab_delivery(project_id):
            findings.append(
                self._finding(
                    "delivery",
                    "high",
                    "No A/B delivery packet exists",
                    action="Build the A/B delivery packet",
                )
            )
        return findings

    def _latest_captions(self, project_id: str) -> dict[str, Any] | None:
        candidates = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if (artifact.get("operation") or {}).get("type") == "captions"
        ]
        return candidates[-1] if candidates else None

    def _video_duration(self, project_id: str, artifact_id: str | None) -> float:
        if not artifact_id:
            return 0.0
        artifact = self.repository.get_artifact(artifact_id)
        path = self.artifact_store.resolve(project_id, artifact["relative_path"])
        if not path.is_file():
            return 0.0
        return float(analyze_video(path)["duration_seconds"])

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from ..adapters.encode import FFmpegEncodeAdapter
from ..adapters.mux import FFmpegMuxAdapter
from ..enums import (
    ArtifactKind,
    ProjectStatus,
    QAStatus,
)
from ..errors import (
    ApprovalRequiredError,
    ConflictError,
    IntegrityError,
    NotFoundError,
)
from ..models import (
    DependencyRef,
)
from ..qa import (
    build_report,
    check_audio_layers,
    run_animatic_qa,
    run_voiced_animatic_qa,
)
from ..utils import canonical_json, sha256_bytes
from ._shared import (
    _as_float,
    _caption_coverage,
)


class QaUseCases:
    def qa_render(
        self,
        project_id: str,
        *,
        artifact_id: str,
        expected_duration: float | None = None,
        expected_width: int | None = None,
        expected_height: int | None = None,
        expected_fps: float | None = None,
        expect_audio: bool = True,
        captions_artifact_id: str | None = None,
        max_true_peak_dbtp: float | None = None,
        probe: Any | None = None,
        measure_loudness: Any | None = None,
    ) -> dict[str, Any]:
        """Technical QA on a rendered deliverable, recorded per check."""

        true_peak_limit = max_true_peak_dbtp
        if true_peak_limit is None:
            true_peak_limit = -1.0
            creative = self.current_creative_plan(project_id)
            if creative:
                subjects = {
                    str(subject).casefold()
                    for subject in creative["plan"].profile.subjects
                }
                if "kids" in subjects or "children" in subjects:
                    from ..kids import KIDS_TRUE_PEAK_DBTP

                    true_peak_limit = KIDS_TRUE_PEAK_DBTP
        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Artifact belongs to a different project")
        if not self.verify_artifact(artifact_id):
            raise IntegrityError("Rendered file does not match its registered hash")
        path = self.artifact_store.resolve(project_id, artifact["relative_path"])
        metadata = (probe or FFmpegEncodeAdapter().probe)(path)
        checks: list[dict[str, Any]] = []

        def record(
            name: str, status: str, detail: str, evidence: dict[str, Any]
        ) -> None:
            self.repository.record_qa_result(
                project_id=project_id,
                artifact_id=artifact_id,
                check_name=name,
                status=status,
                findings=evidence,
                evidence_hash=artifact["content_hash"],
            )
            checks.append(
                {
                    "check": name,
                    "status": status,
                    "detail": detail,
                    "evidence": evidence,
                }
            )

        container = str(metadata.get("format", "")).lower()
        container_tokens = {token.strip() for token in container.split(",")}
        record(
            "technical.container",
            "pass" if container_tokens & {"mp4", "mov", "m4a"} else "fail",
            f"container {container or 'unknown'}",
            {"format": metadata.get("format")},
        )
        codec = str(metadata.get("codec", "")).lower()
        record(
            "technical.codec",
            "pass" if codec in {"h264", "hevc"} else "warn",
            f"video codec {codec or 'unknown'}",
            {"codec": metadata.get("codec")},
        )
        duration = float(metadata.get("duration_seconds") or 0)
        if expected_duration is None:
            record(
                "technical.duration",
                "pass",
                f"duration {duration}s",
                {"duration_seconds": duration},
            )
        else:
            delta = abs(duration - expected_duration)
            status = "pass" if delta <= 0.25 else "warn" if delta <= 1.0 else "fail"
            record(
                "technical.duration",
                status,
                f"duration {duration}s vs expected {expected_duration}s",
                {"duration_seconds": duration, "expected": expected_duration},
            )
        width = int(metadata.get("width_px") or 0)
        height = int(metadata.get("height_px") or 0)
        resolution_ok = True
        if expected_width is not None:
            resolution_ok = resolution_ok and width == expected_width
        if expected_height is not None:
            resolution_ok = resolution_ok and height == expected_height
        record(
            "technical.resolution",
            "pass" if resolution_ok else "fail",
            f"{width}x{height}",
            {
                "width_px": width,
                "height_px": height,
                "expected_width": expected_width,
                "expected_height": expected_height,
            },
        )
        if expected_fps is not None:
            fps = float(metadata.get("fps") or 0)
            record(
                "technical.frame_rate",
                "pass" if abs(fps - expected_fps) <= 0.51 else "warn",
                f"{fps} fps",
                {"fps": fps, "expected": expected_fps},
            )
        has_audio = bool(metadata.get("has_audio"))
        record(
            "technical.audio",
            "pass" if has_audio == expect_audio else "fail",
            f"audio stream {'present' if has_audio else 'absent'}",
            {"has_audio": has_audio, "expected": expect_audio},
        )
        if has_audio and expect_audio:
            try:
                loudness = (measure_loudness or FFmpegMuxAdapter().loudness)(path)
            except (OSError, RuntimeError, ValueError):
                record(
                    "audio.loudness",
                    "warn",
                    "loudness could not be measured",
                    {},
                )
            else:
                integrated = _as_float(loudness.get("input_i"))
                peak = _as_float(loudness.get("input_tp"))
                if integrated is None:
                    loudness_status = "warn"
                elif -24.0 <= integrated <= -9.0:
                    loudness_status = "pass"
                elif -27.0 <= integrated <= -6.0:
                    loudness_status = "warn"
                else:
                    loudness_status = "fail"
                record(
                    "audio.loudness",
                    loudness_status,
                    f"integrated {integrated if integrated is not None else 'n/a'} LUFS",
                    {"integrated_lufs": integrated},
                )
                if peak is not None:
                    peak_status = (
                        "pass"
                        if peak <= true_peak_limit
                        else "warn"
                        if peak <= true_peak_limit + 1.0
                        else "fail"
                    )
                    record(
                        "audio.true_peak",
                        peak_status,
                        f"true peak {peak:.1f} dBTP (limit {true_peak_limit:.1f})",
                        {
                            "true_peak_dbtp": peak,
                            "limit_dbtp": true_peak_limit,
                        },
                    )
        if captions_artifact_id:
            captions = self.repository.get_artifact(captions_artifact_id)
            if captions["project_id"] != project_id:
                raise ConflictError("Caption artifact belongs to a different project")
            srt_path = self.artifact_store.resolve(
                project_id, captions["relative_path"]
            )
            covered, cues, overrun = _caption_coverage(
                srt_path.read_text(encoding="utf-8"), duration
            )
            ratio = covered / duration if duration > 0 else 0.0
            coverage_status = (
                "pass" if ratio >= 0.9 else "warn" if ratio >= 0.7 else "fail"
            )
            record(
                "captions.coverage",
                coverage_status,
                f"{cues} cues covering {ratio * 100:.0f}% of the runtime",
                {
                    "cues": cues,
                    "covered_seconds": round(covered, 3),
                    "ratio": round(ratio, 3),
                },
            )
            record(
                "captions.timing",
                "fail" if overrun else "pass",
                "cue extends past the runtime" if overrun else "all cues fit",
                {"overrun": overrun},
            )

        if "audio_layers" in artifact["operation"]:
            for check in check_audio_layers(
                {"audio_layers": artifact["operation"]["audio_layers"]}
            ):
                record(check.check, check.status.value, check.detail, check.evidence)

        statuses = {check["status"] for check in checks}
        overall = (
            QAStatus.FAIL.value
            if QAStatus.FAIL.value in statuses
            else QAStatus.WARN.value
            if QAStatus.WARN.value in statuses
            else QAStatus.PASS.value
        )
        report = {
            "schema": "pinaka.render-qa/v1",
            "project_id": project_id,
            "artifact_id": artifact_id,
            "content_hash": artifact["content_hash"],
            "overall": overall,
            "metadata": metadata,
            "checks": checks,
        }
        content = canonical_json(report).encode("utf-8")
        digest = sha256_bytes(content)
        report_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.QA,
            relative_path=f"qa/render-report-{digest[:12]}.json",
            content=content,
            operation={
                "type": "render_qa",
                "artifact_id": artifact_id,
                "overall": overall,
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=artifact_id,
                    entity_hash=artifact["content_hash"],
                ),
            ),
        )
        return {
            "overall": overall,
            "checks": checks,
            "report": report,
            "report_artifact": report_artifact,
        }

    def qa_animatic(self, project_id: str) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status not in {
            ProjectStatus.ANIMATIC_READY,
            ProjectStatus.ANIMATIC_APPROVED,
        }:
            raise ApprovalRequiredError(
                "Animatic QA requires an animatic-ready project"
            )
        bundle = self._latest_animatic_bundle(project_id)
        if bundle:
            manifest_artifact, manifest, artifacts = bundle
        else:
            manifest_artifact = None
            manifest = None
            legacy_paths = {
                "timeline": "artifacts/timeline.json",
                "animatic": "designs/animatic.html",
                "captions": "artifacts/captions.srt",
            }
            artifacts = {
                name: self.repository.artifact_by_path(project_id, path)
                for name, path in legacy_paths.items()
            }
        missing = [name for name, artifact in artifacts.items() if artifact is None]
        if missing:
            raise NotFoundError(
                "Animatic QA is missing artifacts: " + ", ".join(sorted(missing))
            )
        verified = all(
            self.verify_artifact(artifact["id"]) for artifact in artifacts.values()
        )
        timeline = json.loads(self.read_artifact_text(artifacts["timeline"]["id"]))
        html_text = self.read_artifact_text(artifacts["animatic"]["id"])
        srt_text = self.read_artifact_text(artifacts["captions"]["id"])
        grounding_kinds = {
            bundle["kind"]
            for bundle in self.repository.current_grounding_bundles(project_id)
        }
        checks = run_animatic_qa(
            timeline=timeline,
            html_text=html_text,
            srt_text=srt_text,
            artifacts_verified=verified,
            grounding_kinds=grounding_kinds,
        )
        report = build_report(
            project_id=project_id,
            subject_artifact_id=artifacts["animatic"]["id"],
            checks=checks,
        )
        report_bytes = canonical_json(report.model_dump(mode="json")).encode("utf-8")
        report_hash = sha256_bytes(report_bytes)
        report_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.QA,
            relative_path=f"qa/animatic-report-{report_hash[:12]}.json",
            content=report_bytes,
            operation={
                "type": "animatic_qa",
                "overall": report.overall.value,
                "checks": len(checks),
            },
            dependencies=tuple(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=artifact["id"],
                    entity_hash=artifact["content_hash"],
                )
                for artifact in artifacts.values()
            ),
        )
        rows = [
            self.repository.record_qa_result(
                project_id=project_id,
                artifact_id=artifacts["animatic"]["id"],
                check_name=check.check,
                status=check.status.value,
                findings=check.model_dump(mode="json"),
                evidence_hash=report_hash,
            )
            for check in checks
        ]
        return {
            "project": project.model_dump(mode="json"),
            "overall": report.overall.value,
            "checks": [check.model_dump(mode="json") for check in checks],
            "report": report.model_dump(mode="json"),
            "report_artifact": report_artifact,
            "manifest_artifact": manifest_artifact,
            "animatic_bundle": manifest,
            "qa_results": rows,
        }

    @staticmethod
    def _validate_mux_result(result: Any, duration: Decimal) -> None:
        metadata = result.metadata
        if abs(float(metadata.get("duration_seconds", 0)) - float(duration)) > 0.05:
            raise IntegrityError("Muxed duration does not match the approved timeline")
        if (
            metadata.get("video_codec") != "h264"
            or metadata.get("width_px") != 1080
            or metadata.get("height_px") != 1920
            or float(metadata.get("fps", 0)) != 30.0
        ):
            raise IntegrityError(
                "Muxed video stream does not match the approved profile"
            )
        if (
            metadata.get("audio_codec") != "aac"
            or metadata.get("audio_sample_rate") != 48000
            or metadata.get("audio_channels") != "stereo"
        ):
            raise IntegrityError(
                "Muxed audio stream does not match the approved profile"
            )
        try:
            integrated = float(result.loudness.get("input_i"))
            true_peak = float(result.loudness.get("input_tp"))
        except (TypeError, ValueError) as error:
            raise IntegrityError("Muxed loudness measurement is invalid") from error
        if abs(integrated + 16.0) > 1.0:
            raise IntegrityError(
                f"Muxed loudness {integrated:.1f} LUFS is outside the -16 LUFS target"
            )
        if true_peak > -1.0:
            raise IntegrityError(
                f"Muxed true peak {true_peak:.1f} dBTP exceeds the -1.0 dBTP ceiling"
            )

    def qa_voiced_animatic(self, project_id: str) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        if project.status not in {
            ProjectStatus.ANIMATIC_READY,
            ProjectStatus.ANIMATIC_APPROVED,
            ProjectStatus.FINAL_CANDIDATE,
        }:
            raise ApprovalRequiredError(
                "Voiced QA requires an animatic-ready or final-candidate project"
            )
        mux_manifests = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if artifact["kind"] == ArtifactKind.PREVIEW.value
            and artifact["relative_path"].startswith("renders/mux-manifest-")
        ]
        if not mux_manifests:
            raise NotFoundError("No voiced mux manifest exists for this project")
        mux_manifest_artifact = max(mux_manifests, key=lambda item: item["created_at"])
        if not self.verify_artifact(mux_manifest_artifact["id"]):
            raise IntegrityError("Mux manifest failed hash verification")
        mux_manifest = json.loads(self.read_artifact_text(mux_manifest_artifact["id"]))
        video_artifact = self.repository.get_artifact(mux_manifest["video_artifact_id"])
        narration_artifact = self.repository.get_artifact(
            mux_manifest["narration_artifact_id"]
        )
        video_verified = self.verify_artifact(video_artifact["id"])
        narration_verified = self.verify_artifact(narration_artifact["id"])

        bundle = self._latest_animatic_bundle(project_id)
        if bundle:
            _, _, animatic_artifacts = bundle
        else:
            legacy_paths = {
                "timeline": "artifacts/timeline.json",
                "animatic": "designs/animatic.html",
                "captions": "artifacts/captions.srt",
            }
            animatic_artifacts = {
                name: self.repository.artifact_by_path(project_id, path)
                for name, path in legacy_paths.items()
            }
        if animatic_artifacts["timeline"] is None:
            raise NotFoundError("Voiced QA requires the animatic timeline")
        timeline = json.loads(
            self.read_artifact_text(animatic_artifacts["timeline"]["id"])
        )
        expected_text_hash = sha256_bytes(
            str(timeline.get("voiceover") or "").encode("utf-8")
        )
        checks = run_voiced_animatic_qa(
            mux_manifest=mux_manifest,
            video_verified=video_verified,
            narration_verified=narration_verified,
            expected_text_hash=expected_text_hash,
        )
        checks = checks + check_audio_layers(mux_manifest)
        report = build_report(
            project_id=project_id,
            subject_artifact_id=video_artifact["id"],
            checks=checks,
        )
        report_bytes = canonical_json(report.model_dump(mode="json")).encode("utf-8")
        report_hash = sha256_bytes(report_bytes)
        report_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.QA,
            relative_path=f"qa/voiced-report-{report_hash[:12]}.json",
            content=report_bytes,
            operation={
                "type": "voiced_animatic_qa",
                "overall": report.overall.value,
                "checks": len(checks),
            },
            dependencies=(
                DependencyRef(
                    entity_type="artifact",
                    entity_id=video_artifact["id"],
                    entity_hash=video_artifact["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=narration_artifact["id"],
                    entity_hash=narration_artifact["content_hash"],
                ),
                DependencyRef(
                    entity_type="artifact",
                    entity_id=mux_manifest_artifact["id"],
                    entity_hash=mux_manifest_artifact["content_hash"],
                ),
            ),
        )
        rows = [
            self.repository.record_qa_result(
                project_id=project_id,
                artifact_id=video_artifact["id"],
                check_name=check.check,
                status=check.status.value,
                findings=check.model_dump(mode="json"),
                evidence_hash=report_hash,
            )
            for check in checks
        ]
        return {
            "project": project.model_dump(mode="json"),
            "overall": report.overall.value,
            "checks": [check.model_dump(mode="json") for check in checks],
            "report": report.model_dump(mode="json"),
            "report_artifact": report_artifact,
            "video_artifact": video_artifact,
            "qa_results": rows,
        }

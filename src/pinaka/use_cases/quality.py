from __future__ import annotations

import json
from typing import Any

import cv2
import numpy as np

from ..contracts.quality import (
    CreativeReviewCreate,
    IntentMatrixCreate,
    QualityContractCreate,
)
from ..enums import ArtifactKind
from ..errors import ApprovalRequiredError, ConflictError, IntegrityError
from ..utils import canonical_json, sha256_json, utc_now
from ..visual_qc import analyze_video, caption_metrics


class QualityUseCases:
    """The frozen quality contract, intent matrix and creative review."""

    def record_quality_contract(
        self, project_id: str, request: QualityContractCreate
    ) -> dict[str, Any]:
        self.repository.get_project(project_id)
        body = {
            "schema": "quality-contract/v1",
            "project_id": project_id,
            **request.model_dump(mode="json"),
        }
        content = canonical_json(body).encode("utf-8")
        digest = sha256_json(body)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.CONTRACT,
            relative_path=f"quality/contract-{digest[:12]}.json",
            content=content,
            operation={
                "type": "quality_contract",
                "purpose": request.purpose,
                "publication_allowed": request.publication_allowed,
                "human_review_required": request.human_review_required,
                "review_required": request.review_required,
                "must_have_beats": list(request.must_have_beats),
                "execution_owner": "local",
                "studio_role": "none",
            },
        )
        return {"artifact": artifact, "contract": body}

    def current_quality_contract(self, project_id: str) -> dict[str, Any] | None:
        return self._latest_artifact(project_id, "quality_contract")

    def record_intent_matrix(
        self, project_id: str, request: IntentMatrixCreate
    ) -> dict[str, Any]:
        contract = self.current_quality_contract(project_id)
        if not contract:
            raise IntegrityError("Record a quality contract before the intent matrix")
        required = set(contract["contract"]["must_have_beats"])
        delivered = {
            beat.beat_id for beat in request.beats if beat.status == "delivered"
        }
        missing = required - delivered
        if missing:
            raise ConflictError(
                "Intent matrix is missing delivered beats: "
                + ", ".join(sorted(missing))
            )
        body = {
            "schema": "intent-matrix/v1",
            "project_id": project_id,
            **request.model_dump(mode="json"),
        }
        content = canonical_json(body).encode("utf-8")
        digest = sha256_json(body)
        artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"quality/intent-{digest[:12]}.json",
            content=content,
            operation={
                "type": "intent_matrix",
                "beats": len(request.beats),
                "delivered": sum(
                    1 for beat in request.beats if beat.status == "delivered"
                ),
                "execution_owner": "local",
                "studio_role": "none",
            },
        )
        return {"artifact": artifact, "matrix": body}

    def current_intent_matrix(self, project_id: str) -> dict[str, Any] | None:
        return self._latest_artifact(project_id, "intent_matrix")

    def record_creative_review(
        self, project_id: str, request: CreativeReviewCreate
    ) -> dict[str, Any]:
        artifact = self.repository.get_artifact(request.artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Review artifact belongs to another project")
        if artifact["content_hash"] != request.artifact_hash:
            raise ConflictError("Review hash does not match the exact artifact")
        body = {
            "schema": "creative-review/v1",
            "project_id": project_id,
            **request.model_dump(mode="json"),
        }
        content = canonical_json(body).encode("utf-8")
        digest = sha256_json(body)
        review_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.QA,
            relative_path=f"quality/review-{digest[:12]}.json",
            content=content,
            operation={
                "type": "creative_review",
                "artifact_id": request.artifact_id,
                "artifact_hash": request.artifact_hash,
                "reviewer_kind": request.reviewer_kind,
                "verdict": request.verdict,
                "average_score": sum(request.scores.values()) / len(request.scores),
                "execution_owner": "local",
                "studio_role": "none",
            },
        )
        return {"artifact": review_artifact, "review": body}

    def current_creative_review(
        self, project_id: str, artifact_id: str
    ) -> dict[str, Any] | None:
        artifact = self.repository.get_artifact(artifact_id)
        candidates = [
            item
            for item in self.repository.list_artifacts(project_id)
            if (item.get("operation") or {}).get("type") == "creative_review"
            and (item.get("operation") or {}).get("artifact_hash")
            == artifact["content_hash"]
        ]
        if not candidates:
            return None
        review = candidates[-1]
        path = self.artifact_store.resolve(project_id, review["relative_path"])
        return {
            "artifact": review,
            "review": json.loads(path.read_text(encoding="utf-8")),
        }

    def quality_readiness(self, project_id: str, *, artifact_id: str) -> dict[str, Any]:
        contract_record = self.current_quality_contract(project_id)
        if not contract_record:
            return {
                "passed": False,
                "issues": ["no quality contract recorded"],
                "warnings": [],
            }
        contract = contract_record["contract"]
        intent_record = self.current_intent_matrix(project_id)
        issues: list[str] = []
        warnings: list[str] = []
        if not intent_record:
            issues.append("no intent matrix recorded")
        else:
            delivered = {
                beat["beat_id"]: beat
                for beat in intent_record["matrix"]["beats"]
                if beat["status"] == "delivered"
            }
            missing = set(contract["must_have_beats"]) - set(delivered)
            if missing:
                issues.append(
                    "undelivered must-have beats: " + ", ".join(sorted(missing))
                )
            for beat_id, beat in delivered.items():
                if not beat.get("evidence"):
                    issues.append(f"delivered beat has no evidence: {beat_id}")
        artifact = self.repository.get_artifact(artifact_id)
        review_record = self.current_creative_review(project_id, artifact_id)
        if contract["review_required"]:
            if not review_record:
                issues.append("no creative review for the exact final artifact")
            else:
                review = review_record["review"]
                if review["verdict"] != "pass":
                    issues.append(f"creative review verdict is {review['verdict']}")
                if (
                    contract["human_review_required"]
                    and review["reviewer_kind"] != "human"
                ):
                    issues.append("creative review requires a human reviewer")
                average = sum(review["scores"].values()) / len(review["scores"])
                if average < 4.0:
                    warnings.append(f"creative review average is {average:.2f}")
        if contract["purpose"] == "production" and not contract["publication_allowed"]:
            issues.append("production contract does not allow publication")
        return {
            "passed": not issues,
            "issues": issues,
            "warnings": warnings,
            "contract": contract,
            "intent_matrix": intent_record["matrix"] if intent_record else None,
            "review": review_record["review"] if review_record else None,
            "artifact": {
                "id": artifact["id"],
                "hash": artifact["content_hash"],
            },
        }

    def require_quality_readiness(
        self, project_id: str, *, artifact_id: str
    ) -> dict[str, Any]:
        readiness = self.quality_readiness(project_id, artifact_id=artifact_id)
        if not readiness["passed"]:
            raise ApprovalRequiredError(
                "Quality contract is not ready: " + "; ".join(readiness["issues"])
            )
        return readiness

    def build_review_packet(
        self, project_id: str, *, artifact_id: str
    ) -> dict[str, Any]:
        """Build a contact sheet and evidence packet for the exact final hash."""

        artifact = self.repository.get_artifact(artifact_id)
        if artifact["project_id"] != project_id:
            raise ConflictError("Review packet artifact belongs to another project")
        path = self.artifact_store.resolve(project_id, artifact["relative_path"])
        probe = analyze_video(path)
        caption_artifact = next(
            (
                item
                for item in reversed(self.repository.list_artifacts(project_id))
                if (item.get("operation") or {}).get("type") == "captions"
            ),
            None,
        )
        captions = None
        if caption_artifact:
            srt = self.artifact_store.resolve(
                project_id, caption_artifact["relative_path"]
            ).read_text(encoding="utf-8")
            captions = caption_metrics(srt, probe["duration_seconds"])
        qa = [
            result
            for result in self.repository.list_qa_results(project_id)
            if result["artifact_id"] == artifact_id
        ]
        try:
            motion = self.motion_continuity_report(project_id, artifact_id)
        except (ValueError, OSError):
            motion = None
        contact_sheet = self._contact_sheet(
            project_id, path, artifact_id, probe["duration_seconds"]
        )
        packet = {
            "schema": "review-packet/v1",
            "created_at": utc_now(),
            "project_id": project_id,
            "artifact": {
                "id": artifact["id"],
                "hash": artifact["content_hash"],
                "path": artifact["relative_path"],
            },
            "video": probe,
            "captions": captions,
            "qa": qa,
            "motion": motion,
            "contact_sheet_artifact_id": contact_sheet["id"],
            "readiness": self.quality_readiness(project_id, artifact_id=artifact_id),
        }
        content = canonical_json(packet).encode("utf-8")
        digest = sha256_json(packet)
        packet_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.QA,
            relative_path=f"quality/review-packet-{digest[:12]}.json",
            content=content,
            operation={
                "type": "review_packet",
                "artifact_hash": artifact["content_hash"],
                "contact_sheet_artifact_id": contact_sheet["id"],
                "execution_owner": "local",
                "studio_role": "none",
            },
        )
        return {
            "packet": packet,
            "artifact": packet_artifact,
            "contact_sheet": contact_sheet,
        }

    def _contact_sheet(
        self, project_id: str, video_path: Any, artifact_id: str, duration: float
    ) -> dict[str, Any]:
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise IntegrityError("Cannot open the final video for the contact sheet")
        times = [(index + 0.5) * duration / 8 for index in range(8)]
        frames = []
        for timestamp in times:
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok:
                break
            frame = cv2.resize(frame, (270, 480), interpolation=cv2.INTER_AREA)
            cv2.rectangle(frame, (0, 0), (270, 26), (0, 0, 0), -1)
            cv2.putText(
                frame,
                f"{timestamp:.1f}s",
                (6, 18),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
            frames.append(frame)
        capture.release()
        if len(frames) < 8:
            raise IntegrityError("Could not sample eight frames for the review packet")
        rows = [np.hstack(frames[index * 4 : (index + 1) * 4]) for index in range(2)]
        sheet = np.vstack(rows)
        encoded, buffer = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not encoded:
            raise IntegrityError("Could not encode the contact sheet")
        digest = sha256_json({"artifact_id": artifact_id})[:12]
        return self.write_artifact(
            project_id,
            kind=ArtifactKind.PREVIEW,
            relative_path=f"quality/contact-sheet-{digest}.jpg",
            content=buffer.tobytes(),
            operation={
                "type": "review_contact_sheet",
                "artifact_id": artifact_id,
                "frames": 8,
                "execution_owner": "local",
                "studio_role": "none",
            },
        )

    def quality_status(
        self, project_id: str, *, artifact_id: str | None = None
    ) -> dict[str, Any]:
        return {
            "contract": self.current_quality_contract(project_id),
            "intent_matrix": self.current_intent_matrix(project_id),
            "artifact_id": artifact_id,
            "readiness": (
                self.quality_readiness(project_id, artifact_id=artifact_id)
                if artifact_id
                else None
            ),
        }

    def _latest_artifact(
        self, project_id: str, operation_type: str
    ) -> dict[str, Any] | None:
        candidates = [
            artifact
            for artifact in self.repository.list_artifacts(project_id)
            if (artifact.get("operation") or {}).get("type") == operation_type
        ]
        if not candidates:
            return None
        artifact = candidates[-1]
        path = self.artifact_store.resolve(project_id, artifact["relative_path"])
        return {
            "artifact": artifact,
            "contract"
            if operation_type == "quality_contract"
            else "matrix"
            if operation_type == "intent_matrix"
            else "review": json.loads(path.read_text(encoding="utf-8")),
        }

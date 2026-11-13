from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ..contracts.change_request import ChangeRequestCreate
from ..enums import (
    ArtifactKind,
    ProjectStatus,
)
from ..errors import ConflictError, IntegrityError
from ..utils import canonical_json, sha256_bytes

CHANGE_TYPES = ["copy", "visual", "motion", "audio", "caption", "metadata", "other"]

CHANGE_REQUEST_FIELDS: tuple[tuple[str, str], ...] = (
    ("requested_by", ""),
    ("requested_at", ""),
    ("change_type", ""),
    ("element_or_artifact_id", ""),
    ("current_hash", ""),
    ("requested_change", ""),
    ("reason", ""),
    ("deadline", ""),
    ("approver", ""),
    ("approval_reference", ""),
)


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _yaml(value: Any, indent: int = 0) -> list[str]:
    pad = "  " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        if not value:
            return [f"{pad}{{}}"]
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}{key}:")
                lines.extend(_yaml(item, indent + 1))
            else:
                lines.append(f"{pad}{key}: {_yaml_scalar(item)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{pad}[]"]
        for item in value:
            if isinstance(item, dict):
                lines.append(f"{pad}-")
                lines.extend(_yaml(item, indent + 1))
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
        return lines
    return [f"{pad}{_yaml_scalar(value)}"]


class ChangeRequestUseCases:
    """Generate the delivered change-request template in Markdown and YAML."""

    def change_request(
        self, project_id: str, output_dir: str | None = None
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        brief = self.repository.latest_workable_brief(project_id)
        contract = self.current_production_contract(project_id)
        artifacts = {
            item["id"]: item for item in self.repository.list_artifacts(project_id)
        }
        elements = self.repository.list_creative_elements(project_id)
        slots = self.repository.list_slots(project_id)
        picks = {
            item["slot_id"]: item for item in self.repository.slot_picks(project_id)
        }
        assignments: dict[str, dict[str, dict[str, Any]]] = {}
        for assignment in self.repository.list_slot_assets(project_id):
            assignments.setdefault(assignment["slot_id"], {})[assignment["role"]] = (
                assignment
            )
        flow = self.flow_completeness(project_id)

        slot_entries = []
        for slot in slots:
            pick = picks.get(slot["id"], {})
            roles = assignments.get(slot["id"], {})
            slot_entries.append(
                {
                    "slot_id": slot["id"],
                    "stable_key": slot["stable_key"],
                    "role": slot["role"],
                    "treatment_ceiling": slot.get("treatment_ceiling"),
                    "primary": self._bound_entry(
                        roles.get("primary"), pick.get("primary"), artifacts
                    ),
                    "backup": self._bound_entry(
                        roles.get("backup"), pick.get("backup"), artifacts
                    ),
                }
            )

        body = {
            "schema": "change-request/v1",
            "project": {
                "id": project.id,
                "name": project.name,
                "status": project.status.value,
            },
            "brief": (
                {
                    "id": brief["id"],
                    "content_hash": brief["content_hash"],
                    "objective": brief["package"]["objective"],
                }
                if brief
                else None
            ),
            "production_contract": (
                {
                    "artifact_id": contract["artifact"]["id"],
                    "hash": contract["artifact"]["content_hash"],
                }
                if contract
                else None
            ),
            "flow_complete": flow["complete"],
            "elements": [
                {
                    "element_id": element["id"],
                    "stable_key": element["stable_key"],
                    "kind": element["kind"],
                    "name": element["name"],
                    "specification": element["specification"],
                    "continuity": element["continuity"],
                    "artifact_id": element.get("source_artifact_id"),
                    "artifact_hash": (
                        artifacts[element["source_artifact_id"]]["content_hash"]
                        if element.get("source_artifact_id") in artifacts
                        else None
                    ),
                }
                for element in elements
            ],
            "slots": slot_entries,
            "production": (
                self.current_creative_plan(project_id)["plan"].production.model_dump(
                    mode="json"
                )
                if self.current_creative_plan(project_id)
                else None
            ),
            "shot_productions": self.repository.list_shot_productions(project_id),
            "deliverables": self._deliverables(project_id, artifacts),
            "change_request": dict(CHANGE_REQUEST_FIELDS),
            "allowed_change_types": CHANGE_TYPES,
        }
        yaml_body = "\n".join(_yaml(body)) + "\n"
        markdown = self._markdown(body)
        yaml_hash = sha256_bytes(yaml_body.encode("utf-8"))
        markdown_bytes = markdown.encode("utf-8")
        yaml_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"delivery/CHANGE-REQUEST-{yaml_hash[:12]}.yaml",
            content=yaml_body.encode("utf-8"),
            operation={"type": "change_request", "format": "yaml"},
        )
        markdown_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"delivery/CHANGE-REQUEST-{yaml_hash[:12]}.md",
            content=markdown_bytes,
            operation={"type": "change_request", "format": "markdown"},
        )
        written = {}
        if output_dir:
            destination = Path(output_dir).expanduser()
            destination.mkdir(parents=True, exist_ok=True)
            yaml_path = destination / "CHANGE-REQUEST.yaml"
            md_path = destination / "CHANGE-REQUEST.md"
            yaml_path.write_text(yaml_body, encoding="utf-8")
            md_path.write_text(markdown, encoding="utf-8")
            written = {"yaml": str(yaml_path), "markdown": str(md_path)}
        return {
            "change_request": body,
            "yaml": yaml_body,
            "markdown": markdown,
            "yaml_artifact": yaml_artifact,
            "markdown_artifact": markdown_artifact,
            "written": written,
        }

    def submit_change_request(
        self,
        project_id: str,
        *,
        path: str | None = None,
        text: str | None = None,
        submitted_by: str | None = None,
    ) -> dict[str, Any]:
        """Parse a filled template, validate it and record the impact analysis."""

        if bool(path) == bool(text):
            raise IntegrityError("Provide exactly one change-request source")
        raw = Path(path).expanduser().read_text(encoding="utf-8") if path else str(text)
        document = self._load_change_document(raw)
        self.repository.get_project(project_id)
        project_section = document.get("project") or {}
        if project_section.get("id") != project_id:
            raise ConflictError("Change request belongs to a different project")
        if document.get("schema") != "change-request/v1":
            raise ConflictError("Unsupported change-request schema")

        brief = self.repository.latest_workable_brief(project_id)
        brief_section = document.get("brief") or {}
        if brief and brief_section.get("content_hash") not in {
            None,
            brief["content_hash"],
        }:
            raise ConflictError("Change request is bound to a stale brief hash")
        contract = self.current_production_contract(project_id)
        contract_section = document.get("production_contract") or {}
        if contract and contract_section.get("hash") not in {
            None,
            contract["artifact"]["content_hash"],
        }:
            raise ConflictError("Change request is bound to a stale contract hash")
        actual_flow = self.flow_completeness(project_id)["complete"]
        if bool(document.get("flow_complete")) != actual_flow:
            raise ConflictError(
                "Change request flow_complete does not match the recorded ledger"
            )

        form = document.get("change_request")
        if not isinstance(form, dict):
            raise ConflictError("change_request must be a mapping")
        payload = {
            "requested_by": str(form.get("requested_by") or submitted_by or "").strip(),
            "requested_at": str(form.get("requested_at") or "").strip(),
            "change_type": str(form.get("change_type") or "").strip(),
            "requested_change": str(form.get("requested_change") or "").strip(),
            "reason": str(form.get("reason") or "").strip(),
            "element_or_artifact_id": str(
                form.get("element_or_artifact_id") or ""
            ).strip(),
            "current_hash": str(form.get("current_hash") or "").strip() or None,
            "deadline": str(form.get("deadline") or "").strip() or None,
            "approver": str(form.get("approver") or "").strip(),
            "approval_reference": str(form.get("approval_reference") or "").strip()
            or None,
            "version_scope": str(form.get("version_scope") or "none").strip(),
        }
        missing = [
            key
            for key in (
                "requested_by",
                "requested_at",
                "change_type",
                "requested_change",
                "reason",
                "approver",
            )
            if not payload[key]
        ]
        if missing:
            raise ConflictError(
                "Change request is missing required fields: " + ", ".join(missing)
            )
        request = ChangeRequestCreate.model_validate(payload)
        target_type, target_id = self._resolve_change_target(
            project_id, request.element_or_artifact_id
        )
        impact = self._change_impact(project_id, request, target_type, target_id)
        document_hash = sha256_bytes(raw.encode("utf-8"))
        record = self.repository.create_change_request(
            project_id,
            request,
            target_type=target_type,
            target_id=target_id,
            document_hash=document_hash,
            impact=impact,
            classification=impact["classification"],
        )
        return {
            "request": record.model_dump(mode="json"),
            "impact": impact,
            "events": self.repository.change_request_events(record.id),
            "document_hash": document_hash,
        }

    def decide_change_request(
        self,
        request_id: str,
        *,
        decision: str,
        decided_by: str,
        reason: str,
    ) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise IntegrityError("Decision must be approve or reject")
        record = self.repository.get_change_request(request_id)
        updated = self.repository.record_change_request_status(
            request_id,
            status="approved" if decision == "approve" else "rejected",
            actor=decided_by,
            reason=reason,
            evidence=(f"impact_classification:{record.classification}",),
        )
        return {
            "request": updated.model_dump(mode="json"),
            "events": self.repository.change_request_events(request_id),
        }

    def change_request_status(
        self, project_id: str, request_id: str | None = None
    ) -> dict[str, Any]:
        if request_id:
            record = self.repository.get_change_request(request_id)
            return {
                "request": record.model_dump(mode="json"),
                "events": self.repository.change_request_events(request_id),
                "revisions": self.repository.list_revision_children(request_id),
            }
        records = self.repository.list_change_requests(project_id)
        return {"requests": [record.model_dump(mode="json") for record in records]}

    @staticmethod
    def _load_change_document(raw: str) -> dict[str, Any]:
        try:
            document = yaml.safe_load(raw)
        except yaml.YAMLError as error:
            raise ConflictError(f"Change request is not valid YAML: {error}") from error
        if not isinstance(document, dict):
            raise ConflictError("Change request root must be a mapping")
        return document

    def _resolve_change_target(self, project_id: str, target: str) -> tuple[str, str]:
        for artifact in self.repository.list_artifacts(project_id):
            if target in {
                artifact["id"],
                artifact["content_hash"],
                artifact["relative_path"],
            }:
                return "artifact", artifact["id"]
        for element in self.repository.list_creative_elements(project_id):
            if target in {element["id"], element["stable_key"]}:
                return "element", element["id"]
        for slot in self.repository.list_slots(project_id):
            if target in {slot["id"], slot["stable_key"]}:
                return "slot", slot["id"]
        for shot in self.repository.list_shots(project_id):
            if target in {shot["id"], shot["stable_key"]}:
                return "shot", shot["id"]
        for deliverable in self.repository.list_deliverables(project_id):
            if target in {deliverable.id, deliverable.stable_key}:
                return "deliverable", deliverable.id
        for bed in self.repository.list_audio_beds(project_id):
            if target in {bed["id"], bed["stable_key"]}:
                return "audio_bed", bed["id"]
        if target.startswith("abx_"):
            return "experiment", target
        return "other", target

    def _change_impact(
        self,
        project_id: str,
        request: ChangeRequestCreate,
        target_type: str,
        target_id: str,
    ) -> dict[str, Any]:
        project = self.repository.get_project(project_id)
        artifacts = self.repository.list_artifacts(project_id)
        shots = self.repository.list_shots(project_id)
        slots = self.repository.list_slots(project_id)
        elements = self.repository.list_creative_elements(project_id)
        bindings = self.repository.list_shot_element_bindings(project_id)
        approvals = self.repository.list_approvals(project_id)
        shots_by_id = {shot["id"]: shot for shot in shots}
        slots_by_id = {slot["id"]: slot for slot in slots}

        affected_slots: set[str] = {target_id} if target_type == "slot" else set()
        affected_shots: set[str] = set()
        target_artifact = None
        if target_type == "artifact":
            target_artifact = next(
                (artifact for artifact in artifacts if artifact["id"] == target_id),
                None,
            )
            if target_artifact:
                operation = target_artifact.get("operation") or {}
                if operation.get("shot_id") in shots_by_id:
                    affected_shots.add(str(operation["shot_id"]))
                if operation.get("slot_id") in slots_by_id:
                    affected_slots.add(str(operation["slot_id"]))
        elif target_type == "element":
            affected_shots.update(
                binding["shot_id"]
                for binding in bindings
                if binding["element_id"] == target_id
            )
        elif target_type == "shot":
            affected_shots.add(target_id)
        elif target_type == "deliverable":
            affected_shots.update(
                shot["id"]
                for shot in shots
                if shot.get("deliverable_id") in {None, target_id}
            )
        else:
            affected_shots.update(shot["id"] for shot in shots)
        for slot_id in list(affected_slots):
            if slot_id in slots_by_id:
                affected_shots.add(slots_by_id[slot_id]["shot_id"])
        for shot_id in list(affected_shots):
            affected_slots.update(
                slot["id"] for slot in slots if slot["shot_id"] == shot_id
            )

        affected_artifacts = []
        for artifact in artifacts:
            operation = artifact.get("operation") or {}
            referenced = (
                artifact["id"] == target_id
                or operation.get("shot_id") in affected_shots
                or operation.get("slot_id") in affected_slots
                or artifact["kind"] == "render"
            )
            if referenced and request.change_type != "metadata":
                affected_artifacts.append(artifact)

        affected_approvals = [
            approval
            for approval in approvals
            if approval["status"] == "active"
            and (
                approval["stage"] in {"animatic", "final"}
                or any(
                    dependency.get("entity_id") == target_id
                    for dependency in approval.get("dependencies", ())
                )
            )
        ]

        budget_items = []
        plan = self.budget_plan(project_id)
        if plan:
            for item in plan["items"]:
                if (
                    item["shot_id"] in affected_shots
                    or item["slot_id"] in affected_slots
                ):
                    budget_items.append(
                        {
                            "stable_key": item["stable_key"],
                            "variant_label": item.get("variant_label"),
                            "estimated_minor": item["estimated_minor"],
                        }
                    )
        estimated_minor = sum(item["estimated_minor"] for item in budget_items)
        rights = (target_artifact or {}).get("operation", {}).get("rights") or {}
        facts_recheck = target_type == "element" and any(
            element["id"] == target_id
            and str(element.get("fact_requirement", "none")) != "none"
            for element in elements
        )

        if project.status == ProjectStatus.ARCHIVED:
            classification = "blocked"
            rationale = "The project is archived; create a child revision first"
        elif request.change_type == "metadata" and target_type in {
            "artifact",
            "deliverable",
        }:
            classification = "safe_in_place"
            rationale = "Metadata can be replaced without re-rendering the master"
        elif request.change_type == "copy":
            classification = "re_render"
            rationale = "Copy changes invalidate narration, captions and the render"
        elif affected_approvals:
            classification = "re_approval"
            rationale = "An active animatic or final approval depends on the target"
        elif target_type in {"artifact", "slot", "element", "shot"}:
            classification = "re_render"
            rationale = "The target feeds a rendered artifact"
        else:
            classification = "re_approval"
            rationale = (
                "The target cannot be mapped to a render; human review is required"
            )

        return {
            "schema": "change-impact/v1",
            "target": {
                "type": target_type,
                "id": target_id,
                "requested": request.element_or_artifact_id,
            },
            "affected": {
                "shots": sorted(
                    shots_by_id[shot_id]["stable_key"]
                    for shot_id in affected_shots
                    if shot_id in shots_by_id
                ),
                "slots": sorted(
                    slots_by_id[slot_id]["stable_key"]
                    for slot_id in affected_slots
                    if slot_id in slots_by_id
                ),
                "artifacts": [
                    {
                        "id": artifact["id"],
                        "path": artifact["relative_path"],
                        "hash": artifact["content_hash"],
                    }
                    for artifact in affected_artifacts
                ],
                "approvals": [
                    {
                        "id": approval["id"],
                        "stage": approval["stage"],
                        "status": approval["status"],
                    }
                    for approval in affected_approvals
                ],
            },
            "budget": {
                "estimated_minor": estimated_minor,
                "currency": plan["currency"] if plan else project.max_budget.currency,
                "items": budget_items,
            },
            "checks": {
                "rights_recheck": bool(rights),
                "facts_recheck": facts_recheck,
                "flow_complete": self.flow_completeness(project_id)["complete"],
                "version_scope": request.version_scope,
            },
            "classification": classification,
            "rationale": rationale,
            "revision_required": classification == "blocked"
            or project.status
            in {
                ProjectStatus.FINAL_APPROVED,
                ProjectStatus.ARCHIVED,
            },
            "requested_change": request.requested_change,
        }

    @staticmethod
    def _bound_entry(
        assignment: dict[str, Any] | None,
        candidate: dict[str, Any] | None,
        artifacts: dict[str, Any],
    ) -> Any:
        if not assignment and not candidate:
            return None
        artifact_id = (assignment or {}).get("artifact_id") or (candidate or {}).get(
            "artifact_id"
        )
        artifact = artifacts.get(artifact_id or "")
        rights = (artifact or {}).get("operation", {}).get("rights") or {}
        entry: dict[str, Any] = {
            "artifact_id": artifact_id,
            "artifact_hash": artifact["content_hash"] if artifact else None,
            "attribution": rights.get("attribution_text"),
        }
        if candidate:
            entry.update(
                {
                    "candidate_id": candidate["id"],
                    "provider": candidate["provider"],
                    "provider_asset_id": candidate["provider_asset_id"],
                    "licence": candidate["licence"],
                    "treatment": candidate["treatment"],
                    "score": candidate["score"],
                }
            )
        return entry

    def _deliverables(
        self, project_id: str, artifacts: dict[str, Any]
    ) -> list[dict[str, Any]]:
        selected: list[str] = []
        for approval in self.repository.list_approvals(project_id):
            if approval["stage"] == "final" and approval["output_hash"]:
                artifact = self.repository.artifact_by_content_hash(
                    project_id, approval["output_hash"]
                )
                if artifact:
                    selected.append(artifact["id"])
        for experiment in self.repository.list_ab_experiments(project_id):
            for variant in self.repository.list_ab_variants(experiment["id"]):
                selected.append(variant["artifact_id"])
        if not selected:
            selected = [
                artifact["id"]
                for artifact in artifacts.values()
                if artifact["kind"] == "render"
            ]
        seen: set[str] = set()
        rows = []
        for artifact_id in selected:
            if artifact_id in seen or artifact_id not in artifacts:
                continue
            seen.add(artifact_id)
            artifact = artifacts[artifact_id]
            rows.append(
                {
                    "artifact_id": artifact["id"],
                    "path": artifact["relative_path"],
                    "hash": artifact["content_hash"],
                    "kind": artifact["kind"],
                }
            )
        return rows

    @staticmethod
    def _markdown(body: dict[str, Any]) -> str:
        project = body["project"]
        lines = [
            f"# Change request - {project['name']}",
            "",
            f"Project: `{project['id']}`  ",
            f"Status: {project['status']}  ",
            f"Brief: `{body['brief']['id']}` ({body['brief']['content_hash']})  "
            if body["brief"]
            else "Brief: none  ",
            f"Flow complete: {body['flow_complete']}",
            "",
            "## Elements",
            "",
            "| Element | Kind | ID | Artifact | Hash |",
            "|---|---|---|---|---|",
        ]
        for element in body["elements"]:
            lines.append(
                f"| {element['stable_key']} | {element['kind']} | `{element['element_id']}` "
                f"| `{element['artifact_id'] or ''}` | `{element['artifact_hash'] or ''}` |"
            )
        lines.extend(["", "## Slots", ""])
        for slot in body["slots"]:
            primary = slot["primary"] or {}
            backup = slot["backup"] or {}
            lines.append(
                f"- `{slot['stable_key']}` ({slot['role']}): "
                f"primary `{primary.get('artifact_id') or 'none'}` "
                f"`{primary.get('artifact_hash') or ''}`, "
                f"backup `{backup.get('artifact_id') or 'none'}` "
                f"`{backup.get('artifact_hash') or ''}`, "
                f"ceiling {slot['treatment_ceiling']}, "
                f"credit {primary.get('attribution') or 'none'}"
            )
        lines.extend(["", "## Deliverables", ""])
        for artifact in body["deliverables"]:
            lines.append(
                f"- `{artifact['artifact_id']}` {artifact['path']} `{artifact['hash']}`"
            )
        lines.extend(
            [
                "",
                "## Change request form",
                "",
                "Complete every field. A change is not accepted until the approver",
                "records an approval against the new hash.",
                "",
            ]
        )
        for field, _ in CHANGE_REQUEST_FIELDS:
            lines.append(f"- **{field}**: ")
        lines.extend(
            [
                "",
                f"Allowed change types: {', '.join(body['allowed_change_types'])}",
                "",
            ]
        )
        return "\n".join(lines)

    def write_delivery_metadata(self, project_id: str) -> dict[str, Any]:
        """Write CREDITS.txt and title/description metadata for the delivery."""

        project = self.repository.get_project(project_id)
        credits: list[str] = []
        sources: list[dict[str, Any]] = []
        for assignment in self.repository.list_slot_assets(project_id):
            artifact = self.repository.get_artifact(assignment["artifact_id"])
            rights = (artifact.get("operation") or {}).get("rights") or {}
            attribution = rights.get("attribution_text")
            if (
                rights.get("attribution_required")
                and attribution
                and attribution not in credits
            ):
                credits.append(attribution)
            sources.append(
                {
                    "slot_id": assignment["slot_id"],
                    "role": assignment["role"],
                    "artifact_id": artifact["id"],
                    "content_hash": artifact["content_hash"],
                    "licence": rights.get("license_name"),
                    "attribution": attribution,
                }
            )
        title = project.name
        description = (
            f"{project.name}. {project.objective}. "
            "Created with Pinaka; sources and hashes are recorded in the delivery packet."
        )
        metadata = {
            "schema": "delivery-metadata/v1",
            "project_id": project_id,
            "title": title,
            "description": description,
            "credits": credits,
            "sources": sources,
        }
        metadata_bytes = canonical_json(metadata).encode("utf-8")
        metadata_hash = sha256_bytes(metadata_bytes)
        metadata_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"delivery/metadata-{metadata_hash[:12]}.json",
            content=metadata_bytes,
            operation={"type": "delivery_metadata", "format": "json"},
        )
        credits_text = "\n".join(credits) if credits else "No attribution required."
        credits_artifact = self.write_artifact(
            project_id,
            kind=ArtifactKind.DESIGN,
            relative_path=f"delivery/CREDITS-{metadata_hash[:12]}.txt",
            content=(credits_text + "\n").encode("utf-8"),
            operation={"type": "delivery_metadata", "format": "credits"},
        )
        return {
            "metadata": metadata,
            "metadata_hash": metadata_hash,
            "metadata_artifact": metadata_artifact,
            "credits_artifact": credits_artifact,
            "credits": credits,
        }

    def require_delivery_metadata(self, project_id: str) -> dict[str, Any]:
        artifacts = self.repository.list_artifacts(project_id)
        metadata = [
            artifact
            for artifact in artifacts
            if artifact["relative_path"].startswith("delivery/metadata-")
            and artifact["relative_path"].endswith(".json")
        ]
        credits = [
            artifact
            for artifact in artifacts
            if artifact["relative_path"].startswith("delivery/CREDITS-")
        ]
        if not metadata or not credits:
            raise ConflictError(
                "Delivery metadata and CREDITS.txt are required before final approval"
            )
        body = json.loads(
            self.read_artifact_text(
                max(metadata, key=lambda item: item["created_at"])["id"]
            )
        )
        if any(
            source.get("attribution")
            for source in body.get("sources", [])
            if source.get("attribution")
        ) and not body.get("credits"):
            raise ConflictError("Attribution-required sources have no credits")
        return {
            "metadata": body,
            "credits": body.get("credits", []),
            "metadata_artifact": max(metadata, key=lambda item: item["created_at"]),
            "credits_artifact": max(credits, key=lambda item: item["created_at"]),
        }

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..enums import ArtifactKind
from ..utils import sha256_bytes

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

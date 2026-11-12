from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from typing import Any

from .database import Database
from .errors import (
    NotFoundError,
)
from .models import (
    JobRecord,
    Money,
    ProjectRecord,
)
from .store.ab import ExperimentsStore
from .store.approvals import ApprovalsStore
from .store.artifacts import ArtifactsStore
from .store.assets import AssetsStore
from .store.audio import AudioStore
from .store.budget import BudgetStore
from .store.candidates import CandidatesStore
from .store.costs import CostsStore
from .store.elements import ElementsStore
from .store.flow import FlowStore
from .store.grounding import GroundingStore
from .store.jobs import JobsStore
from .store.planning import PlanningStore
from .store.projects import ProjectsStore
from .store.providers import ProvidersStore
from .store.qa import QaStore
from .store.shots import ShotsStore
from .store.studio_jobs import StudioJobsStore
from .store.studio_reservations import StudioReservationsStore
from .store.studio_results import StudioResultsStore
from .store.tools import ToolsStore
from .utils import canonical_json, new_id, utc_now


class Repository(
    ProjectsStore,
    PlanningStore,
    GroundingStore,
    ShotsStore,
    ElementsStore,
    FlowStore,
    CandidatesStore,
    ExperimentsStore,
    ApprovalsStore,
    ArtifactsStore,
    AssetsStore,
    AudioStore,
    ProvidersStore,
    ToolsStore,
    StudioReservationsStore,
    StudioJobsStore,
    StudioResultsStore,
    QaStore,
    JobsStore,
    BudgetStore,
    CostsStore,
):
    """Compatible facade; feature behavior lives in store/."""

    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        *,
        project_id: str | None,
        entity_type: str,
        entity_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO events(id, project_id, entity_type, entity_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("evt"),
                project_id,
                entity_type,
                entity_id,
                event_type,
                canonical_json(payload),
                utc_now(),
            ),
        )

    @staticmethod
    def _project_from_row(row: sqlite3.Row) -> ProjectRecord:
        currency = row["currency"]
        return ProjectRecord(
            id=row["id"],
            parent_project_id=row["parent_project_id"],
            slug=row["slug"],
            name=row["name"],
            objective=row["objective"],
            mode=row["mode"],
            audience=row["audience"],
            output_format=row["output_format"],
            language=row["language"],
            duration_seconds=Decimal(row["duration_seconds"]),
            status=row["status"],
            max_budget=Money.from_minor(row["max_budget_minor"], currency),
            reserved=Money.from_minor(row["reserved_minor"], currency),
            spent=Money.from_minor(row["spent_minor"], currency),
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> JobRecord:
        currency = row["currency"]
        return JobRecord(
            id=row["id"],
            project_id=row["project_id"],
            provider=row["provider"],
            endpoint=row["endpoint"],
            operation=row["operation"],
            idempotency_key=row["idempotency_key"],
            status=row["status"],
            estimated_cost=Money.from_minor(row["estimated_cost_minor"], currency),
            reserved_cost=Money.from_minor(row["reserved_cost_minor"], currency),
            actual_cost=Money.from_minor(row["actual_cost_minor"], currency),
            provider_job_id=row["provider_job_id"],
            request_hash=row["request_hash"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _require_project(
        connection: sqlite3.Connection, project_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not row:
            raise NotFoundError(f"Project not found: {project_id}")
        return row

    @staticmethod
    def _require_job(connection: sqlite3.Connection, job_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if not row:
            raise NotFoundError(f"Job not found: {job_id}")
        return row

    def events(self, project_id: str | None = None) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            if project_id:
                self._require_project(connection, project_id)
                rows = connection.execute(
                    "SELECT * FROM events WHERE project_id = ? ORDER BY sequence",
                    (project_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM events ORDER BY sequence"
                ).fetchall()
            return [
                {
                    "sequence": row["sequence"],
                    "id": row["id"],
                    "project_id": row["project_id"],
                    "entity_type": row["entity_type"],
                    "entity_id": row["entity_id"],
                    "event_type": row["event_type"],
                    "payload": json.loads(row["payload_json"]),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

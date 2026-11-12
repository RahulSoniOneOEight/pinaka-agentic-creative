from __future__ import annotations

import sqlite3
from typing import Any

from ..enums import (
    BudgetEntryKind,
    JobStatus,
)
from ..errors import (
    BudgetExceededError,
    ConflictError,
    IntegrityError,
    VarianceApprovalRequiredError,
)
from ..models import (
    JobRecord,
    JobRequest,
    Money,
)
from ..utils import canonical_json, new_id, utc_now


class JobsStore:
    @staticmethod
    def _ledger(
        connection: sqlite3.Connection,
        *,
        project_id: str,
        job_id: str,
        kind: BudgetEntryKind,
        amount_minor: int,
        currency: str,
        note: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO budget_ledger(id, project_id, job_id, kind, amount_minor, currency, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("led"),
                project_id,
                job_id,
                kind.value,
                amount_minor,
                currency,
                note,
                utc_now(),
            ),
        )

    def reserve_job(
        self, project_id: str, request: JobRequest, request_hash: str
    ) -> JobRecord:
        now = utc_now()
        with self.database.transaction() as connection:
            project = self._require_project(connection, project_id)
            existing = connection.execute(
                """
                SELECT * FROM jobs
                WHERE project_id = ? AND provider = ? AND idempotency_key = ?
                """,
                (project_id, request.provider, request.idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise ConflictError(
                        "Idempotency key was already used for a different request"
                    )
                return self._job_from_row(existing)
            if project["currency"] != request.estimated_cost.currency:
                raise IntegrityError(
                    f"Job currency {request.estimated_cost.currency} does not match "
                    f"project currency {project['currency']}"
                )
            exposure = project["spent_minor"] + project["reserved_minor"]
            if exposure + request.estimated_cost.minor > project["max_budget_minor"]:
                available = project["max_budget_minor"] - exposure
                raise BudgetExceededError(
                    f"Insufficient budget: requested {request.estimated_cost.minor} minor units, "
                    f"available {available}"
                )
            job_id = new_id("job")
            connection.execute(
                """
                INSERT INTO jobs(
                    id, project_id, provider, endpoint, operation, idempotency_key,
                    request_hash, request_json, status, estimated_cost_minor,
                    reserved_cost_minor, currency, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    project_id,
                    request.provider,
                    request.endpoint,
                    request.operation,
                    request.idempotency_key,
                    request_hash,
                    canonical_json(request.model_dump(mode="json")),
                    JobStatus.BUDGET_RESERVED.value,
                    request.estimated_cost.minor,
                    request.estimated_cost.minor,
                    request.estimated_cost.currency,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE projects
                SET reserved_minor = reserved_minor + ?, version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (request.estimated_cost.minor, now, project_id),
            )
            self._ledger(
                connection,
                project_id=project_id,
                job_id=job_id,
                kind=BudgetEntryKind.RESERVE,
                amount_minor=request.estimated_cost.minor,
                currency=request.estimated_cost.currency,
                note="Controller-approved reservation",
            )
            self._event(
                connection,
                project_id=project_id,
                entity_type="job",
                entity_id=job_id,
                event_type="job.budget_reserved",
                payload={
                    "request_hash": request_hash,
                    "reserved_minor": request.estimated_cost.minor,
                },
            )
            return self._job_from_row(self._require_job(connection, job_id))

    def get_job(self, job_id: str) -> JobRecord:
        with self.database.read() as connection:
            return self._job_from_row(self._require_job(connection, job_id))

    def update_job_status(
        self,
        job_id: str,
        *,
        expected: set[JobStatus],
        target: JobStatus,
        provider_job_id: str | None = None,
        response: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> JobRecord:
        now = utc_now()
        with self.database.transaction() as connection:
            job = self._require_job(connection, job_id)
            if JobStatus(job["status"]) not in expected:
                expected_values = ", ".join(sorted(item.value for item in expected))
                raise ConflictError(
                    f"Job {job_id} must be in one of [{expected_values}], observed {job['status']}"
                )
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, provider_job_id = COALESCE(?, provider_job_id),
                    response_json = COALESCE(?, response_json),
                    error_json = COALESCE(?, error_json), updated_at = ?
                WHERE id = ?
                """,
                (
                    target.value,
                    provider_job_id,
                    canonical_json(response) if response is not None else None,
                    canonical_json(error) if error is not None else None,
                    now,
                    job_id,
                ),
            )
            self._event(
                connection,
                project_id=job["project_id"],
                entity_type="job",
                entity_id=job_id,
                event_type="job.status_changed",
                payload={"from": job["status"], "to": target.value},
            )
            return self._job_from_row(self._require_job(connection, job_id))

    def settle_job(
        self,
        job_id: str,
        *,
        target: JobStatus,
        actual_cost: Money,
        response: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        approved_variance_minor: int | None = None,
        asset_artifact_id: str | None = None,
    ) -> JobRecord:
        if target not in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
            raise IntegrityError(f"Invalid terminal status: {target.value}")
        now = utc_now()
        with self.database.transaction() as connection:
            job = self._require_job(connection, job_id)
            current = JobStatus(job["status"])
            terminal = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
            if current in terminal:
                if current == target and job["actual_cost_minor"] == actual_cost.minor:
                    return self._job_from_row(job)
                raise ConflictError(f"Job is already terminal: {current.value}")
            if actual_cost.currency != job["currency"]:
                raise IntegrityError(
                    "Actual cost currency does not match the reservation"
                )
            budget_item = connection.execute(
                """
                SELECT i.id, i.plan_id, i.project_id, i.estimated_minor,
                       p.tolerance_bps, p.tolerance_floor_minor, p.currency
                FROM budget_plan_items i
                JOIN budget_plans p ON p.id = i.plan_id
                WHERE i.job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if actual_cost.minor > job["reserved_cost_minor"]:
                if not budget_item:
                    raise BudgetExceededError(
                        "Actual cost exceeds the approved reservation; "
                        "manual reconciliation is required"
                    )
                allowance = max(
                    budget_item["estimated_minor"]
                    * budget_item["tolerance_bps"]
                    // 10000,
                    budget_item["tolerance_floor_minor"],
                )
                allowed_minor = budget_item["estimated_minor"] + allowance
                variance_approved = (
                    approved_variance_minor is not None
                    and approved_variance_minor == actual_cost.minor
                )
                if actual_cost.minor > allowed_minor and not variance_approved:
                    raise VarianceApprovalRequiredError(
                        "Actual cost exceeds the plan's variance tolerance; "
                        "approval is required",
                        job_id=job_id,
                        plan_id=budget_item["plan_id"],
                        item_id=budget_item["id"],
                        estimated_minor=budget_item["estimated_minor"],
                        actual_minor=actual_cost.minor,
                        allowed_minor=allowed_minor,
                        currency=budget_item["currency"],
                    )
                project_row = self._require_project(connection, job["project_id"])
                if (
                    project_row["spent_minor"] + actual_cost.minor
                    > project_row["max_budget_minor"]
                ):
                    raise BudgetExceededError(
                        "Actual cost would exceed the project budget; "
                        "further approval is required"
                    )
            reserved = job["reserved_cost_minor"]
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, reserved_cost_minor = 0, actual_cost_minor = ?,
                    response_json = COALESCE(?, response_json),
                    error_json = COALESCE(?, error_json), updated_at = ?
                WHERE id = ?
                """,
                (
                    target.value,
                    actual_cost.minor,
                    canonical_json(response) if response is not None else None,
                    canonical_json(error) if error is not None else None,
                    now,
                    job_id,
                ),
            )
            connection.execute(
                """
                UPDATE projects
                SET reserved_minor = reserved_minor - ?, spent_minor = spent_minor + ?,
                    version = version + 1, updated_at = ?
                WHERE id = ?
                """,
                (reserved, actual_cost.minor, now, job["project_id"]),
            )
            self._ledger(
                connection,
                project_id=job["project_id"],
                job_id=job_id,
                kind=BudgetEntryKind.RELEASE,
                amount_minor=reserved,
                currency=job["currency"],
                note="Released terminal job reservation",
            )
            if actual_cost.minor:
                self._ledger(
                    connection,
                    project_id=job["project_id"],
                    job_id=job_id,
                    kind=BudgetEntryKind.CHARGE,
                    amount_minor=actual_cost.minor,
                    currency=job["currency"],
                    note=f"Provider cost for {target.value} job",
                )
            self.settle_budget_item_for_job(connection, job_id, actual_cost.minor)
            if asset_artifact_id:
                self._record_asset_cost_tx(
                    connection, job_id, asset_artifact_id, actual_cost.minor, now
                )
            self._event(
                connection,
                project_id=job["project_id"],
                entity_type="job",
                entity_id=job_id,
                event_type="job.settled",
                payload={
                    "from": job["status"],
                    "to": target.value,
                    "reserved_released_minor": reserved,
                    "actual_cost_minor": actual_cost.minor,
                },
            )
            return self._job_from_row(self._require_job(connection, job_id))

    def recoverable_jobs(self, project_id: str | None = None) -> list[JobRecord]:
        statuses = (
            JobStatus.BUDGET_RESERVED.value,
            JobStatus.SUBMITTING.value,
            JobStatus.SUBMITTED.value,
            JobStatus.RUNNING.value,
            JobStatus.SUBMISSION_UNKNOWN.value,
        )
        placeholders = ",".join("?" for _ in statuses)
        with self.database.read() as connection:
            if project_id:
                self._require_project(connection, project_id)
                rows = connection.execute(
                    f"SELECT * FROM jobs WHERE project_id = ? AND status IN ({placeholders}) "
                    "ORDER BY created_at",
                    (project_id, *statuses),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT * FROM jobs WHERE status IN ({placeholders}) ORDER BY created_at",
                    statuses,
                ).fetchall()
            return [self._job_from_row(row) for row in rows]

    def budget_ledger(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM budget_ledger WHERE project_id = ? ORDER BY rowid",
                (project_id,),
            ).fetchall()
            return [dict(row) for row in rows]

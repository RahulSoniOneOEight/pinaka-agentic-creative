from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..models import (
    CapabilityResult,
    ProviderModelProfileCreate,
)
from ..utils import canonical_json, new_id, sha256_json, utc_now


class ProvidersStore:
    def record_provider_spend(
        self,
        provider: str,
        amount_cents: int,
        tier: str,
        purpose: str,
        *,
        notifications: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        spend_id = new_id("spd")
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO provider_spend(id, provider, amount_cents, tier, purpose, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (spend_id, provider, amount_cents, tier, purpose, now),
            )
            self._event(
                connection,
                project_id=None,
                entity_type="provider_spend",
                entity_id=spend_id,
                event_type="provider_spend.recorded",
                payload={
                    "provider": provider,
                    "amount_cents": amount_cents,
                    "tier": tier,
                    "purpose": purpose,
                },
            )
            for message in notifications:
                self._event(
                    connection,
                    project_id=None,
                    entity_type="provider_spend",
                    entity_id=spend_id,
                    event_type="budget.threshold_reached",
                    payload={"provider": provider, "message": message},
                )
        return {
            "id": spend_id,
            "provider": provider,
            "amount_cents": amount_cents,
            "tier": tier,
            "purpose": purpose,
            "created_at": now,
            "notifications": list(notifications),
        }

    def provider_spend_total_cents(self, provider: str) -> int:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM provider_spend "
                "WHERE provider = ?",
                (provider,),
            ).fetchone()
            return int(row["total"])

    def list_provider_spend(self, provider: str) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM provider_spend WHERE provider = ? ORDER BY created_at",
                (provider,),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "provider": row["provider"],
                    "amount_cents": row["amount_cents"],
                    "tier": row["tier"],
                    "purpose": row["purpose"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def record_provider_request(self, provider: str, operation: str) -> dict[str, Any]:
        request_id = new_id("req")
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO provider_requests(id, provider, operation, requested_at) "
                "VALUES (?, ?, ?, ?)",
                (request_id, provider, operation, now),
            )
        return {
            "id": request_id,
            "provider": provider,
            "operation": operation,
            "requested_at": now,
        }

    def count_provider_requests(self, provider: str, since: str) -> int:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM provider_requests "
                "WHERE provider = ? AND requested_at >= ?",
                (provider, since),
            ).fetchone()
            return int(row["total"])

    @staticmethod
    def _provider_profile_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "stable_key": row["stable_key"],
            "provider": row["provider"],
            "endpoint": row["endpoint"],
            "model": row["model"],
            "approved_roles": json.loads(row["approved_roles_json"]),
            "credential_ref": row["credential_ref"],
            "data_classification": row["data_classification"],
            "limits": json.loads(row["limits_json"]),
            "terms": json.loads(row["terms_json"]),
            "fallback_profiles": json.loads(row["fallback_profiles_json"]),
            "enabled": bool(row["enabled"]),
            "version": row["version"],
            "content_hash": row["content_hash"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def register_provider_profile(
        self, request: ProviderModelProfileCreate
    ) -> dict[str, Any]:
        now = utc_now()
        payload = request.model_dump(mode="json")
        content_hash = sha256_json(payload)
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM provider_profiles WHERE stable_key = ?",
                (request.stable_key,),
            ).fetchone()
            if existing and existing["content_hash"] == content_hash:
                return self._provider_profile_dict(existing)
            if existing:
                connection.execute(
                    """
                    UPDATE provider_profiles SET
                        provider = ?, endpoint = ?, model = ?, approved_roles_json = ?,
                        credential_ref = ?, data_classification = ?, limits_json = ?,
                        terms_json = ?, fallback_profiles_json = ?, enabled = ?,
                        version = version + 1, content_hash = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        request.provider,
                        request.endpoint,
                        request.model,
                        canonical_json(request.approved_roles),
                        request.credential_ref,
                        request.data_classification.value,
                        canonical_json(request.limits),
                        canonical_json(request.terms),
                        canonical_json(request.fallback_profiles),
                        int(request.enabled),
                        content_hash,
                        now,
                        existing["id"],
                    ),
                )
                profile_id = existing["id"]
                event_type = "provider_profile.updated"
            else:
                profile_id = new_id("provider")
                connection.execute(
                    """
                    INSERT INTO provider_profiles(
                        id, stable_key, provider, endpoint, model, approved_roles_json,
                        credential_ref, data_classification, limits_json, terms_json,
                        fallback_profiles_json, enabled, content_hash, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile_id,
                        request.stable_key,
                        request.provider,
                        request.endpoint,
                        request.model,
                        canonical_json(request.approved_roles),
                        request.credential_ref,
                        request.data_classification.value,
                        canonical_json(request.limits),
                        canonical_json(request.terms),
                        canonical_json(request.fallback_profiles),
                        int(request.enabled),
                        content_hash,
                        now,
                        now,
                    ),
                )
                event_type = "provider_profile.registered"
            self._event(
                connection,
                project_id=None,
                entity_type="provider_profile",
                entity_id=profile_id,
                event_type=event_type,
                payload={
                    "stable_key": request.stable_key,
                    "content_hash": content_hash,
                    "enabled": request.enabled,
                },
            )
            row = connection.execute(
                "SELECT * FROM provider_profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            return self._provider_profile_dict(row)

    def list_provider_profiles(self) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM provider_profiles ORDER BY stable_key"
            ).fetchall()
            return [self._provider_profile_dict(row) for row in rows]

    def record_capability(self, result: CapabilityResult) -> None:
        with self.database.transaction() as connection:
            check_id = new_id("cap")
            connection.execute(
                """
                INSERT INTO capability_checks(id, name, status, detail, checked_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (check_id, result.name, result.status.value, result.detail, utc_now()),
            )
            self._event(
                connection,
                project_id=None,
                entity_type="capability_check",
                entity_id=check_id,
                event_type="capability.checked",
                payload={"name": result.name, "status": result.status.value},
            )

    def latest_capability(self, name: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                """
                SELECT * FROM capability_checks
                WHERE name = ? ORDER BY checked_at DESC, rowid DESC LIMIT 1
                """,
                (name,),
            ).fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "name": row["name"],
                "status": row["status"],
                "detail": row["detail"],
                "checked_at": row["checked_at"],
            }

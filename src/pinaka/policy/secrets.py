"""One credential boundary for every payload the controller persists.

Two independent checks run over a payload:

* field names that can only mean a credential are refused by exact, normalised
  name (``fraud_key`` is fine, ``fal_key`` is not);
* field *values* are scanned for credential shapes, so an innocent field name
  cannot smuggle a secret into SQLite, a manifest or an event.

The error names the location only. It never repeats the value, because the
message itself is persisted in the event log.
"""

from __future__ import annotations

import re
from typing import Any

from ..errors import IntegrityError

SENSITIVE_KEY_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "api_token",
        "access_token",
        "refresh_token",
        "id_token",
        "auth_token",
        "bearer_token",
        "session_token",
        "token",
        "bearer",
        "authorization",
        "password",
        "passwd",
        "secret",
        "client_secret",
        "secret_key",
        "private_key",
        "fal_key",
        "credential",
        "credentials",
        "cookie",
        "cookies",
    }
)

SENSITIVE_VALUE_PATTERNS = (
    # fal keys: fal_sk_<32 hex>, fal_<scope>_<secret>
    re.compile(r"\bfal_[a-z0-9]+_[A-Za-z0-9_\-]{16,}\b"),
    # OpenAI/Anthropic-style keys, including sk-proj- and sk-ant-
    re.compile(r"\bsk-(?:proj-|ant-|live-|test-)?[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    # JSON Web Tokens
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def _normalise(name: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def reject_sensitive_payload(value: Any, path: str = "request") -> None:
    """Raise when a persisted payload carries a credential name or value."""

    if isinstance(value, dict):
        for key, child in value.items():
            if _normalise(key) in SENSITIVE_KEY_NAMES:
                raise IntegrityError(
                    f"Sensitive field is forbidden in persisted payloads: {path}.{key}"
                )
            reject_sensitive_payload(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            reject_sensitive_payload(child, f"{path}[{index}]")
    elif isinstance(value, str):
        for pattern in SENSITIVE_VALUE_PATTERNS:
            if pattern.search(value):
                raise IntegrityError(
                    "A credential-like value is forbidden in persisted payloads "
                    f"at {path}"
                )

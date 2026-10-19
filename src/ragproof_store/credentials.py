"""Credential generation and configuration validation without service dependencies."""
from __future__ import annotations

import hashlib
import json
import re
import secrets


ROLES = frozenset({"reader", "writer", "admin"})
_FIELDS = {"key_hash", "tenant_id", "project_id", "role"}


def validate_scope(value: object, field: str) -> str:
    """Use canonical ID length limits and reject ambiguous/control characters."""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 256
        or value != value.strip()
        or not value.isprintable()
    ):
        raise ValueError(f"{field} must be 1–256 printable characters without surrounding whitespace")
    return value


def validate_credentials(value: object) -> list[dict[str, str]]:
    """Return a validated copy; errors never include configured values."""
    if not isinstance(value, list):
        raise ValueError("API credentials must be a JSON array")
    result = []
    seen = set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != _FIELDS:
            raise ValueError("Each API credential requires only key_hash, tenant_id, project_id, role")
        validate_scope(entry["tenant_id"], "tenant_id")
        validate_scope(entry["project_id"], "project_id")
        if not isinstance(entry["role"], str) or entry["role"] not in ROLES:
            raise ValueError("API credential role must be reader, writer, or admin")
        digest = entry["key_hash"]
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("API credential key_hash must be a lowercase SHA-256 hex digest")
        if digest in seen:
            raise ValueError("Duplicate API credential key_hash")
        seen.add(digest)
        result.append(dict(entry))
    return result


def load_credentials(serialized: str) -> list[dict[str, str]]:
    try:
        value = json.loads(serialized)
    except (ValueError, TypeError) as error:
        raise ValueError("RAGPROOF_API_KEYS_JSON must contain a valid JSON array") from error
    return validate_credentials(value)


def create_credential(tenant_id: str, project_id: str, role: str) -> tuple[str, dict[str, str]]:
    validate_scope(tenant_id, "tenant_id")
    validate_scope(project_id, "project_id")
    if not isinstance(role, str) or role not in ROLES:
        raise ValueError("API credential role must be reader, writer, or admin")
    key = "rpf_" + secrets.token_urlsafe(32)
    return key, {
        "key_hash": hashlib.sha256(key.encode("utf-8")).hexdigest(),
        "tenant_id": tenant_id,
        "project_id": project_id,
        "role": role,
    }

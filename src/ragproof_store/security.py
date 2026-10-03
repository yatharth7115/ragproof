"""Project-scoped API credentials and browser sessions for self-hosted deployments."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    project_id: str
    role: str
    key_hash: str = "local"

    @property
    def can_write(self) -> bool:
        return self.role in {"writer", "admin"}


class AccessControl:
    cookie_name = "ragproof_session"

    def __init__(self, mode=None, keys=None, secret=None, cookie_secure=None):
        self.mode = mode or os.getenv("RAGPROOF_AUTH_MODE", "local")
        if self.mode not in {"local", "token"}:
            raise ValueError("RAGPROOF_AUTH_MODE must be local or token")
        self.keys = keys if keys is not None else json.loads(os.getenv("RAGPROOF_API_KEYS_JSON", "[]"))
        self.secret = secret if secret is not None else os.getenv("RAGPROOF_SESSION_SECRET", "")
        self.cookie_secure = cookie_secure if cookie_secure is not None else os.getenv("RAGPROOF_COOKIE_SECURE", "true").lower() == "true"
        if self.mode == "token" and (not self.keys or len(self.secret) < 32):
            raise ValueError("Token mode requires API key hashes and a session secret of at least 32 characters")
        for entry in self.keys:
            if set(entry) != {"key_hash", "tenant_id", "project_id", "role"}:
                raise ValueError("Each API credential requires key_hash, tenant_id, project_id, role")
            if entry["role"] not in {"reader", "writer", "admin"} or len(entry["key_hash"]) != 64:
                raise ValueError("Invalid API credential hash or role")

    def authenticate_key(self, key: str) -> Principal | None:
        digest = hashlib.sha256(key.encode()).hexdigest()
        for entry in self.keys:
            if hmac.compare_digest(digest, entry["key_hash"]):
                return Principal(**entry)
        return None

    def session(self, principal: Principal) -> str:
        payload = f"{principal.key_hash}.{int(time.time()) + 28800}.{secrets.token_hex(16)}"
        signature = hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"

    def resolve(self, request: Request) -> Principal | None:
        if self.mode == "local":
            return Principal("cuad-public", "cuad-contract-review", "admin")
        authorization = request.headers.get("authorization", "")
        if authorization:
            if not authorization.startswith("Bearer "):
                return None
            return self.authenticate_key(authorization[7:])
        cookie = request.cookies.get(self.cookie_name, "")
        try:
            key_hash, expires, nonce, signature = cookie.split(".")
            payload = f"{key_hash}.{expires}.{nonce}"
            expected = hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected) or int(expires) <= time.time():
                return None
            for entry in self.keys:
                if hmac.compare_digest(entry["key_hash"], key_hash):
                    return Principal(**entry)
        except (ValueError, TypeError):
            return None
        return None

    @staticmethod
    def check_origin(request: Request) -> None:
        """Browser mutations must originate from this host (bearer clients have no Origin)."""
        origin = request.headers.get("origin")
        if origin and (urlsplit(origin).netloc != request.headers.get("host") or urlsplit(origin).scheme != request.url.scheme):
            raise HTTPException(403, "Cross-origin mutations are not allowed")

    def check_identity(self, principal: Principal, tenant_id: str, project_id: str) -> None:
        if self.mode != "local" and (tenant_id, project_id) != (principal.tenant_id, principal.project_id):
            raise HTTPException(403, "Credential is scoped to a different project")

    def check_trace(self, principal: Principal, trace: dict) -> None:
        self.check_identity(principal, trace.get("tenant_id"), trace.get("project_id"))

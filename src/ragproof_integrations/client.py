from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from urllib.parse import quote, urlsplit

import httpx
from jsonschema import Draft202012Validator, FormatChecker

from ragproof_resources import load_schema


MAX_BODY_BYTES = 16 * 1024 * 1024


class IntegrationError(RuntimeError):
    """A sanitized failure safe to display without disclosing trace content."""


def validate_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Endpoint must be an HTTP(S) URL without credentials, query or fragment")
    try:
        local = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        local = parsed.hostname.lower() == "localhost"
    if parsed.scheme != "https" and not local:
        raise ValueError("Remote endpoints require HTTPS; HTTP is permitted for loopback only")
    return endpoint.rstrip("/")


def validate_canonical(trace: dict) -> None:
    validator = Draft202012Validator(load_schema("canonical-trace.schema.json"),
                                     format_checker=FormatChecker())
    errors = list(validator.iter_errors(trace))
    if errors:
        paths = ["/" + "/".join(map(str, error.absolute_path)) for error in errors[:10]]
        raise ValueError("Canonical contract violation at " + ", ".join(paths))
    for item in [trace["query"], trace["generation"]["response"],
                 *trace["retrieval"]["candidates"], *trace["generation"].get("citations", [])]:
        if "content" in item and trace["privacy"]["capture_mode"] == "full":
            digest = hashlib.sha256(item["content"].encode("utf-8")).hexdigest()
            if digest != item["content_hash"].lower():
                raise ValueError("Content does not match its declared SHA-256 hash")


class JsonTransport:
    def __init__(self, endpoint: str, *, headers=None, timeout=20.0, client=None):
        self.endpoint = validate_endpoint(endpoint)
        self.headers = dict(headers or {})
        self._owned = client is None
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=False)

    def request(self, method, path, *, params=None, payload=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Request path must be relative to the configured origin")
        try:
            with self.client.stream(method, self.endpoint + path, headers=self.headers,
                                    params=params, json=payload, follow_redirects=False) as response:
                if not 200 <= response.status_code < 300:
                    raise IntegrationError(f"HTTP {response.status_code}; check endpoint, access and request configuration")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BODY_BYTES:
                        raise IntegrationError("Response exceeds the 16 MiB limit")
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise IntegrationError("Expected a JSON object response")
                return result
        except (httpx.HTTPError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise IntegrationError("Request failed or returned invalid JSON") from error

    def close(self):
        if self._owned:
            self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class RAGProofClient(JsonTransport):
    """Submit actual canonical traces; content transmission requires opt-in."""

    def __init__(self, endpoint=None, *, api_key=None, allow_content=False, **kwargs):
        endpoint = endpoint or os.getenv("RAGPROOF_ENDPOINT", "http://127.0.0.1:8080")
        api_key = api_key if api_key is not None else os.getenv("RAGPROOF_API_KEY")
        self.allow_content = allow_content
        super().__init__(endpoint, headers={"Authorization": f"Bearer {api_key}"}
                         if api_key else {}, **kwargs)

    def ingest(self, trace: dict) -> dict:
        validate_canonical(trace)
        if trace["privacy"]["capture_mode"] == "full" and not self.allow_content:
            raise ValueError("Full-content transmission requires allow_content=True")
        encoded = json.dumps(trace, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_BODY_BYTES:
            raise ValueError("Canonical trace exceeds the 16 MiB limit")
        return self.request("POST", "/v1/traces", payload=trace)

    def verify(self, response_id: str) -> dict:
        return self.request("POST", "/v1/verifications/" + quote(response_id, safe=""))

    def get_trace(self, trace_id: str) -> dict:
        return self.request("GET", "/v1/traces/" + quote(trace_id, safe=""))

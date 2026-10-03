"""Enforce content policy at the durable storage boundary, including extensions."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

from .object_store import ContentObjectStore


CONTENT_KEYS = (".content", ".contents")
SENSITIVE = re.compile(r"api.?key|authorization|cookie|password|secret|access.?token", re.I)


def validate_and_sanitize(trace: dict, bucket: str) -> None:
    mode = trace["privacy"]["capture_mode"]
    objects = [trace["query"], trace["generation"]["response"],
               *trace["retrieval"]["candidates"], *trace["generation"].get("citations", [])]
    for item in objects:
        if mode == "full" and "content" not in item and not item.get("content_ref"):
            raise ValueError("Full capture requires content or an owned content reference")
        if "content" in item and mode == "full":
            if hashlib.sha256(item["content"].encode()).hexdigest() != item["content_hash"]:
                raise ValueError("Captured content does not match its SHA-256 hash")
        if item.get("content_ref"):
            parsed = urlparse(item["content_ref"])
            owned_key = ContentObjectStore.key_for(trace["tenant_id"], trace["project_id"], item["content_hash"])
            if parsed.scheme != "s3" or parsed.netloc != bucket or parsed.path != f"/{owned_key}":
                raise ValueError("Content reference is outside this tenant/project namespace")
    # Only explicit semantic metadata is retained. Raw provider extras belong in
    # protected source systems, never in metadata-only traces.
    for span in trace["spans"]:
        attrs = span["attributes"]
        for key in list(attrs):
            if SENSITIVE.search(key) or key.endswith(CONTENT_KEYS):
                del attrs[key]
            elif mode != "full" and not safe_metadata_key(key):
                del attrs[key]
    extensions = trace.get("extensions", {})
    allowed = {
        "cuad": {"dataset", "license", "primary_document_id", "current_document_version"},
        "evaluation_ground_truth": {"source", "expected_evidence_hash", "source_contains_evidence", "parse_contains_evidence", "chunk_contains_evidence", "retrieval_contains_evidence", "context_contains_evidence"},
        "replay": {"replay_id", "original_trace_id", "role", "changed_component"},
        "quality_gate": {"gate_run_id"},
        "opentelemetry": {"service_name", "ignored_span_count"},
        "controlled_test": {"ground_truth_source", "scenario", "injected_fault"},
    }
    trace["extensions"] = {
        namespace: {key: value for key, value in metadata.items() if key in allowed[namespace]}
        for namespace, metadata in extensions.items()
        if namespace in allowed and isinstance(metadata, dict)
    }


def safe_metadata_key(key: str) -> bool:
    return (
        key.startswith("ragproof.pipeline.") or key.startswith("ragproof.ground_truth.")
        or key.startswith("ragproof.knowledge_base.")
        or key.endswith((".sha256", ".id", ".ids", ".version", ".count", ".rank", ".score", ".scores", ".chunk_ids", ".document_ids", ".document_versions", ".content_sha256"))
        or key in {"ragproof.capture.mode", "ragproof.scenario.name", "ragproof.response.abstained", "ragproof.content_retention_days", "ragproof.dataset.name", "ragproof.dataset.license", "gen_ai.operation.name", "gen_ai.provider.name", "gen_ai.request.model", "ragproof.embedding.dimension"}
    )

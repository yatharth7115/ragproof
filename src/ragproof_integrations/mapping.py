"""Declared vendor field mappings: no inferred evidence, versions, or labels."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import re

from .client import validate_canonical


def pointer(document, path):
    if path == "":
        return document
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("Mapping source must be an RFC 6901 JSON pointer")
    current = document
    try:
        for part in path[1:].split("/"):
            if re.search(r"~(?![01])", part):
                raise ValueError("Invalid JSON pointer escape")
            key = part.replace("~1", "/").replace("~0", "~")
            current = current[int(key)] if isinstance(current, list) else current[key]
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ValueError("A declared mapping points to a missing field") from error
    return deepcopy(current)


def _id(value, length=16):
    if not isinstance(value, str) or not value:
        raise ValueError("Vendor observation is missing its recorded ID")
    return value.lower() if re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", value) else hashlib.sha256(value.encode()).hexdigest()[:length]


def _time(value):
    if not isinstance(value, str):
        raise ValueError("Vendor observation has no recorded completion time")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Vendor observation has an invalid timestamp") from error
    if timestamp.tzinfo is None:
        raise ValueError("Vendor observation timestamps must include a timezone")
    return timestamp


def map_vendor_trace(provider: str, native_id: str, exported: dict, mapping: dict) -> dict:
    """Map a fetched trace with an explicit template, selectors and field pointers.

    Root/observation timing and IDs always come from the vendor. Templates can
    declare project, privacy and pipeline configuration, never answer content.
    """
    if provider not in {"langfuse", "langsmith"}:
        raise ValueError("Unsupported vendor")
    observations = exported.get("observations")
    root = exported.get("root")
    if not isinstance(observations, list) or not observations or not isinstance(root, dict):
        raise ValueError("Vendor export must contain actual root and observation records")
    template = deepcopy(mapping.get("template", {}))
    allowed_template = {"tenant_id", "project_id", "pipeline", "knowledge_base", "privacy"}
    if set(template) - allowed_template:
        raise ValueError("Mapping template may only declare project, pipeline, knowledge base and privacy")
    selected = {}
    for alias, selector in mapping.get("select", {}).items():
        if not isinstance(selector, dict) or len(selector) != 1 or not set(selector) <= {"id", "name"}:
            raise ValueError("Each observation selector requires one exact ID or name")
        matches = [record for record in observations if all(record.get(key) == value for key, value in selector.items())]
        if len(matches) != 1:
            raise ValueError("Observation selector must match exactly one real record")
        selected[alias] = matches[0]
    source = {**exported, "selected": selected}
    fields = mapping.get("fields", {})
    if set(fields) != {"query", "retrieval", "generation"}:
        raise ValueError("Mapping requires query, retrieval and generation JSON pointers")
    for target, path in fields.items():
        template[target] = pointer(source, path)
    if isinstance(template["query"], str):
        template["query"] = {"content": template["query"]}
    if not isinstance(template["generation"], dict) or not isinstance(template["retrieval"], dict):
        raise ValueError("Mapped retrieval and generation must be canonical objects")
    response = template["generation"].get("response")
    if isinstance(response, str):
        template["generation"]["response"] = {"content": response}
    mode = template.get("privacy", {}).get("capture_mode")
    if mode not in {"full", "redacted", "metadata_only"}:
        raise ValueError("Mapping must explicitly declare the capture mode")
    objects = [template["query"], template["generation"].get("response"),
               *template["retrieval"].get("candidates", []), *template["generation"].get("citations", [])]
    for item in objects:
        if not isinstance(item, dict):
            raise ValueError("Mapped content objects are missing or malformed")
        if "content" in item:
            if not isinstance(item["content"], str):
                raise ValueError("Mapped content must be text")
            digest = hashlib.sha256(item["content"].encode("utf-8")).hexdigest()
            if item.get("content_hash", digest).lower() != digest:
                raise ValueError("Mapped content hash does not match the vendor content")
            item["content_hash"] = digest
        if mode != "full":
            item.pop("content", None)
            item.pop("content_ref", None)
        if "content_hash" not in item:
            raise ValueError("Mapped content requires recorded text or its SHA-256 hash")
    name_map = mapping.get("span_names", {})
    spans = []
    for record in observations:
        if record.get("name") not in name_map:
            continue
        start_key, end_key, parent_key = (("startTime", "endTime", "parentObservationId")
                                         if provider == "langfuse" else ("start_time", "end_time", "parent_run_id"))
        start, end = record.get(start_key), record.get(end_key)
        if _time(end) < _time(start):
            raise ValueError("Vendor observation timestamps are out of order")
        spans.append({"span_id": _id(record.get("id")),
                      "parent_span_id": _id(record[parent_key]) if record.get(parent_key) else None,
                      "name": name_map[record["name"]], "started_at": start, "ended_at": end,
                      "status": "error" if record.get("error") or record.get("level") == "ERROR" else "ok",
                      "attributes": {}})
    roots = [span for span in spans if span["name"] == "rag.request"]
    if len(roots) != 1 or roots[0]["span_id"] != _id(root.get("id")):
        raise ValueError("span_names must map the actual root to exactly one rag.request")
    root_span = roots[0]
    trace_id = _id(provider + ":" + native_id, 32)
    template["generation"]["response_id"] = hashlib.sha256(
        (trace_id + ":" + template["generation"]["response"]["content_hash"]).encode()).hexdigest()
    template.update({"schema_version": "0.1.0", "trace_id": trace_id,
                     "started_at": root_span["started_at"], "ended_at": root_span["ended_at"],
                     "status": root_span["status"], "spans": spans,
                     "source": {"provider": provider, "native_trace_id": native_id,
                                "adapter_name": "ragproof-explicit-" + provider, "adapter_version": "0.1.0"}})
    validate_canonical(template)
    return template

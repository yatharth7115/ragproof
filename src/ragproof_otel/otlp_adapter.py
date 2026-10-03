from __future__ import annotations

from collections import defaultdict
import base64
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Iterable

from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from .canonical import component


ALLOWED_SPANS = {
    "rag.request", "document.parse", "document.chunk", "embedding.create",
    "retrieval.search", "retrieval.rerank", "prompt.construct", "llm.generate",
    "ragproof.evaluate",
}
PIPELINE_COMPONENTS = (
    "parser", "chunker", "embedder", "retriever", "reranker", "prompt",
    "generator", "citation",
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1_000_000_000, tz=timezone.utc).isoformat()


def _any_value(value) -> Any:
    selected = value.WhichOneof("value")
    if selected == "array_value":
        return [_any_value(item) for item in value.array_value.values]
    if selected == "kvlist_value":
        return {item.key: _any_value(item.value) for item in value.kvlist_value.values}
    if selected == "bytes_value":
        return value.bytes_value.hex()
    return getattr(value, selected) if selected else None


def _attributes(items: Iterable) -> dict[str, Any]:
    attributes = {}
    for item in items:
        if item.key in attributes:
            raise ValueError("OTLP attributes contain a duplicate key")
        attributes[item.key] = _any_value(item.value)
    return attributes


def _convert_json_ids(value: Any, *, to_protobuf: bool) -> Any:
    """OTLP deliberately differs from protobuf JSON for trace/span identifiers."""
    if isinstance(value, list):
        return [_convert_json_ids(item, to_protobuf=to_protobuf) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"traceId", "spanId", "parentSpanId"}:
            if not isinstance(item, str):
                raise ValueError("OTLP JSON identifiers must be hexadecimal strings")
            if to_protobuf:
                length = 32 if key == "traceId" else 16
                if not (key == "parentSpanId" and item == "") and not re.fullmatch(
                    rf"[a-fA-F0-9]{{{length}}}", item
                ):
                    raise ValueError("OTLP JSON identifiers have invalid hex encoding or length")
                item = base64.b64encode(bytes.fromhex(item)).decode("ascii")
            else:
                item = base64.b64decode(item).hex()
        else:
            item = _convert_json_ids(item, to_protobuf=to_protobuf)
        result[key] = item
    return result


def encode_export_json(request: ExportTraceServiceRequest) -> str:
    """Serialize OTLP JSON using the specification's hexadecimal identifiers."""
    return json.dumps(_convert_json_ids(
        MessageToDict(request, use_integers_for_enums=True), to_protobuf=False
    ), allow_nan=False)


def decode_export_request(payload: bytes, content_type: str) -> ExportTraceServiceRequest:
    request = ExportTraceServiceRequest()
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type not in {"application/x-protobuf", "application/json"}:
        raise ValueError(
            "OTLP traces require Content-Type application/x-protobuf or application/json"
        )
    try:
        if media_type == "application/x-protobuf":
            request.ParseFromString(payload)
        else:
            document = json.loads(payload.decode("utf-8"))
            if not isinstance(document, dict):
                raise ValueError("OTLP JSON request must be an object")
            ParseDict(_convert_json_ids(document, to_protobuf=True), request,
                      ignore_unknown_fields=True)
    except (DecodeError, ParseError, UnicodeDecodeError, json.JSONDecodeError,
            TypeError, RecursionError) as error:
        # Parser exceptions can contain the original content or credentials.
        raise ValueError("Malformed OTLP request") from error
    return request


class OtlpCanonicalAdapter:
    """Strict OTLP trace-to-RAGProof adapter; missing RAG lineage is rejected."""

    name = "ragproof-otlp-http"
    version = "0.1.0"

    def convert(self, request: ExportTraceServiceRequest) -> list[tuple[dict, int]]:
        grouped: dict[str, list[tuple[Any, dict[str, Any]]]] = defaultdict(list)
        for resource_spans in request.resource_spans:
            resource = _attributes(resource_spans.resource.attributes)
            for scope_spans in resource_spans.scope_spans:
                for span in scope_spans.spans:
                    if len(span.trace_id) != 16 or not any(span.trace_id):
                        raise ValueError("OTLP trace ID must be 16 nonzero bytes")
                    if len(span.span_id) != 8 or not any(span.span_id):
                        raise ValueError("OTLP span ID must be 8 nonzero bytes")
                    if span.parent_span_id and len(span.parent_span_id) != 8:
                        raise ValueError("OTLP parent span ID must be 8 bytes")
                    if not 0 < span.start_time_unix_nano <= span.end_time_unix_nano:
                        raise ValueError("OTLP span must have a completed, ordered time range")
                    grouped[span.trace_id.hex()].append((span, resource))
        return [
            (self._convert_trace(trace_id, entries), len(entries))
            for trace_id, entries in sorted(grouped.items())
        ]

    def _convert_trace(self, trace_id: str, entries: list[tuple[Any, dict]]) -> dict:
        root_matches = [entry for entry in entries if entry[0].name == "rag.request"]
        if len(root_matches) != 1:
            raise ValueError(
                f"Trace {trace_id} must contain exactly one rag.request root span"
            )
        root, resource = root_matches[0]
        span_ids = {span.span_id for span, _ in entries}
        if len(span_ids) != len(entries):
            raise ValueError("OTLP trace contains duplicate span IDs")
        parents = {span.span_id: span.parent_span_id for span, _ in entries}
        for span, _ in entries:
            current = span.span_id
            visited = set()
            while current != root.span_id:
                if current in visited or current not in parents:
                    raise ValueError("OTLP batch must contain one complete connected RAG trace")
                visited.add(current)
                current = parents[current]
        required_names = {"retrieval.search", "retrieval.rerank", "prompt.construct", "llm.generate"}
        if any(sum(span.name == name for span, _ in entries) != 1 for name in required_names):
            raise ValueError("OTLP trace requires exactly one span for each retrieval, rerank, prompt and generation stage")
        root_attrs = _attributes(root.attributes)
        by_name = {span.name: span for span, _ in entries}
        retrieval = self._required_span(by_name, "retrieval.search", trace_id)
        rerank = self._required_span(by_name, "retrieval.rerank", trace_id)
        prompt = self._required_span(by_name, "prompt.construct", trace_id)
        generation = self._required_span(by_name, "llm.generate", trace_id)
        retrieval_attrs = _attributes(retrieval.attributes)
        rerank_attrs = _attributes(rerank.attributes)
        prompt_attrs = _attributes(prompt.attributes)
        generation_attrs = _attributes(generation.attributes)

        capture_mode = self._required(root_attrs, "ragproof.capture.mode", trace_id)
        if capture_mode not in {"full", "redacted", "metadata_only"}:
            raise ValueError(f"Trace {trace_id} has unsupported capture mode {capture_mode!r}")
        versions = {
            name: self._required(root_attrs, f"ragproof.pipeline.{name}", trace_id)
            for name in PIPELINE_COMPONENTS
        }
        candidate_ids = self._list(retrieval_attrs, "ragproof.retrieval.chunk_ids")
        document_ids = self._list(retrieval_attrs, "ragproof.retrieval.document_ids")
        document_versions = self._list(
            retrieval_attrs, "ragproof.retrieval.document_versions"
        )
        content_hashes = self._list(retrieval_attrs, "ragproof.retrieval.content_sha256")
        scores = self._list(retrieval_attrs, "ragproof.retrieval.scores")
        contents = self._list(retrieval_attrs, "ragproof.retrieval.contents")
        aligned = [document_ids, document_versions, content_hashes, scores]
        if any(len(values) != len(candidate_ids) for values in aligned):
            raise ValueError(f"Trace {trace_id} has misaligned retrieval candidate attributes")
        if contents and len(contents) != len(candidate_ids):
            raise ValueError(f"Trace {trace_id} has misaligned retrieval content attributes")
        if any(not isinstance(value, str) or not value for values in
               (candidate_ids, document_ids, document_versions, content_hashes) for value in values):
            raise ValueError("Retrieval lineage values must be nonempty strings")
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("Retrieval chunk IDs must be unique within a trace")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or
               not math.isfinite(value) for value in scores):
            raise ValueError("Retrieval scores must be finite numbers")

        reranked_ids = self._list(rerank_attrs, "ragproof.rerank.chunk_ids")
        reranked_scores = self._list(rerank_attrs, "ragproof.rerank.scores")
        raw_context_ids = self._list(prompt_attrs, "ragproof.context.chunk_ids")
        if any(not isinstance(value, str) or not value for value in [*reranked_ids, *raw_context_ids]):
            raise ValueError("Rerank and context IDs must be nonempty strings")
        if len(reranked_scores) != len(reranked_ids):
            raise ValueError("OTLP rerank IDs and scores must be aligned")
        if len(set(reranked_ids)) != len(reranked_ids) or not set(reranked_ids) <= set(candidate_ids):
            raise ValueError("OTLP rerank IDs must be a unique subset of retrieval IDs")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or
               not math.isfinite(value) for value in reranked_scores):
            raise ValueError("Rerank scores must be finite numbers")
        rerank_by_id = {
            chunk_id: (rank, reranked_scores[rank - 1] if len(reranked_scores) >= rank else None)
            for rank, chunk_id in enumerate(reranked_ids, start=1)
        }
        context_ids = set(raw_context_ids)
        if not context_ids <= set(reranked_ids):
            raise ValueError("OTLP context IDs must be present in reranked retrieval candidates")
        candidates = []
        for index, chunk_id in enumerate(candidate_ids):
            rerank_rank, rerank_score = rerank_by_id.get(chunk_id, (None, None))
            candidate = {
                "chunk_id": chunk_id,
                "document_id": document_ids[index],
                "document_version": document_versions[index],
                "content_hash": content_hashes[index],
                "retrieval_rank": index + 1,
                "retrieval_score": scores[index],
                "rerank_rank": rerank_rank,
                "rerank_score": rerank_score,
                "included_in_context": chunk_id in context_ids,
            }
            if contents:
                candidate["content"] = contents[index]
            candidates.append(candidate)

        query_hash = self._required(retrieval_attrs, "ragproof.query.sha256", trace_id)
        response_hash = self._required(generation_attrs, "ragproof.response.sha256", trace_id)
        query = {"content_hash": query_hash}
        response = {"content_hash": response_hash}
        if "ragproof.query.content" in retrieval_attrs:
            query["content"] = retrieval_attrs["ragproof.query.content"]
        if "ragproof.response.content" in generation_attrs:
            response["content"] = generation_attrs["ragproof.response.content"]

        citations = []
        cited_chunk_id = generation_attrs.get("ragproof.citation.chunk_id")
        if cited_chunk_id:
            citation = {
                "chunk_id": cited_chunk_id,
                "document_id": self._required(
                    generation_attrs, "ragproof.citation.document_id", trace_id
                ),
                "document_version": self._required(
                    generation_attrs, "ragproof.citation.document_version", trace_id
                ),
                "content_hash": self._required(
                    generation_attrs, "ragproof.citation.content_sha256", trace_id
                ),
            }
            if "ragproof.citation.content" in generation_attrs:
                citation["content"] = generation_attrs["ragproof.citation.content"]
            citations.append(citation)

        corpus_version = self._required(
            root_attrs, "ragproof.knowledge_base.corpus_version", trace_id
        )
        retention = root_attrs.get("ragproof.content_retention_days", 0)
        if isinstance(retention, bool) or not isinstance(retention, int) or retention < 0:
            raise ValueError("OTLP retention must be a nonnegative integer")
        pipeline = {name: component(value) for name, value in versions.items()}
        pipeline["fingerprint"] = _digest(
            json.dumps({**versions, "corpus": corpus_version}, sort_keys=True)
        )
        accepted_spans = [
            self._span_record(span) for span, _ in entries if span.name in ALLOWED_SPANS
        ]
        accepted_spans.sort(key=lambda item: item["started_at"])
        trace = {
            "schema_version": "0.1.0",
            "trace_id": trace_id,
            "tenant_id": self._required(root_attrs, "ragproof.tenant.id", trace_id),
            "project_id": self._required(root_attrs, "ragproof.project.id", trace_id),
            "started_at": _timestamp(root.start_time_unix_nano),
            "ended_at": _timestamp(root.end_time_unix_nano),
            "status": "error" if root.status.code == 2 else "ok",
            "source": {
                "provider": "opentelemetry",
                "native_trace_id": trace_id,
                "adapter_name": self.name,
                "adapter_version": self.version,
            },
            "pipeline": pipeline,
            "knowledge_base": {
                "data_source_id": self._required(
                    root_attrs, "gen_ai.data_source.id", trace_id
                ),
                "corpus_version": corpus_version,
                "index_version": self._required(
                    root_attrs, "ragproof.knowledge_base.index_version", trace_id
                ),
            },
            "query": query,
            "retrieval": {"top_k": max(1, len(candidate_ids)), "candidates": candidates},
            "generation": {
                "response_id": _digest(f"{trace_id}:{response_hash}"),
                "response": response,
                "model_provider": self._required(
                    generation_attrs, "gen_ai.provider.name", trace_id
                ),
                "model_name": self._required(
                    generation_attrs, "gen_ai.request.model", trace_id
                ),
                "citations": citations,
            },
            "privacy": {
                "capture_mode": capture_mode,
                "redaction_applied": capture_mode == "redacted",
                "content_retention_days": retention,
            },
            "spans": accepted_spans,
            "extensions": {
                "opentelemetry": {
                    "service_name": resource.get("service.name", "unknown"),
                    "ignored_span_count": len(entries) - len(accepted_spans),
                }
            },
        }
        self._attach_real_data_provenance(trace, root_attrs)
        content_objects = [query, response, *candidates, *citations]
        for item in content_objects:
            if not isinstance(item["content_hash"], str) or not re.fullmatch(r"[0-9a-fA-F]{64}", item["content_hash"]):
                raise ValueError("OTLP content hashes must be SHA-256 hex digests")
            if "content" in item:
                if capture_mode == "metadata_only":
                    raise ValueError("Metadata-only OTLP traces cannot contain content")
                if not isinstance(item["content"], str):
                    raise ValueError("OTLP content must be text")
                if capture_mode == "redacted" and item["content"] != "[REDACTED]":
                    raise ValueError("Redacted OTLP content must use the redaction marker")
                if capture_mode == "full" and _digest(item["content"]) != item["content_hash"].lower():
                    raise ValueError("OTLP content does not match its declared SHA-256 hash")
        return trace

    @staticmethod
    def _required_span(by_name: dict, name: str, trace_id: str):
        if name not in by_name:
            raise ValueError(f"Trace {trace_id} is missing required span {name}")
        return by_name[name]

    @staticmethod
    def _required(attributes: dict, key: str, trace_id: str):
        value = attributes.get(key)
        if not isinstance(value, str) or value == "":
            raise ValueError(f"Trace {trace_id} is missing required attribute {key}")
        return value

    @staticmethod
    def _list(attributes: dict, key: str) -> list:
        if key not in attributes:
            if key == "ragproof.retrieval.contents":
                return []
            raise ValueError(f"Missing required OTLP array attribute {key}")
        value = attributes[key]
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        raise ValueError(f"OTLP attribute {key} must be an array")

    @staticmethod
    def _span_record(span) -> dict:
        parent_id = span.parent_span_id.hex() if span.parent_span_id else None
        status = "error" if span.status.code == 2 else "ok" if span.status.code == 1 else "unset"
        return {
            "span_id": span.span_id.hex(),
            "parent_span_id": parent_id,
            "name": span.name,
            "started_at": _timestamp(span.start_time_unix_nano),
            "ended_at": _timestamp(span.end_time_unix_nano),
            "status": status,
            "attributes": _attributes(span.attributes),
        }

    @staticmethod
    def _attach_real_data_provenance(trace: dict, root_attrs: dict) -> None:
        dataset = root_attrs.get("ragproof.dataset.name")
        license_name = root_attrs.get("ragproof.dataset.license")
        if dataset and license_name:
            trace["extensions"]["cuad"] = {
                "dataset": dataset,
                "license": license_name,
                "primary_document_id": root_attrs.get(
                    "ragproof.document.primary_id", "unknown"
                ),
                "current_document_version": root_attrs.get(
                    "ragproof.document.current_version", "unknown"
                ),
            }
        expected_hash = root_attrs.get(
            "ragproof.ground_truth.expected_evidence_sha256"
        )
        if expected_hash:
            trace["extensions"]["evaluation_ground_truth"] = {
                "source": "CUAD human annotation",
                "expected_evidence_hash": expected_hash,
                **{
                    name: bool(root_attrs.get(f"ragproof.ground_truth.{name}"))
                    for name in (
                        "source_contains_evidence", "parse_contains_evidence",
                        "chunk_contains_evidence", "retrieval_contains_evidence",
                        "context_contains_evidence",
                    )
                },
            }

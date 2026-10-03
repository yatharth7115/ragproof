from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Iterable

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import StatusCode

from ragproof_lab.components import tokenize
from ragproof_lab.models import RunArtifacts


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def iso_timestamp(nanoseconds: int) -> str:
    return datetime.fromtimestamp(nanoseconds / 1_000_000_000, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def component(value: str) -> dict[str, str]:
    name, _, version = value.partition(":")
    version = version or "unspecified"
    return {"name": name, "version": version, "config_hash": digest(value)}


def content_object(content: str, capture_mode: str) -> dict[str, str]:
    result = {"content_hash": digest(content)}
    if capture_mode == "full":
        result["content"] = content
    elif capture_mode == "redacted":
        result["content"] = "[REDACTED]"
    return result


def serializable_attributes(attributes: dict[str, Any] | None) -> dict[str, Any]:
    if not attributes:
        return {}
    return {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in attributes.items()
    }


def status_name(code: StatusCode) -> str:
    if code is StatusCode.ERROR:
        return "error"
    if code is StatusCode.OK:
        return "ok"
    return "unset"


def build_canonical_trace(
    run: RunArtifacts,
    spans: Iterable[ReadableSpan],
    capture_mode: str,
) -> dict[str, Any]:
    ordered_spans = sorted(spans, key=lambda span: span.start_time or 0)
    root = next(span for span in ordered_spans if span.name == "rag.request")
    trace_id = f"{root.context.trace_id:032x}"
    context_ids = {chunk.chunk_id for chunk in run.context_chunks}

    versions = asdict(run.pipeline_versions)
    pipeline = {
        "fingerprint": digest(json.dumps(versions, sort_keys=True)),
        "parser": component(run.pipeline_versions.parser),
        "chunker": component(run.pipeline_versions.chunker),
        "embedder": component(run.pipeline_versions.embedder),
        "retriever": component(run.pipeline_versions.retriever),
        "reranker": component(run.pipeline_versions.reranker),
        "prompt": component(run.pipeline_versions.prompt),
        "generator": component(run.pipeline_versions.generator),
        "citation": component(run.pipeline_versions.citation),
    }

    candidates = []
    reranked_by_id = {
        candidate.chunk.chunk_id: candidate for candidate in run.reranked_candidates
    }
    for candidate in run.retrieved_candidates:
        reranked = reranked_by_id[candidate.chunk.chunk_id]
        item: dict[str, Any] = {
            "chunk_id": candidate.chunk.chunk_id,
            "document_id": candidate.chunk.document_id,
            "document_version": candidate.chunk.document_version,
            "content_hash": digest(candidate.chunk.text),
            "retrieval_rank": candidate.retrieval_rank,
            "retrieval_score": candidate.hybrid_score,
            "rerank_rank": reranked.rerank_rank,
            "rerank_score": reranked.rerank_score,
            "included_in_context": candidate.chunk.chunk_id in context_ids,
        }
        if capture_mode == "full":
            item["content"] = candidate.chunk.text
        elif capture_mode == "redacted":
            item["content"] = "[REDACTED]"
        candidates.append(item)

    answer_text = run.answer.text if run.answer else ""
    citations = []
    if run.answer and run.answer.cited_chunk_id:
        cited_chunk = next(
            chunk for chunk in run.chunks if chunk.chunk_id == run.answer.cited_chunk_id
        )
        citation = {
            "chunk_id": cited_chunk.chunk_id,
            "document_id": cited_chunk.document_id,
            "document_version": cited_chunk.document_version,
            "content_hash": digest(cited_chunk.text),
        }
        if capture_mode == "full":
            citation["content"] = cited_chunk.text
        elif capture_mode == "redacted":
            citation["content"] = "[REDACTED]"
        citations.append(citation)
    span_records = []
    for span in ordered_spans:
        parent_span_id = None
        if span.parent is not None:
            parent_span_id = f"{span.parent.span_id:016x}"
        span_records.append(
            {
                "span_id": f"{span.context.span_id:016x}",
                "parent_span_id": parent_span_id,
                "name": span.name,
                "started_at": iso_timestamp(span.start_time),
                "ended_at": iso_timestamp(span.end_time),
                "status": status_name(span.status.status_code),
                "attributes": serializable_attributes(dict(span.attributes or {})),
            }
        )

    return {
        "schema_version": "0.1.0",
        "trace_id": trace_id,
        "tenant_id": "cuad-public",
        "project_id": "cuad-contract-review",
        "started_at": iso_timestamp(root.start_time),
        "ended_at": iso_timestamp(root.end_time),
        "status": "error" if root.status.status_code is StatusCode.ERROR else "ok",
        "source": {
            "provider": "opentelemetry",
            "native_trace_id": trace_id,
            "adapter_name": "ragproof-otel-python",
            "adapter_version": "0.1.0",
        },
        "pipeline": pipeline,
        "knowledge_base": {
            "data_source_id": "cuad-v1",
            "corpus_version": run.pipeline_versions.corpus,
            "index_version": "in-memory-hybrid:v1",
        },
        "query": content_object(run.query, capture_mode),
        "retrieval": {"top_k": 6, "candidates": candidates},
        "generation": {
            "response_id": digest(f"{trace_id}:{answer_text}"),
            "response": content_object(answer_text, capture_mode),
            "model_provider": "ragproof-lab",
            "model_name": run.pipeline_versions.generator,
            "input_tokens": len(tokenize(run.query))
            + sum(len(tokenize(chunk.text)) for chunk in run.context_chunks),
            "output_tokens": len(tokenize(answer_text)),
            "citations": citations,
        },
        "privacy": {
            "capture_mode": capture_mode,
            "redaction_applied": capture_mode == "redacted",
            "content_retention_days": 0 if capture_mode == "metadata_only" else 30,
        },
        "spans": span_records,
        "extensions": {
            "cuad": {
                "dataset": "CUAD v1",
                "license": "CC BY 4.0",
                "primary_document_id": run.expected_document_id,
                "current_document_version": run.current_document_version,
            },
            "evaluation_ground_truth": {
                "source": "CUAD human annotation",
                "expected_evidence_hash": digest(run.expected_evidence),
                "source_contains_evidence": any(
                    run.expected_evidence.lower() in document.content.lower()
                    for document in run.source_documents
                ),
                "parse_contains_evidence": any(
                    run.expected_evidence.lower() in block.text.lower()
                    for block in run.parsed_blocks
                ),
                "chunk_contains_evidence": any(
                    run.expected_evidence.lower() in chunk.text.lower()
                    for chunk in run.chunks
                ),
                "retrieval_contains_evidence": any(
                    run.expected_evidence.lower() in candidate.chunk.text.lower()
                    for candidate in run.retrieved_candidates
                ),
                "context_contains_evidence": any(
                    run.expected_evidence.lower() in chunk.text.lower()
                    for chunk in run.context_chunks
                ),
            },
        },
    }

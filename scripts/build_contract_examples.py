"""Regenerate tracked schema examples from the real CUAD failure laboratory."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ragproof_otel import capture_scenario


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def stable_hex(label: str, length: int) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()[:length]


def write_json(name: str, payload: dict) -> None:
    (EXAMPLES / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    captured = capture_scenario("generation_failure", capture_mode="full")
    healthy = capture_scenario("healthy", capture_mode="full")
    stale = capture_scenario("stale_knowledge", capture_mode="full")
    trace = captured.canonical_trace
    run = captured.artifacts

    stable_trace_id = stable_hex("cuad-v1-generation-failure", 32)
    trace["trace_id"] = stable_trace_id
    trace["source"]["native_trace_id"] = stable_trace_id
    trace["generation"]["response_id"] = stable_hex(
        f"{stable_trace_id}:{run.answer.text}", 64
    )

    original_to_stable: dict[str, str] = {}
    for index, span in enumerate(trace["spans"]):
        original_to_stable[span["span_id"]] = stable_hex(f"{span['name']}:{index}", 16)
    base_time = datetime(2021, 3, 11, 12, 0, tzinfo=timezone.utc)
    for index, span in enumerate(trace["spans"]):
        span["span_id"] = original_to_stable[span["span_id"]]
        if span["parent_span_id"] is not None:
            span["parent_span_id"] = original_to_stable[span["parent_span_id"]]
        span["started_at"] = (base_time + timedelta(milliseconds=index)).isoformat().replace(
            "+00:00", "Z"
        )
        span["ended_at"] = (base_time + timedelta(milliseconds=index + 1)).isoformat().replace(
            "+00:00", "Z"
        )
    trace["started_at"] = base_time.isoformat().replace("+00:00", "Z")
    trace["ended_at"] = (base_time + timedelta(milliseconds=len(trace["spans"]) + 1)).isoformat().replace(
        "+00:00", "Z"
    )
    trace["extensions"]["controlled_test"] = {
        "scenario": "generation_failure",
        "source_content_modified": False,
        "ground_truth_source": "CUAD human annotation",
    }

    evidence_chunk = next(
        chunk for chunk in run.context_chunks if run.expected_evidence.lower() in chunk.text.lower()
    )
    claim_id = hashlib.sha256(
        f"{trace['generation']['response_id']}:1:{run.answer.text}".encode("utf-8")
    ).hexdigest()
    verification = {
        "schema_version": "0.1.0",
        "trace_id": stable_trace_id,
        "response_id": trace["generation"]["response_id"],
        "evaluator": {
            "name": "ragproof-claim-verifier",
            "version": "lexical-numeric-0.1.1",
        },
        "claims": [
            {
                "claim_id": claim_id,
                "text": run.answer.text,
                "verdict": "CONTRADICTED",
                "confidence": 1.0,
                "evidence": [
                    {
                        "chunk_id": evidence_chunk.chunk_id,
                        "relation": "contradicts",
                        "document_version": evidence_chunk.document_version,
                        "freshness": "current",
                    }
                ],
                "explanation": (
                    "A closely matching evidence statement contains conflicting numeric terms."
                ),
                "judge_disagreement": False,
            }
        ],
    }
    diagnosis = {
        "schema_version": "0.1.0",
        "diagnosis_id": hashlib.sha256(
            (
                f"{stable_trace_id}\0ragproof-root-cause-diagnoser\0"
                f"evidence-precedence-0.1.0\0{claim_id}"
            ).encode("utf-8")
        ).hexdigest(),
        "trace_id": stable_trace_id,
        "claim_ids": [claim_id],
        "status": "suspected",
        "primary_hypothesis": {
            "category": "GENERATION_FAILURE",
            "component": "generator",
            "confidence": 0.98,
        },
        "alternative_hypotheses": [],
        "observations": [
            "Relevant evidence reached the model context, but at least one answer claim is not fully supported."
        ],
        "recommended_experiment": (
            "Regenerate from the identical captured context with a controlled generator or prompt change."
        ),
        "recommended_action": (
            "Do not mark the cause confirmed until a one-variable replay or human review agrees."
        ),
    }

    replay_id = hashlib.sha256(
        (
            f"{diagnosis['diagnosis_id']}\0generator\0corrupt_annotated_value"
            "\0healthy\0one-variable-0.1.0"
        ).encode("utf-8")
    ).hexdigest()
    replay = {
        "schema_version": "0.1.0",
        "replay_id": replay_id,
        "diagnosis_id": diagnosis["diagnosis_id"],
        "original_trace_id": stable_trace_id,
        "changed_variable": {
            "component": "generator",
            "from_value": "corrupt_annotated_value",
            "to_value": "healthy",
        },
        "baseline": {
            "trace_id": stable_hex("cuad-generation-failure-replay-baseline", 32),
            "pipeline_fingerprint": trace["pipeline"]["fingerprint"],
            "response_hash": trace["generation"]["response"]["content_hash"],
            "verdicts": ["CONTRADICTED"],
            "all_claims_supported": False,
            "citation_supported": False,
            "reproduced_original": True,
        },
        "candidate": {
            "trace_id": stable_hex("cuad-generation-failure-replay-candidate", 32),
            "pipeline_fingerprint": healthy.canonical_trace["pipeline"]["fingerprint"],
            "response_hash": healthy.canonical_trace["generation"]["response"]["content_hash"],
            "verdicts": ["SUPPORTED"],
            "all_claims_supported": True,
            "citation_supported": True,
        },
        "outcome": "CONFIRMED",
        "observations": [
            "The unchanged baseline reproduced the original outcome.",
            "Changing only the diagnosed component removed the observed failure.",
        ],
    }

    stale_trace = stale.canonical_trace
    primary_document_id = stale_trace["extensions"]["cuad"]["primary_document_id"]
    from_version = "cuad-v1:stale-index-snapshot"
    to_version = stale_trace["extensions"]["cuad"]["current_document_version"]

    def observed_hashes(observed_trace: dict, version: str) -> list[str]:
        chunks = [
            *observed_trace["retrieval"]["candidates"],
            *observed_trace["generation"].get("citations", []),
        ]
        return sorted(
            {
                chunk["content_hash"]
                for chunk in chunks
                if chunk["document_id"] == primary_document_id
                and chunk["document_version"] == version
            }
        )

    stale_stable_trace_id = stable_hex("cuad-v1-stale-knowledge", 32)
    stale_claim_id = stable_hex("cuad-v1-stale-knowledge-claim", 64)
    stale_diagnosis_id = stable_hex("cuad-v1-stale-knowledge-diagnosis", 64)
    usage_modes = []
    primary_candidates = [
        item
        for item in stale_trace["retrieval"]["candidates"]
        if item["document_id"] == primary_document_id
        and item["document_version"] == from_version
    ]
    if any(item["included_in_context"] for item in primary_candidates):
        usage_modes.append("context")
    if any(not item["included_in_context"] for item in primary_candidates):
        usage_modes.append("retrieved_only")
    if any(
        item["document_id"] == primary_document_id
        and item["document_version"] == from_version
        for item in stale_trace["generation"].get("citations", [])
    ):
        usage_modes.append("citation")
    change_id = hashlib.sha256(
        "\0".join(
            (
                stale_trace["tenant_id"], stale_trace["project_id"],
                primary_document_id, from_version, to_version,
            )
        ).encode("utf-8")
    ).hexdigest()
    affected = [
        {
            "trace_id": stale_stable_trace_id,
            "response_id": stable_hex("cuad-v1-stale-knowledge-response", 64),
            "usage_modes": usage_modes,
            "claim_ids": [stale_claim_id],
            "diagnoses": [
                {
                    "diagnosis_id": stale_diagnosis_id,
                    "category": "STALE_KNOWLEDGE",
                    "status": "confirmed",
                }
            ],
        }
    ]
    impact_id = hashlib.sha256(
        "\0".join(
            (
                change_id,
                "direct-usage-0.1.1",
                json.dumps(affected, sort_keys=True, separators=(",", ":")),
            )
        ).encode("utf-8")
    ).hexdigest()
    impact = {
        "schema_version": "0.1.0",
        "impact_id": impact_id,
        "change_id": change_id,
        "tenant_id": stale_trace["tenant_id"],
        "project_id": stale_trace["project_id"],
        "document_id": primary_document_id,
        "change": {
            "from_version": from_version,
            "to_version": to_version,
            "version_changed": True,
            "content_change": "unknown",
            "from_chunk_hashes": observed_hashes(stale_trace, from_version),
            "to_chunk_hashes": observed_hashes(healthy.canonical_trace, to_version),
        },
        "summary": {
            "affected_trace_count": 1,
            "affected_answer_count": 1,
            "affected_claim_count": 1,
            "confirmed_incident_count": 1,
        },
        "affected_traces": affected,
    }
    regression_case = {
        "schema_version": "0.1.0",
        "case_id": hashlib.sha256(
            f"{replay_id}\0confirmed-replay-0.1.0".encode("utf-8")
        ).hexdigest(),
        "source": {
            "dataset": trace["extensions"]["cuad"]["dataset"],
            "license": trace["extensions"]["cuad"]["license"],
        },
        "incident": {
            "diagnosis_id": diagnosis["diagnosis_id"],
            "replay_id": replay_id,
            "category": "GENERATION_FAILURE",
        },
        "input": {
            "original_trace_id": stable_trace_id,
            "query_hash": trace["query"]["content_hash"],
        },
        "failure": {
            key: replay["baseline"][key]
            for key in ("trace_id", "pipeline_fingerprint", "response_hash", "verdicts")
        },
        "passing_reference": {
            key: replay["candidate"][key]
            for key in ("trace_id", "pipeline_fingerprint", "response_hash", "verdicts")
        },
        "expectations": {
            "acceptable_verdicts": ["SUPPORTED"],
            "citation_must_support": True,
        },
    }
    incident_created_at = base_time.isoformat().replace("+00:00", "Z")
    incident_summary = {
        "diagnosis_id": diagnosis["diagnosis_id"],
        "trace_id": stable_trace_id,
        "response_id": trace["generation"]["response_id"],
        "status": "confirmed",
        "category": "GENERATION_FAILURE",
        "component": "generator",
        "confidence": 0.98,
        "created_at": incident_created_at,
        "claim_count": 1,
        "verdict_counts": {"CONTRADICTED": 1},
        "replay": {"replay_id": replay_id, "outcome": "CONFIRMED"},
        "regression_case_id": regression_case["case_id"],
    }
    incident_list = {
        "schema_version": "0.1.0",
        "summary": {
            "total": 1,
            "status_counts": {
                "confirmed": 1,
                "needs_review": 0,
                "rejected": 0,
                "replaying": 0,
                "suspected": 0,
            },
            "category_counts": {"GENERATION_FAILURE": 1},
        },
        "page": {"total": 1, "limit": 100, "offset": 0},
        "incidents": [incident_summary],
    }
    confirmed_diagnosis = dict(diagnosis)
    confirmed_diagnosis["status"] = "confirmed"
    verification_summary = {
        "status": "completed",
        "trace_id": verification["trace_id"],
        "response_id": verification["response_id"],
        "evaluator": verification["evaluator"],
        "claims": [
            {
                key: verification["claims"][0][key]
                for key in (
                    "claim_id", "verdict", "confidence", "evidence",
                    "judge_disagreement",
                )
            }
        ],
    }
    incident_detail = {
        "schema_version": "0.1.0",
        "diagnosis": confirmed_diagnosis,
        "verification": verification_summary,
        "replay": replay,
        "regression_case": regression_case,
        "lineage": {
            "trace_id": stable_trace_id,
            "response_id": trace["generation"]["response_id"],
            "query_hash": trace["query"]["content_hash"],
            "response_hash": trace["generation"]["response"]["content_hash"],
            "pipeline_fingerprint": trace["pipeline"]["fingerprint"],
            "corpus_version": trace["knowledge_base"]["corpus_version"],
            "index_version": trace["knowledge_base"]["index_version"],
            "candidates": [
                {
                    "chunk_id": item["chunk_id"],
                    "content_hash": item["content_hash"],
                    "document_id": item["document_id"],
                    "document_version": item["document_version"],
                    "included_in_context": item["included_in_context"],
                }
                for item in trace["retrieval"]["candidates"]
            ],
            "citations": [
                {
                    "chunk_id": item["chunk_id"],
                    "content_hash": item["content_hash"],
                    "document_id": item["document_id"],
                    "document_version": item["document_version"],
                }
                for item in trace["generation"].get("citations", [])
            ],
        },
        "impact_reports": [],
        "timeline": [
            {"status": "suspected", "replay_id": None, "changed_at": incident_created_at},
            {"status": "replaying", "replay_id": None, "changed_at": incident_created_at},
            {"status": "confirmed", "replay_id": replay_id, "changed_at": incident_created_at},
        ],
    }

    write_json("canonical-trace.json", trace)
    write_json("claim-verification.json", verification)
    write_json("diagnosis.json", diagnosis)
    write_json("replay-result.json", replay)
    write_json("change-impact.json", impact)
    write_json("regression-case.json", regression_case)
    write_json("incident-list.json", incident_list)
    write_json("incident-detail.json", incident_detail)


if __name__ == "__main__":
    main()

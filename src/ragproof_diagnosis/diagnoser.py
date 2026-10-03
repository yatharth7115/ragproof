from __future__ import annotations

import hashlib
import json
from ragproof_resources import load_schema

from jsonschema import Draft202012Validator, FormatChecker
from opentelemetry import trace as otel_trace

from ragproof_lab.components import normalize
from ragproof_store import TraceRepository
from ragproof_verifier import ClaimVerifier


HYPOTHESES = {
    "PARSING_FAILURE": ("parser", 0.99),
    "CHUNKING_FAILURE": ("chunker", 0.99),
    "RETRIEVAL_FAILURE": ("retriever", 0.99),
    "RERANKING_FAILURE": ("reranker", 0.99),
    "GENERATION_FAILURE": ("generator", 0.98),
    "CITATION_FAILURE": ("citation_builder", 0.98),
    "STALE_KNOWLEDGE": ("knowledge_base", 1.0),
    "UNDETERMINED": ("unknown", 0.25),
}

EXPERIMENTS = {
    "PARSING_FAILURE": "Replay with the source artifact and a parser that preserves the labelled span.",
    "CHUNKING_FAILURE": "Replay with a chunker that keeps the labelled span intact.",
    "RETRIEVAL_FAILURE": "Replay retrieval against the same index with the labelled chunk forced into the candidate audit.",
    "RERANKING_FAILURE": "Replay with reranking bypassed while holding retrieved candidates constant.",
    "GENERATION_FAILURE": "Regenerate from the identical captured context with a controlled generator or prompt change.",
    "CITATION_FAILURE": "Rebuild citations from verified claim-evidence links without changing the answer.",
    "STALE_KNOWLEDGE": "Replay against the recorded current document version.",
    "UNDETERMINED": "Capture the missing source, intermediate-stage, context, or evaluator evidence and rerun diagnosis.",
}


class RootCauseDiagnoser:
    name = "ragproof-root-cause-diagnoser"
    version = "evidence-precedence-0.1.0"

    def __init__(
        self,
        repository: TraceRepository,
        verifier: ClaimVerifier | None = None,
        tracer=None,
    ) -> None:
        self.repository = repository
        self.verifier = verifier or ClaimVerifier(repository)
        self.tracer = tracer or otel_trace.get_tracer("ragproof-diagnosis", self.version)
        self.validator = Draft202012Validator(
            load_schema("diagnosis.schema.json"),
            format_checker=FormatChecker(),
        )

    def diagnose_response(self, response_id: str) -> dict:
        existing = self.repository.get_diagnosis(response_id, self.name, self.version)
        if existing is not None:
            return existing
        with self.tracer.start_as_current_span(
            "ragproof.diagnose",
            attributes={
                "ragproof.response.id": response_id,
                "ragproof.diagnoser.name": self.name,
                "ragproof.diagnoser.version": self.version,
            },
        ) as span:
            result = self._diagnose_response(response_id)
            span.set_attribute(
                "ragproof.diagnosis.status", result.get("status", "blocked")
            )
            if "primary_hypothesis" in result:
                span.set_attribute(
                    "ragproof.diagnosis.category",
                    result["primary_hypothesis"]["category"],
                )
            return result

    def _diagnose_response(self, response_id: str) -> dict:
        verification = self.verifier.evaluate_response(response_id)
        if verification.get("status") == "blocked_content_unavailable":
            return {
                "trace_id": verification["trace_id"],
                "response_id": response_id,
                "status": "blocked_verification_unavailable",
                "reason": verification["reason"],
            }

        trace = self.repository.get_trace(verification["trace_id"])
        lineage = self.repository.answer_lineage(response_id)
        if trace is None or lineage is None:
            raise KeyError(f"Missing trace lineage for response: {response_id}")

        category, observations = self._classify(trace, lineage, verification)
        component, confidence = HYPOTHESES[category]
        claim_ids = [claim["claim_id"] for claim in verification["claims"]]
        diagnosis_id = hashlib.sha256(
            f"{trace['trace_id']}\0{self.name}\0{self.version}\0{','.join(claim_ids)}".encode(
                "utf-8"
            )
        ).hexdigest()
        diagnosis = {
            "schema_version": "0.1.0",
            "diagnosis_id": diagnosis_id,
            "trace_id": trace["trace_id"],
            "claim_ids": claim_ids,
            "status": "needs_review" if category == "UNDETERMINED" else "suspected",
            "primary_hypothesis": {
                "category": category,
                "component": component,
                "confidence": confidence,
            },
            "alternative_hypotheses": self._alternatives(category, verification),
            "observations": observations,
            "recommended_experiment": EXPERIMENTS[category],
            "recommended_action": (
                "Do not mark the cause confirmed until a one-variable replay or human review agrees."
                if category != "UNDETERMINED"
                else "Collect the requested evidence before assigning a component-level cause."
            ),
        }
        errors = sorted(
            self.validator.iter_errors(diagnosis), key=lambda error: list(error.path)
        )
        if errors:
            messages = "; ".join(error.message for error in errors)
            raise ValueError(f"Diagnosis contract violation: {messages}")
        return self.repository.save_diagnosis(
            response_id,
            diagnosis,
            {"name": self.name, "version": self.version},
        )

    def _classify(self, trace: dict, lineage: dict, verification: dict) -> tuple[str, list[str]]:
        probes = trace.get("extensions", {}).get("evaluation_ground_truth", {})
        if probes.get("source_contains_evidence") is True:
            if probes.get("parse_contains_evidence") is False:
                return "PARSING_FAILURE", [
                    "The labelled evidence exists in the source but is absent after parsing."
                ]
            if probes.get("chunk_contains_evidence") is False:
                return "CHUNKING_FAILURE", [
                    "Parsing retains the labelled evidence, but no complete chunk retains it."
                ]
            if probes.get("retrieval_contains_evidence") is False:
                return "RETRIEVAL_FAILURE", [
                    "An indexed chunk contains the labelled evidence, but retrieval omits it."
                ]
            if probes.get("context_contains_evidence") is False:
                return "RERANKING_FAILURE", [
                    "Retrieval returns the labelled evidence, but final context selection excludes it."
                ]

        verdicts = {claim["verdict"] for claim in verification["claims"]}
        if "STALE_EVIDENCE" in verdicts:
            return "STALE_KNOWLEDGE", [
                "The answer is supported only by a captured document version marked superseded."
            ]
        if verdicts & {"CONTRADICTED", "UNSUPPORTED", "PARTIALLY_SUPPORTED"}:
            if probes.get("context_contains_evidence") is True or "CONTRADICTED" in verdicts:
                return "GENERATION_FAILURE", [
                    "Relevant evidence reached the model context, but at least one answer claim is not fully supported."
                ]

        citation_state = self.citation_state(lineage, verification)
        if citation_state is False and verdicts <= {"SUPPORTED", "STALE_EVIDENCE"}:
            return "CITATION_FAILURE", [
                "The answer claim is grounded in context, but the recorded citation target does not support it."
            ]

        observations = [
            "The recorded evidence does not isolate one failing pipeline component."
        ]
        if not probes:
            observations.append(
                "No optional human-labelled intermediate-stage probes were captured."
            )
        if citation_state is None:
            observations.append("Citation content was unavailable for alignment.")
        return "UNDETERMINED", observations

    def citation_state(self, lineage: dict, verification: dict) -> bool | None:
        if not verification.get("claims"):
            return None
        citations = lineage.get("citations", [])
        if not citations or any(not citation["content_ref"] for citation in citations):
            return None
        cited_text = " ".join(
            self.repository.objects.get_text(citation["content_ref"])
            for citation in citations
        )
        return all(
            normalize(claim["text"]) in normalize(cited_text)
            for claim in verification["claims"]
        )

    @staticmethod
    def _alternatives(category: str, verification: dict) -> list[dict]:
        if category != "UNDETERMINED":
            return []
        verdicts = {claim["verdict"] for claim in verification["claims"]}
        alternatives = []
        if verdicts & {"UNSUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED"}:
            alternatives.append(
                {"category": "GENERATION_FAILURE", "component": "generator", "confidence": 0.35}
            )
        alternatives.append(
            {"category": "RETRIEVAL_FAILURE", "component": "retriever", "confidence": 0.2}
        )
        return alternatives[:2]

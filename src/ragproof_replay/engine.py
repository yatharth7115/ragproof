from __future__ import annotations

from dataclasses import fields, replace
import hashlib
import json
from ragproof_resources import load_schema

from jsonschema import Draft202012Validator, FormatChecker
from opentelemetry import trace as otel_trace

from ragproof_diagnosis import RootCauseDiagnoser
from ragproof_lab.scenarios import Scenario
from ragproof_otel import capture_configuration
from ragproof_store import TraceRepository
from ragproof_verifier import ClaimVerifier


CHANGE_MAP = {
    "PARSING_FAILURE": ("parser_mode", "parser"),
    "CHUNKING_FAILURE": ("chunker_mode", "chunker"),
    "RETRIEVAL_FAILURE": ("retriever_mode", "retriever"),
    "RERANKING_FAILURE": ("reranker_mode", "reranker"),
    "GENERATION_FAILURE": ("generator_mode", "generator"),
    "CITATION_FAILURE": ("citation_mode", "citation"),
    "STALE_KNOWLEDGE": ("corpus_mode", "knowledge_base"),
}
BEHAVIOR_FIELDS = {
    "parser_mode", "chunker_mode", "retriever_mode", "reranker_mode",
    "generator_mode", "citation_mode", "corpus_mode",
}


class DifferentialReplayEngine:
    name = "ragproof-cuad-differential-replay"
    version = "one-variable-0.1.0"

    def __init__(
        self,
        repository: TraceRepository,
        verifier: ClaimVerifier | None = None,
        diagnoser: RootCauseDiagnoser | None = None,
        tracer=None,
    ) -> None:
        self.repository = repository
        self.verifier = verifier or ClaimVerifier(repository)
        self.diagnoser = diagnoser or RootCauseDiagnoser(repository, self.verifier)
        self.tracer = tracer or otel_trace.get_tracer("ragproof-replay", self.version)
        self.validator = Draft202012Validator(
            load_schema("replay-result.schema.json"),
            format_checker=FormatChecker(),
        )

    def replay_diagnosis(self, diagnosis_id: str) -> dict:
        existing = self.repository.get_replay_for_diagnosis(diagnosis_id)
        if existing is not None:
            return existing
        diagnosis = self.repository.get_diagnosis_by_id(diagnosis_id)
        if diagnosis is None:
            raise KeyError(f"Unknown diagnosis: {diagnosis_id}")
        category = diagnosis["primary_hypothesis"]["category"]
        if category not in CHANGE_MAP:
            raise ValueError(
                f"Diagnosis {category} has no safe one-variable automatic replay"
            )

        with self.tracer.start_as_current_span(
            "ragproof.replay",
            attributes={
                "ragproof.diagnosis.id": diagnosis_id,
                "ragproof.diagnosis.category": category,
                "ragproof.replay.engine.version": self.version,
            },
        ) as span:
            self.repository.transition_diagnosis(diagnosis_id, "replaying")
            try:
                result = self._execute(diagnosis)
            except Exception:
                self.repository.transition_diagnosis(diagnosis_id, "needs_review")
                raise
            span.set_attribute("ragproof.replay.id", result["replay_id"])
            span.set_attribute("ragproof.replay.outcome", result["outcome"])
            span.set_attribute(
                "ragproof.replay.changed_component",
                result["changed_variable"]["component"],
            )
            return result

    def _execute(self, diagnosis: dict) -> dict:
        original = self.repository.get_trace(diagnosis["trace_id"])
        if original is None:
            raise KeyError(f"Unknown original trace: {diagnosis['trace_id']}")
        if original["knowledge_base"]["data_source_id"] != "cuad-v1":
            raise ValueError("The Stage 6 provider only replays the real CUAD v1 laboratory")

        category = diagnosis["primary_hypothesis"]["category"]
        field_name, component = CHANGE_MAP[category]
        baseline_config = self._configuration_from_trace(original, "replay-baseline")
        from_value = getattr(baseline_config, field_name)
        candidate_config = replace(
            baseline_config,
            name="replay-candidate",
            **{field_name: "current" if field_name == "corpus_mode" else "healthy"},
        )
        to_value = getattr(candidate_config, field_name)
        if from_value == to_value:
            raise ValueError(f"Diagnosis proposes no effective change for {component}")
        differences = {
            item.name
            for item in fields(Scenario)
            if item.name in BEHAVIOR_FIELDS
            and getattr(baseline_config, item.name) != getattr(candidate_config, item.name)
        }
        if differences != {field_name}:
            raise RuntimeError(f"Replay must change exactly one variable, changed: {differences}")

        replay_id = hashlib.sha256(
            f"{diagnosis['diagnosis_id']}\0{component}\0{from_value}\0{to_value}\0{self.version}".encode(
                "utf-8"
            )
        ).hexdigest()
        baseline = capture_configuration(baseline_config, capture_mode="full")
        candidate = capture_configuration(candidate_config, capture_mode="full")
        for role, captured in (("baseline", baseline), ("candidate", candidate)):
            captured.canonical_trace["extensions"]["replay"] = {
                "replay_id": replay_id,
                "original_trace_id": original["trace_id"],
                "role": role,
                "changed_component": component,
            }

        baseline_stored = self.repository.ingest(baseline.canonical_trace)
        candidate_stored = self.repository.ingest(candidate.canonical_trace)
        baseline_verification = self.verifier.evaluate_response(
            baseline_stored["response_id"]
        )
        candidate_verification = self.verifier.evaluate_response(
            candidate_stored["response_id"]
        )
        original_response_id = original["generation"]["response_id"]
        original_verification = self.verifier.evaluate_response(original_response_id)
        original_lineage = self.repository.answer_lineage(original_response_id)
        baseline_lineage = self.repository.answer_lineage(baseline_stored["response_id"])
        candidate_lineage = self.repository.answer_lineage(candidate_stored["response_id"])

        original_verdicts = self._verdicts(original_verification)
        baseline_verdicts = self._verdicts(baseline_verification)
        candidate_verdicts = self._verdicts(candidate_verification)
        original_citation = self.diagnoser.citation_state(
            original_lineage, original_verification
        )
        baseline_citation = self.diagnoser.citation_state(
            baseline_lineage, baseline_verification
        )
        candidate_citation = self.diagnoser.citation_state(
            candidate_lineage, candidate_verification
        )
        reproduced = (
            baseline.canonical_trace["pipeline"]["fingerprint"]
            == original["pipeline"]["fingerprint"]
            and baseline.canonical_trace["generation"]["response"]["content_hash"]
            == original["generation"]["response"]["content_hash"]
            and baseline_verdicts == original_verdicts
            and baseline_citation == original_citation
        )
        candidate_supported = all(
            verdict == "SUPPORTED" for verdict in candidate_verdicts
        )
        if category == "CITATION_FAILURE":
            fixed = baseline_citation is False and candidate_citation is True
        else:
            fixed = candidate_supported and not all(
                verdict == "SUPPORTED" for verdict in baseline_verdicts
            )

        if not reproduced:
            outcome = "NEEDS_REVIEW"
            observations = [
                "The unchanged baseline did not reproduce the original trace outcome."
            ]
        elif fixed:
            outcome = "CONFIRMED"
            observations = [
                "The unchanged baseline reproduced the original outcome.",
                "Changing only the diagnosed component removed the observed failure.",
            ]
        else:
            outcome = "REJECTED"
            observations = [
                "The unchanged baseline reproduced the original outcome.",
                "Changing only the diagnosed component did not remove the observed failure.",
            ]

        result = {
            "schema_version": "0.1.0",
            "replay_id": replay_id,
            "diagnosis_id": diagnosis["diagnosis_id"],
            "original_trace_id": original["trace_id"],
            "changed_variable": {
                "component": component,
                "from_value": from_value,
                "to_value": to_value,
            },
            "baseline": self._summary(
                baseline.canonical_trace,
                baseline_verdicts,
                baseline_citation,
                reproduced_original=reproduced,
            ),
            "candidate": self._summary(
                candidate.canonical_trace,
                candidate_verdicts,
                candidate_citation,
            ),
            "outcome": outcome,
            "observations": observations,
        }
        errors = sorted(
            self.validator.iter_errors(result), key=lambda error: list(error.path)
        )
        if errors:
            messages = "; ".join(error.message for error in errors)
            raise ValueError(f"Replay result contract violation: {messages}")
        return self.repository.save_replay(result)

    @staticmethod
    def _configuration_from_trace(trace: dict, name: str) -> Scenario:
        pipeline = trace["pipeline"]
        corpus_mode = trace["knowledge_base"]["corpus_version"].rsplit(":", 1)[-1]
        return Scenario(
            name=name,
            injected_fault=None,
            parser_mode=pipeline["parser"]["version"],
            chunker_mode=pipeline["chunker"]["version"],
            retriever_mode=pipeline["retriever"]["version"],
            reranker_mode=pipeline["reranker"]["version"],
            generator_mode=pipeline["generator"]["version"],
            citation_mode=pipeline["citation"]["version"],
            corpus_mode=corpus_mode,
        )

    @staticmethod
    def _verdicts(verification: dict) -> list[str]:
        return [claim["verdict"] for claim in verification["claims"]]

    @staticmethod
    def _summary(
        trace: dict,
        verdicts: list[str],
        citation_supported: bool | None,
        reproduced_original: bool | None = None,
    ) -> dict:
        summary = {
            "trace_id": trace["trace_id"],
            "pipeline_fingerprint": trace["pipeline"]["fingerprint"],
            "response_hash": trace["generation"]["response"]["content_hash"],
            "verdicts": verdicts,
            "all_claims_supported": all(verdict == "SUPPORTED" for verdict in verdicts),
            "citation_supported": citation_supported,
        }
        if reproduced_original is not None:
            summary["reproduced_original"] = reproduced_original
        return summary

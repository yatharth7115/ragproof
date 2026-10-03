import json
import os
from pathlib import Path
import unittest
import uuid

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ragproof_diagnosis import DiagnosisWorker, RootCauseDiagnoser
from ragproof_lab.components import HashedEmbedder
from ragproof_otel import capture_scenario
from ragproof_store import StorageSettings, TraceRepository
from ragproof_store.api import create_app
from ragproof_verifier import ClaimVerifier


ROOT = Path(__file__).resolve().parents[1]
RUN_INTEGRATION = os.getenv("RAGPROOF_RUN_STORAGE_INTEGRATION") == "1"
FAULT_SCENARIOS = {
    "parsing_failure": "PARSING_FAILURE",
    "chunking_failure": "CHUNKING_FAILURE",
    "retrieval_failure": "RETRIEVAL_FAILURE",
    "reranking_failure": "RERANKING_FAILURE",
    "generation_failure": "GENERATION_FAILURE",
    "citation_failure": "CITATION_FAILURE",
    "stale_knowledge": "STALE_KNOWLEDGE",
}


@unittest.skipUnless(RUN_INTEGRATION, "set RAGPROOF_RUN_STORAGE_INTEGRATION=1")
class DiagnosisIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = TraceRepository(
            StorageSettings.from_env(), embedding_function=HashedEmbedder().embed
        )
        cls.repository.initialize()
        cls.verifier = ClaimVerifier(cls.repository)
        cls.diagnoser = RootCauseDiagnoser(cls.repository, cls.verifier)
        schema = json.loads((ROOT / "schemas" / "diagnosis.schema.json").read_text())
        cls.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def diagnose(self, scenario: str) -> tuple[dict, dict]:
        trace = capture_scenario(scenario, capture_mode="full").canonical_trace
        stored = self.repository.ingest(trace)
        diagnosis = self.diagnoser.diagnose_response(stored["response_id"])
        errors = list(self.validator.iter_errors(diagnosis))
        self.assertFalse(errors, "\n".join(error.message for error in errors))
        return trace, diagnosis

    def test_every_labelled_real_cuad_fault_is_in_top_two_hypotheses(self) -> None:
        hits = 0
        for scenario, expected in FAULT_SCENARIOS.items():
            with self.subTest(scenario=scenario):
                _, diagnosis = self.diagnose(scenario)
                ranked = [
                    diagnosis["primary_hypothesis"]["category"],
                    *[
                        hypothesis["category"]
                        for hypothesis in diagnosis.get("alternative_hypotheses", [])
                    ],
                ]
                hits += expected in ranked[:2]
                self.assertEqual(diagnosis["status"], "suspected")
        self.assertGreaterEqual(hits / len(FAULT_SCENARIOS), 0.8)
        self.assertEqual(hits, len(FAULT_SCENARIOS))

    def test_healthy_trace_is_not_given_a_false_component_failure(self) -> None:
        _, diagnosis = self.diagnose("healthy")

        self.assertEqual(
            diagnosis["primary_hypothesis"]["category"], "UNDETERMINED"
        )
        self.assertEqual(diagnosis["status"], "needs_review")

    def test_generation_failure_does_not_require_lab_ground_truth_probes(self) -> None:
        trace = capture_scenario(
            "generation_failure", capture_mode="full"
        ).canonical_trace
        del trace["extensions"]["evaluation_ground_truth"]
        stored = self.repository.ingest(trace)

        diagnosis = self.diagnoser.diagnose_response(stored["response_id"])

        self.assertEqual(
            diagnosis["primary_hypothesis"]["category"], "GENERATION_FAILURE"
        )

    def test_classifier_does_not_read_the_lab_scenario_label(self) -> None:
        trace = capture_scenario("retrieval_failure", capture_mode="full").canonical_trace
        root = next(span for span in trace["spans"] if span["name"] == "rag.request")
        root["attributes"]["ragproof.scenario.name"] = "healthy"
        stored = self.repository.ingest(trace)

        diagnosis = self.diagnoser.diagnose_response(stored["response_id"])

        self.assertEqual(
            diagnosis["primary_hypothesis"]["category"], "RETRIEVAL_FAILURE"
        )

    def test_metadata_only_trace_is_blocked_without_a_fabricated_diagnosis(self) -> None:
        trace = capture_scenario("generation_failure", capture_mode="metadata_only").canonical_trace
        stored = self.repository.ingest(trace)

        result = self.diagnoser.diagnose_response(stored["response_id"])

        self.assertEqual(result["status"], "blocked_verification_unavailable")
        self.assertNotIn("primary_hypothesis", result)

    def test_diagnosis_worker_consumes_completed_verification_event(self) -> None:
        worker = DiagnosisWorker(
            self.diagnoser,
            consumer_name=f"test-{uuid.uuid4().hex}",
            group_name=f"ragproof-diagnosers-test-{uuid.uuid4().hex}",
        )
        worker.ensure_group(start_id="$")
        trace = capture_scenario("generation_failure", capture_mode="full").canonical_trace
        stored = self.repository.ingest(trace)
        self.verifier.evaluate_response(stored["response_id"])

        processed = worker.process_next(block_ms=2_000)
        persisted = self.repository.get_diagnosis(
            stored["response_id"], self.diagnoser.name, self.diagnoser.version
        )

        self.assertIsNotNone(processed)
        self.assertEqual(processed, persisted)
        self.assertEqual(
            persisted["primary_hypothesis"]["category"], "GENERATION_FAILURE"
        )

    def test_http_api_creates_and_reads_persisted_diagnosis(self) -> None:
        trace = capture_scenario("citation_failure", capture_mode="full").canonical_trace
        stored = self.repository.ingest(trace)
        client = TestClient(
            create_app(self.repository, self.verifier, self.diagnoser)
        )

        created = client.post(f"/v1/diagnoses/{stored['response_id']}")
        fetched = client.get(f"/v1/diagnoses/{stored['response_id']}")

        self.assertEqual(created.status_code, 200)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(created.json(), fetched.json())
        self.assertEqual(
            fetched.json()["primary_hypothesis"]["category"], "CITATION_FAILURE"
        )

    def test_diagnosis_span_contains_no_contract_or_answer_text(self) -> None:
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        diagnoser = RootCauseDiagnoser(
            self.repository,
            self.verifier,
            tracer=provider.get_tracer("ragproof-diagnosis-test"),
        )
        captured = capture_scenario("generation_failure", capture_mode="full")
        stored = self.repository.ingest(captured.canonical_trace)

        diagnoser.diagnose_response(stored["response_id"])
        provider.shutdown()

        span = next(item for item in exporter.get_finished_spans() if item.name == "ragproof.diagnose")
        attributes = json.dumps(dict(span.attributes))
        self.assertEqual(span.attributes["ragproof.diagnosis.category"], "GENERATION_FAILURE")
        self.assertNotIn(captured.artifacts.answer.text, attributes)
        self.assertNotIn(captured.artifacts.expected_evidence, attributes)


if __name__ == "__main__":
    unittest.main()

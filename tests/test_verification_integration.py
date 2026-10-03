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

from ragproof_lab.components import HashedEmbedder
from ragproof_otel import capture_scenario
from ragproof_store import StorageSettings, TraceRepository
from ragproof_store.api import create_app
from ragproof_verifier import ClaimVerifier, VerificationWorker


ROOT = Path(__file__).resolve().parents[1]
RUN_INTEGRATION = os.getenv("RAGPROOF_RUN_STORAGE_INTEGRATION") == "1"


@unittest.skipUnless(RUN_INTEGRATION, "set RAGPROOF_RUN_STORAGE_INTEGRATION=1")
class VerificationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = TraceRepository(
            StorageSettings.from_env(), embedding_function=HashedEmbedder().embed
        )
        cls.repository.initialize()
        cls.verifier = ClaimVerifier(cls.repository)
        schema = json.loads(
            (ROOT / "schemas" / "claim-verification.schema.json").read_text()
        )
        cls.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def ingest_and_evaluate(self, scenario: str) -> tuple[dict, dict]:
        trace = capture_scenario(scenario, capture_mode="full").canonical_trace
        stored = self.repository.ingest(trace)
        verification = self.verifier.evaluate_response(stored["response_id"])
        errors = list(self.validator.iter_errors(verification))
        self.assertFalse(errors, "\n".join(error.message for error in errors))
        return trace, verification

    def test_human_annotated_cuad_answer_is_supported(self) -> None:
        trace, verification = self.ingest_and_evaluate("healthy")

        self.assertEqual(len(verification["claims"]), 1)
        claim = verification["claims"][0]
        self.assertEqual(claim["verdict"], "SUPPORTED")
        self.assertEqual(claim["confidence"], 1.0)
        self.assertEqual(claim["evidence"][0]["relation"], "supports")
        self.assertIn(
            claim["evidence"][0]["chunk_id"],
            {
                item["chunk_id"]
                for item in trace["retrieval"]["candidates"]
                if item["included_in_context"]
            },
        )
        impact = self.repository.document_impact(
            trace["tenant_id"],
            trace["project_id"],
            trace["extensions"]["cuad"]["primary_document_id"],
            trace["extensions"]["cuad"]["current_document_version"],
        )
        self.assertIn(
            claim["claim_id"],
            {item["claim_id"] for item in impact["affected_claims"]},
        )

    def test_labelled_numeric_fault_is_contradicted_by_real_cuad_evidence(self) -> None:
        _, verification = self.ingest_and_evaluate("generation_failure")

        claim = verification["claims"][0]
        self.assertEqual(claim["verdict"], "CONTRADICTED")
        self.assertEqual(claim["evidence"][0]["relation"], "contradicts")
        self.assertGreaterEqual(claim["confidence"], 0.9)

    def test_supported_claim_against_superseded_real_version_is_stale(self) -> None:
        _, verification = self.ingest_and_evaluate("stale_knowledge")

        claim = verification["claims"][0]
        self.assertEqual(claim["verdict"], "STALE_EVIDENCE")
        self.assertEqual(claim["evidence"][0]["freshness"], "stale")

    def test_metadata_only_trace_is_blocked_instead_of_inventing_claim_text(self) -> None:
        trace = capture_scenario("healthy", capture_mode="metadata_only").canonical_trace
        stored = self.repository.ingest(trace)

        result = self.verifier.evaluate_response(stored["response_id"])

        self.assertEqual(result["status"], "blocked_content_unavailable")
        self.assertNotIn("claims", result)

    def test_redis_worker_consumes_ingestion_event_and_persists_verdict(self) -> None:
        worker = VerificationWorker(
            self.verifier,
            consumer_name=f"test-{uuid.uuid4().hex}",
            group_name=f"ragproof-verifiers-test-{uuid.uuid4().hex}",
        )
        worker.ensure_group(start_id="$")
        trace = capture_scenario("generation_failure", capture_mode="full").canonical_trace
        stored = self.repository.ingest(trace)

        processed = worker.process_next(block_ms=2_000)
        persisted = self.repository.get_verification(
            stored["response_id"], self.verifier.name, self.verifier.version
        )

        self.assertIsNotNone(processed)
        self.assertEqual(processed, persisted)
        self.assertEqual(persisted["claims"][0]["verdict"], "CONTRADICTED")

    def test_http_api_evaluates_and_reads_persisted_verification(self) -> None:
        trace = capture_scenario("healthy", capture_mode="full").canonical_trace
        stored = self.repository.ingest(trace)
        client = TestClient(create_app(self.repository, self.verifier))

        evaluated = client.post(f"/v1/verifications/{stored['response_id']}")
        fetched = client.get(f"/v1/verifications/{stored['response_id']}")

        self.assertEqual(evaluated.status_code, 200)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(evaluated.json(), fetched.json())
        self.assertEqual(fetched.json()["claims"][0]["verdict"], "SUPPORTED")

    def test_evaluation_emits_content_free_opentelemetry_span(self) -> None:
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        verifier = ClaimVerifier(
            self.repository,
            tracer=provider.get_tracer("ragproof-verifier-test"),
        )
        trace = capture_scenario("healthy", capture_mode="full").canonical_trace
        stored = self.repository.ingest(trace)

        verifier.evaluate_response(stored["response_id"])
        provider.shutdown()

        span = next(item for item in exporter.get_finished_spans() if item.name == "ragproof.evaluate")
        self.assertEqual(span.attributes["ragproof.claim.verdicts"], ("SUPPORTED",))
        attributes = json.dumps(dict(span.attributes))
        self.assertNotIn(
            capture_scenario("healthy", capture_mode="full").artifacts.expected_answer,
            attributes,
        )


if __name__ == "__main__":
    unittest.main()

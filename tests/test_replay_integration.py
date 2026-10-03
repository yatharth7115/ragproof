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
import psycopg

from ragproof_diagnosis import RootCauseDiagnoser
from ragproof_lab.components import HashedEmbedder
from ragproof_lab.scenarios import Scenario
from ragproof_otel import capture_configuration, capture_scenario
from ragproof_replay import DifferentialReplayEngine, ReplayWorker
from ragproof_store import StorageSettings, TraceRepository
from ragproof_store.api import create_app
from ragproof_verifier import ClaimVerifier


ROOT = Path(__file__).resolve().parents[1]
RUN_INTEGRATION = os.getenv("RAGPROOF_RUN_STORAGE_INTEGRATION") == "1"
FAULT_SCENARIOS = {
    "parsing_failure": "parser",
    "chunking_failure": "chunker",
    "retrieval_failure": "retriever",
    "reranking_failure": "reranker",
    "generation_failure": "generator",
    "citation_failure": "citation",
    "stale_knowledge": "knowledge_base",
}


@unittest.skipUnless(RUN_INTEGRATION, "set RAGPROOF_RUN_STORAGE_INTEGRATION=1")
class ReplayIntegrationTests(unittest.TestCase):
    results: dict[str, tuple[dict, dict, dict]] = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.settings = StorageSettings.from_env()
        cls.repository = TraceRepository(
            cls.settings, embedding_function=HashedEmbedder().embed
        )
        cls.repository.initialize()
        cls.verifier = ClaimVerifier(cls.repository)
        cls.diagnoser = RootCauseDiagnoser(cls.repository, cls.verifier)
        cls.engine = DifferentialReplayEngine(
            cls.repository, cls.verifier, cls.diagnoser
        )
        schema = json.loads(
            (ROOT / "schemas" / "replay-result.schema.json").read_text()
        )
        cls.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def execute(self, scenario: str) -> tuple[dict, dict, dict]:
        if scenario in self.results:
            return self.results[scenario]
        trace = capture_scenario(scenario, capture_mode="full").canonical_trace
        stored = self.repository.ingest(trace)
        diagnosis = self.diagnoser.diagnose_response(stored["response_id"])
        replay = self.engine.replay_diagnosis(diagnosis["diagnosis_id"])
        errors = list(self.validator.iter_errors(replay))
        self.assertFalse(errors, "\n".join(error.message for error in errors))
        self.results[scenario] = (trace, diagnosis, replay)
        return trace, diagnosis, replay

    def test_all_seven_real_cuad_faults_are_confirmed_by_one_variable_replay(self) -> None:
        for scenario, component in FAULT_SCENARIOS.items():
            with self.subTest(scenario=scenario):
                _, _, replay = self.execute(scenario)
                self.assertEqual(replay["outcome"], "CONFIRMED")
                self.assertEqual(replay["changed_variable"]["component"], component)
                self.assertTrue(replay["baseline"]["reproduced_original"])
                self.assertNotEqual(
                    replay["baseline"]["pipeline_fingerprint"],
                    replay["candidate"]["pipeline_fingerprint"],
                )
                if scenario == "citation_failure":
                    self.assertFalse(replay["baseline"]["citation_supported"])
                    self.assertTrue(replay["candidate"]["citation_supported"])
                else:
                    self.assertFalse(replay["baseline"]["all_claims_supported"])
                    self.assertTrue(replay["candidate"]["all_claims_supported"])

    def test_confirmed_replay_updates_diagnosis_lifecycle_with_audit_history(self) -> None:
        _, diagnosis, replay = self.execute("generation_failure")

        current = self.repository.get_diagnosis_by_id(diagnosis["diagnosis_id"])
        with psycopg.connect(self.settings.postgres_dsn) as connection:
            statuses = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT status FROM ragproof_diagnosis_status_history
                    WHERE diagnosis_id = %s ORDER BY sequence
                    """,
                    (diagnosis["diagnosis_id"],),
                ).fetchall()
            ]

        self.assertEqual(current["status"], "confirmed")
        self.assertEqual(statuses, ["suspected", "replaying", "confirmed"])
        self.assertEqual(
            self.repository.get_replay(replay["replay_id"]), replay
        )

    def test_replay_is_idempotent_for_one_diagnosis(self) -> None:
        _, diagnosis, first = self.execute("retrieval_failure")

        second = self.engine.replay_diagnosis(diagnosis["diagnosis_id"])

        self.assertEqual(first, second)

    def test_undetermined_healthy_diagnosis_is_not_automatically_replayed(self) -> None:
        trace = capture_scenario("healthy", capture_mode="full").canonical_trace
        stored = self.repository.ingest(trace)
        diagnosis = self.diagnoser.diagnose_response(stored["response_id"])

        with self.assertRaisesRegex(ValueError, "no safe one-variable"):
            self.engine.replay_diagnosis(diagnosis["diagnosis_id"])

    def test_incomplete_one_variable_repair_is_rejected_on_real_cuad_trace(self) -> None:
        configuration = Scenario(
            name="controlled-multi-fault",
            injected_fault="CONTROLLED_MULTI_FAULT",
            retriever_mode="exclude_expected_evidence",
            generator_mode="corrupt_annotated_value",
        )
        trace = capture_configuration(configuration, capture_mode="full").canonical_trace
        stored = self.repository.ingest(trace)
        diagnosis = self.diagnoser.diagnose_response(stored["response_id"])

        replay = self.engine.replay_diagnosis(diagnosis["diagnosis_id"])

        self.assertEqual(
            diagnosis["primary_hypothesis"]["category"], "RETRIEVAL_FAILURE"
        )
        self.assertEqual(replay["changed_variable"]["component"], "retriever")
        self.assertEqual(replay["outcome"], "REJECTED")
        self.assertFalse(replay["candidate"]["all_claims_supported"])
        self.assertEqual(
            self.repository.get_diagnosis_by_id(diagnosis["diagnosis_id"])["status"],
            "rejected",
        )

    def test_worker_consumes_diagnosis_event_and_executes_real_replay(self) -> None:
        worker = ReplayWorker(
            self.engine,
            consumer_name=f"test-{uuid.uuid4().hex}",
            group_name=f"ragproof-replays-test-{uuid.uuid4().hex}",
        )
        worker.ensure_group(start_id="$")
        trace = capture_scenario("chunking_failure", capture_mode="full").canonical_trace
        stored = self.repository.ingest(trace)
        diagnosis = self.diagnoser.diagnose_response(stored["response_id"])

        replay = worker.process_next(block_ms=2_000)

        self.assertIsNotNone(replay)
        self.assertEqual(replay["diagnosis_id"], diagnosis["diagnosis_id"])
        self.assertEqual(replay["outcome"], "CONFIRMED")

    def test_http_api_executes_and_reads_replay(self) -> None:
        _, diagnosis, expected = self.execute("citation_failure")
        client = TestClient(
            create_app(
                self.repository, self.verifier, self.diagnoser, self.engine
            )
        )

        created = client.post(
            f"/v1/replays/diagnoses/{diagnosis['diagnosis_id']}"
        )
        fetched = client.get(f"/v1/replays/{expected['replay_id']}")

        self.assertEqual(created.status_code, 200)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(created.json(), expected)
        self.assertEqual(fetched.json(), expected)

    def test_replay_span_contains_no_cuad_content(self) -> None:
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        engine = DifferentialReplayEngine(
            self.repository,
            self.verifier,
            self.diagnoser,
            tracer=provider.get_tracer("ragproof-replay-test"),
        )
        captured = capture_scenario("reranking_failure", capture_mode="full")
        stored = self.repository.ingest(captured.canonical_trace)
        diagnosis = self.diagnoser.diagnose_response(stored["response_id"])

        engine.replay_diagnosis(diagnosis["diagnosis_id"])
        provider.shutdown()

        span = next(item for item in exporter.get_finished_spans() if item.name == "ragproof.replay")
        attributes = json.dumps(dict(span.attributes))
        self.assertEqual(span.attributes["ragproof.replay.outcome"], "CONFIRMED")
        self.assertNotIn(captured.artifacts.answer.text, attributes)
        self.assertNotIn(captured.artifacts.expected_evidence, attributes)


if __name__ == "__main__":
    unittest.main()

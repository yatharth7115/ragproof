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

from ragproof_diagnosis import RootCauseDiagnoser
from ragproof_impact import ChangeImpactAnalyzer, RegressionCaseBuilder
from ragproof_impact.worker import RegressionWorker
from ragproof_lab.components import HashedEmbedder
from ragproof_lab.scenarios import Scenario
from ragproof_otel import capture_configuration, capture_scenario
from ragproof_replay import DifferentialReplayEngine
from ragproof_store import StorageSettings, TraceRepository
from ragproof_store.api import create_app
from ragproof_verifier import ClaimVerifier


ROOT = Path(__file__).resolve().parents[1]
RUN_INTEGRATION = os.getenv("RAGPROOF_RUN_STORAGE_INTEGRATION") == "1"


@unittest.skipUnless(RUN_INTEGRATION, "set RAGPROOF_RUN_STORAGE_INTEGRATION=1")
class ImpactIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = TraceRepository(
            StorageSettings.from_env(), embedding_function=HashedEmbedder().embed
        )
        cls.repository.initialize()
        cls.verifier = ClaimVerifier(cls.repository)
        cls.diagnoser = RootCauseDiagnoser(cls.repository, cls.verifier)
        cls.replay_engine = DifferentialReplayEngine(
            cls.repository, cls.verifier, cls.diagnoser
        )
        cls.analyzer = ChangeImpactAnalyzer(cls.repository)
        cls.builder = RegressionCaseBuilder(cls.repository)
        cls.impact_validator = cls._validator("change-impact.schema.json")
        cls.regression_validator = cls._validator("regression-case.schema.json")

        cls.original = capture_scenario(
            "stale_knowledge", capture_mode="full"
        ).canonical_trace
        stored = cls.repository.ingest(cls.original)
        cls.diagnosis = cls.diagnoser.diagnose_response(stored["response_id"])
        cls.replay = cls.replay_engine.replay_diagnosis(
            cls.diagnosis["diagnosis_id"]
        )
        cls.document_id = cls.original["extensions"]["cuad"]["primary_document_id"]
        cls.from_version = "cuad-v1:stale-index-snapshot"
        cls.to_version = cls.original["extensions"]["cuad"]["current_document_version"]

    @staticmethod
    def _validator(name: str) -> Draft202012Validator:
        schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        return Draft202012Validator(schema, format_checker=FormatChecker())

    def impact(self) -> dict:
        return self.analyzer.analyze(
            self.original["tenant_id"],
            self.original["project_id"],
            self.document_id,
            self.from_version,
            self.to_version,
        )

    def test_real_document_change_impact_distinguishes_direct_usage(self) -> None:
        report = self.impact()

        self.assertFalse(list(self.impact_validator.iter_errors(report)))
        self.assertTrue(report["change"]["version_changed"])
        self.assertEqual(report["change"]["content_change"], "unknown")
        self.assertTrue(report["change"]["from_chunk_hashes"])
        self.assertTrue(report["change"]["to_chunk_hashes"])
        affected = next(
            item
            for item in report["affected_traces"]
            if item["trace_id"] == self.original["trace_id"]
        )
        self.assertIn("context", affected["usage_modes"])
        self.assertIn("citation", affected["usage_modes"])
        self.assertTrue(affected["claim_ids"])
        self.assertTrue(
            any(
                item["diagnosis_id"] == self.diagnosis["diagnosis_id"]
                and item["status"] == "confirmed"
                for item in affected["diagnoses"]
            )
        )
        self.assertGreaterEqual(report["summary"]["confirmed_incident_count"], 1)
        self.assertEqual(self.repository.get_impact(report["impact_id"]), report)

    def test_confirmed_replay_becomes_real_cuad_regression_case(self) -> None:
        case = self.builder.create_from_replay(self.replay["replay_id"])

        self.assertFalse(list(self.regression_validator.iter_errors(case)))
        self.assertEqual(case["source"], {"dataset": "CUAD v1", "license": "CC BY 4.0"})
        self.assertEqual(case["failure"]["verdicts"], ["STALE_EVIDENCE"])
        self.assertEqual(case["passing_reference"]["verdicts"], ["SUPPORTED"])
        self.assertNotEqual(
            case["failure"]["pipeline_fingerprint"],
            case["passing_reference"]["pipeline_fingerprint"],
        )
        query = self.repository.objects.get_text(case["input"]["query_ref"])
        expected_query = self.repository.objects.get_text(
            self.repository.answer_lineage(
                self.original["generation"]["response_id"]
            )["query_ref"]
        )
        self.assertEqual(query, expected_query)
        self.assertEqual(self.repository.get_regression(case["case_id"]), case)

    def test_non_confirmed_replay_is_not_promoted(self) -> None:
        configuration = Scenario(
            name="stage7-controlled-multi-fault",
            injected_fault="CONTROLLED_MULTI_FAULT",
            retriever_mode="exclude_expected_evidence",
            generator_mode="corrupt_annotated_value",
        )
        trace = capture_configuration(configuration, capture_mode="full").canonical_trace
        stored = self.repository.ingest(trace)
        diagnosis = self.diagnoser.diagnose_response(stored["response_id"])
        replay = self.replay_engine.replay_diagnosis(diagnosis["diagnosis_id"])

        self.assertEqual(replay["outcome"], "REJECTED")
        with self.assertRaisesRegex(ValueError, "Only CONFIRMED"):
            self.builder.create_from_replay(replay["replay_id"])

    def test_worker_consumes_only_confirmed_replay_event(self) -> None:
        worker = RegressionWorker(
            self.builder,
            consumer_name=f"test-{uuid.uuid4().hex}",
            group_name=f"ragproof-regressions-test-{uuid.uuid4().hex}",
        )
        worker.ensure_group(start_id="$")
        self.repository.events.publish(
            {
                "event": "replay.completed",
                "replay_id": self.replay["replay_id"],
                "diagnosis_id": self.replay["diagnosis_id"],
                "original_trace_id": self.replay["original_trace_id"],
                "outcome": "CONFIRMED",
            }
        )

        case = worker.process_next(block_ms=2_000)

        self.assertIsNotNone(case)
        self.assertEqual(case["incident"]["replay_id"], self.replay["replay_id"])

    def test_http_api_creates_and_reads_stage7_artifacts(self) -> None:
        client = TestClient(
            create_app(
                self.repository,
                self.verifier,
                self.diagnoser,
                self.replay_engine,
                self.analyzer,
                self.builder,
            )
        )
        created_impact = client.post(
            f"/v1/impact/documents/{self.document_id}/analyze",
            params={
                "tenant_id": self.original["tenant_id"],
                "project_id": self.original["project_id"],
                "from_version": self.from_version,
                "to_version": self.to_version,
            },
        )
        impact = created_impact.json()
        fetched_impact = client.get(f"/v1/impact/reports/{impact['impact_id']}")
        created_case = client.post(
            f"/v1/regressions/replays/{self.replay['replay_id']}"
        )
        case = created_case.json()
        fetched_case = client.get(f"/v1/regressions/{case['case_id']}")

        self.assertEqual(created_impact.status_code, 200)
        self.assertEqual(fetched_impact.json(), impact)
        self.assertEqual(created_case.status_code, 200)
        self.assertEqual(fetched_case.json(), case)

    def test_stage7_spans_contain_identifiers_not_cuad_content(self) -> None:
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        analyzer = ChangeImpactAnalyzer(
            self.repository, tracer=provider.get_tracer("ragproof-impact-test")
        )
        builder = RegressionCaseBuilder(
            self.repository, tracer=provider.get_tracer("ragproof-regression-test")
        )

        analyzer.analyze(
            self.original["tenant_id"], self.original["project_id"], self.document_id,
            self.from_version, self.to_version,
        )
        builder.create_from_replay(self.replay["replay_id"])
        provider.shutdown()

        attributes = json.dumps(
            [dict(span.attributes) for span in exporter.get_finished_spans()]
        )
        self.assertNotIn(self.original["generation"]["response"].get("content", ""), attributes)
        self.assertNotIn("What is the", attributes)


if __name__ == "__main__":
    unittest.main()

import json
import os
from pathlib import Path
import unittest

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ragproof_diagnosis import RootCauseDiagnoser
from ragproof_gate import GatePolicy, QualityGateRunner
from ragproof_gate.cli import run_cli
from ragproof_impact import ChangeImpactAnalyzer, RegressionCaseBuilder
from ragproof_lab.components import HashedEmbedder
from ragproof_lab.scenarios import Scenario
from ragproof_otel import capture_scenario
from ragproof_replay import DifferentialReplayEngine
from ragproof_store import StorageSettings, TraceRepository
from ragproof_store.api import create_app
from ragproof_verifier import ClaimVerifier


ROOT = Path(__file__).resolve().parents[1]
RUN_INTEGRATION = os.getenv("RAGPROOF_RUN_STORAGE_INTEGRATION") == "1"


@unittest.skipUnless(RUN_INTEGRATION, "set RAGPROOF_RUN_STORAGE_INTEGRATION=1")
class QualityGateIntegrationTests(unittest.TestCase):
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
        broken = capture_scenario("generation_failure", capture_mode="full")
        cls.real_query = broken.artifacts.query
        stored = cls.repository.ingest(broken.canonical_trace)
        diagnosis = cls.diagnoser.diagnose_response(stored["response_id"])
        replay = cls.replay_engine.replay_diagnosis(diagnosis["diagnosis_id"])
        cls.case = cls.builder.create_from_replay(replay["replay_id"])
        cls.runner = QualityGateRunner(cls.repository, cls.verifier, cls.diagnoser)

    def test_healthy_candidate_passes_confirmed_real_cuad_case(self) -> None:
        result = self.runner.run(
            Scenario(name="stage9-healthy", injected_fault=None),
            GatePolicy(required_categories=("GENERATION_FAILURE",)),
            cases=[self.case],
        )

        schema = json.loads(
            (ROOT / "schemas" / "quality-gate-result.schema.json").read_text()
        )
        errors = list(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(result)
        )
        self.assertFalse(errors, "\n".join(error.message for error in errors))
        self.assertEqual(result["outcome"], "PASS")
        self.assertEqual(result["summary"]["pass_rate"], 1.0)
        self.assertEqual(result["case_results"][0]["verdicts"], ["SUPPORTED"])
        self.assertTrue(result["case_results"][0]["citation_supported"])
        self.assertEqual(
            self.repository.get_quality_gate(result["gate_run_id"]), result
        )

    def test_controlled_broken_candidate_fails_same_real_case(self) -> None:
        result = self.runner.run(
            Scenario(
                name="stage9-controlled-broken-generator",
                injected_fault=None,
                generator_mode="corrupt_annotated_value",
            ),
            cases=[self.case],
        )

        self.assertEqual(result["outcome"], "FAIL")
        self.assertEqual(result["summary"]["failed_cases"], 1)
        self.assertEqual(result["case_results"][0]["verdicts"], ["CONTRADICTED"])
        self.assertIn(
            "unacceptable_claim_verdict", result["case_results"][0]["failures"]
        )

    def test_empty_case_set_fails_closed(self) -> None:
        result = self.runner.run(
            Scenario(name="stage9-no-cases", injected_fault=None), cases=[]
        )

        self.assertEqual(result["outcome"], "FAIL")
        self.assertEqual(result["summary"]["total_cases"], 0)
        self.assertEqual(
            result["summary"]["policy_failures"],
            ["minimum_cases_not_met", "minimum_pass_rate_not_met"],
        )

    def test_invalid_candidate_mode_is_rejected_instead_of_silently_running(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported generator_mode"):
            self.runner.run(
                Scenario(
                    name="stage9-typo",
                    injected_fault=None,
                    generator_mode="healhty",
                ),
                cases=[self.case],
            )

    def test_api_runs_and_reads_gate(self) -> None:
        client = TestClient(
            create_app(
                self.repository, self.verifier, self.diagnoser, self.replay_engine,
                self.analyzer, self.builder, self.runner,
            )
        )
        response = client.post(
            "/v1/quality-gates",
            json={
                "candidate": {"name": "stage9-api-healthy"},
                "policy": {"minimum_cases": 1, "minimum_pass_rate": 1.0},
            },
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["outcome"], "PASS")
        fetched = client.get(f"/v1/quality-gates/{result['gate_run_id']}")
        self.assertEqual(fetched.json(), result)

    def test_cli_returns_process_grade_exit_codes(self) -> None:
        self.assertEqual(
            run_cli(["--candidate-name", "stage9-cli-healthy"], self.repository), 0
        )
        self.assertEqual(
            run_cli(
                [
                    "--candidate-name", "stage9-cli-broken",
                    "--generator-mode", "corrupt_annotated_value",
                ],
                self.repository,
            ),
            1,
        )

    def test_gate_telemetry_contains_no_cuad_content(self) -> None:
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        runner = QualityGateRunner(
            self.repository,
            self.verifier,
            self.diagnoser,
            tracer=provider.get_tracer("ragproof-gate-test"),
        )

        runner.run(
            Scenario(name="stage9-observed", injected_fault=None), cases=[self.case]
        )
        provider.shutdown()
        attributes = json.dumps(
            [dict(span.attributes) for span in exporter.get_finished_spans()]
        )

        self.assertNotIn(self.real_query, attributes)
        self.assertNotIn("What is the", attributes)


if __name__ == "__main__":
    unittest.main()

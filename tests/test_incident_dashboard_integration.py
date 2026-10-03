import json
import os
from pathlib import Path
import unittest

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from ragproof_diagnosis import RootCauseDiagnoser
from ragproof_impact import ChangeImpactAnalyzer, RegressionCaseBuilder
from ragproof_lab.components import HashedEmbedder
from ragproof_otel import capture_scenario
from ragproof_replay import DifferentialReplayEngine
from ragproof_store import StorageSettings, TraceRepository
from ragproof_store.api import create_app
from ragproof_verifier import ClaimVerifier


ROOT = Path(__file__).resolve().parents[1]
RUN_INTEGRATION = os.getenv("RAGPROOF_RUN_STORAGE_INTEGRATION") == "1"


@unittest.skipUnless(RUN_INTEGRATION, "set RAGPROOF_RUN_STORAGE_INTEGRATION=1")
class IncidentDashboardIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = TraceRepository(
            StorageSettings.from_env(), embedding_function=HashedEmbedder().embed
        )
        cls.repository.initialize()
        cls.verifier = ClaimVerifier(cls.repository)
        cls.diagnoser = RootCauseDiagnoser(cls.repository, cls.verifier)
        cls.engine = DifferentialReplayEngine(
            cls.repository, cls.verifier, cls.diagnoser
        )
        cls.analyzer = ChangeImpactAnalyzer(cls.repository)
        cls.builder = RegressionCaseBuilder(cls.repository)
        cls.captured = capture_scenario("generation_failure", capture_mode="full")
        cls.trace = cls.captured.canonical_trace
        stored = cls.repository.ingest(cls.trace)
        cls.diagnosis = cls.diagnoser.diagnose_response(stored["response_id"])
        cls.replay = cls.engine.replay_diagnosis(cls.diagnosis["diagnosis_id"])
        cls.regression = cls.builder.create_from_replay(cls.replay["replay_id"])
        cls.client = TestClient(
            create_app(
                cls.repository, cls.verifier, cls.diagnoser, cls.engine,
                cls.analyzer, cls.builder,
            )
        )

    @staticmethod
    def validator(name: str) -> Draft202012Validator:
        schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
        return Draft202012Validator(schema, format_checker=FormatChecker())

    def test_incident_list_is_filterable_and_contract_valid(self) -> None:
        payload = self.repository.list_incidents(
            status="confirmed", category="GENERATION_FAILURE", limit=500
        )

        errors = list(self.validator("incident-list.schema.json").iter_errors(payload))
        self.assertFalse(errors, "\n".join(error.message for error in errors))
        self.assertEqual(
            payload["summary"]["total"],
            sum(payload["summary"]["status_counts"].values()),
        )
        incident = next(
            item
            for item in payload["incidents"]
            if item["diagnosis_id"] == self.diagnosis["diagnosis_id"]
        )
        self.assertEqual(incident["status"], "confirmed")
        self.assertEqual(incident["verdict_counts"], {"CONTRADICTED": 1})
        self.assertEqual(incident["replay"]["outcome"], "CONFIRMED")
        self.assertEqual(
            incident["regression_case_id"], self.regression["case_id"]
        )

    def test_incident_detail_links_evidence_without_exposing_cuad_text(self) -> None:
        detail = self.repository.incident_detail(self.diagnosis["diagnosis_id"])

        errors = list(
            self.validator("incident-detail.schema.json").iter_errors(detail)
        )
        self.assertFalse(errors, "\n".join(error.message for error in errors))
        self.assertEqual(detail["replay"]["outcome"], "CONFIRMED")
        self.assertEqual(
            detail["regression_case"]["case_id"], self.regression["case_id"]
        )
        self.assertEqual(
            [item["status"] for item in detail["timeline"]],
            ["suspected", "replaying", "confirmed"],
        )
        serialized = json.dumps(detail)
        self.assertNotIn(self.captured.artifacts.query, serialized)
        self.assertNotIn(self.captured.artifacts.answer.text, serialized)
        self.assertNotIn(self.captured.artifacts.expected_evidence, serialized)

    def test_dashboard_and_incident_api_are_served_by_the_local_app(self) -> None:
        dashboard = self.client.get("/dashboard")
        stylesheet = self.client.get(
            "/dashboard-assets/incident-dashboard.css"
        )
        javascript = self.client.get(
            "/dashboard-assets/incident-dashboard.js"
        )
        listing = self.client.get(
            "/v1/incidents",
            params={"status": "confirmed", "category": "GENERATION_FAILURE"},
        )
        detail = self.client.get(
            f"/v1/incidents/{self.diagnosis['diagnosis_id']}"
        )

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("Evidence incidents", dashboard.text)
        self.assertIn('id="posture-dial"', dashboard.text)
        self.assertEqual(stylesheet.status_code, 200)
        self.assertEqual(javascript.status_code, 200)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(
            detail.json()["diagnosis"]["diagnosis_id"],
            self.diagnosis["diagnosis_id"],
        )

    def test_incident_api_rejects_invalid_filters_and_unknown_ids(self) -> None:
        invalid = self.client.get("/v1/incidents", params={"status": "maybe"})
        missing = self.client.get("/v1/incidents/not-a-real-diagnosis")

        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()

import unittest

from fastapi.testclient import TestClient

from ragproof_otel import capture_scenario
from ragproof_store.api import create_app


class RecordingRepository:
    def __init__(self) -> None:
        self.ingested = []

    def ingest(self, trace: dict) -> dict:
        self.ingested.append(trace)
        return {
            "trace_id": trace["trace_id"],
            "response_id": trace["generation"]["response_id"],
            "created": True,
        }

    def get_trace(self, trace_id: str):
        return next((trace for trace in self.ingested if trace["trace_id"] == trace_id), None)

    def answer_lineage(self, response_id: str):
        return None

    def document_impact(self, tenant_id, project_id, document_id, document_version):
        return {"affected_answer_count": 0, "affected_answers": []}


class StorageApiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = RecordingRepository()
        self.client = TestClient(create_app(self.repository))

    def test_accepts_a_valid_trace_captured_from_real_cuad(self) -> None:
        trace = capture_scenario("healthy", capture_mode="full").canonical_trace

        response = self.client.post("/v1/traces", json=trace)

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["created"])
        self.assertEqual(self.repository.ingested, [trace])

    def test_rejects_an_invalid_trace_before_storage(self) -> None:
        response = self.client.post("/v1/traces", json={"trace_id": "incomplete"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.repository.ingested, [])

    def test_unknown_trace_returns_not_found(self) -> None:
        response = self.client.get("/v1/traces/not-present")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()

import unittest

from fastapi.testclient import TestClient

from ragproof_lab.api import app


class LaboratoryApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_health(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_lists_scenarios(self) -> None:
        response = self.client.get("/v1/scenarios")

        self.assertEqual(response.status_code, 200)
        self.assertIn("generation_failure", response.json()["scenarios"])

    def test_executes_scenario(self) -> None:
        response = self.client.post("/v1/runs/generation_failure")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["injected_fault"], "GENERATION_FAILURE")

    def test_unknown_scenario_returns_404(self) -> None:
        response = self.client.post("/v1/runs/not-a-scenario")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()

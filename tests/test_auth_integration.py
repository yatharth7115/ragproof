"""Generated credentials against the actual PostgreSQL/S3/Redis laboratory stack."""
import os
import secrets
import unittest
import uuid

from fastapi.testclient import TestClient

from ragproof_lab.components import HashedEmbedder
from ragproof_otel import capture_scenario
from ragproof_store import StorageSettings, TraceRepository
from ragproof_store.api import create_app
from ragproof_store.credentials import create_credential
from ragproof_store.security import AccessControl


@unittest.skipUnless(
    os.getenv("RAGPROOF_RUN_STORAGE_INTEGRATION") == "1",
    "set RAGPROOF_RUN_STORAGE_INTEGRATION=1",
)
class AuthenticatedStorageIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repository = TraceRepository(
            StorageSettings.from_env(), embedding_function=HashedEmbedder().embed,
        )
        cls.repository.initialize()

    def setUp(self):
        self.trace = capture_scenario("healthy", capture_mode="full").canonical_trace
        tenant, project = self.trace["tenant_id"], self.trace["project_id"]
        self.writer, writer_record = create_credential(tenant, project, "writer")
        self.reader, reader_record = create_credential(tenant, project, "reader")
        self.foreign, foreign_record = create_credential(tenant, f"auth-test-{uuid.uuid4().hex}", "admin")
        self.access = AccessControl(
            mode="token", keys=[writer_record, reader_record, foreign_record],
            secret=secrets.token_urlsafe(32), cookie_secure=False,
        )
        self.client = TestClient(create_app(self.repository, access_control=self.access))

    @staticmethod
    def bearer(key):
        return {"Authorization": f"Bearer {key}"}

    def test_generated_key_persists_real_cuad_trace_and_browser_session_reads_it(self):
        with self.client as client:
            response = client.post("/v1/traces", json=self.trace, headers=self.bearer(self.writer))
            self.assertEqual(response.status_code, 201)
            self.assertTrue(response.json()["created"])
            trace_id = response.json()["trace_id"]
            response_id = response.json()["response_id"]
            stored = self.repository.get_trace(trace_id)
            self.assertEqual(stored["project_id"], self.trace["project_id"])
            self.assertTrue(stored["query"]["content_ref"].startswith("s3://"))
            self.assertTrue(
                self.repository.objects.get_text(stored["query"]["content_ref"])
                == self.trace["query"]["content"]
            )
            self.assertEqual(client.get(f"/v1/traces/{trace_id}").status_code, 401)
            login = client.post("/auth/session", json={"api_key": self.writer})
            self.assertEqual(login.status_code, 200)
            fetched = client.get(f"/v1/traces/{trace_id}")
            self.assertEqual(fetched.status_code, 200)
            self.assertEqual(fetched.json()["trace_id"], trace_id)
            lineage = client.get(f"/v1/lineage/answers/{response_id}")
            self.assertEqual(lineage.status_code, 200)
            self.assertEqual(lineage.json()["trace_id"], trace_id)
            self.assertEqual(client.delete("/auth/session").status_code, 200)
            self.assertEqual(client.get(f"/v1/traces/{trace_id}").status_code, 401)

    def test_reader_and_foreign_credentials_cannot_mutate_or_read_another_scope(self):
        with self.client as client:
            response = client.post("/v1/traces", json=self.trace, headers=self.bearer(self.writer))
            self.assertEqual(response.status_code, 201)
            trace_id = response.json()["trace_id"]
            self.assertEqual(client.get(f"/v1/traces/{trace_id}", headers=self.bearer(self.reader)).status_code, 200)
            self.assertEqual(client.get(f"/v1/traces/{trace_id}", headers=self.bearer(self.foreign)).status_code, 404)
            other = capture_scenario("healthy", capture_mode="full").canonical_trace
            self.assertEqual(client.post("/v1/traces", json=other, headers=self.bearer(self.reader)).status_code, 403)
            self.assertEqual(client.post("/v1/traces", json=other, headers=self.bearer(self.foreign)).status_code, 403)
            self.assertIsNone(self.repository.get_trace(other["trace_id"]))


if __name__ == "__main__":
    unittest.main()

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from ragproof_lab import run_scenario
from ragproof_lab.components import HashedEmbedder
from ragproof_otel import RagProofTracer, capture_scenario, configure_otlp_provider
from ragproof_otel import encode_export_json
from ragproof_store import StorageSettings, TraceRepository
from ragproof_store.api import create_app
from ragproof_verifier import ClaimVerifier


RUN_INTEGRATION = os.getenv("RAGPROOF_RUN_STORAGE_INTEGRATION") == "1"


class OtlpCaptureHandler(BaseHTTPRequestHandler):
    payloads: list[tuple[str, bytes]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.__class__.payloads.append(
            (self.headers.get("Content-Type", ""), self.rfile.read(length))
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        return


@unittest.skipUnless(RUN_INTEGRATION, "set RAGPROOF_RUN_STORAGE_INTEGRATION=1")
class ExternalOtlpIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = TraceRepository(
            StorageSettings.from_env(), embedding_function=HashedEmbedder().embed
        )
        cls.repository.initialize()
        cls.verifier = ClaimVerifier(cls.repository)
        cls.client = TestClient(create_app(cls.repository, cls.verifier))

        OtlpCaptureHandler.payloads = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), OtlpCaptureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        provider = configure_otlp_provider(
            f"http://127.0.0.1:{server.server_port}/v1/traces",
            service_name="external-cuad-rag-application",
        )
        try:
            telemetry = RagProofTracer(
                tracer=provider.get_tracer("external-cuad-rag-application"),
                capture_mode="full",
            )
            cls.real_run = run_scenario("healthy", telemetry=telemetry, capture_mode="full")
            if not provider.force_flush(timeout_millis=5_000):
                raise RuntimeError("The external OTLP exporter did not flush")
        finally:
            provider.shutdown()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        cls.content_type, cls.payload = OtlpCaptureHandler.payloads[0]
        cls.export_request = ExportTraceServiceRequest()
        cls.export_request.ParseFromString(cls.payload)
        cls.trace_id = next(
            span.trace_id.hex()
            for resource in cls.export_request.resource_spans
            for scope in resource.scope_spans
            for span in scope.spans
            if span.name == "rag.request"
        )

    def test_real_external_otlp_trace_is_ingested_and_verifiable(self) -> None:
        response = self.client.post(
            "/v1/otlp/traces",
            content=self.payload,
            headers={"content-type": self.content_type},
        )

        self.assertEqual(response.status_code, 200, response.text)
        stored = self.client.get(f"/v1/traces/{self.trace_id}").json()
        self.assertEqual(stored["source"]["adapter_name"], "ragproof-otlp-http")
        self.assertEqual(
            stored["extensions"]["opentelemetry"]["service_name"],
            "external-cuad-rag-application",
        )
        self.assertEqual(stored["extensions"]["cuad"]["dataset"], "CUAD v1")
        self.assertNotIn("content", stored["query"])
        self.assertEqual(
            self.repository.objects.get_text(stored["query"]["content_ref"]),
            self.real_run.query,
        )
        result = self.verifier.evaluate_response(stored["generation"]["response_id"])
        self.assertEqual([claim["verdict"] for claim in result["claims"]], ["SUPPORTED"])
        direct = capture_scenario("healthy", capture_mode="full").canonical_trace
        self.assertEqual(
            stored["pipeline"]["fingerprint"], direct["pipeline"]["fingerprint"]
        )
        self.assertNotIn(self.real_run.query, json.dumps(stored))
        self.assertNotIn(self.real_run.expected_evidence, json.dumps(stored))

    def test_otlp_json_encoding_is_accepted_without_duplicate_storage(self) -> None:
        response = self.client.post(
            "/v1/otlp/traces",
            content=encode_export_json(self.export_request),
            headers={"content-type": "application/json"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertEqual(self.client.get(f"/v1/traces/{self.trace_id}").status_code, 200)

    def test_incomplete_real_trace_is_rejected_not_repaired(self) -> None:
        altered = ExportTraceServiceRequest()
        altered.CopyFrom(self.export_request)
        root = next(
            span
            for resource in altered.resource_spans
            for scope in resource.scope_spans
            for span in scope.spans
            if span.name == "rag.request"
        )
        kept = [
            attribute
            for attribute in root.attributes
            if attribute.key != "ragproof.pipeline.retriever"
        ]
        del root.attributes[:]
        root.attributes.extend(kept)

        response = self.client.post(
            "/v1/otlp/traces",
            content=altered.SerializeToString(),
            headers={"content-type": "application/x-protobuf"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("missing required attribute ragproof.pipeline.retriever", response.text)


if __name__ == "__main__":
    unittest.main()

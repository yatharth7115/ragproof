"""Actual HTTP transport and canonical import tests using real CUAD captures."""

from copy import deepcopy
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ragproof_otel import capture_scenario
from ragproof_integrations import RAGProofClient
from ragproof_integrations.client import validate_endpoint
from ragproof_integrations.mapping import pointer
from ragproof_integrations.vendors import fetch_langfuse_trace, fetch_langsmith_trace


class CaptureHandler(BaseHTTPRequestHandler):
    received = []
    def do_POST(self):
        trace = json.loads(self.rfile.read(int(self.headers["content-length"])))
        self.__class__.received.append((self.path, trace, self.headers.get("authorization")))
        body = json.dumps({"trace_id": trace["trace_id"], "created": True}).encode()
        self.send_response(201)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass


class ExternalClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.captured = capture_scenario("healthy", capture_mode="full")

    def test_real_canonical_capture_travels_over_http(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with RAGProofClient(f"http://127.0.0.1:{server.server_port}", allow_content=True) as client:
                result = client.ingest(self.captured.canonical_trace)
            path, received, _ = CaptureHandler.received[-1]
            self.assertEqual(path, "/v1/traces")
            self.assertEqual(received["query"]["content"], self.captured.artifacts.query)
            self.assertEqual(result["trace_id"], self.captured.canonical_trace["trace_id"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_full_content_requires_explicit_opt_in_before_network(self):
        with RAGProofClient() as client:
            with self.assertRaisesRegex(ValueError, "allow_content"):
                client.ingest(self.captured.canonical_trace)

    def test_content_hash_mismatch_rejected_before_network(self):
        trace = deepcopy(self.captured.canonical_trace)
        trace["query"]["content"] = self.captured.artifacts.expected_evidence
        with RAGProofClient(allow_content=True) as client:
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                client.ingest(trace)

    def test_pointer_maps_real_observed_values_and_rejects_missing_fields(self):
        query = pointer(self.captured.canonical_trace, "/query/content")
        self.assertEqual(query, self.captured.artifacts.query)
        with self.assertRaisesRegex(ValueError, "missing field"):
            pointer(self.captured.canonical_trace, "/retrieval/candidates/99999")

    def test_credentials_cannot_be_sent_to_remote_cleartext_or_url_userinfo(self):
        for endpoint in ("http://example.com", "https://user:password@example.com", "https://example.com?key=hidden"):
            with self.assertRaises(ValueError):
                validate_endpoint(endpoint)


@unittest.skipUnless(os.getenv("RAGPROOF_LIVE_LANGFUSE_TRACE_ID"), "real Langfuse trace and credentials not configured")
class LiveLangfuseReadTests(unittest.TestCase):
    def test_read_real_vendor_trace(self):
        exported = fetch_langfuse_trace(os.environ["RAGPROOF_LIVE_LANGFUSE_TRACE_ID"],
                                       from_time=os.environ["RAGPROOF_LIVE_FROM_TIME"],
                                       to_time=os.environ["RAGPROOF_LIVE_TO_TIME"])
        self.assertTrue(exported["observations"])


@unittest.skipUnless(os.getenv("RAGPROOF_LIVE_LANGSMITH_RUN_ID"), "real LangSmith run and credentials not configured")
class LiveLangsmithReadTests(unittest.TestCase):
    def test_read_real_vendor_trace(self):
        exported = fetch_langsmith_trace(os.environ["RAGPROOF_LIVE_LANGSMITH_RUN_ID"])
        self.assertTrue(exported["observations"])


if __name__ == "__main__":
    unittest.main()

import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from ragproof_lab import SCENARIOS, run_scenario
from ragproof_otel import RagProofTracer, capture_scenario, configure_otlp_provider


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SPANS = {
    "rag.request",
    "document.parse",
    "document.chunk",
    "embedding.create",
    "retrieval.search",
    "retrieval.rerank",
    "prompt.construct",
    "llm.generate",
}


class OtlpCaptureHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        content_length = int(self.headers.get("Content-Length", "0"))
        self.__class__.requests.append(
            {
                "path": self.path,
                "content_type": self.headers.get("Content-Type"),
                "body": self.rfile.read(content_length),
            }
        )
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:
        return


class OpenTelemetryCaptureTests(unittest.TestCase):
    def test_real_cuad_run_emits_complete_span_tree(self) -> None:
        captured = capture_scenario("healthy")
        root = next(span for span in captured.spans if span.name == "rag.request")
        children = [span for span in captured.spans if span.parent is not None]

        self.assertEqual({span.name for span in captured.spans}, EXPECTED_SPANS)
        self.assertEqual(len(captured.spans), 8)
        self.assertTrue(children)
        self.assertTrue(all(span.context.trace_id == root.context.trace_id for span in captured.spans))
        self.assertTrue(all(span.parent.span_id == root.context.span_id for span in children))

    def test_canonicalized_real_trace_validates_against_contract(self) -> None:
        captured = capture_scenario("healthy", capture_mode="full")
        schema = json.loads((ROOT / "schemas" / "canonical-trace.schema.json").read_text())
        validator = Draft202012Validator(schema, format_checker=FormatChecker())

        errors = list(validator.iter_errors(captured.canonical_trace))

        self.assertFalse(errors, "\n".join(error.message for error in errors))
        self.assertEqual(captured.canonical_trace["extensions"]["cuad"]["license"], "CC BY 4.0")

    def test_every_real_data_scenario_canonicalizes(self) -> None:
        schema = json.loads((ROOT / "schemas" / "canonical-trace.schema.json").read_text())
        validator = Draft202012Validator(schema, format_checker=FormatChecker())

        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                trace = capture_scenario(scenario).canonical_trace
                errors = list(validator.iter_errors(trace))
                self.assertFalse(errors, "\n".join(error.message for error in errors))

    def test_metadata_only_trace_contains_no_query_answer_or_contract_text(self) -> None:
        captured = capture_scenario("healthy", capture_mode="metadata_only")
        serialized = json.dumps(captured.canonical_trace)

        self.assertNotIn(captured.artifacts.query, serialized)
        self.assertNotIn(captured.artifacts.answer.text, serialized)
        self.assertNotIn(captured.artifacts.expected_evidence, serialized)
        self.assertNotIn("content", captured.canonical_trace["query"])
        self.assertNotIn("content", captured.canonical_trace["generation"]["response"])

    def test_redacted_trace_does_not_retain_real_content(self) -> None:
        captured = capture_scenario("healthy", capture_mode="redacted")
        serialized = json.dumps(captured.canonical_trace)

        self.assertNotIn(captured.artifacts.query, serialized)
        self.assertNotIn(captured.artifacts.answer.text, serialized)
        self.assertEqual(captured.canonical_trace["query"]["content"], "[REDACTED]")
        self.assertTrue(captured.canonical_trace["privacy"]["redaction_applied"])

    def test_full_capture_retains_real_cuad_query_and_answer(self) -> None:
        captured = capture_scenario("healthy", capture_mode="full")

        self.assertEqual(captured.canonical_trace["query"]["content"], captured.artifacts.query)
        self.assertEqual(
            captured.canonical_trace["generation"]["response"]["content"],
            captured.artifacts.answer.text,
        )

    def test_llm_span_uses_standard_gen_ai_attributes(self) -> None:
        captured = capture_scenario("healthy")
        llm_span = next(span for span in captured.spans if span.name == "llm.generate")

        self.assertEqual(llm_span.attributes["gen_ai.operation.name"], "chat")
        self.assertEqual(llm_span.attributes["gen_ai.provider.name"], "ragproof-lab")
        self.assertIn("gen_ai.request.model", llm_span.attributes)

    def test_pipeline_fingerprint_is_stable_across_trace_ids(self) -> None:
        first = capture_scenario("healthy").canonical_trace
        second = capture_scenario("healthy").canonical_trace

        self.assertNotEqual(first["trace_id"], second["trace_id"])
        self.assertEqual(first["pipeline"]["fingerprint"], second["pipeline"]["fingerprint"])

    def test_invalid_capture_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RagProofTracer(capture_mode="unsafe")

    def test_capture_cli_defaults_to_metadata_only_json(self) -> None:
        process = subprocess.run(
            [sys.executable, "-m", "ragproof_otel.cli", "--scenario", "healthy"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        payload = json.loads(process.stdout)
        self.assertEqual(payload["privacy"]["capture_mode"], "metadata_only")
        self.assertNotIn("content", payload["query"])

    def test_real_cuad_trace_exports_over_otlp_http(self) -> None:
        OtlpCaptureHandler.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), OtlpCaptureHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        endpoint = f"http://127.0.0.1:{server.server_port}/v1/traces"
        provider = configure_otlp_provider(endpoint, service_name="ragproof-otel-test")

        try:
            telemetry = RagProofTracer(
                tracer=provider.get_tracer("ragproof-otel-test"),
                capture_mode="metadata_only",
            )
            run_scenario("healthy", telemetry=telemetry)
            self.assertTrue(provider.force_flush(timeout_millis=5_000))
        finally:
            provider.shutdown()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)

        self.assertTrue(OtlpCaptureHandler.requests)
        request = OtlpCaptureHandler.requests[0]
        self.assertEqual(request["path"], "/v1/traces")
        self.assertEqual(request["content_type"], "application/x-protobuf")
        self.assertGreater(len(request["body"]), 0)


if __name__ == "__main__":
    unittest.main()

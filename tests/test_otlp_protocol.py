"""Protocol checks over spans captured from the licensed real CUAD fixture."""

import json
import subprocess
import sys
import unittest

from google.protobuf.json_format import MessageToJson
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.trace.export import SpanExportResult

from ragproof_otel import capture_scenario
from ragproof_otel.otlp_adapter import OtlpCanonicalAdapter, decode_export_request, encode_export_json
from ragproof_otel.processor import CompleteTraceSpanProcessor
from ragproof_store.otlp import OtlpTraceIngestor


class OtlpProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.captured = capture_scenario("healthy", capture_mode="full")

    def request(self):
        return encode_spans(self.captured.spans)

    @staticmethod
    def span(request, name):
        return next(span for resource in request.resource_spans for scope in resource.scope_spans
                    for span in scope.spans if span.name == name)

    @staticmethod
    def attribute(span, key):
        return next(attribute.value for attribute in span.attributes if attribute.key == key)

    def test_spec_json_hex_ids_preserve_real_trace_and_content(self):
        request = self.request()
        encoded = encode_export_json(request)
        document = json.loads(encoded)
        span = document["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        self.assertEqual(len(span["traceId"]), 32)
        self.assertEqual(len(span["spanId"]), 16)
        decoded = decode_export_request(encoded.encode(), "application/json")
        self.assertEqual(request, decoded)
        canonical, _ = OtlpCanonicalAdapter().convert(decoded)[0]
        self.assertEqual(canonical["query"]["content"], self.captured.artifacts.query)
        self.assertEqual(canonical["pipeline"]["fingerprint"], self.captured.canonical_trace["pipeline"]["fingerprint"])

    def test_generic_protobuf_base64_json_is_not_otlp_json(self):
        with self.assertRaisesRegex(ValueError, "hex encoding"):
            decode_export_request(MessageToJson(self.request()).encode(), "application/json")

    def test_malformed_wire_payloads_return_safe_value_errors(self):
        for payload, media_type in ((b"\xff", "application/x-protobuf"),
                                    (b"{broken", "application/json"),
                                    (b"[]", "application/json")):
            with self.subTest(media_type=media_type):
                with self.assertRaises(ValueError) as caught:
                    decode_export_request(payload, media_type)
                self.assertNotIn("broken", str(caught.exception))

    def test_changed_content_cannot_reuse_a_real_hash(self):
        request = self.request()
        retrieval = self.span(request, "retrieval.search")
        self.attribute(retrieval, "ragproof.query.content").string_value = self.captured.artifacts.expected_evidence
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            OtlpCanonicalAdapter().convert(request)

    def test_metadata_only_cannot_smuggle_full_content(self):
        request = self.request()
        self.attribute(self.span(request, "rag.request"), "ragproof.capture.mode").string_value = "metadata_only"
        with self.assertRaisesRegex(ValueError, "cannot contain content"):
            OtlpCanonicalAdapter().convert(request)

    def test_misaligned_rerank_fails_without_guessing(self):
        request = self.request()
        values = self.attribute(self.span(request, "retrieval.rerank"), "ragproof.rerank.scores").array_value.values
        del values[-1]
        with self.assertRaisesRegex(ValueError, "aligned"):
            OtlpCanonicalAdapter().convert(request)

    def test_dangling_parent_fails_complete_tree_requirement(self):
        request = self.request()
        self.span(request, "llm.generate").parent_span_id = self.span(request, "llm.generate").span_id
        with self.assertRaisesRegex(ValueError, "complete connected"):
            OtlpCanonicalAdapter().convert(request)

    def test_all_authorizations_precede_any_storage(self):
        second = capture_scenario("generation_failure", capture_mode="full")
        request = encode_spans([*self.captured.spans, *second.spans])
        class Sink:
            writes = []
            def ingest(self, trace):
                self.writes.append(trace)
                return {}
        sink = Sink()
        authorized = []
        def authorize(trace):
            authorized.append(trace["trace_id"])
            if len(authorized) == 2:
                raise PermissionError("Outside current project")
        with self.assertRaises(PermissionError):
            OtlpTraceIngestor(sink).ingest(request.SerializeToString(), "application/x-protobuf", authorize=authorize)
        self.assertEqual(sink.writes, [])

    def test_complete_trace_export_preserves_real_tree_and_reports_failures(self):
        class Exporter:
            def __init__(self, result):
                self.batches = []
                self.result = result
            def export(self, spans):
                self.batches.append(list(spans))
                return self.result
            def shutdown(self):
                pass
        for result in (SpanExportResult.SUCCESS, SpanExportResult.FAILURE):
            exporter = Exporter(result)
            processor = CompleteTraceSpanProcessor(exporter)
            for span in self.captured.spans:
                processor.on_end(span)
            self.assertEqual(len(exporter.batches), 1)
            self.assertEqual(len(exporter.batches[0]), len(self.captured.spans))
            self.assertEqual(processor.force_flush(), result is SpanExportResult.SUCCESS)

    def test_capture_import_does_not_require_optional_protobuf(self):
        script = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(('google.protobuf', 'opentelemetry.proto')):
        raise ImportError('Optional protobuf deliberately unavailable')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from ragproof_otel import capture_scenario
assert capture_scenario('healthy').canonical_trace['query']['content_hash']
"""
        process = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)


if __name__ == "__main__":
    unittest.main()

import json
import os
import unittest

import psycopg
from psycopg.rows import dict_row
from fastapi.testclient import TestClient

from ragproof_lab.components import HashedEmbedder
from ragproof_otel import capture_scenario
from ragproof_store import StorageSettings, TraceRepository
from ragproof_store.api import create_app


RUN_INTEGRATION = os.getenv("RAGPROOF_RUN_STORAGE_INTEGRATION") == "1"


@unittest.skipUnless(RUN_INTEGRATION, "set RAGPROOF_RUN_STORAGE_INTEGRATION=1")
class StorageIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.settings = StorageSettings.from_env()
        cls.repository = TraceRepository(
            cls.settings, embedding_function=HashedEmbedder().embed
        )
        cls.repository.initialize()

    def test_real_cuad_trace_has_durable_private_lineage_and_deduplication(self) -> None:
        captured = capture_scenario("healthy", capture_mode="full")
        trace = captured.canonical_trace
        answer = captured.artifacts.answer.text
        query = captured.artifacts.query
        evidence = captured.artifacts.expected_evidence

        first = self.repository.ingest(trace)
        second = self.repository.ingest(trace)

        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["trace_id"], second["trace_id"])

        stored = self.repository.get_trace(trace["trace_id"])
        serialized = json.dumps(stored)
        self.assertNotIn(query, serialized)
        self.assertNotIn(answer, serialized)
        self.assertNotIn(evidence, serialized)
        self.assertTrue(stored["query"]["content_ref"].startswith("s3://"))
        self.assertEqual(
            self.repository.objects.get_text(stored["query"]["content_ref"]), query
        )
        self.assertEqual(
            self.repository.objects.get_text(
                stored["generation"]["response"]["content_ref"]
            ),
            answer,
        )

        lineage = self.repository.answer_lineage(first["response_id"])
        self.assertEqual(lineage["trace_id"], trace["trace_id"])
        self.assertEqual(lineage["pipeline_fingerprint"], trace["pipeline"]["fingerprint"])
        self.assertTrue(lineage["candidates"])
        selected = [item for item in lineage["candidates"] if item["included_in_context"]]
        self.assertEqual(len(selected), 1)
        self.assertEqual(
            self.repository.objects.get_text(selected[0]["content_ref"]),
            captured.artifacts.context_chunks[0].text,
        )
        self.assertEqual(len(lineage["citations"]), 1)
        cited = next(
            chunk
            for chunk in captured.artifacts.chunks
            if chunk.chunk_id == captured.artifacts.answer.cited_chunk_id
        )
        self.assertEqual(
            self.repository.objects.get_text(lineage["citations"][0]["content_ref"]),
            cited.text,
        )

        candidate = trace["retrieval"]["candidates"][0]
        impact = self.repository.document_impact(
            trace["tenant_id"],
            trace["project_id"],
            candidate["document_id"],
            candidate["document_version"],
        )
        self.assertGreaterEqual(impact["affected_answer_count"], 1)
        self.assertIn(
            first["response_id"],
            {item["response_id"] for item in impact["affected_answers"]},
        )

        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT count(*) AS trace_count,
                       count(*) FILTER (WHERE c.embedding IS NOT NULL) AS embedded_count,
                       min(vector_dims(c.embedding)) AS dimensions
                FROM ragproof_traces t
                JOIN ragproof_retrieval_candidates rc ON rc.trace_id = t.trace_id
                JOIN ragproof_chunks c
                  ON c.tenant_id = rc.tenant_id AND c.project_id = rc.project_id
                 AND c.chunk_id = rc.chunk_id AND c.content_hash = rc.content_hash
                WHERE t.trace_id = %s
                """,
                (trace["trace_id"],),
            ).fetchone()
        self.assertEqual(row["trace_count"], len(trace["retrieval"]["candidates"]))
        self.assertEqual(row["embedded_count"], row["trace_count"])
        self.assertEqual(row["dimensions"], 64)

        event_entries = self.repository.events.client.xrevrange(
            self.repository.events.stream_name, count=20
        )
        events = [json.loads(fields["payload"]) for _, fields in event_entries]
        self.assertTrue(
            any(event["trace_id"] == trace["trace_id"] for event in events)
        )

    def test_metadata_only_real_trace_stores_hashes_without_content_objects(self) -> None:
        trace = capture_scenario("healthy", capture_mode="metadata_only").canonical_trace

        result = self.repository.ingest(trace)
        stored = self.repository.get_trace(result["trace_id"])
        lineage = self.repository.answer_lineage(result["response_id"])

        self.assertTrue(result["created"])
        self.assertNotIn("content_ref", stored["query"])
        self.assertIsNone(lineage["query_ref"])
        self.assertIsNone(lineage["response_ref"])
        self.assertTrue(all(item["content_ref"] is None for item in lineage["candidates"]))
        self.assertTrue(all(item["content_ref"] is None for item in lineage["citations"]))

    def test_http_api_serves_real_answer_lineage_and_document_impact(self) -> None:
        trace = capture_scenario("healthy", capture_mode="full").canonical_trace
        client = TestClient(create_app(self.repository))

        ingest = client.post("/v1/traces", json=trace)
        lineage = client.get(
            f"/v1/lineage/answers/{trace['generation']['response_id']}"
        )
        candidate = trace["retrieval"]["candidates"][0]
        impact = client.get(
            f"/v1/impact/documents/{candidate['document_id']}",
            params={
                "tenant_id": trace["tenant_id"],
                "project_id": trace["project_id"],
                "document_version": candidate["document_version"],
            },
        )

        self.assertEqual(ingest.status_code, 201)
        self.assertEqual(lineage.status_code, 200)
        self.assertEqual(lineage.json()["trace_id"], trace["trace_id"])
        self.assertEqual(impact.status_code, 200)
        self.assertGreaterEqual(impact.json()["affected_answer_count"], 1)


if __name__ == "__main__":
    unittest.main()

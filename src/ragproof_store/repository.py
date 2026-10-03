from __future__ import annotations

from copy import deepcopy
import hashlib
from importlib.resources import files
import json
from typing import Callable, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import StorageSettings
from .events import IngestionEventPublisher
from .object_store import ContentObjectStore
from .privacy import validate_and_sanitize


EmbeddingFunction = Callable[[str], Sequence[float]]


def _vector_literal(values: Sequence[float] | None) -> str | None:
    if values is None:
        return None
    if len(values) != 64:
        raise ValueError(f"Expected a 64-dimensional embedding, got {len(values)}")
    return "[" + ",".join(format(float(value), ".12g") for value in values) + "]"


def _remove_span_content(trace: dict) -> None:
    for span in trace["spans"]:
        attributes = span["attributes"]
        for key in list(attributes):
            if key.endswith(".content") or key.endswith(".contents"):
                del attributes[key]


class TraceRepository:
    """PostgreSQL system of record with S3 content and Redis ingestion events."""

    def __init__(
        self,
        settings: StorageSettings | None = None,
        embedding_function: EmbeddingFunction | None = None,
    ) -> None:
        self.settings = settings or StorageSettings.from_env()
        self.objects = ContentObjectStore(self.settings)
        self.events = IngestionEventPublisher(self.settings.redis_url)
        self.embedding_function = embedding_function

    def initialize(self) -> None:
        from .migrations import apply_migrations
        apply_migrations(self.settings.postgres_dsn)
        self.objects.ensure_bucket()
        self.events.ping()

    def resource_identity(self, kind: str, identifier: str) -> dict | None:
        joins = {
            "trace": ("ragproof_traces t", "t.trace_id"),
            "response": ("ragproof_responses r JOIN ragproof_traces t ON t.trace_id=r.trace_id", "r.response_id"),
            "diagnosis": ("ragproof_diagnoses d JOIN ragproof_traces t ON t.trace_id=d.trace_id", "d.diagnosis_id"),
            "replay": ("ragproof_replays r JOIN ragproof_traces t ON t.trace_id=r.original_trace_id", "r.replay_id"),
            "regression": ("ragproof_regression_cases r JOIN ragproof_traces t ON t.trace_id=r.original_trace_id", "r.case_id"),
            "impact": ("ragproof_impact_reports r JOIN ragproof_document_changes t ON t.change_id=r.change_id", "r.impact_id"),
            "gate": ("ragproof_quality_gate_runs t", "t.gate_run_id"),
        }
        tables, field = joins[kind]
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            return connection.execute(
                f"SELECT t.tenant_id, t.project_id FROM {tables} WHERE {field} = %s",
                (identifier,),
            ).fetchone()

    def list_traces(self, limit=30, offset=0, tenant_id=None, project_id=None) -> dict:
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("Invalid pagination")
        where = "WHERE t.tenant_id=%s AND t.project_id=%s" if tenant_id else ""
        params = [tenant_id, project_id] if tenant_id else []
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            total = connection.execute(f"SELECT count(*) AS n FROM ragproof_traces t {where}", params).fetchone()["n"]
            rows = connection.execute(f"""
                SELECT t.trace_id, r.response_id, t.started_at, t.source_provider,
                       t.canonical_trace->'source'->>'adapter_name' AS adapter_name,
                       t.pipeline_fingerprint, t.capture_mode, t.corpus_version, t.status,
                       v.status AS verification_status,
                       COALESCE(c.verdict_counts, '{{}}'::jsonb) AS verdict_counts
                FROM ragproof_traces t JOIN ragproof_responses r ON r.trace_id=t.trace_id
                LEFT JOIN LATERAL (
                    SELECT verification_id, status FROM ragproof_verifications
                    WHERE response_id=r.response_id ORDER BY created_at DESC LIMIT 1
                ) v ON true
                LEFT JOIN LATERAL (
                    SELECT jsonb_object_agg(verdict, n) AS verdict_counts FROM (
                        SELECT verdict, count(*) AS n FROM ragproof_claims
                        WHERE verification_id=v.verification_id GROUP BY verdict
                    ) counts
                ) c ON true
                {where} ORDER BY t.started_at DESC, t.trace_id LIMIT %s OFFSET %s
                """, [*params, limit, offset]).fetchall()
        for row in rows:
            row["started_at"] = row["started_at"].isoformat()
        return {"items": rows, "total": total, "limit": limit, "offset": offset}

    def list_quality_gates(self, limit=30, offset=0, tenant_id=None, project_id=None) -> dict:
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("Invalid pagination")
        where = "WHERE tenant_id=%s AND project_id=%s" if tenant_id else ""
        params = [tenant_id, project_id] if tenant_id else []
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            total = connection.execute(f"SELECT count(*) AS n FROM ragproof_quality_gate_runs {where}", params).fetchone()["n"]
            rows = connection.execute(f"""
                SELECT gate_run_id, candidate_name, outcome, total_cases, passed_cases,
                       failed_cases, pass_rate, created_at FROM ragproof_quality_gate_runs
                {where} ORDER BY created_at DESC LIMIT %s OFFSET %s
                """, [*params, limit, offset]).fetchall()
        for row in rows:
            row["created_at"] = row["created_at"].isoformat()
        return {"items": rows, "total": total, "limit": limit, "offset": offset}

    def health(self) -> dict:
        result = {"postgres": False, "redis": False, "object_store": False}
        try:
            with psycopg.connect(self.settings.postgres_dsn, connect_timeout=2) as connection:
                result["postgres"] = connection.execute("SELECT 1").fetchone()[0] == 1
        except Exception:
            pass
        try:
            result["redis"] = self.events.ping()
        except Exception:
            pass
        try:
            self.objects.client.head_bucket(Bucket=self.objects.bucket)
            result["object_store"] = True
        except Exception:
            pass
        return result

    def audit(self, principal, action: str, target: str, status: int) -> None:
        with psycopg.connect(self.settings.postgres_dsn) as connection:
            connection.execute("""
                INSERT INTO ragproof_audit_events (tenant_id, project_id, actor_hash, action, target, status_code)
                VALUES (%s, %s, %s, %s, %s, %s)
                """, (principal.tenant_id, principal.project_id, principal.key_hash, action, target, status))

    def _publish_event(self, event: dict) -> str | None:
        from .delivery import dispatch
        # The transactional database trigger already recorded these events.
        # Redis downtime cannot discard committed work; the dispatcher retries.
        if event["event"] == "impact.created":
            with psycopg.connect(self.settings.postgres_dsn) as connection:
                connection.execute("INSERT INTO ragproof_event_outbox(payload) VALUES (%s)", (Jsonb(event),))
        try:
            return dispatch(self.settings, self.events)
        except Exception:
            return None

    def ingest(self, canonical_trace: dict) -> dict:
        trace = deepcopy(canonical_trace)
        validate_and_sanitize(trace, self.settings.s3_bucket)
        trace_id = trace["trace_id"]
        tenant_id = trace["tenant_id"]
        project_id = trace["project_id"]
        capture_mode = trace["privacy"]["capture_mode"]

        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (trace_id,))
            existing = connection.execute(
                "SELECT trace_id, tenant_id, project_id, ingested_at FROM ragproof_traces WHERE trace_id = %s",
                (trace_id,),
            ).fetchone()
            if existing:
                if (existing["tenant_id"], existing["project_id"]) != (tenant_id, project_id):
                    raise ValueError("Trace ID is already assigned to another project")
                return {
                    "trace_id": trace_id,
                    "response_id": trace["generation"]["response_id"],
                    "created": False,
                    "ingested_at": existing["ingested_at"].isoformat(),
                }

            if capture_mode == "full":
                self._externalize_content(trace, tenant_id, project_id)
            else:
                self._discard_non_full_content(trace)
            _remove_span_content(trace)

            connection.execute(
                "INSERT INTO ragproof_tenants (tenant_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (tenant_id,),
            )
            connection.execute(
                """
                INSERT INTO ragproof_projects (tenant_id, project_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
                """,
                (tenant_id, project_id),
            )
            inserted = connection.execute(
                """
                INSERT INTO ragproof_traces (
                    trace_id, tenant_id, project_id, started_at, ended_at, status,
                    source_provider, pipeline_fingerprint, corpus_version,
                    index_version, capture_mode, canonical_trace
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING ingested_at
                """,
                (
                    trace_id,
                    tenant_id,
                    project_id,
                    trace["started_at"],
                    trace["ended_at"],
                    trace.get("status", "ok"),
                    trace["source"]["provider"],
                    trace["pipeline"]["fingerprint"],
                    trace["knowledge_base"]["corpus_version"],
                    trace["knowledge_base"]["index_version"],
                    capture_mode,
                    Jsonb(trace),
                ),
            ).fetchone()

            for span in trace["spans"]:
                connection.execute(
                    """
                    INSERT INTO ragproof_spans (
                        trace_id, span_id, parent_span_id, name, started_at,
                        ended_at, status, attributes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        trace_id,
                        span["span_id"],
                        span["parent_span_id"],
                        span["name"],
                        span["started_at"],
                        span["ended_at"],
                        span["status"],
                        Jsonb(span["attributes"]),
                    ),
                )

            connection.execute(
                """
                INSERT INTO ragproof_queries (trace_id, content_hash, content_ref)
                VALUES (%s, %s, %s)
                """,
                (trace_id, trace["query"]["content_hash"], trace["query"].get("content_ref")),
            )
            generation = trace["generation"]
            response = generation["response"]
            connection.execute(
                """
                INSERT INTO ragproof_responses (
                    response_id, trace_id, content_hash, content_ref,
                    model_provider, model_name, input_tokens, output_tokens
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    generation["response_id"],
                    trace_id,
                    response["content_hash"],
                    response.get("content_ref"),
                    generation["model_provider"],
                    generation["model_name"],
                    generation.get("input_tokens"),
                    generation.get("output_tokens"),
                ),
            )

            for candidate in trace["retrieval"]["candidates"]:
                self._insert_candidate(connection, trace, candidate)
            for ordinal, citation in enumerate(
                trace["generation"].get("citations", []), start=1
            ):
                self._insert_citation(connection, trace, citation, ordinal)

        event = {
            "event": "trace.ingested",
            "trace_id": trace_id,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "response_id": trace["generation"]["response_id"],
        }
        event_id = self._publish_event(event)
        return {
            "trace_id": trace_id,
            "response_id": trace["generation"]["response_id"],
            "created": True,
            "ingested_at": inserted["ingested_at"].isoformat(),
            "event_id": event_id,
        }

    def _externalize_content(self, trace: dict, tenant_id: str, project_id: str) -> None:
        content_objects = [trace["query"], trace["generation"]["response"]]
        content_objects.extend(trace["retrieval"]["candidates"])
        content_objects.extend(trace["generation"].get("citations", []))
        for item in content_objects:
            content = item.pop("content", None)
            if content is not None:
                item["content_ref"] = self.objects.put_text(
                    tenant_id, project_id, item["content_hash"], content
                )

    @staticmethod
    def _discard_non_full_content(trace: dict) -> None:
        trace["query"].pop("content", None)
        trace["generation"]["response"].pop("content", None)
        for candidate in trace["retrieval"]["candidates"]:
            candidate.pop("content", None)
        for citation in trace["generation"].get("citations", []):
            citation.pop("content", None)

    def _insert_candidate(self, connection, trace: dict, candidate: dict) -> None:
        tenant_id = trace["tenant_id"]
        project_id = trace["project_id"]
        trace_id = trace["trace_id"]
        document_id = candidate["document_id"]
        version = candidate["document_version"]
        content_hash = candidate["content_hash"]
        connection.execute(
            """
            INSERT INTO ragproof_documents (
                tenant_id, project_id, document_id, data_source_id
            ) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
            """,
            (tenant_id, project_id, document_id, trace["knowledge_base"]["data_source_id"]),
        )
        connection.execute(
            """
            INSERT INTO ragproof_document_versions (
                tenant_id, project_id, document_id, document_version, first_seen_trace_id
            ) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
            """,
            (tenant_id, project_id, document_id, version, trace_id),
        )
        content_ref = candidate.get("content_ref")
        content = None
        if content_ref and self.embedding_function:
            content = self.objects.get_text(content_ref)
        embedding = _vector_literal(self.embedding_function(content)) if content is not None else None
        connection.execute(
            """
            INSERT INTO ragproof_chunks (
                tenant_id, project_id, chunk_id, document_id, document_version,
                content_hash, content_ref, embedding, first_seen_trace_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s)
            ON CONFLICT (tenant_id, project_id, chunk_id, content_hash)
            DO UPDATE SET
                content_ref = COALESCE(ragproof_chunks.content_ref, EXCLUDED.content_ref),
                embedding = COALESCE(ragproof_chunks.embedding, EXCLUDED.embedding)
            """,
            (
                tenant_id,
                project_id,
                candidate["chunk_id"],
                document_id,
                version,
                content_hash,
                content_ref,
                embedding,
                trace_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO ragproof_retrieval_candidates (
                trace_id, tenant_id, project_id, chunk_id, content_hash,
                content_ref, retrieval_rank, retrieval_score, rerank_rank, rerank_score,
                included_in_context
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                trace_id,
                tenant_id,
                project_id,
                candidate["chunk_id"],
                content_hash,
                content_ref,
                candidate["retrieval_rank"],
                candidate.get("retrieval_score"),
                candidate.get("rerank_rank"),
                candidate.get("rerank_score"),
                candidate["included_in_context"],
            ),
        )

    def _insert_citation(
        self, connection, trace: dict, citation: dict, ordinal: int
    ) -> None:
        tenant_id = trace["tenant_id"]
        project_id = trace["project_id"]
        trace_id = trace["trace_id"]
        document_id = citation["document_id"]
        version = citation["document_version"]
        content_hash = citation["content_hash"]
        content_ref = citation.get("content_ref")
        connection.execute(
            """
            INSERT INTO ragproof_documents (
                tenant_id, project_id, document_id, data_source_id
            ) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
            """,
            (tenant_id, project_id, document_id, trace["knowledge_base"]["data_source_id"]),
        )
        connection.execute(
            """
            INSERT INTO ragproof_document_versions (
                tenant_id, project_id, document_id, document_version, first_seen_trace_id
            ) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
            """,
            (tenant_id, project_id, document_id, version, trace_id),
        )
        content = self.objects.get_text(content_ref) if content_ref and self.embedding_function else None
        embedding = _vector_literal(self.embedding_function(content)) if content is not None else None
        connection.execute(
            """
            INSERT INTO ragproof_chunks (
                tenant_id, project_id, chunk_id, document_id, document_version,
                content_hash, content_ref, embedding, first_seen_trace_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s)
            ON CONFLICT (tenant_id, project_id, chunk_id, content_hash)
            DO UPDATE SET
                content_ref = COALESCE(ragproof_chunks.content_ref, EXCLUDED.content_ref),
                embedding = COALESCE(ragproof_chunks.embedding, EXCLUDED.embedding)
            """,
            (
                tenant_id, project_id, citation["chunk_id"], document_id, version,
                content_hash, content_ref, embedding, trace_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO ragproof_citations (
                response_id, ordinal, tenant_id, project_id, chunk_id,
                content_hash, content_ref
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                trace["generation"]["response_id"], ordinal, tenant_id, project_id,
                citation["chunk_id"], content_hash, content_ref,
            ),
        )
    def get_trace(self, trace_id: str) -> dict | None:
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT canonical_trace FROM ragproof_traces WHERE trace_id = %s", (trace_id,)
            ).fetchone()
        return row["canonical_trace"] if row else None

    def answer_lineage(self, response_id: str) -> dict | None:
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            response = connection.execute(
                """
                SELECT r.response_id, r.content_hash AS response_hash, r.content_ref AS response_ref,
                       r.model_provider, r.model_name, t.trace_id, t.tenant_id, t.project_id,
                       t.pipeline_fingerprint, t.corpus_version, t.index_version,
                       q.content_hash AS query_hash, q.content_ref AS query_ref
                FROM ragproof_responses r
                JOIN ragproof_traces t ON t.trace_id = r.trace_id
                JOIN ragproof_queries q ON q.trace_id = t.trace_id
                WHERE r.response_id = %s
                """,
                (response_id,),
            ).fetchone()
            if not response:
                return None
            candidates = connection.execute(
                """
                SELECT c.chunk_id, c.content_hash, rc.content_ref, c.document_id,
                       c.document_version, rc.retrieval_rank, rc.retrieval_score,
                       rc.rerank_rank, rc.rerank_score, rc.included_in_context
                FROM ragproof_retrieval_candidates rc
                JOIN ragproof_chunks c
                  ON c.tenant_id = rc.tenant_id
                 AND c.project_id = rc.project_id
                 AND c.chunk_id = rc.chunk_id
                 AND c.content_hash = rc.content_hash
                WHERE rc.trace_id = %s
                ORDER BY rc.retrieval_rank
                """,
                (response["trace_id"],),
            ).fetchall()
            citations = connection.execute(
                """
                SELECT ci.ordinal, ci.chunk_id, ci.content_hash, ci.content_ref,
                       c.document_id, c.document_version
                FROM ragproof_citations ci
                JOIN ragproof_chunks c
                  ON c.tenant_id = ci.tenant_id
                 AND c.project_id = ci.project_id
                 AND c.chunk_id = ci.chunk_id
                 AND c.content_hash = ci.content_hash
                WHERE ci.response_id = %s
                ORDER BY ci.ordinal
                """,
                (response_id,),
            ).fetchall()
        return {**response, "candidates": candidates, "citations": citations}

    def document_impact(
        self, tenant_id: str, project_id: str, document_id: str, document_version: str
    ) -> dict:
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT r.response_id, t.trace_id, r.content_hash AS response_hash,
                       t.pipeline_fingerprint,
                       bool_or(rc.included_in_context) AS used_in_context,
                       jsonb_agg(
                           jsonb_build_object(
                               'chunk_id', rc.chunk_id,
                               'chunk_hash', rc.content_hash,
                               'included_in_context', rc.included_in_context,
                               'retrieval_rank', rc.retrieval_rank,
                               'rerank_rank', rc.rerank_rank
                           ) ORDER BY rc.retrieval_rank
                       ) AS evidence_uses
                FROM ragproof_chunks c
                JOIN ragproof_retrieval_candidates rc
                  ON rc.tenant_id = c.tenant_id
                 AND rc.project_id = c.project_id
                 AND rc.chunk_id = c.chunk_id
                 AND rc.content_hash = c.content_hash
                JOIN ragproof_traces t ON t.trace_id = rc.trace_id
                JOIN ragproof_responses r ON r.trace_id = t.trace_id
                WHERE c.tenant_id = %s AND c.project_id = %s
                  AND c.document_id = %s AND c.document_version = %s
                GROUP BY r.response_id, t.trace_id, r.content_hash, t.pipeline_fingerprint
                ORDER BY t.trace_id
                """,
                (tenant_id, project_id, document_id, document_version),
            ).fetchall()
            claim_rows = connection.execute(
                """
                SELECT cl.claim_id, cl.verdict, cl.confidence,
                       v.response_id, v.trace_id,
                       jsonb_agg(
                           jsonb_build_object(
                               'chunk_id', ce.chunk_id,
                               'relation', ce.relation,
                               'freshness', ce.freshness
                           ) ORDER BY ce.chunk_id
                       ) AS evidence_uses
                FROM ragproof_claim_evidence ce
                JOIN ragproof_claims cl ON cl.claim_id = ce.claim_id
                JOIN ragproof_verifications v
                  ON v.verification_id = cl.verification_id
                JOIN ragproof_retrieval_candidates rc
                  ON rc.trace_id = ce.trace_id AND rc.chunk_id = ce.chunk_id
                JOIN ragproof_chunks c
                  ON c.tenant_id = rc.tenant_id
                 AND c.project_id = rc.project_id
                 AND c.chunk_id = rc.chunk_id
                 AND c.content_hash = rc.content_hash
                WHERE c.tenant_id = %s AND c.project_id = %s
                  AND c.document_id = %s AND c.document_version = %s
                GROUP BY cl.claim_id, cl.verdict, cl.confidence,
                         v.response_id, v.trace_id
                ORDER BY v.trace_id, cl.claim_id
                """,
                (tenant_id, project_id, document_id, document_version),
            ).fetchall()
        return {
            "tenant_id": tenant_id,
            "project_id": project_id,
            "document_id": document_id,
            "document_version": document_version,
            "affected_answers": rows,
            "affected_answer_count": len(rows),
            "affected_claims": claim_rows,
            "affected_claim_count": len(claim_rows),
        }

    def get_verification(
        self, response_id: str, evaluator_name: str, evaluator_version: str
    ) -> dict | None:
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT verification_id, trace_id, response_id, evaluator_name,
                       evaluator_version, status, result_ref, blocked_reason, created_at
                FROM ragproof_verifications
                WHERE response_id = %s AND evaluator_name = %s AND evaluator_version = %s
                """,
                (response_id, evaluator_name, evaluator_version),
            ).fetchone()
        if not row:
            return None
        if row["status"] == "completed":
            return json.loads(self.objects.get_text(row["result_ref"]))
        return {
            "verification_id": row["verification_id"].strip(),
            "trace_id": row["trace_id"],
            "response_id": row["response_id"],
            "evaluator": {
                "name": row["evaluator_name"],
                "version": row["evaluator_version"],
            },
            "status": row["status"],
            "reason": row["blocked_reason"],
            "created_at": row["created_at"].isoformat(),
        }

    def save_verification(self, verification: dict) -> dict:
        evaluator = verification["evaluator"]
        response_id = verification["response_id"]
        existing = self.get_verification(response_id, evaluator["name"], evaluator["version"])
        if existing is not None:
            return existing

        serialized = json.dumps(verification, sort_keys=True, separators=(",", ":"))
        result_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        verification_id = hashlib.sha256(
            f"{response_id}\0{evaluator['name']}\0{evaluator['version']}".encode("utf-8")
        ).hexdigest()
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            identity = connection.execute(
                """
                SELECT tenant_id, project_id FROM ragproof_traces WHERE trace_id = %s
                """,
                (verification["trace_id"],),
            ).fetchone()
            if identity is None:
                raise KeyError(f"Unknown trace: {verification['trace_id']}")
            result_ref = self.objects.put_text(
                identity["tenant_id"], identity["project_id"], result_hash, serialized
            )
            connection.execute(
                """
                INSERT INTO ragproof_verifications (
                    verification_id, trace_id, response_id, evaluator_name,
                    evaluator_version, status, result_hash, result_ref
                ) VALUES (%s, %s, %s, %s, %s, 'completed', %s, %s)
                """,
                (
                    verification_id,
                    verification["trace_id"],
                    response_id,
                    evaluator["name"],
                    evaluator["version"],
                    result_hash,
                    result_ref,
                ),
            )
            for ordinal, claim in enumerate(verification["claims"], start=1):
                connection.execute(
                    """
                    INSERT INTO ragproof_claims (
                        claim_id, verification_id, ordinal, text_hash,
                        verdict, confidence, judge_disagreement
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        claim["claim_id"],
                        verification_id,
                        ordinal,
                        hashlib.sha256(claim["text"].encode("utf-8")).hexdigest(),
                        claim["verdict"],
                        claim["confidence"],
                        claim.get("judge_disagreement", False),
                    ),
                )
                for evidence in claim["evidence"]:
                    connection.execute(
                        """
                        INSERT INTO ragproof_claim_evidence (
                            claim_id, trace_id, chunk_id, relation,
                            document_version, freshness
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            claim["claim_id"],
                            verification["trace_id"],
                            evidence["chunk_id"],
                            evidence["relation"],
                            evidence["document_version"],
                            evidence["freshness"],
                        ),
                    )
        self._publish_event(
            {
                "event": "verification.completed",
                "trace_id": verification["trace_id"],
                "response_id": response_id,
                "evaluator_name": evaluator["name"],
                "evaluator_version": evaluator["version"],
            }
        )
        return verification

    def save_blocked_verification(
        self,
        trace_id: str,
        response_id: str,
        evaluator_name: str,
        evaluator_version: str,
        reason: str,
    ) -> dict:
        existing = self.get_verification(response_id, evaluator_name, evaluator_version)
        if existing is not None:
            return existing
        verification_id = hashlib.sha256(
            f"{response_id}\0{evaluator_name}\0{evaluator_version}".encode("utf-8")
        ).hexdigest()
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                INSERT INTO ragproof_verifications (
                    verification_id, trace_id, response_id, evaluator_name,
                    evaluator_version, status, blocked_reason
                ) VALUES (%s, %s, %s, %s, %s, 'blocked_content_unavailable', %s)
                RETURNING created_at
                """,
                (
                    verification_id,
                    trace_id,
                    response_id,
                    evaluator_name,
                    evaluator_version,
                    reason,
                ),
            ).fetchone()
        return {
            "verification_id": verification_id,
            "trace_id": trace_id,
            "response_id": response_id,
            "evaluator": {"name": evaluator_name, "version": evaluator_version},
            "status": "blocked_content_unavailable",
            "reason": reason,
            "created_at": row["created_at"].isoformat(),
        }

    def get_diagnosis(
        self, response_id: str, diagnoser_name: str, diagnoser_version: str
    ) -> dict | None:
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT result_ref, lifecycle_status FROM ragproof_diagnoses
                WHERE response_id = %s AND diagnoser_name = %s AND diagnoser_version = %s
                """,
                (response_id, diagnoser_name, diagnoser_version),
            ).fetchone()
        if not row:
            return None
        result = json.loads(self.objects.get_text(row["result_ref"]))
        result["status"] = row["lifecycle_status"]
        return result

    def save_diagnosis(self, response_id: str, diagnosis: dict, diagnoser: dict) -> dict:
        existing = self.get_diagnosis(
            response_id, diagnoser["name"], diagnoser["version"]
        )
        if existing is not None:
            return existing
        serialized = json.dumps(diagnosis, sort_keys=True, separators=(",", ":"))
        result_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            identity = connection.execute(
                "SELECT tenant_id, project_id FROM ragproof_traces WHERE trace_id = %s",
                (diagnosis["trace_id"],),
            ).fetchone()
            if identity is None:
                raise KeyError(f"Unknown trace: {diagnosis['trace_id']}")
            result_ref = self.objects.put_text(
                identity["tenant_id"], identity["project_id"], result_hash, serialized
            )
            primary = diagnosis["primary_hypothesis"]
            connection.execute(
                """
                INSERT INTO ragproof_diagnoses (
                    diagnosis_id, trace_id, response_id, diagnoser_name,
                    diagnoser_version, lifecycle_status, primary_category,
                    primary_component, primary_confidence, result_hash, result_ref
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    diagnosis["diagnosis_id"], diagnosis["trace_id"], response_id,
                    diagnoser["name"], diagnoser["version"], diagnosis["status"],
                    primary["category"], primary["component"], primary["confidence"],
                    result_hash, result_ref,
                ),
            )
            connection.execute(
                """
                INSERT INTO ragproof_diagnosis_status_history (diagnosis_id, status)
                VALUES (%s, %s)
                """,
                (diagnosis["diagnosis_id"], diagnosis["status"]),
            )
            for claim_id in diagnosis["claim_ids"]:
                connection.execute(
                    """
                    INSERT INTO ragproof_diagnosis_claims (diagnosis_id, claim_id)
                    VALUES (%s, %s)
                    """,
                    (diagnosis["diagnosis_id"], claim_id),
                )
            hypotheses = [primary, *diagnosis.get("alternative_hypotheses", [])]
            for rank, hypothesis in enumerate(hypotheses, start=1):
                connection.execute(
                    """
                    INSERT INTO ragproof_diagnosis_hypotheses (
                        diagnosis_id, rank, category, component, confidence
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        diagnosis["diagnosis_id"], rank, hypothesis["category"],
                        hypothesis["component"], hypothesis["confidence"],
                    ),
                )
        self._publish_event(
            {
                "event": "diagnosis.created",
                "trace_id": diagnosis["trace_id"],
                "response_id": response_id,
                "diagnosis_id": diagnosis["diagnosis_id"],
                "primary_category": diagnosis["primary_hypothesis"]["category"],
            }
        )
        return diagnosis

    def transition_diagnosis(
        self, diagnosis_id: str, status: str, replay_id: str | None = None
    ) -> None:
        allowed = {"suspected", "replaying", "confirmed", "rejected", "needs_review"}
        if status not in allowed:
            raise ValueError(f"Unsupported diagnosis lifecycle status: {status}")
        with psycopg.connect(self.settings.postgres_dsn) as connection:
            updated = connection.execute(
                """
                UPDATE ragproof_diagnoses SET lifecycle_status = %s
                WHERE diagnosis_id = %s
                """,
                (status, diagnosis_id),
            )
            if updated.rowcount != 1:
                raise KeyError(f"Unknown diagnosis: {diagnosis_id}")
            connection.execute(
                """
                INSERT INTO ragproof_diagnosis_status_history (
                    diagnosis_id, status, replay_id
                ) VALUES (%s, %s, %s)
                """,
                (diagnosis_id, status, replay_id),
            )

    def get_replay(self, replay_id: str) -> dict | None:
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT result_ref FROM ragproof_replays WHERE replay_id = %s",
                (replay_id,),
            ).fetchone()
        return json.loads(self.objects.get_text(row["result_ref"])) if row else None

    def get_diagnosis_by_id(self, diagnosis_id: str) -> dict | None:
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT result_ref, lifecycle_status FROM ragproof_diagnoses
                WHERE diagnosis_id = %s
                """,
                (diagnosis_id,),
            ).fetchone()
        if not row:
            return None
        result = json.loads(self.objects.get_text(row["result_ref"]))
        result["status"] = row["lifecycle_status"]
        return result

    def get_replay_for_diagnosis(self, diagnosis_id: str) -> dict | None:
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT result_ref FROM ragproof_replays WHERE diagnosis_id = %s",
                (diagnosis_id,),
            ).fetchone()
        return json.loads(self.objects.get_text(row["result_ref"])) if row else None

    def save_replay(self, replay: dict) -> dict:
        existing = self.get_replay_for_diagnosis(replay["diagnosis_id"])
        if existing is not None:
            return existing
        serialized = json.dumps(replay, sort_keys=True, separators=(",", ":"))
        result_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            identity = connection.execute(
                "SELECT tenant_id, project_id FROM ragproof_traces WHERE trace_id = %s",
                (replay["original_trace_id"],),
            ).fetchone()
            if identity is None:
                raise KeyError(f"Unknown trace: {replay['original_trace_id']}")
            result_ref = self.objects.put_text(
                identity["tenant_id"], identity["project_id"], result_hash, serialized
            )
            connection.execute(
                """
                INSERT INTO ragproof_replays (
                    replay_id, diagnosis_id, original_trace_id, baseline_trace_id,
                    candidate_trace_id, changed_component, outcome, result_hash, result_ref
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    replay["replay_id"], replay["diagnosis_id"],
                    replay["original_trace_id"], replay["baseline"]["trace_id"],
                    replay["candidate"]["trace_id"],
                    replay["changed_variable"]["component"], replay["outcome"],
                    result_hash, result_ref,
                ),
            )
        lifecycle = {
            "CONFIRMED": "confirmed",
            "REJECTED": "rejected",
            "NEEDS_REVIEW": "needs_review",
        }[replay["outcome"]]
        self.transition_diagnosis(replay["diagnosis_id"], lifecycle, replay["replay_id"])
        self._publish_event(
            {
                "event": "replay.completed",
                "replay_id": replay["replay_id"],
                "diagnosis_id": replay["diagnosis_id"],
                "original_trace_id": replay["original_trace_id"],
                "outcome": replay["outcome"],
            }
        )
        return replay

    def document_change_facts(
        self,
        tenant_id: str,
        project_id: str,
        document_id: str,
        from_version: str,
        to_version: str,
    ) -> dict:
        """Return direct, persisted uses of a document version and its content hashes."""
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            document = connection.execute(
                """
                SELECT document_id FROM ragproof_documents
                WHERE tenant_id = %s AND project_id = %s AND document_id = %s
                """,
                (tenant_id, project_id, document_id),
            ).fetchone()
            if document is None:
                raise KeyError(f"Unknown document: {document_id}")

            hash_rows = connection.execute(
                """
                SELECT document_version, content_hash
                FROM ragproof_chunks
                WHERE tenant_id = %s AND project_id = %s AND document_id = %s
                  AND document_version IN (%s, %s)
                GROUP BY document_version, content_hash
                ORDER BY document_version, content_hash
                """,
                (tenant_id, project_id, document_id, from_version, to_version),
            ).fetchall()
            usage_rows = connection.execute(
                """
                WITH uses AS (
                    SELECT rc.trace_id,
                           CASE WHEN rc.included_in_context
                                THEN 'context' ELSE 'retrieved_only' END AS usage_mode
                    FROM ragproof_retrieval_candidates rc
                    JOIN ragproof_chunks c
                      ON c.tenant_id = rc.tenant_id
                     AND c.project_id = rc.project_id
                     AND c.chunk_id = rc.chunk_id
                     AND c.content_hash = rc.content_hash
                    WHERE c.tenant_id = %s AND c.project_id = %s
                      AND c.document_id = %s AND c.document_version = %s
                    UNION ALL
                    SELECT r.trace_id, 'citation' AS usage_mode
                    FROM ragproof_citations ci
                    JOIN ragproof_responses r ON r.response_id = ci.response_id
                    JOIN ragproof_chunks c
                      ON c.tenant_id = ci.tenant_id
                     AND c.project_id = ci.project_id
                     AND c.chunk_id = ci.chunk_id
                     AND c.content_hash = ci.content_hash
                    WHERE c.tenant_id = %s AND c.project_id = %s
                      AND c.document_id = %s AND c.document_version = %s
                )
                SELECT u.trace_id, r.response_id,
                       array_agg(DISTINCT u.usage_mode) AS usage_modes
                FROM uses u
                JOIN ragproof_responses r ON r.trace_id = u.trace_id
                GROUP BY u.trace_id, r.response_id
                ORDER BY u.trace_id
                """,
                (
                    tenant_id, project_id, document_id, from_version,
                    tenant_id, project_id, document_id, from_version,
                ),
            ).fetchall()
            claim_rows = connection.execute(
                """
                SELECT DISTINCT v.trace_id, cl.claim_id
                FROM ragproof_claim_evidence ce
                JOIN ragproof_claims cl ON cl.claim_id = ce.claim_id
                JOIN ragproof_verifications v
                  ON v.verification_id = cl.verification_id
                JOIN ragproof_retrieval_candidates rc
                  ON rc.trace_id = ce.trace_id AND rc.chunk_id = ce.chunk_id
                JOIN ragproof_chunks c
                  ON c.tenant_id = rc.tenant_id
                 AND c.project_id = rc.project_id
                 AND c.chunk_id = rc.chunk_id
                 AND c.content_hash = rc.content_hash
                WHERE c.tenant_id = %s AND c.project_id = %s
                  AND c.document_id = %s AND c.document_version = %s
                ORDER BY v.trace_id, cl.claim_id
                """,
                (tenant_id, project_id, document_id, from_version),
            ).fetchall()
            trace_ids = [row["trace_id"] for row in usage_rows]
            diagnosis_rows = []
            if trace_ids:
                diagnosis_rows = connection.execute(
                    """
                    SELECT trace_id, diagnosis_id, primary_category AS category,
                           lifecycle_status AS status
                    FROM ragproof_diagnoses
                    WHERE trace_id = ANY(%s)
                    ORDER BY trace_id, diagnosis_id
                    """,
                    (trace_ids,),
                ).fetchall()

        hashes_by_version = {from_version: [], to_version: []}
        for row in hash_rows:
            hashes_by_version[row["document_version"]].append(row["content_hash"])
        claims_by_trace: dict[str, list[str]] = {}
        for row in claim_rows:
            claims_by_trace.setdefault(row["trace_id"], []).append(row["claim_id"])
        diagnoses_by_trace: dict[str, list[dict]] = {}
        for row in diagnosis_rows:
            diagnoses_by_trace.setdefault(row["trace_id"], []).append(
                {
                    "diagnosis_id": row["diagnosis_id"],
                    "category": row["category"],
                    "status": row["status"],
                }
            )
        mode_order = {"context": 0, "retrieved_only": 1, "citation": 2}
        affected_traces = [
            {
                "trace_id": row["trace_id"],
                "response_id": row["response_id"],
                "usage_modes": sorted(row["usage_modes"], key=mode_order.__getitem__),
                "claim_ids": claims_by_trace.get(row["trace_id"], []),
                "diagnoses": diagnoses_by_trace.get(row["trace_id"], []),
            }
            for row in usage_rows
        ]
        return {
            "from_chunk_hashes": hashes_by_version[from_version],
            "to_chunk_hashes": hashes_by_version[to_version],
            "affected_traces": affected_traces,
        }

    def get_impact(self, impact_id: str) -> dict | None:
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT result_ref FROM ragproof_impact_reports WHERE impact_id = %s",
                (impact_id,),
            ).fetchone()
        return json.loads(self.objects.get_text(row["result_ref"])) if row else None

    def save_impact(self, report: dict) -> dict:
        existing = self.get_impact(report["impact_id"])
        if existing is not None:
            return existing
        serialized = json.dumps(report, sort_keys=True, separators=(",", ":"))
        result_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        result_ref = self.objects.put_text(
            report["tenant_id"], report["project_id"], result_hash, serialized
        )
        change = report["change"]
        with psycopg.connect(self.settings.postgres_dsn) as connection:
            connection.execute(
                """
                INSERT INTO ragproof_document_changes (
                    change_id, tenant_id, project_id, document_id,
                    from_version, to_version
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (change_id) DO NOTHING
                """,
                (
                    report["change_id"], report["tenant_id"], report["project_id"],
                    report["document_id"], change["from_version"], change["to_version"],
                ),
            )
            connection.execute(
                """
                INSERT INTO ragproof_impact_reports (
                    impact_id, change_id, affected_trace_count,
                    affected_claim_count, result_hash, result_ref
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    report["impact_id"], report["change_id"],
                    report["summary"]["affected_trace_count"],
                    report["summary"]["affected_claim_count"], result_hash, result_ref,
                ),
            )
        self._publish_event(
            {
                "event": "impact.created",
                "impact_id": report["impact_id"],
                "change_id": report["change_id"],
                "document_id": report["document_id"],
                "affected_trace_count": report["summary"]["affected_trace_count"],
            }
        )
        return report

    def get_regression(self, case_id: str) -> dict | None:
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT result_ref FROM ragproof_regression_cases WHERE case_id = %s",
                (case_id,),
            ).fetchone()
        return json.loads(self.objects.get_text(row["result_ref"])) if row else None

    def get_regression_for_replay(self, replay_id: str) -> dict | None:
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT result_ref FROM ragproof_regression_cases WHERE replay_id = %s",
                (replay_id,),
            ).fetchone()
        return json.loads(self.objects.get_text(row["result_ref"])) if row else None

    def list_confirmed_replays_without_regressions(self) -> list[str]:
        with psycopg.connect(self.settings.postgres_dsn) as connection:
            rows = connection.execute(
                """
                SELECT r.replay_id
                FROM ragproof_replays r
                LEFT JOIN ragproof_regression_cases c ON c.replay_id = r.replay_id
                WHERE r.outcome = 'CONFIRMED' AND c.case_id IS NULL
                ORDER BY r.created_at, r.replay_id
                """
            ).fetchall()
        return [row[0].strip() for row in rows]

    def save_regression(self, case: dict) -> dict:
        existing = self.get_regression_for_replay(case["incident"]["replay_id"])
        if existing is not None:
            return existing
        original = self.get_trace(case["input"]["original_trace_id"])
        if original is None:
            raise KeyError(f"Unknown trace: {case['input']['original_trace_id']}")
        serialized = json.dumps(case, sort_keys=True, separators=(",", ":"))
        result_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        result_ref = self.objects.put_text(
            original["tenant_id"], original["project_id"], result_hash, serialized
        )
        with psycopg.connect(self.settings.postgres_dsn) as connection:
            connection.execute(
                """
                INSERT INTO ragproof_regression_cases (
                    case_id, replay_id, diagnosis_id, category,
                    original_trace_id, passing_trace_id, query_hash, query_ref,
                    result_hash, result_ref
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    case["case_id"], case["incident"]["replay_id"],
                    case["incident"]["diagnosis_id"], case["incident"]["category"],
                    case["input"]["original_trace_id"],
                    case["passing_reference"]["trace_id"], case["input"]["query_hash"],
                    case["input"].get("query_ref"), result_hash, result_ref,
                ),
            )
        self._publish_event(
            {
                "event": "regression.created",
                "case_id": case["case_id"],
                "replay_id": case["incident"]["replay_id"],
                "category": case["incident"]["category"],
            }
        )
        return case

    def list_regressions(self) -> list[dict]:
        """Return immutable cases newest-first for deterministic gate selection."""
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT result_ref FROM ragproof_regression_cases
                ORDER BY created_at DESC, case_id DESC
                """
            ).fetchall()
        return [json.loads(self.objects.get_text(row["result_ref"])) for row in rows]

    def get_quality_gate(self, gate_run_id: str) -> dict | None:
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT result_ref FROM ragproof_quality_gate_runs WHERE gate_run_id = %s",
                (gate_run_id,),
            ).fetchone()
        return json.loads(self.objects.get_text(row["result_ref"])) if row else None

    def save_quality_gate(self, result: dict) -> dict:
        existing = self.get_quality_gate(result["gate_run_id"])
        if existing is not None:
            return existing
        serialized = json.dumps(result, sort_keys=True, separators=(",", ":"))
        result_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        result_ref = self.objects.put_text(
            result["tenant_id"], result["project_id"], result_hash, serialized
        )
        summary = result["summary"]
        candidate = result["candidate"]
        with psycopg.connect(self.settings.postgres_dsn) as connection:
            connection.execute(
                "INSERT INTO ragproof_tenants (tenant_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (result["tenant_id"],),
            )
            connection.execute(
                """
                INSERT INTO ragproof_projects (tenant_id, project_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
                """,
                (result["tenant_id"], result["project_id"]),
            )
            connection.execute(
                """
                INSERT INTO ragproof_quality_gate_runs (
                    gate_run_id, tenant_id, project_id, candidate_name,
                    candidate_pipeline_fingerprint, outcome, total_cases,
                    passed_cases, failed_cases, pass_rate, result_hash, result_ref
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    result["gate_run_id"], result["tenant_id"], result["project_id"],
                    candidate["name"], candidate.get("pipeline_fingerprint"),
                    result["outcome"], summary["total_cases"], summary["passed_cases"],
                    summary["failed_cases"], summary["pass_rate"], result_hash, result_ref,
                ),
            )
            for case in result["case_results"]:
                connection.execute(
                    """
                    INSERT INTO ragproof_quality_gate_case_results (
                        gate_run_id, case_id, category, candidate_trace_id,
                        passed, failures
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        result["gate_run_id"], case["case_id"], case["category"],
                        case.get("candidate_trace_id"), case["passed"],
                        Jsonb(case["failures"]),
                    ),
                )
        self._publish_event(
            {
                "event": "quality_gate.completed",
                "gate_run_id": result["gate_run_id"],
                "candidate_name": candidate["name"],
                "outcome": result["outcome"],
                "total_cases": summary["total_cases"],
                "failed_cases": summary["failed_cases"],
            }
        )
        return result

    def list_incidents(
        self,
        status: str | None = None,
        category: str | None = None,
        limit: int = 100,
        offset: int = 0,
        tenant_id: str | None = None,
        project_id: str | None = None,
        q: str | None = None,
    ) -> dict:
        allowed_statuses = {
            "suspected", "replaying", "confirmed", "rejected", "needs_review"
        }
        if status is not None and status not in allowed_statuses:
            raise ValueError(f"Unsupported incident status: {status}")
        if not 1 <= limit <= 500:
            raise ValueError("Incident limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("Incident offset must be non-negative")

        clauses = []
        parameters: list[object] = []
        scope = ""
        scope_parameters = []
        if tenant_id is not None and project_id is not None:
            scope = "d.trace_id IN (SELECT trace_id FROM ragproof_traces WHERE tenant_id = %s AND project_id = %s)"
            scope_parameters = [tenant_id, project_id]
            clauses.append(scope)
            parameters.extend(scope_parameters)
        if status:
            clauses.append("d.lifecycle_status = %s")
            parameters.append(status)
        if category:
            clauses.append("d.primary_category = %s")
            parameters.append(category)
        if q:
            clauses.append("(d.diagnosis_id ILIKE %s OR d.trace_id ILIKE %s OR d.primary_category ILIKE %s OR d.primary_component ILIKE %s)")
            parameters.extend([f"%{q}%"] * 4)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            summary_rows = connection.execute(
                f"""
                SELECT lifecycle_status AS status, count(*) AS count
                FROM ragproof_diagnoses d {f'WHERE {scope}' if scope else ''} GROUP BY lifecycle_status
                ORDER BY lifecycle_status
                """, scope_parameters,
            ).fetchall()
            category_rows = connection.execute(
                f"""
                SELECT primary_category AS category, count(*) AS count
                FROM ragproof_diagnoses d {f'WHERE {scope}' if scope else ''} GROUP BY primary_category
                ORDER BY count(*) DESC, primary_category
                """, scope_parameters,
            ).fetchall()
            total = connection.execute(
                f"SELECT count(*) AS count FROM ragproof_diagnoses d {where}",
                parameters,
            ).fetchone()["count"]
            rows = connection.execute(
                f"""
                SELECT d.diagnosis_id, d.trace_id, d.response_id,
                       d.lifecycle_status AS status,
                       d.primary_category AS category,
                       d.primary_component AS component,
                       d.primary_confidence AS confidence,
                       d.created_at,
                       rp.replay_id, rp.outcome AS replay_outcome,
                       rc.case_id AS regression_case_id,
                       COALESCE(claims.claim_count, 0) AS claim_count,
                       COALESCE(claims.verdict_counts, '{{}}'::jsonb) AS verdict_counts
                FROM ragproof_diagnoses d
                LEFT JOIN ragproof_replays rp ON rp.diagnosis_id = d.diagnosis_id
                LEFT JOIN ragproof_regression_cases rc ON rc.replay_id = rp.replay_id
                LEFT JOIN LATERAL (
                    SELECT count(*) AS claim_count,
                           jsonb_object_agg(verdict, verdict_count) AS verdict_counts
                    FROM (
                        SELECT c.verdict, count(*) AS verdict_count
                        FROM ragproof_verifications v
                        JOIN ragproof_claims c ON c.verification_id = v.verification_id
                        WHERE v.response_id = d.response_id
                        GROUP BY c.verdict
                    ) verdicts
                ) claims ON true
                {where}
                ORDER BY d.created_at DESC, d.diagnosis_id
                LIMIT %s OFFSET %s
                """,
                [*parameters, limit, offset],
            ).fetchall()

        incidents = []
        for row in rows:
            item = {
                "diagnosis_id": row["diagnosis_id"],
                "trace_id": row["trace_id"],
                "response_id": row["response_id"],
                "status": row["status"],
                "category": row["category"],
                "component": row["component"],
                "confidence": row["confidence"],
                "created_at": row["created_at"].isoformat(),
                "claim_count": row["claim_count"],
                "verdict_counts": row["verdict_counts"],
            }
            if row["replay_id"]:
                item["replay"] = {
                    "replay_id": row["replay_id"],
                    "outcome": row["replay_outcome"],
                }
            if row["regression_case_id"]:
                item["regression_case_id"] = row["regression_case_id"]
            incidents.append(item)
        status_counts = {item: 0 for item in sorted(allowed_statuses)}
        status_counts.update({row["status"]: row["count"] for row in summary_rows})
        return {
            "schema_version": "0.1.0",
            "summary": {
                "total": sum(status_counts.values()),
                "status_counts": status_counts,
                "category_counts": {
                    row["category"]: row["count"] for row in category_rows
                },
            },
            "page": {"total": total, "limit": limit, "offset": offset},
            "incidents": incidents,
        }

    def incident_detail(self, diagnosis_id: str) -> dict | None:
        diagnosis = self.get_diagnosis_by_id(diagnosis_id)
        if diagnosis is None:
            return None
        with psycopg.connect(self.settings.postgres_dsn, row_factory=dict_row) as connection:
            identity = connection.execute(
                """
                SELECT response_id FROM ragproof_diagnoses WHERE diagnosis_id = %s
                """,
                (diagnosis_id,),
            ).fetchone()
            response_id = identity["response_id"]
            verification_row = connection.execute(
                """
                SELECT status, result_ref, blocked_reason
                FROM ragproof_verifications
                WHERE response_id = %s ORDER BY created_at DESC LIMIT 1
                """,
                (response_id,),
            ).fetchone()
            history = connection.execute(
                """
                SELECT status, replay_id, changed_at
                FROM ragproof_diagnosis_status_history
                WHERE diagnosis_id = %s ORDER BY sequence
                """,
                (diagnosis_id,),
            ).fetchall()
            impact_rows = connection.execute(
                """
                SELECT impact_id, result_ref FROM ragproof_impact_reports
                ORDER BY created_at DESC
                """
            ).fetchall()

        verification = None
        if verification_row:
            if verification_row["result_ref"]:
                stored_verification = json.loads(
                    self.objects.get_text(verification_row["result_ref"])
                )
                verification = {
                    "status": "completed",
                    "trace_id": stored_verification["trace_id"],
                    "response_id": stored_verification["response_id"],
                    "evaluator": stored_verification["evaluator"],
                    "claims": [
                        {
                            "claim_id": claim["claim_id"],
                            "verdict": claim["verdict"],
                            "confidence": claim["confidence"],
                            "evidence": claim["evidence"],
                            "judge_disagreement": claim.get(
                                "judge_disagreement", False
                            ),
                        }
                        for claim in stored_verification["claims"]
                    ],
                }
            else:
                verification = {
                    "status": verification_row["status"],
                    "blocked_reason": verification_row["blocked_reason"],
                }
        replay = self.get_replay_for_diagnosis(diagnosis_id)
        regression = (
            self.get_regression_for_replay(replay["replay_id"])
            if replay is not None else None
        )
        trace_id = diagnosis["trace_id"]
        impacts = []
        for row in impact_rows:
            report = json.loads(self.objects.get_text(row["result_ref"]))
            affected = next(
                (
                    item for item in report["affected_traces"]
                    if item["trace_id"] == trace_id
                ),
                None,
            )
            if affected:
                impacts.append(
                    {
                        "impact_id": row["impact_id"],
                        "document_id": report["document_id"],
                        "from_version": report["change"]["from_version"],
                        "to_version": report["change"]["to_version"],
                        "usage_modes": affected["usage_modes"],
                    }
                )
        return {
            "schema_version": "0.1.0",
            "diagnosis": diagnosis,
            "verification": verification,
            "replay": replay,
            "regression_case": regression,
            "lineage": self.answer_lineage(response_id),
            "impact_reports": impacts,
            "timeline": [
                {
                    "status": row["status"],
                    "replay_id": row["replay_id"],
                    "changed_at": row["changed_at"].isoformat(),
                }
                for row in history
            ],
        }

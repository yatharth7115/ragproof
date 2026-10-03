CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS ragproof_tenants (
    tenant_id text PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ragproof_audit_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    actor_hash text NOT NULL,
    action text NOT NULL,
    target text NOT NULL,
    status_code integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ragproof_projects (
    tenant_id text NOT NULL REFERENCES ragproof_tenants (tenant_id),
    project_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, project_id)
);

CREATE TABLE IF NOT EXISTS ragproof_traces (
    trace_id text PRIMARY KEY,
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    started_at timestamptz NOT NULL,
    ended_at timestamptz NOT NULL,
    status text NOT NULL,
    source_provider text NOT NULL,
    pipeline_fingerprint char(64) NOT NULL,
    corpus_version text NOT NULL,
    index_version text NOT NULL,
    capture_mode text NOT NULL,
    canonical_trace jsonb NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, project_id)
        REFERENCES ragproof_projects (tenant_id, project_id),
    CHECK (capture_mode IN ('full', 'redacted', 'metadata_only'))
);

CREATE INDEX IF NOT EXISTS ragproof_traces_project_started_idx
    ON ragproof_traces (tenant_id, project_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ragproof_traces_pipeline_idx
    ON ragproof_traces (tenant_id, project_id, pipeline_fingerprint);

CREATE TABLE IF NOT EXISTS ragproof_spans (
    trace_id text NOT NULL REFERENCES ragproof_traces (trace_id) ON DELETE CASCADE,
    span_id char(16) NOT NULL,
    parent_span_id char(16),
    name text NOT NULL,
    started_at timestamptz NOT NULL,
    ended_at timestamptz NOT NULL,
    status text NOT NULL,
    attributes jsonb NOT NULL,
    PRIMARY KEY (trace_id, span_id)
);

CREATE INDEX IF NOT EXISTS ragproof_spans_name_idx
    ON ragproof_spans (name);

CREATE TABLE IF NOT EXISTS ragproof_queries (
    trace_id text PRIMARY KEY REFERENCES ragproof_traces (trace_id) ON DELETE CASCADE,
    content_hash char(64) NOT NULL,
    content_ref text
);

CREATE TABLE IF NOT EXISTS ragproof_responses (
    response_id text PRIMARY KEY,
    trace_id text NOT NULL UNIQUE REFERENCES ragproof_traces (trace_id) ON DELETE CASCADE,
    content_hash char(64) NOT NULL,
    content_ref text,
    model_provider text NOT NULL,
    model_name text NOT NULL,
    input_tokens integer,
    output_tokens integer
);

CREATE TABLE IF NOT EXISTS ragproof_documents (
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    document_id text NOT NULL,
    data_source_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, project_id, document_id),
    FOREIGN KEY (tenant_id, project_id)
        REFERENCES ragproof_projects (tenant_id, project_id)
);

CREATE TABLE IF NOT EXISTS ragproof_document_versions (
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    document_id text NOT NULL,
    document_version text NOT NULL,
    first_seen_trace_id text NOT NULL REFERENCES ragproof_traces (trace_id),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, project_id, document_id, document_version),
    FOREIGN KEY (tenant_id, project_id, document_id)
        REFERENCES ragproof_documents (tenant_id, project_id, document_id)
);

CREATE TABLE IF NOT EXISTS ragproof_chunks (
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    chunk_id text NOT NULL,
    document_id text NOT NULL,
    document_version text NOT NULL,
    content_hash char(64) NOT NULL,
    content_ref text,
    embedding vector(64),
    first_seen_trace_id text NOT NULL REFERENCES ragproof_traces (trace_id),
    PRIMARY KEY (tenant_id, project_id, chunk_id, content_hash),
    FOREIGN KEY (tenant_id, project_id, document_id, document_version)
        REFERENCES ragproof_document_versions (
            tenant_id, project_id, document_id, document_version
        )
);

CREATE INDEX IF NOT EXISTS ragproof_chunks_document_idx
    ON ragproof_chunks (tenant_id, project_id, document_id, document_version);
CREATE INDEX IF NOT EXISTS ragproof_chunks_embedding_hnsw
    ON ragproof_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS ragproof_retrieval_candidates (
    trace_id text NOT NULL REFERENCES ragproof_traces (trace_id) ON DELETE CASCADE,
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    chunk_id text NOT NULL,
    content_hash char(64) NOT NULL,
    content_ref text,
    retrieval_rank integer NOT NULL,
    retrieval_score double precision,
    rerank_rank integer,
    rerank_score double precision,
    included_in_context boolean NOT NULL,
    PRIMARY KEY (trace_id, chunk_id),
    FOREIGN KEY (tenant_id, project_id, chunk_id, content_hash)
        REFERENCES ragproof_chunks (tenant_id, project_id, chunk_id, content_hash)
);

ALTER TABLE ragproof_retrieval_candidates
    ADD COLUMN IF NOT EXISTS content_ref text;

CREATE INDEX IF NOT EXISTS ragproof_candidates_chunk_idx
    ON ragproof_retrieval_candidates (tenant_id, project_id, chunk_id);

CREATE TABLE IF NOT EXISTS ragproof_citations (
    response_id text NOT NULL REFERENCES ragproof_responses (response_id) ON DELETE CASCADE,
    ordinal integer NOT NULL,
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    chunk_id text NOT NULL,
    content_hash char(64) NOT NULL,
    content_ref text,
    PRIMARY KEY (response_id, ordinal),
    FOREIGN KEY (tenant_id, project_id, chunk_id, content_hash)
        REFERENCES ragproof_chunks (tenant_id, project_id, chunk_id, content_hash)
);

CREATE INDEX IF NOT EXISTS ragproof_citations_chunk_idx
    ON ragproof_citations (tenant_id, project_id, chunk_id);

CREATE TABLE IF NOT EXISTS ragproof_verifications (
    verification_id char(64) PRIMARY KEY,
    trace_id text NOT NULL REFERENCES ragproof_traces (trace_id) ON DELETE CASCADE,
    response_id text NOT NULL REFERENCES ragproof_responses (response_id) ON DELETE CASCADE,
    evaluator_name text NOT NULL,
    evaluator_version text NOT NULL,
    status text NOT NULL,
    result_hash char(64),
    result_ref text,
    blocked_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (response_id, evaluator_name, evaluator_version),
    CHECK (status IN ('completed', 'blocked_content_unavailable')),
    CHECK (
        (status = 'completed' AND result_hash IS NOT NULL AND result_ref IS NOT NULL)
        OR
        (status = 'blocked_content_unavailable' AND blocked_reason IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS ragproof_claims (
    claim_id text PRIMARY KEY,
    verification_id char(64) NOT NULL
        REFERENCES ragproof_verifications (verification_id) ON DELETE CASCADE,
    ordinal integer NOT NULL,
    text_hash char(64) NOT NULL,
    verdict text NOT NULL,
    confidence double precision NOT NULL,
    judge_disagreement boolean NOT NULL DEFAULT false,
    UNIQUE (verification_id, ordinal),
    CHECK (verdict IN (
        'SUPPORTED', 'PARTIALLY_SUPPORTED', 'UNSUPPORTED', 'CONTRADICTED',
        'INSUFFICIENT_CONTEXT', 'STALE_EVIDENCE'
    )),
    CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE TABLE IF NOT EXISTS ragproof_claim_evidence (
    claim_id text NOT NULL REFERENCES ragproof_claims (claim_id) ON DELETE CASCADE,
    trace_id text NOT NULL,
    chunk_id text NOT NULL,
    relation text NOT NULL,
    document_version text NOT NULL,
    freshness text NOT NULL,
    PRIMARY KEY (claim_id, chunk_id),
    FOREIGN KEY (trace_id, chunk_id)
        REFERENCES ragproof_retrieval_candidates (trace_id, chunk_id),
    CHECK (relation IN ('supports', 'partially_supports', 'contradicts', 'mentions')),
    CHECK (freshness IN ('current', 'stale', 'unknown'))
);

CREATE INDEX IF NOT EXISTS ragproof_claims_verdict_idx
    ON ragproof_claims (verdict);
CREATE INDEX IF NOT EXISTS ragproof_claim_evidence_chunk_idx
    ON ragproof_claim_evidence (trace_id, chunk_id);

CREATE TABLE IF NOT EXISTS ragproof_diagnoses (
    diagnosis_id char(64) PRIMARY KEY,
    trace_id text NOT NULL REFERENCES ragproof_traces (trace_id) ON DELETE CASCADE,
    response_id text NOT NULL REFERENCES ragproof_responses (response_id) ON DELETE CASCADE,
    diagnoser_name text NOT NULL,
    diagnoser_version text NOT NULL,
    lifecycle_status text NOT NULL,
    primary_category text NOT NULL,
    primary_component text NOT NULL,
    primary_confidence double precision NOT NULL,
    result_hash char(64) NOT NULL,
    result_ref text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (response_id, diagnoser_name, diagnoser_version),
    CHECK (lifecycle_status IN (
        'suspected', 'replaying', 'confirmed', 'rejected', 'needs_review'
    )),
    CHECK (primary_confidence >= 0 AND primary_confidence <= 1)
);

CREATE TABLE IF NOT EXISTS ragproof_diagnosis_claims (
    diagnosis_id char(64) NOT NULL
        REFERENCES ragproof_diagnoses (diagnosis_id) ON DELETE CASCADE,
    claim_id text NOT NULL REFERENCES ragproof_claims (claim_id) ON DELETE CASCADE,
    PRIMARY KEY (diagnosis_id, claim_id)
);

CREATE TABLE IF NOT EXISTS ragproof_diagnosis_hypotheses (
    diagnosis_id char(64) NOT NULL
        REFERENCES ragproof_diagnoses (diagnosis_id) ON DELETE CASCADE,
    rank integer NOT NULL,
    category text NOT NULL,
    component text NOT NULL,
    confidence double precision NOT NULL,
    PRIMARY KEY (diagnosis_id, rank),
    CHECK (rank >= 1),
    CHECK (confidence >= 0 AND confidence <= 1)
);

CREATE INDEX IF NOT EXISTS ragproof_diagnoses_category_idx
    ON ragproof_diagnoses (primary_category, lifecycle_status);

CREATE TABLE IF NOT EXISTS ragproof_replays (
    replay_id char(64) PRIMARY KEY,
    diagnosis_id char(64) NOT NULL
        REFERENCES ragproof_diagnoses (diagnosis_id) ON DELETE CASCADE,
    original_trace_id text NOT NULL REFERENCES ragproof_traces (trace_id),
    baseline_trace_id text NOT NULL REFERENCES ragproof_traces (trace_id),
    candidate_trace_id text NOT NULL REFERENCES ragproof_traces (trace_id),
    changed_component text NOT NULL,
    outcome text NOT NULL,
    result_hash char(64) NOT NULL,
    result_ref text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (diagnosis_id),
    CHECK (outcome IN ('CONFIRMED', 'REJECTED', 'NEEDS_REVIEW'))
);

CREATE TABLE IF NOT EXISTS ragproof_diagnosis_status_history (
    diagnosis_id char(64) NOT NULL
        REFERENCES ragproof_diagnoses (diagnosis_id) ON DELETE CASCADE,
    sequence bigint GENERATED ALWAYS AS IDENTITY,
    status text NOT NULL,
    replay_id char(64) REFERENCES ragproof_replays (replay_id),
    changed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (diagnosis_id, sequence),
    CHECK (status IN (
        'suspected', 'replaying', 'confirmed', 'rejected', 'needs_review'
    ))
);

CREATE TABLE IF NOT EXISTS ragproof_document_changes (
    change_id char(64) PRIMARY KEY,
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    document_id text NOT NULL,
    from_version text NOT NULL,
    to_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, project_id, document_id)
        REFERENCES ragproof_documents (tenant_id, project_id, document_id)
);

CREATE TABLE IF NOT EXISTS ragproof_impact_reports (
    impact_id char(64) PRIMARY KEY,
    change_id char(64) NOT NULL REFERENCES ragproof_document_changes (change_id),
    affected_trace_count integer NOT NULL,
    affected_claim_count integer NOT NULL,
    result_hash char(64) NOT NULL,
    result_ref text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ragproof_regression_cases (
    case_id char(64) PRIMARY KEY,
    replay_id char(64) NOT NULL UNIQUE REFERENCES ragproof_replays (replay_id),
    diagnosis_id char(64) NOT NULL REFERENCES ragproof_diagnoses (diagnosis_id),
    category text NOT NULL,
    original_trace_id text NOT NULL REFERENCES ragproof_traces (trace_id),
    passing_trace_id text NOT NULL REFERENCES ragproof_traces (trace_id),
    query_hash char(64) NOT NULL,
    query_ref text,
    result_hash char(64) NOT NULL,
    result_ref text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ragproof_regression_cases_category_idx
    ON ragproof_regression_cases (category);

CREATE TABLE IF NOT EXISTS ragproof_quality_gate_runs (
    gate_run_id char(32) PRIMARY KEY,
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    candidate_name text NOT NULL,
    candidate_pipeline_fingerprint char(64),
    outcome text NOT NULL,
    total_cases integer NOT NULL,
    passed_cases integer NOT NULL,
    failed_cases integer NOT NULL,
    pass_rate double precision NOT NULL,
    result_hash char(64) NOT NULL,
    result_ref text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, project_id)
        REFERENCES ragproof_projects (tenant_id, project_id),
    CHECK (outcome IN ('PASS', 'FAIL')),
    CHECK (total_cases >= 0),
    CHECK (passed_cases >= 0),
    CHECK (failed_cases >= 0),
    CHECK (pass_rate >= 0 AND pass_rate <= 1)
);

CREATE INDEX IF NOT EXISTS ragproof_quality_gate_runs_project_idx
    ON ragproof_quality_gate_runs (tenant_id, project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ragproof_quality_gate_case_results (
    gate_run_id char(32) NOT NULL
        REFERENCES ragproof_quality_gate_runs (gate_run_id) ON DELETE CASCADE,
    case_id char(64) NOT NULL REFERENCES ragproof_regression_cases (case_id),
    category text NOT NULL,
    candidate_trace_id text REFERENCES ragproof_traces (trace_id),
    passed boolean NOT NULL,
    failures jsonb NOT NULL,
    PRIMARY KEY (gate_run_id, case_id)
);

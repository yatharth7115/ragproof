CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS lab_document_versions (
    document_id text NOT NULL,
    version text NOT NULL,
    current_version text NOT NULL,
    title text NOT NULL,
    effective_at timestamptz NOT NULL,
    content text NOT NULL,
    content_hash text NOT NULL,
    PRIMARY KEY (document_id, version)
);

CREATE TABLE IF NOT EXISTS lab_chunks (
    chunk_id text PRIMARY KEY,
    document_id text NOT NULL,
    document_version text NOT NULL,
    block_id text NOT NULL,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    content_hash text NOT NULL,
    embedding vector(64),
    FOREIGN KEY (document_id, document_version)
        REFERENCES lab_document_versions (document_id, version)
);

CREATE INDEX IF NOT EXISTS lab_chunks_embedding_hnsw
    ON lab_chunks USING hnsw (embedding vector_cosine_ops);

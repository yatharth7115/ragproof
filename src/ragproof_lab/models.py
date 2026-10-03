from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class DocumentVersion:
    document_id: str
    version: str
    current_version: str
    title: str
    effective_at: str
    content: str

    @property
    def is_current(self) -> bool:
        return self.version == self.current_version


@dataclass(frozen=True)
class ParsedBlock:
    block_id: str
    document_id: str
    document_version: str
    text: str
    source_line: int


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document_id: str
    document_version: str
    text: str
    block_id: str
    chunk_index: int


@dataclass(frozen=True)
class Candidate:
    chunk: Chunk
    lexical_score: float
    vector_score: float
    hybrid_score: float
    retrieval_rank: int
    rerank_score: float | None = None
    rerank_rank: int | None = None


@dataclass(frozen=True)
class Answer:
    text: str
    cited_chunk_id: str | None
    abstained: bool


@dataclass(frozen=True)
class PipelineVersions:
    parser: str
    chunker: str
    embedder: str
    retriever: str
    reranker: str
    prompt: str
    generator: str
    citation: str
    corpus: str


@dataclass
class RunArtifacts:
    scenario: str
    injected_fault: str | None
    query: str
    expected_answer: str
    expected_evidence: str
    expected_document_id: str
    current_document_version: str
    pipeline_versions: PipelineVersions
    source_documents: list[DocumentVersion] = field(default_factory=list)
    parsed_blocks: list[ParsedBlock] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    retrieved_candidates: list[Candidate] = field(default_factory=list)
    reranked_candidates: list[Candidate] = field(default_factory=list)
    context_chunks: list[Chunk] = field(default_factory=list)
    answer: Answer | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

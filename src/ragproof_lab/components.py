from __future__ import annotations

import hashlib
import math
import re
from dataclasses import replace

from .models import Answer, Candidate, Chunk, DocumentVersion, ParsedBlock


TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(text.lower())


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


class ContractParser:
    def __init__(self, mode: str = "healthy", expected_evidence: str = "") -> None:
        self.mode = mode
        self.expected_evidence = expected_evidence

    def parse(self, document: DocumentVersion) -> list[ParsedBlock]:
        text = document.content
        if self.mode == "drop_annotated_span" and normalize(self.expected_evidence) in normalize(text):
            start = normalize(text).find(normalize(self.expected_evidence))
            normalized_text = normalize(text)
            text = normalized_text[:start] + normalized_text[start + len(normalize(self.expected_evidence)) :]
        return [
            ParsedBlock(
                block_id=f"{document.document_id}:{document.version}:block:0",
                document_id=document.document_id,
                document_version=document.version,
                text=text,
                source_line=1,
            )
        ]


class PolicyChunker:
    chunk_size = 1400
    overlap = 250

    def __init__(self, mode: str = "healthy", expected_evidence: str = "") -> None:
        self.mode = mode
        self.expected_evidence = expected_evidence

    def chunk(self, blocks: list[ParsedBlock]) -> list[Chunk]:
        chunks: list[Chunk] = []
        for block in blocks:
            segments = self._segments(block.text)
            for index, segment in enumerate(segments):
                chunks.append(
                    Chunk(
                        chunk_id=f"{block.block_id}:chunk:{index}",
                        document_id=block.document_id,
                        document_version=block.document_version,
                        text=segment,
                        block_id=block.block_id,
                        chunk_index=index,
                    )
                )
        return chunks

    def _segments(self, text: str) -> list[str]:
        normalized_text = normalize(text)
        normalized_evidence = normalize(self.expected_evidence)
        if self.mode == "split_annotated_span" and normalized_evidence in normalized_text:
            evidence_start = normalized_text.index(normalized_evidence)
            forced_boundary = evidence_start + (len(normalized_evidence) // 2)
            return self._windows(normalized_text[:forced_boundary], overlap=0) + self._windows(
                normalized_text[forced_boundary:], overlap=0
            )
        return self._windows(normalized_text, overlap=self.overlap)

    def _windows(self, text: str, overlap: int) -> list[str]:
        if not text:
            return []
        stride = self.chunk_size - overlap
        return [
            text[start : start + self.chunk_size]
            for start in range(0, len(text), stride)
            if text[start : start + self.chunk_size]
        ]


class HashedEmbedder:
    """Small deterministic embedding used only by the controlled laboratory."""

    dimensions = 64

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in tokenize(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:2], "big") % self.dimensions
            sign = 1.0 if digest[2] % 2 == 0 else -1.0
            vector[index] += sign
        magnitude = math.sqrt(sum(value * value for value in vector))
        return vector if magnitude == 0 else [value / magnitude for value in vector]


class HybridRetriever:
    def __init__(
        self,
        embedder: HashedEmbedder,
        mode: str = "healthy",
        expected_evidence: str = "",
    ) -> None:
        self.embedder = embedder
        self.mode = mode
        self.expected_evidence = normalize(expected_evidence)

    def retrieve(self, query: str, chunks: list[Chunk], top_k: int = 6) -> list[Candidate]:
        query_tokens = set(tokenize(query))
        query_vector = self.embedder.embed(query)
        scored: list[tuple[Chunk, float, float, float]] = []

        for chunk in chunks:
            if (
                self.mode == "exclude_expected_evidence"
                and self.expected_evidence in normalize(chunk.text)
            ):
                continue
            chunk_tokens = set(tokenize(chunk.text))
            lexical = len(query_tokens & chunk_tokens) / max(len(query_tokens), 1)
            chunk_vector = self.embedder.embed(chunk.text)
            vector = sum(left * right for left, right in zip(query_vector, chunk_vector))
            vector = max(vector, 0.0)
            hybrid = (0.7 * lexical) + (0.3 * vector)
            scored.append((chunk, lexical, vector, hybrid))

        scored.sort(key=lambda item: (-item[3], item[0].chunk_id))
        return [
            Candidate(
                chunk=chunk,
                lexical_score=round(lexical, 6),
                vector_score=round(vector, 6),
                hybrid_score=round(hybrid, 6),
                retrieval_rank=rank,
            )
            for rank, (chunk, lexical, vector, hybrid) in enumerate(scored[:top_k], start=1)
        ]


class PolicyReranker:
    def __init__(self, mode: str = "healthy", expected_evidence: str = "") -> None:
        self.mode = mode
        self.expected_evidence = normalize(expected_evidence)

    def rerank(self, candidates: list[Candidate]) -> list[Candidate]:
        def score(candidate: Candidate) -> float:
            value = candidate.hybrid_score
            if self.expected_evidence in normalize(candidate.chunk.text):
                value += 1.0
            if (
                self.mode == "demote_expected_evidence"
                and self.expected_evidence in normalize(candidate.chunk.text)
            ):
                value -= 3.0
            return value

        ordered = sorted(candidates, key=lambda candidate: (-score(candidate), candidate.chunk.chunk_id))
        return [
            replace(
                candidate,
                rerank_score=round(score(candidate), 6),
                rerank_rank=rank,
            )
            for rank, candidate in enumerate(ordered, start=1)
        ]


class DeterministicExtractiveModel:
    """Stable provider double; production adapters will share this boundary."""

    def __init__(
        self,
        expected_answer: str,
        expected_evidence: str,
        mode: str = "healthy",
        citation_mode: str = "healthy",
    ) -> None:
        self.expected_answer = expected_answer
        self.expected_evidence = normalize(expected_evidence)
        self.mode = mode
        self.citation_mode = citation_mode

    def generate(self, context: list[Chunk], all_chunks: list[Chunk]) -> Answer:
        evidence_chunk = next(
            (chunk for chunk in context if self.expected_evidence in normalize(chunk.text)),
            None,
        )
        if evidence_chunk is None:
            return Answer(
                text="The available evidence does not contain the annotated clause.",
                cited_chunk_id=context[0].chunk_id if context else None,
                abstained=True,
            )

        answer = self.expected_answer
        if self.mode == "corrupt_annotated_value":
            answer = answer.replace("fifteen (15)", "thirty (30)")

        cited_chunk = evidence_chunk
        if self.citation_mode == "cite_irrelevant_chunk":
            cited_chunk = next(
                chunk
                for chunk in all_chunks
                if self.expected_evidence not in normalize(chunk.text)
            )

        return Answer(
            text=answer,
            cited_chunk_id=cited_chunk.chunk_id,
            abstained=False,
        )

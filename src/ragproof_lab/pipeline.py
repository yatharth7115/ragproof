from __future__ import annotations

import hashlib

from .components import (
    ContractParser,
    DeterministicExtractiveModel,
    HashedEmbedder,
    HybridRetriever,
    PolicyChunker,
    PolicyReranker,
)
from .fixtures import load_case
from .models import PipelineVersions, RunArtifacts
from .scenarios import SCENARIOS, Scenario
from ragproof_otel import RagProofTracer


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def run_scenario(
    name: str,
    telemetry: RagProofTracer | None = None,
    capture_mode: str = "metadata_only",
) -> RunArtifacts:
    try:
        scenario = SCENARIOS[name]
    except KeyError as error:
        available = ", ".join(sorted(SCENARIOS))
        raise ValueError(f"Unknown scenario {name!r}. Available: {available}") from error

    return run_configuration(scenario, telemetry=telemetry, capture_mode=capture_mode)


def run_configuration(
    scenario: Scenario,
    telemetry: RagProofTracer | None = None,
    capture_mode: str = "metadata_only",
) -> RunArtifacts:

    telemetry = telemetry or RagProofTracer(capture_mode=capture_mode)
    case = load_case(scenario.corpus_mode)

    component_versions = {
        "parser": f"cuad-text:{scenario.parser_mode}",
        "chunker": f"character-window:{scenario.chunker_mode}",
        "embedder": "hashed-64:v1",
        "retriever": f"hybrid:{scenario.retriever_mode}",
        "reranker": f"policy:{scenario.reranker_mode}",
        "prompt": "cuad-clause-extraction:v1",
        "generator": f"deterministic-extractive:{scenario.generator_mode}",
        "citation": f"citation-linker:{scenario.citation_mode}",
        "corpus": f"cuad-v1:{scenario.corpus_mode}",
    }

    with telemetry.span(
        "rag.request",
        {
            "ragproof.tenant.id": "cuad-public",
            "ragproof.project.id": "cuad-contract-review",
            "ragproof.scenario.name": scenario.name,
            "ragproof.capture.mode": capture_mode,
            "gen_ai.data_source.id": "cuad-v1",
            "ragproof.knowledge_base.corpus_version": component_versions["corpus"],
            "ragproof.knowledge_base.index_version": "in-memory-hybrid:v1",
            "ragproof.content_retention_days": 0 if capture_mode == "metadata_only" else 30,
            **{
                f"ragproof.pipeline.{name}": version
                for name, version in component_versions.items()
                if name != "corpus"
            },
        },
    ) as root_span:
        documents = case.documents
        parser = ContractParser(scenario.parser_mode, case.expected_evidence)
        blocks = []
        with telemetry.span(
            "document.parse",
            {
                "ragproof.component.version": f"cuad-text:{scenario.parser_mode}",
                "ragproof.document.count": len(documents),
                "ragproof.document.ids": [document.document_id for document in documents],
            },
        ) as span:
            blocks = [block for document in documents for block in parser.parse(document)]
            span.set_attribute("ragproof.parsed_block.count", len(blocks))

        chunker = PolicyChunker(scenario.chunker_mode, case.expected_evidence)
        with telemetry.span(
            "document.chunk",
            {"ragproof.component.version": f"character-window:{scenario.chunker_mode}"},
        ) as span:
            chunks = chunker.chunk(blocks)
            span.set_attribute("ragproof.chunk.count", len(chunks))
            span.set_attribute("ragproof.chunk.ids", [chunk.chunk_id for chunk in chunks])

        embedder = HashedEmbedder()
        with telemetry.span(
            "embedding.create",
            {
                "gen_ai.operation.name": "embeddings",
                "gen_ai.provider.name": "ragproof-lab",
                "gen_ai.request.model": "hashed-64:v1",
                "ragproof.embedding.dimension": embedder.dimensions,
                "ragproof.chunk.count": len(chunks),
            },
        ):
            for chunk in chunks:
                embedder.embed(chunk.text)

        retriever = HybridRetriever(
            embedder,
            mode=scenario.retriever_mode,
            expected_evidence=case.expected_evidence,
        )
        with telemetry.span(
            "retrieval.search",
            {
                "ragproof.component.version": f"hybrid:{scenario.retriever_mode}",
                "ragproof.query.sha256": _digest(case.query),
                **telemetry.content_attributes("ragproof.query", case.query),
            },
        ) as span:
            retrieved = retriever.retrieve(case.query, chunks)
            span.set_attribute(
                "ragproof.retrieval.chunk_ids",
                [candidate.chunk.chunk_id for candidate in retrieved],
            )
            span.set_attribute(
                "ragproof.retrieval.scores",
                [candidate.hybrid_score for candidate in retrieved],
            )
            span.set_attribute(
                "ragproof.retrieval.document_ids",
                [candidate.chunk.document_id for candidate in retrieved],
            )
            span.set_attribute(
                "ragproof.retrieval.document_versions",
                [candidate.chunk.document_version for candidate in retrieved],
            )
            span.set_attribute(
                "ragproof.retrieval.content_sha256",
                [_digest(candidate.chunk.text) for candidate in retrieved],
            )
            if capture_mode != "metadata_only":
                span.set_attribute(
                    "ragproof.retrieval.contents",
                    [
                        candidate.chunk.text if capture_mode == "full" else "[REDACTED]"
                        for candidate in retrieved
                    ],
                )

        reranker = PolicyReranker(
            mode=scenario.reranker_mode,
            expected_evidence=case.expected_evidence,
        )
        with telemetry.span(
            "retrieval.rerank",
            {"ragproof.component.version": f"policy:{scenario.reranker_mode}"},
        ) as span:
            reranked = reranker.rerank(retrieved)
            span.set_attribute(
                "ragproof.rerank.chunk_ids",
                [candidate.chunk.chunk_id for candidate in reranked],
            )
            span.set_attribute(
                "ragproof.rerank.scores",
                [candidate.rerank_score for candidate in reranked],
            )

        with telemetry.span(
            "prompt.construct",
            {"ragproof.component.version": "cuad-clause-extraction:v1"},
        ) as span:
            context = [candidate.chunk for candidate in reranked[:1]]
            span.set_attribute(
                "ragproof.context.chunk_ids",
                [chunk.chunk_id for chunk in context],
            )
            span.set_attribute(
                "ragproof.context.sha256",
                _digest("\n".join(chunk.text for chunk in context)),
            )
            context_content = "\n".join(chunk.text for chunk in context)
            for key, value in telemetry.content_attributes(
                "ragproof.context", context_content
            ).items():
                span.set_attribute(key, value)

        generator = DeterministicExtractiveModel(
            expected_answer=case.expected_answer,
            expected_evidence=case.expected_evidence,
            mode=scenario.generator_mode,
            citation_mode=scenario.citation_mode,
        )
        with telemetry.span(
            "llm.generate",
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "ragproof-lab",
                "gen_ai.request.model": "deterministic-extractive:v1",
            },
        ) as span:
            answer = generator.generate(context, chunks)
            span.set_attribute("ragproof.response.sha256", _digest(answer.text))
            span.set_attribute("ragproof.response.abstained", answer.abstained)
            if answer.cited_chunk_id:
                span.set_attribute("ragproof.citation.chunk_id", answer.cited_chunk_id)
                cited = next(
                    chunk for chunk in chunks if chunk.chunk_id == answer.cited_chunk_id
                )
                span.set_attribute("ragproof.citation.document_id", cited.document_id)
                span.set_attribute("ragproof.citation.document_version", cited.document_version)
                span.set_attribute("ragproof.citation.content_sha256", _digest(cited.text))
                if capture_mode != "metadata_only":
                    span.set_attribute(
                        "ragproof.citation.content",
                        cited.text if capture_mode == "full" else "[REDACTED]",
                    )
            for key, value in telemetry.content_attributes(
                "ragproof.response", answer.text
            ).items():
                span.set_attribute(key, value)

        root_span.set_attribute("ragproof.dataset.name", "CUAD v1")
        root_span.set_attribute("ragproof.dataset.license", "CC BY 4.0")
        root_span.set_attribute("ragproof.document.primary_id", case.expected_document_id)
        root_span.set_attribute(
            "ragproof.document.current_version", case.current_document_version
        )
        root_span.set_attribute(
            "ragproof.ground_truth.expected_evidence_sha256", _digest(case.expected_evidence)
        )
        root_span.set_attribute(
            "ragproof.ground_truth.source_contains_evidence",
            any(case.expected_evidence.lower() in document.content.lower() for document in documents),
        )
        root_span.set_attribute(
            "ragproof.ground_truth.parse_contains_evidence",
            any(case.expected_evidence.lower() in block.text.lower() for block in blocks),
        )
        root_span.set_attribute(
            "ragproof.ground_truth.chunk_contains_evidence",
            any(case.expected_evidence.lower() in chunk.text.lower() for chunk in chunks),
        )
        root_span.set_attribute(
            "ragproof.ground_truth.retrieval_contains_evidence",
            any(
                case.expected_evidence.lower() in candidate.chunk.text.lower()
                for candidate in retrieved
            ),
        )
        root_span.set_attribute(
            "ragproof.ground_truth.context_contains_evidence",
            any(case.expected_evidence.lower() in chunk.text.lower() for chunk in context),
        )

        return RunArtifacts(
            scenario=scenario.name,
            injected_fault=scenario.injected_fault,
            query=case.query,
            expected_answer=case.expected_answer,
            expected_evidence=case.expected_evidence,
            expected_document_id=case.expected_document_id,
            current_document_version=case.current_document_version,
            pipeline_versions=PipelineVersions(
                **component_versions,
            ),
            source_documents=documents,
            parsed_blocks=blocks,
            chunks=chunks,
            retrieved_candidates=retrieved,
            reranked_candidates=reranked,
            context_chunks=context,
            answer=answer,
        )

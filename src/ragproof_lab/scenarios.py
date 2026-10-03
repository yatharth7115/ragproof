from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    name: str
    injected_fault: str | None
    parser_mode: str = "healthy"
    chunker_mode: str = "healthy"
    retriever_mode: str = "healthy"
    reranker_mode: str = "healthy"
    generator_mode: str = "healthy"
    citation_mode: str = "healthy"
    corpus_mode: str = "current"


SCENARIOS: dict[str, Scenario] = {
    "healthy": Scenario(name="healthy", injected_fault=None),
    "parsing_failure": Scenario(
        name="parsing_failure",
        injected_fault="PARSING_FAILURE",
        parser_mode="drop_annotated_span",
    ),
    "chunking_failure": Scenario(
        name="chunking_failure",
        injected_fault="CHUNKING_FAILURE",
        chunker_mode="split_annotated_span",
    ),
    "retrieval_failure": Scenario(
        name="retrieval_failure",
        injected_fault="RETRIEVAL_FAILURE",
        retriever_mode="exclude_expected_evidence",
    ),
    "reranking_failure": Scenario(
        name="reranking_failure",
        injected_fault="RERANKING_FAILURE",
        reranker_mode="demote_expected_evidence",
    ),
    "generation_failure": Scenario(
        name="generation_failure",
        injected_fault="GENERATION_FAILURE",
        generator_mode="corrupt_annotated_value",
    ),
    "citation_failure": Scenario(
        name="citation_failure",
        injected_fault="CITATION_FAILURE",
        citation_mode="cite_irrelevant_chunk",
    ),
    "stale_knowledge": Scenario(
        name="stale_knowledge",
        injected_fault="STALE_KNOWLEDGE",
        corpus_mode="stale",
    ),
}

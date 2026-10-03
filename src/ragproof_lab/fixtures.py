from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ragproof_resources import load_cuad_fixture

from .models import DocumentVersion


@dataclass(frozen=True)
class CuadCase:
    documents: list[DocumentVersion]
    query: str
    expected_answer: str
    expected_evidence: str
    expected_document_id: str
    current_document_version: str
    provenance: dict[str, str]


def stable_document_id(title: str) -> str:
    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:12]
    return f"cuad-contract-{digest}"


def load_case(corpus_mode: str = "current") -> CuadCase:
    fixture = load_cuad_fixture()
    primary_case = fixture["primary_case"]
    dataset_version = f"cuad-v1:{fixture['provenance']['archive_sha256'][:12]}"
    primary_document_id = stable_document_id(primary_case["title"])

    documents: list[DocumentVersion] = []
    query = ""
    expected_answer = ""
    for contract in fixture["contracts"]:
        document_id = stable_document_id(contract["title"])
        version = dataset_version
        current_version = dataset_version
        if corpus_mode == "stale" and document_id == primary_document_id:
            version = "cuad-v1:stale-index-snapshot"

        documents.append(
            DocumentVersion(
                document_id=document_id,
                version=version,
                current_version=current_version,
                title=contract["title"],
                effective_at="2021-03-11T00:00:00Z",
                content=contract["context"],
            )
        )

        if contract["title"] == primary_case["title"]:
            qa = next(item for item in contract["qas"] if item["id"] == primary_case["qa_id"])
            answer = qa["answers"][primary_case["answer_index"]]
            query = qa["question"]
            expected_answer = answer["text"]

    if not query or not expected_answer:
        raise RuntimeError("Pinned CUAD primary case is incomplete")

    return CuadCase(
        documents=documents,
        query=query,
        expected_answer=expected_answer,
        expected_evidence=expected_answer,
        expected_document_id=primary_document_id,
        current_document_version=dataset_version,
        provenance=fixture["provenance"],
    )

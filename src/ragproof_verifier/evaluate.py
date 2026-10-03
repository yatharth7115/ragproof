"""Reproducible support-preservation evaluation using unchanged CUAD annotations."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

from ragproof_lab.fixtures import stable_document_id
from ragproof_resources import load_cuad_fixture

from .extractor import AtomicClaimExtractor
from .verifier import ClaimVerifier, EvidenceCandidate


ARCHIVE_SHA256 = "f8161d18bea4e9c05e78fa6dda61c19c846fb8087ea969c172753bc2f45b999a"


def evaluate(contracts: list[dict], provenance: dict, limit: int | None = None) -> dict:
    if limit is not None and limit < 1:
        raise ValueError("Annotation limit must be positive")
    extractor = AtomicClaimExtractor()
    # This path evaluates evidence already present in the licensed dataset;
    # it deliberately does not need the storage services or an LLM judge.
    verifier = ClaimVerifier(None)
    counts: Counter = Counter()
    annotations = []
    questions = set()
    contracts_seen = set()
    invalid_offsets = 0
    stop = False
    for contract in contracts:
        document_id = stable_document_id(contract["title"])
        evidence = [EvidenceCandidate(
            chunk_id=document_id, document_id=document_id,
            document_version=f"cuad-v1:{ARCHIVE_SHA256[:12]}",
            content=contract["context"], freshness="current",
        )]
        for qa in contract["qas"]:
            for index, answer in enumerate(qa["answers"]):
                if limit is not None and len(annotations) >= limit:
                    stop = True
                    break
                text = answer["text"]
                start = answer["answer_start"]
                offset_valid = contract["context"][start:start + len(text)] == text
                invalid_offsets += not offset_valid
                verdicts = [
                    verifier._verify_claim(qa["id"], ordinal, claim, evidence)["verdict"]
                    for ordinal, claim in enumerate(extractor.extract(text), start=1)
                ]
                counts.update(verdicts)
                annotations.append({
                    "question_id": qa["id"], "answer_index": index,
                    "answer_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "original_offset_valid": offset_valid, "verdicts": verdicts,
                    "preserved": bool(verdicts) and all(value == "SUPPORTED" for value in verdicts),
                })
                questions.add(qa["id"])
                contracts_seen.add(document_id)
            if stop:
                break
        if stop:
            break
    preserved = sum(item["preserved"] for item in annotations)
    return {
        "schema_version": "0.1.0", "evaluation": "cuad-positive-support-preservation",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "evaluator": {"name": verifier.name, "version": verifier.version},
        "provenance": provenance,
        "scope": "Unchanged human-annotated answer text against its source contract. This measures support preservation, not hallucination precision, recall, or general semantic correctness.",
        "summary": {
            "contracts": len(contracts_seen), "questions_with_answers": len(questions),
            "annotations": len(annotations), "claims": sum(counts.values()),
            "verdicts": dict(sorted(counts.items())), "preserved_annotations": preserved,
            "support_preservation_rate": preserved / len(annotations) if annotations else 0.0,
            "invalid_source_offsets": invalid_offsets,
        },
        "annotation_results": annotations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Official CUAD v1 data.zip; pinned checksum is verified")
    parser.add_argument("--limit-annotations", type=int)
    parser.add_argument("--details", action="store_true", help="Include answer hashes and per-annotation verdicts")
    parser.add_argument("--minimum-preservation", type=float, default=1.0)
    args = parser.parse_args()
    if not 0 <= args.minimum_preservation <= 1:
        parser.error("minimum-preservation must be between zero and one")
    fixture = load_cuad_fixture()
    if args.archive:
        data = args.archive.read_bytes()
        if hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
            parser.error("Archive checksum does not match the official pinned CUAD v1 release")
        with zipfile.ZipFile(args.archive) as archive:
            dataset = json.loads(archive.read("CUADv1.json"))
        contracts = [
            {"title": record["title"], "context": paragraph["context"], "qas": paragraph["qas"]}
            for record in dataset["data"] for paragraph in record["paragraphs"]
        ]
        provenance = {**fixture["provenance"], "transformation": "Full pinned CUAD v1; text and human annotations unchanged"}
    else:
        contracts, provenance = fixture["contracts"], fixture["provenance"]
    try:
        result = evaluate(contracts, provenance, args.limit_annotations)
    except ValueError as error:
        parser.error(str(error))
    summary = result["summary"]
    passed = (summary["annotations"] > 0 and summary["invalid_source_offsets"] == 0
              and summary["support_preservation_rate"] >= args.minimum_preservation)
    result["outcome"] = "PASS" if passed else "FAIL"
    result["minimum_preservation"] = args.minimum_preservation
    if not args.details:
        del result["annotation_results"]
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()

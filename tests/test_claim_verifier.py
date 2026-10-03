import unittest
import json
from pathlib import Path

from ragproof_verifier.extractor import AtomicClaimExtractor
from ragproof_verifier.verifier import ClaimVerifier, EvidenceCandidate


ROOT = Path(__file__).resolve().parents[1]


class AtomicClaimExtractorTests(unittest.TestCase):
    def test_preserves_real_cuad_legal_clause_as_one_claim(self) -> None:
        claim = (
            "Agreement, which notice must be given not less than fifteen (15) days "
            "before the end of the respective initial or renewal term."
        )

        self.assertEqual(AtomicClaimExtractor().extract(claim), [claim])

    def test_splits_multiple_sentences_without_rewriting_them(self) -> None:
        answer = "The term starts April 1, 1999. It continues for six (6) months."

        self.assertEqual(
            AtomicClaimExtractor().extract(answer),
            ["The term starts April 1, 1999.", "It continues for six (6) months."],
        )

    def test_all_real_cuad_annotations_remain_directly_supported(self) -> None:
        fixture = json.loads((ROOT / "fixtures" / "cuad_eval_subset.json").read_text())
        extractor = AtomicClaimExtractor()
        verifier = ClaimVerifier.__new__(ClaimVerifier)
        annotation_count = 0
        claim_count = 0

        for contract_index, contract in enumerate(fixture["contracts"]):
            evidence = [
                EvidenceCandidate(
                    chunk_id=f"real-cuad-contract-{contract_index}",
                    document_id=f"real-cuad-contract-{contract_index}",
                    document_version="cuad-v1:f8161d18bea4",
                    content=contract["context"],
                    freshness="current",
                )
            ]
            for qa in contract["qas"]:
                for answer in qa["answers"]:
                    annotation_count += 1
                    for ordinal, claim in enumerate(extractor.extract(answer["text"]), start=1):
                        claim_count += 1
                        result = verifier._verify_claim(
                            qa["id"], ordinal, claim, evidence
                        )
                        self.assertEqual(result["verdict"], "SUPPORTED", qa["id"])

        self.assertEqual(annotation_count, 96)
        self.assertGreaterEqual(claim_count, annotation_count)


if __name__ == "__main__":
    unittest.main()

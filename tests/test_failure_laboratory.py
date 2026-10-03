import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from ragproof_lab import SCENARIOS, run_scenario  # noqa: E402
from ragproof_lab.components import normalize  # noqa: E402


def contains_expected(items, expected: str, attribute: str = "text") -> bool:
    normalized_expected = normalize(expected)
    return any(normalized_expected in normalize(getattr(item, attribute)) for item in items)


class FailureLaboratoryTests(unittest.TestCase):
    def test_cuad_fixture_has_pinned_provenance_and_intact_annotation(self) -> None:
        fixture = json.loads((ROOT / "fixtures" / "cuad_eval_subset.json").read_text())
        provenance = fixture["provenance"]
        primary_case = fixture["primary_case"]
        contract = next(
            item for item in fixture["contracts"] if item["title"] == primary_case["title"]
        )
        qa = next(item for item in contract["qas"] if item["id"] == primary_case["qa_id"])
        answer = qa["answers"][primary_case["answer_index"]]

        self.assertEqual(provenance["dataset"], "Contract Understanding Atticus Dataset (CUAD) v1")
        self.assertEqual(provenance["license"], "CC BY 4.0")
        self.assertEqual(
            provenance["archive_sha256"],
            "f8161d18bea4e9c05e78fa6dda61c19c846fb8087ea969c172753bc2f45b999a",
        )
        self.assertEqual(
            contract["context"][answer["answer_start"] : answer["answer_start"] + len(answer["text"])],
            answer["text"],
        )

    def test_all_mvp_scenarios_exist(self) -> None:
        self.assertEqual(
            set(SCENARIOS),
            {
                "healthy",
                "parsing_failure",
                "chunking_failure",
                "retrieval_failure",
                "reranking_failure",
                "generation_failure",
                "citation_failure",
                "stale_knowledge",
            },
        )

    def test_healthy_pipeline_returns_current_grounded_answer(self) -> None:
        run = run_scenario("healthy")

        self.assertEqual(normalize(run.expected_answer), normalize(run.answer.text))
        self.assertIn("fifteen (15) days", run.answer.text.lower())
        self.assertFalse(run.answer.abstained)
        cited = next(chunk for chunk in run.chunks if chunk.chunk_id == run.answer.cited_chunk_id)
        self.assertIn(normalize(run.expected_evidence), normalize(cited.text))
        self.assertEqual(cited.document_version, run.current_document_version)

    def test_parsing_failure_has_source_evidence_but_no_parsed_evidence(self) -> None:
        run = run_scenario("parsing_failure")

        self.assertTrue(
            any(normalize(run.expected_evidence) in normalize(doc.content) for doc in run.source_documents)
        )
        self.assertFalse(contains_expected(run.parsed_blocks, run.expected_evidence))
        self.assertTrue(run.answer.abstained)

    def test_chunking_failure_has_parsed_evidence_but_no_complete_chunk(self) -> None:
        run = run_scenario("chunking_failure")

        self.assertTrue(contains_expected(run.parsed_blocks, run.expected_evidence))
        self.assertFalse(contains_expected(run.chunks, run.expected_evidence))
        self.assertTrue(run.answer.abstained)

    def test_retrieval_failure_has_indexed_evidence_but_does_not_retrieve_it(self) -> None:
        run = run_scenario("retrieval_failure")

        self.assertTrue(contains_expected(run.chunks, run.expected_evidence))
        self.assertFalse(
            contains_expected(
                [candidate.chunk for candidate in run.retrieved_candidates],
                run.expected_evidence,
            )
        )
        self.assertTrue(run.answer.abstained)

    def test_reranking_failure_removes_retrieved_evidence_from_context(self) -> None:
        run = run_scenario("reranking_failure")

        self.assertTrue(
            contains_expected(
                [candidate.chunk for candidate in run.retrieved_candidates],
                run.expected_evidence,
            )
        )
        self.assertFalse(contains_expected(run.context_chunks, run.expected_evidence))
        self.assertTrue(run.answer.abstained)

    def test_generation_failure_contradicts_evidence_present_in_context(self) -> None:
        run = run_scenario("generation_failure")

        self.assertTrue(contains_expected(run.context_chunks, run.expected_evidence))
        self.assertIn("thirty (30) days", run.answer.text.lower())
        self.assertNotEqual(normalize(run.expected_answer), normalize(run.answer.text))
        self.assertFalse(run.answer.abstained)

    def test_citation_failure_uses_non_supporting_chunk(self) -> None:
        run = run_scenario("citation_failure")

        self.assertEqual(normalize(run.expected_answer), normalize(run.answer.text))
        cited = next(chunk for chunk in run.chunks if chunk.chunk_id == run.answer.cited_chunk_id)
        self.assertNotIn(normalize(run.expected_evidence), normalize(cited.text))

    def test_stale_knowledge_uses_superseded_version(self) -> None:
        run = run_scenario("stale_knowledge")

        self.assertEqual(normalize(run.expected_answer), normalize(run.answer.text))
        cited = next(chunk for chunk in run.chunks if chunk.chunk_id == run.answer.cited_chunk_id)
        self.assertNotEqual(cited.document_version, run.current_document_version)

    def test_runs_are_deterministic(self) -> None:
        first = run_scenario("healthy").to_dict()
        second = run_scenario("healthy").to_dict()

        self.assertEqual(first, second)

    def test_cli_outputs_json(self) -> None:
        process = subprocess.run(
            [sys.executable, "-m", "ragproof_lab", "--scenario", "healthy"],
            cwd=ROOT,
            env={"PYTHONPATH": str(SRC)},
            check=True,
            capture_output=True,
            text=True,
        )

        payload = json.loads(process.stdout)
        self.assertEqual(payload["scenario"], "healthy")
        self.assertEqual(payload["injected_fault"], None)


if __name__ == "__main__":
    unittest.main()

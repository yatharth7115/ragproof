import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]


CONTRACTS = (
    ("canonical-trace.schema.json", "canonical-trace.json"),
    ("claim-verification.schema.json", "claim-verification.json"),
    ("diagnosis.schema.json", "diagnosis.json"),
    ("replay-result.schema.json", "replay-result.json"),
    ("change-impact.schema.json", "change-impact.json"),
    ("regression-case.schema.json", "regression-case.json"),
    ("incident-list.schema.json", "incident-list.json"),
    ("incident-detail.schema.json", "incident-detail.json"),
    ("quality-gate-result.schema.json", "quality-gate-result.json"),
)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ContractSchemaTests(unittest.TestCase):
    def test_contract_schemas_are_valid_draft_2020_12(self) -> None:
        for schema_name, _ in CONTRACTS:
            with self.subTest(schema=schema_name):
                schema = load_json(ROOT / "schemas" / schema_name)
                Draft202012Validator.check_schema(schema)

    def test_contract_examples_are_valid(self) -> None:
        for schema_name, example_name in CONTRACTS:
            with self.subTest(schema=schema_name, example=example_name):
                schema = load_json(ROOT / "schemas" / schema_name)
                example = load_json(ROOT / "examples" / example_name)

                validator = Draft202012Validator(
                    schema,
                    format_checker=FormatChecker(),
                )
                errors = sorted(
                    validator.iter_errors(example),
                    key=lambda error: list(error.path),
                )

                self.assertFalse(
                    errors,
                    "\n".join(error.message for error in errors),
                )

    def test_trace_schema_rejects_unknown_top_level_fields(self) -> None:
        schema = load_json(ROOT / "schemas" / "canonical-trace.schema.json")
        example = load_json(ROOT / "examples" / "canonical-trace.json")
        example["accidental_unversioned_field"] = True

        errors = list(Draft202012Validator(schema).iter_errors(example))

        self.assertTrue(errors)

    def test_diagnosis_rejects_unknown_status(self) -> None:
        schema = load_json(ROOT / "schemas" / "diagnosis.schema.json")
        example = load_json(ROOT / "examples" / "diagnosis.json")
        example["status"] = "probably"

        errors = list(Draft202012Validator(schema).iter_errors(example))

        self.assertTrue(errors)

    def test_example_lineage_references_are_consistent(self) -> None:
        trace = load_json(ROOT / "examples" / "canonical-trace.json")
        verification = load_json(ROOT / "examples" / "claim-verification.json")
        diagnosis = load_json(ROOT / "examples" / "diagnosis.json")

        retrieved_chunk_ids = {
            candidate["chunk_id"]
            for candidate in trace["retrieval"]["candidates"]
        }
        evidence_chunk_ids = {
            evidence["chunk_id"]
            for claim in verification["claims"]
            for evidence in claim["evidence"]
        }
        verified_claim_ids = {
            claim["claim_id"]
            for claim in verification["claims"]
        }

        self.assertEqual(trace["trace_id"], verification["trace_id"])
        self.assertEqual(trace["trace_id"], diagnosis["trace_id"])
        self.assertEqual(
            trace["generation"]["response_id"],
            verification["response_id"],
        )
        self.assertLessEqual(evidence_chunk_ids, retrieved_chunk_ids)
        self.assertLessEqual(set(diagnosis["claim_ids"]), verified_claim_ids)

    def test_tracked_examples_are_derived_from_cuad(self) -> None:
        trace = load_json(ROOT / "examples" / "canonical-trace.json")
        regression = load_json(ROOT / "examples" / "regression-case.json")
        incident = load_json(ROOT / "examples" / "incident-detail.json")

        self.assertEqual(trace["extensions"]["cuad"]["dataset"], "CUAD v1")
        self.assertEqual(trace["extensions"]["cuad"]["license"], "CC BY 4.0")
        self.assertEqual(
            trace["extensions"]["controlled_test"]["ground_truth_source"],
            "CUAD human annotation",
        )
        self.assertEqual(regression["source"]["dataset"], "CUAD v1")
        self.assertEqual(
            incident["lineage"]["query_hash"], trace["query"]["content_hash"]
        )


if __name__ == "__main__":
    unittest.main()

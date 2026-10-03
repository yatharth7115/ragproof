from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from ragproof_resources import load_schema
import uuid

from jsonschema import Draft202012Validator, FormatChecker
from opentelemetry import trace as otel_trace

from ragproof_diagnosis import RootCauseDiagnoser
from ragproof_lab.scenarios import Scenario
from ragproof_otel import capture_configuration
from ragproof_store import TraceRepository
from ragproof_verifier import ClaimVerifier


@dataclass(frozen=True)
class GatePolicy:
    minimum_cases: int = 1
    minimum_pass_rate: float = 1.0
    required_categories: tuple[str, ...] = ()

    def validate(self) -> None:
        if isinstance(self.minimum_cases, bool) or not isinstance(self.minimum_cases, int):
            raise ValueError("minimum_cases must be an integer")
        if self.minimum_cases < 1:
            raise ValueError("minimum_cases must be at least 1")
        if isinstance(self.minimum_pass_rate, bool) or not isinstance(
            self.minimum_pass_rate, (int, float)
        ):
            raise ValueError("minimum_pass_rate must be numeric")
        if not 0 <= self.minimum_pass_rate <= 1:
            raise ValueError("minimum_pass_rate must be between 0 and 1")
        if any(not isinstance(category, str) or not category for category in self.required_categories):
            raise ValueError("required_categories must contain non-empty strings")


class QualityGateRunner:
    """Evaluate one candidate against frozen cases from confirmed CUAD replays."""

    name = "ragproof-quality-gate"
    version = "confirmed-regressions-0.1.0"

    def __init__(
        self,
        repository: TraceRepository,
        verifier: ClaimVerifier | None = None,
        diagnoser: RootCauseDiagnoser | None = None,
        tracer=None,
    ) -> None:
        self.repository = repository
        self.verifier = verifier or ClaimVerifier(repository)
        self.diagnoser = diagnoser or RootCauseDiagnoser(repository, self.verifier)
        self.tracer = tracer or otel_trace.get_tracer("ragproof-gate", self.version)
        self.validator = Draft202012Validator(
            load_schema("quality-gate-result.schema.json"),
            format_checker=FormatChecker(),
        )
        self.regression_validator = Draft202012Validator(
            load_schema("regression-case.schema.json"),
            format_checker=FormatChecker(),
        )

    def run(
        self,
        candidate: Scenario,
        policy: GatePolicy | None = None,
        cases: list[dict] | None = None,
    ) -> dict:
        policy = policy or GatePolicy()
        policy.validate()
        self._validate_candidate(candidate)
        selected = self._select_cases(
            self.repository.list_regressions() if cases is None else cases
        )
        gate_run_id = uuid.uuid4().hex
        started_at = datetime.now(timezone.utc)

        with self.tracer.start_as_current_span(
            "ragproof.quality_gate.run",
            attributes={
                "ragproof.quality_gate.run_id": gate_run_id,
                "ragproof.quality_gate.version": self.version,
                "ragproof.quality_gate.candidate": candidate.name,
                "ragproof.quality_gate.case_count": len(selected),
            },
        ) as span:
            candidate_run = None
            candidate_verification = None
            citation_supported = None
            if selected:
                candidate_run = capture_configuration(candidate, capture_mode="full")
                trace = candidate_run.canonical_trace
                trace.setdefault("extensions", {})["quality_gate"] = {"gate_run_id": gate_run_id}
                stored = self.repository.ingest(trace)
                candidate_verification = self.verifier.evaluate_response(stored["response_id"])
                lineage = self.repository.answer_lineage(stored["response_id"])
                citation_supported = self.diagnoser.citation_state(
                    lineage, candidate_verification
                )

            results = [
                self._evaluate_case(
                    case, candidate_run.canonical_trace if candidate_run else None,
                    candidate_verification, citation_supported,
                )
                for case in selected
            ]
            passed_cases = sum(item["passed"] for item in results)
            total_cases = len(results)
            pass_rate = passed_cases / total_cases if total_cases else 0.0
            present_categories = {item["category"] for item in results}
            missing_categories = sorted(set(policy.required_categories) - present_categories)
            policy_failures = []
            if total_cases < policy.minimum_cases:
                policy_failures.append("minimum_cases_not_met")
            if pass_rate < policy.minimum_pass_rate:
                policy_failures.append("minimum_pass_rate_not_met")
            if missing_categories:
                policy_failures.append("required_categories_missing")

            trace = candidate_run.canonical_trace if candidate_run else None
            result = {
                "schema_version": "0.1.0",
                "gate_run_id": gate_run_id,
                "tenant_id": trace["tenant_id"] if trace else "cuad-public",
                "project_id": trace["project_id"] if trace else "cuad-contract-review",
                "started_at": started_at.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "gate": {"name": self.name, "version": self.version},
                "candidate": {
                    "name": candidate.name,
                    "configuration": asdict(candidate),
                    **(
                        {"pipeline_fingerprint": trace["pipeline"]["fingerprint"]}
                        if trace else {}
                    ),
                },
                "source": {
                    "dataset": "CUAD v1",
                    "license": "CC BY 4.0",
                    "selection": "newest_confirmed_case_per_query_and_category",
                },
                "policy": {
                    "minimum_cases": policy.minimum_cases,
                    "minimum_pass_rate": policy.minimum_pass_rate,
                    "required_categories": sorted(set(policy.required_categories)),
                },
                "summary": {
                    "total_cases": total_cases,
                    "passed_cases": passed_cases,
                    "failed_cases": total_cases - passed_cases,
                    "pass_rate": round(pass_rate, 6),
                    "present_categories": sorted(present_categories),
                    "missing_categories": missing_categories,
                    "policy_failures": policy_failures,
                },
                "case_results": results,
                "outcome": "PASS" if not policy_failures else "FAIL",
            }
            errors = sorted(
                self.validator.iter_errors(result), key=lambda error: list(error.path)
            )
            if errors:
                messages = "; ".join(error.message for error in errors)
                raise ValueError(f"Quality-gate contract violation: {messages}")
            stored_result = self.repository.save_quality_gate(result)
            span.set_attribute("ragproof.quality_gate.outcome", stored_result["outcome"])
            span.set_attribute("ragproof.quality_gate.failed_cases", total_cases - passed_cases)
            return stored_result

    def _select_cases(self, cases: list[dict]) -> list[dict]:
        selected = {}
        for case in cases:
            errors = sorted(
                self.regression_validator.iter_errors(case),
                key=lambda error: list(error.path),
            )
            if errors:
                messages = "; ".join(error.message for error in errors)
                raise ValueError(f"Regression-case contract violation: {messages}")
            if case.get("source") != {"dataset": "CUAD v1", "license": "CC BY 4.0"}:
                raise ValueError("Quality gate accepts only licensed CUAD v1 regression cases")
            replay_id = case["incident"]["replay_id"]
            replay = self.repository.get_replay(replay_id)
            if replay is None or replay.get("outcome") != "CONFIRMED":
                raise ValueError("Regression case is not backed by a persisted confirmed replay")
            key = (case["input"]["query_hash"], case["incident"]["category"])
            selected.setdefault(key, case)
        return [selected[key] for key in sorted(selected)]

    @staticmethod
    def _validate_candidate(candidate: Scenario) -> None:
        if not candidate.name:
            raise ValueError("candidate name must not be empty")
        allowed = {
            "parser_mode": {"healthy", "drop_annotated_span"},
            "chunker_mode": {"healthy", "split_annotated_span"},
            "retriever_mode": {"healthy", "exclude_expected_evidence"},
            "reranker_mode": {"healthy", "demote_expected_evidence"},
            "generator_mode": {"healthy", "corrupt_annotated_value"},
            "citation_mode": {"healthy", "cite_irrelevant_chunk"},
            "corpus_mode": {"current", "stale"},
        }
        for field, choices in allowed.items():
            value = getattr(candidate, field)
            if value not in choices:
                raise ValueError(
                    f"Unsupported {field} {value!r}; expected one of {', '.join(sorted(choices))}"
                )

    @staticmethod
    def _evaluate_case(
        case: dict,
        candidate_trace: dict | None,
        verification: dict | None,
        citation_supported: bool | None,
    ) -> dict:
        failures = []
        candidate_query_hash = candidate_trace["query"]["content_hash"] if candidate_trace else None
        verdicts = [claim["verdict"] for claim in verification.get("claims", [])] if verification else []
        expected = case["expectations"]
        acceptable = set(expected["acceptable_verdicts"])
        if candidate_query_hash != case["input"]["query_hash"]:
            failures.append("input_query_hash_mismatch")
        if not verdicts or any(verdict not in acceptable for verdict in verdicts):
            failures.append("unacceptable_claim_verdict")
        if expected["citation_must_support"] and citation_supported is not True:
            failures.append("citation_not_supported")
        return {
            "case_id": case["case_id"],
            "category": case["incident"]["category"],
            "query_hash": case["input"]["query_hash"],
            **(
                {
                    "candidate_trace_id": candidate_trace["trace_id"],
                    "candidate_pipeline_fingerprint": candidate_trace["pipeline"]["fingerprint"],
                }
                if candidate_trace else {}
            ),
            "verdicts": verdicts,
            "citation_supported": citation_supported,
            "passed": not failures,
            "failures": failures,
        }

from __future__ import annotations

import hashlib
import json
from ragproof_resources import load_schema

from jsonschema import Draft202012Validator, FormatChecker
from opentelemetry import trace as otel_trace

from ragproof_store import TraceRepository


class RegressionCaseBuilder:
    """Promote confirmed real-data replays into immutable regression cases."""

    name = "ragproof-confirmed-replay-regression-builder"
    version = "confirmed-replay-0.1.0"

    def __init__(self, repository: TraceRepository, tracer=None) -> None:
        self.repository = repository
        self.tracer = tracer or otel_trace.get_tracer("ragproof-regression", self.version)
        self.validator = Draft202012Validator(
            load_schema("regression-case.schema.json"),
            format_checker=FormatChecker(),
        )

    def create_from_replay(self, replay_id: str) -> dict:
        existing = self.repository.get_regression_for_replay(replay_id)
        if existing is not None:
            return existing
        replay = self.repository.get_replay(replay_id)
        if replay is None:
            raise KeyError(f"Unknown replay: {replay_id}")
        if replay["outcome"] != "CONFIRMED":
            raise ValueError(
                f"Only CONFIRMED replays can become regression cases; got {replay['outcome']}"
            )
        diagnosis = self.repository.get_diagnosis_by_id(replay["diagnosis_id"])
        if diagnosis is None:
            raise KeyError(f"Unknown diagnosis: {replay['diagnosis_id']}")
        original = self.repository.get_trace(replay["original_trace_id"])
        if original is None:
            raise KeyError(f"Unknown trace: {replay['original_trace_id']}")
        lineage = self.repository.answer_lineage(original["generation"]["response_id"])
        if lineage is None:
            raise KeyError(f"Missing lineage for trace: {replay['original_trace_id']}")
        source = original.get("extensions", {}).get("cuad", {})
        if source.get("dataset") != "CUAD v1" or not source.get("license"):
            raise ValueError("Stage 7 regression cases require a licensed real CUAD v1 source")

        with self.tracer.start_as_current_span(
            "ragproof.regression.create",
            attributes={
                "ragproof.replay.id": replay_id,
                "ragproof.diagnosis.id": replay["diagnosis_id"],
                "ragproof.trace.id": replay["original_trace_id"],
                "ragproof.regression.builder.version": self.version,
            },
        ) as span:
            case_id = hashlib.sha256(
                f"{replay_id}\0{self.version}".encode("utf-8")
            ).hexdigest()
            case = {
                "schema_version": "0.1.0",
                "case_id": case_id,
                "source": {
                    "dataset": source["dataset"],
                    "license": source["license"],
                },
                "incident": {
                    "diagnosis_id": replay["diagnosis_id"],
                    "replay_id": replay_id,
                    "category": diagnosis["primary_hypothesis"]["category"],
                },
                "input": {
                    "original_trace_id": replay["original_trace_id"],
                    "query_hash": lineage["query_hash"],
                    **({"query_ref": lineage["query_ref"]} if lineage["query_ref"] else {}),
                },
                "failure": self._run_reference(replay["baseline"]),
                "passing_reference": self._run_reference(replay["candidate"]),
                "expectations": {
                    "acceptable_verdicts": ["SUPPORTED"],
                    "citation_must_support": True,
                },
            }
            errors = sorted(
                self.validator.iter_errors(case), key=lambda error: list(error.path)
            )
            if errors:
                messages = "; ".join(error.message for error in errors)
                raise ValueError(f"Regression-case contract violation: {messages}")
            stored = self.repository.save_regression(case)
            span.set_attribute("ragproof.regression.case_id", stored["case_id"])
            span.set_attribute("ragproof.regression.category", stored["incident"]["category"])
            return stored

    def backfill_confirmed(self) -> list[dict]:
        """Promote every persisted confirmed replay not already represented."""
        return [
            self.create_from_replay(replay_id)
            for replay_id in self.repository.list_confirmed_replays_without_regressions()
        ]

    @staticmethod
    def _run_reference(summary: dict) -> dict:
        return {
            "trace_id": summary["trace_id"],
            "pipeline_fingerprint": summary["pipeline_fingerprint"],
            "response_hash": summary["response_hash"],
            "verdicts": summary["verdicts"],
        }

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from jsonschema import Draft202012Validator, FormatChecker
from opentelemetry import trace as otel_trace
from ragproof_lab.components import tokenize
from ragproof_resources import load_schema
from ragproof_store.repository import TraceRepository

from .extractor import AtomicClaimExtractor


UNIT_BOUNDARY = re.compile(r"(?<=[.!?;])\s+")
NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety", "hundred", "thousand",
}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized(value: str) -> str:
    return " ".join(tokenize(value))


def _numeric_tokens(value: str) -> set[str]:
    return {
        token for token in tokenize(value)
        if token.isdigit() or token in NUMBER_WORDS
    }


def _non_numeric_tokens(value: str) -> set[str]:
    return {
        token for token in tokenize(value)
        if not token.isdigit() and token not in NUMBER_WORDS
    }


def _coverage(claim: str, evidence: str) -> float:
    claim_tokens = set(tokenize(claim))
    if not claim_tokens:
        return 0.0
    return len(claim_tokens & set(tokenize(evidence))) / len(claim_tokens)


def _structural_similarity(claim: str, evidence: str) -> float:
    left = _non_numeric_tokens(claim)
    right = _non_numeric_tokens(evidence)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left)


@dataclass(frozen=True)
class EvidenceCandidate:
    chunk_id: str
    document_id: str
    document_version: str
    content: str
    freshness: str


class ClaimVerifier:
    name = "ragproof-claim-verifier"
    version = "lexical-numeric-0.1.1"

    def __init__(
        self,
        repository: TraceRepository,
        extractor: AtomicClaimExtractor | None = None,
        tracer=None,
    ) -> None:
        self.repository = repository
        self.extractor = extractor or AtomicClaimExtractor()
        self.tracer = tracer or otel_trace.get_tracer("ragproof-verifier", self.version)
        self.validator = Draft202012Validator(
            load_schema("claim-verification.schema.json"),
            format_checker=FormatChecker(),
        )

    def evaluate_response(self, response_id: str) -> dict:
        with self.tracer.start_as_current_span(
            "ragproof.evaluate",
            attributes={
                "ragproof.response.id": response_id,
                "ragproof.evaluator.name": self.name,
                "ragproof.evaluator.version": self.version,
            },
        ) as span:
            result = self._evaluate_response(response_id)
            span.set_attribute(
                "ragproof.evaluation.status", result.get("status", "completed")
            )
            claims = result.get("claims", [])
            span.set_attribute("ragproof.claim.count", len(claims))
            if claims:
                span.set_attribute(
                    "ragproof.claim.verdicts", [claim["verdict"] for claim in claims]
                )
            return result

    def _evaluate_response(self, response_id: str) -> dict:
        existing = self.repository.get_verification(response_id, self.name, self.version)
        if existing is not None:
            return existing

        lineage = self.repository.answer_lineage(response_id)
        if lineage is None:
            raise KeyError(f"Unknown response: {response_id}")
        trace = self.repository.get_trace(lineage["trace_id"])
        if trace is None:
            raise KeyError(f"Unknown trace: {lineage['trace_id']}")

        if not lineage["response_ref"]:
            return self.repository.save_blocked_verification(
                lineage["trace_id"], response_id, self.name, self.version,
                "The trace capture policy did not retain response content.",
            )

        answer = self.repository.objects.get_text(lineage["response_ref"])
        claims = self.extractor.extract(answer)
        if not claims:
            return self.repository.save_blocked_verification(
                lineage["trace_id"], response_id, self.name, self.version,
                "The captured response contains no extractable factual claim.",
            )

        evidence = self._load_context(trace, lineage)
        results = [
            self._verify_claim(response_id, index, claim, evidence)
            for index, claim in enumerate(claims, start=1)
        ]
        verification = {
            "schema_version": "0.1.0",
            "trace_id": lineage["trace_id"],
            "response_id": response_id,
            "evaluator": {"name": self.name, "version": self.version},
            "claims": results,
        }
        errors = sorted(
            self.validator.iter_errors(verification), key=lambda error: list(error.path)
        )
        if errors:
            messages = "; ".join(error.message for error in errors)
            raise ValueError(f"Claim verification contract violation: {messages}")
        return self.repository.save_verification(verification)

    def _load_context(self, trace: dict, lineage: dict) -> list[EvidenceCandidate]:
        current_version = trace.get("extensions", {}).get("cuad", {}).get(
            "current_document_version"
        )
        primary_document = trace.get("extensions", {}).get("cuad", {}).get(
            "primary_document_id"
        )
        evidence = []
        for candidate in lineage["candidates"]:
            if not candidate["included_in_context"] or not candidate["content_ref"]:
                continue
            if candidate["document_id"] == primary_document and current_version:
                freshness = (
                    "current"
                    if candidate["document_version"] == current_version
                    else "stale"
                )
            else:
                freshness = "unknown"
            evidence.append(
                EvidenceCandidate(
                    chunk_id=candidate["chunk_id"],
                    document_id=candidate["document_id"],
                    document_version=candidate["document_version"],
                    content=self.repository.objects.get_text(candidate["content_ref"]),
                    freshness=freshness,
                )
            )
        return evidence

    def _verify_claim(
        self,
        response_id: str,
        ordinal: int,
        claim: str,
        evidence: list[EvidenceCandidate],
    ) -> dict:
        claim_id = _digest(f"{response_id}:{ordinal}:{claim}")
        if not evidence:
            return self._result(
                claim_id, claim, "INSUFFICIENT_CONTEXT", 1.0, [],
                "No captured context content is available for this claim.",
            )

        normalized_claim = _normalized(claim)
        for candidate in evidence:
            if normalized_claim and normalized_claim in _normalized(candidate.content):
                verdict = "STALE_EVIDENCE" if candidate.freshness == "stale" else "SUPPORTED"
                return self._result(
                    claim_id,
                    claim,
                    verdict,
                    1.0,
                    [self._evidence(candidate, "supports")],
                    (
                        "The claim is directly present in captured evidence, but that document "
                        "version is superseded."
                        if verdict == "STALE_EVIDENCE"
                        else "The claim is directly present in the captured model context."
                    ),
                )

        best_candidate = None
        best_unit = ""
        best_structure = 0.0
        best_coverage = 0.0
        for candidate in evidence:
            units = [unit.strip() for unit in UNIT_BOUNDARY.split(candidate.content) if unit.strip()]
            for unit in units or [candidate.content]:
                structure = _structural_similarity(claim, unit)
                coverage = _coverage(claim, unit)
                if (structure, coverage) > (best_structure, best_coverage):
                    best_candidate = candidate
                    best_unit = unit
                    best_structure = structure
                    best_coverage = coverage

        claim_numbers = _numeric_tokens(claim)
        evidence_numbers = _numeric_tokens(best_unit)
        if (
            best_candidate
            and best_structure >= 0.72
            and claim_numbers
            and evidence_numbers
            and claim_numbers != evidence_numbers
        ):
            return self._result(
                claim_id,
                claim,
                "CONTRADICTED",
                round(min(0.99, 0.75 + (best_structure * 0.24)), 3),
                [self._evidence(best_candidate, "contradicts")],
                "A closely matching evidence statement contains conflicting numeric terms.",
            )

        if best_candidate and best_coverage >= 0.75:
            return self._result(
                claim_id,
                claim,
                "PARTIALLY_SUPPORTED",
                round(best_coverage, 3),
                [self._evidence(best_candidate, "partially_supports")],
                "Most claim terms appear in one context statement, but direct support is incomplete.",
            )

        evidence_items = (
            [self._evidence(best_candidate, "mentions")]
            if best_candidate and best_coverage >= 0.2
            else []
        )
        return self._result(
            claim_id,
            claim,
            "UNSUPPORTED",
            round(max(0.5, 1.0 - best_coverage), 3),
            evidence_items,
            "No captured context statement directly supports the claim.",
        )

    @staticmethod
    def _evidence(candidate: EvidenceCandidate, relation: str) -> dict:
        return {
            "chunk_id": candidate.chunk_id,
            "relation": relation,
            "document_version": candidate.document_version,
            "freshness": candidate.freshness,
        }

    @staticmethod
    def _result(
        claim_id: str,
        claim: str,
        verdict: str,
        confidence: float,
        evidence: list[dict],
        explanation: str,
    ) -> dict:
        return {
            "claim_id": claim_id,
            "text": claim,
            "verdict": verdict,
            "confidence": confidence,
            "evidence": evidence,
            "explanation": explanation,
            "judge_disagreement": False,
        }

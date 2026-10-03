from __future__ import annotations

import hashlib
import json
from ragproof_resources import load_schema

from jsonschema import Draft202012Validator, FormatChecker
from opentelemetry import trace as otel_trace

from ragproof_store import TraceRepository


class ChangeImpactAnalyzer:
    """Measure blast radius from directly recorded document usage."""

    name = "ragproof-direct-lineage-impact"
    version = "direct-usage-0.1.1"

    def __init__(self, repository: TraceRepository, tracer=None) -> None:
        self.repository = repository
        self.tracer = tracer or otel_trace.get_tracer("ragproof-impact", self.version)
        self.validator = Draft202012Validator(
            load_schema("change-impact.schema.json"),
            format_checker=FormatChecker(),
        )

    def analyze(
        self,
        tenant_id: str,
        project_id: str,
        document_id: str,
        from_version: str,
        to_version: str,
    ) -> dict:
        with self.tracer.start_as_current_span(
            "ragproof.impact.analyze",
            attributes={
                "ragproof.tenant.id": tenant_id,
                "ragproof.project.id": project_id,
                "ragproof.document.id": document_id,
                "ragproof.document.from_version": from_version,
                "ragproof.document.to_version": to_version,
                "ragproof.impact.analyzer.version": self.version,
            },
        ) as span:
            facts = self.repository.document_change_facts(
                tenant_id, project_id, document_id, from_version, to_version
            )
            from_hashes = sorted(set(facts["from_chunk_hashes"]))
            to_hashes = sorted(set(facts["to_chunk_hashes"]))
            # These hashes represent chunks observed in traces, not complete
            # document manifests. Different retrieval exposure is therefore not
            # sufficient evidence that the underlying document content changed.
            content_change = "unknown"
            change_id = self._digest(
                tenant_id, project_id, document_id, from_version, to_version
            )
            affected_traces = facts["affected_traces"]
            snapshot = json.dumps(affected_traces, sort_keys=True, separators=(",", ":"))
            impact_id = self._digest(change_id, self.version, snapshot)
            claim_ids = {
                claim_id
                for affected in affected_traces
                for claim_id in affected["claim_ids"]
            }
            confirmed = {
                diagnosis["diagnosis_id"]
                for affected in affected_traces
                for diagnosis in affected["diagnoses"]
                if diagnosis["status"] == "confirmed"
            }
            report = {
                "schema_version": "0.1.0",
                "impact_id": impact_id,
                "change_id": change_id,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "document_id": document_id,
                "change": {
                    "from_version": from_version,
                    "to_version": to_version,
                    "version_changed": from_version != to_version,
                    "content_change": content_change,
                    "from_chunk_hashes": from_hashes,
                    "to_chunk_hashes": to_hashes,
                },
                "summary": {
                    "affected_trace_count": len(affected_traces),
                    "affected_answer_count": len(affected_traces),
                    "affected_claim_count": len(claim_ids),
                    "confirmed_incident_count": len(confirmed),
                },
                "affected_traces": affected_traces,
            }
            errors = sorted(
                self.validator.iter_errors(report), key=lambda error: list(error.path)
            )
            if errors:
                messages = "; ".join(error.message for error in errors)
                raise ValueError(f"Change-impact contract violation: {messages}")
            stored = self.repository.save_impact(report)
            span.set_attribute("ragproof.impact.id", stored["impact_id"])
            span.set_attribute(
                "ragproof.impact.affected_trace_count",
                stored["summary"]["affected_trace_count"],
            )
            return stored

    @staticmethod
    def _digest(*parts: str) -> str:
        return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()

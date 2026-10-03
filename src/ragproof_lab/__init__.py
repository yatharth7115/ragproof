"""Controlled RAG failure laboratory for RAGProof development."""

from .scenarios import SCENARIOS, Scenario


def run_scenario(*args, **kwargs):
    """Import lazily so telemetry can depend on lab data models without a cycle."""

    from .pipeline import run_scenario as execute

    return execute(*args, **kwargs)


__all__ = ["SCENARIOS", "Scenario", "run_scenario"]

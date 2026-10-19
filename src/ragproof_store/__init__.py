"""Durable trace storage and evidence-lineage queries for RAGProof."""

from .config import StorageSettings

__all__ = ["StorageSettings", "TraceRepository", "create_app"]


def __getattr__(name):
    # Credential bootstrap must work before optional API/storage extras are installed.
    if name == "create_app":
        from .api import create_app

        return create_app
    if name == "TraceRepository":
        from .repository import TraceRepository

        return TraceRepository
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

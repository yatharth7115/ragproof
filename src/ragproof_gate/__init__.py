"""CI quality gates backed by confirmed real-data regression cases."""

from .gate import GatePolicy, QualityGateRunner

__all__ = ["GatePolicy", "QualityGateRunner"]

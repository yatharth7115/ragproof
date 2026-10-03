from __future__ import annotations

import re


BOUNDARY = re.compile(r"(?<=[.!?;])\s+")


class AtomicClaimExtractor:
    """Conservative deterministic baseline for sentence-level factual claims."""

    name = "ragproof-atomic-claim-extractor"
    version = "sentence-boundary-0.1.0"

    def extract(self, answer: str) -> list[str]:
        claims = [part.strip() for part in BOUNDARY.split(answer.strip()) if part.strip()]
        return claims or ([answer.strip()] if answer.strip() else [])

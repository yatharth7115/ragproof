from __future__ import annotations

import argparse
import json

from ragproof_store import TraceRepository

from .verifier import ClaimVerifier
from .worker import VerificationWorker


def main() -> None:
    parser = argparse.ArgumentParser(description="Consume RAGProof claim ingestion events")
    parser.add_argument("--once", action="store_true", help="Process at most one event")
    args = parser.parse_args()

    repository = TraceRepository()
    repository.initialize()
    worker = VerificationWorker(ClaimVerifier(repository))
    worker.ensure_group()

    if args.once:
        result = worker.process_next(block_ms=1_000)
        if result is not None:
            print(json.dumps(result, indent=2))
        return

    try:
        while True:
            result = worker.process_next(block_ms=5_000)
            if result is not None:
                print(json.dumps(result, separators=(",", ":")), flush=True)
    except KeyboardInterrupt:
        return

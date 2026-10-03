from __future__ import annotations

import argparse
import json

from ragproof_store import TraceRepository
from ragproof_verifier import ClaimVerifier

from .diagnoser import RootCauseDiagnoser
from .worker import DiagnosisWorker


def main() -> None:
    parser = argparse.ArgumentParser(description="Consume RAGProof verification events")
    parser.add_argument("--once", action="store_true", help="Process at most one event batch")
    args = parser.parse_args()

    repository = TraceRepository()
    repository.initialize()
    diagnoser = RootCauseDiagnoser(repository, ClaimVerifier(repository))
    worker = DiagnosisWorker(diagnoser)
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

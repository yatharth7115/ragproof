from __future__ import annotations

import argparse
import json

from ragproof_store import TraceRepository

from .regression import RegressionCaseBuilder
from .worker import RegressionWorker


def main() -> None:
    parser = argparse.ArgumentParser(description="Build regressions from confirmed replay events")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Process at most one event batch")
    mode.add_argument(
        "--backfill-confirmed",
        action="store_true",
        help="Promote every persisted CONFIRMED replay that has no regression case",
    )
    args = parser.parse_args()

    repository = TraceRepository()
    repository.initialize()
    builder = RegressionCaseBuilder(repository)
    if args.backfill_confirmed:
        results = builder.backfill_confirmed()
        print(json.dumps({"created": len(results), "case_ids": [
            result["case_id"] for result in results
        ]}, indent=2))
        return

    worker = RegressionWorker(builder)
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

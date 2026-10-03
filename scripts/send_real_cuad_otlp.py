"""Send the pinned real CUAD RAG run through the external OTLP/HTTP boundary."""

from __future__ import annotations

import argparse
import json
import os

from ragproof_lab import run_scenario
from ragproof_otel import RagProofTracer, configure_otlp_provider


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint",
        default="http://127.0.0.1:8080/v1/otlp/traces",
    )
    parser.add_argument(
        "--capture-mode",
        choices=("metadata_only", "redacted", "full"),
        default="metadata_only",
    )
    args = parser.parse_args()

    provider = configure_otlp_provider(
        args.endpoint, service_name="external-cuad-rag-application",
        headers={"Authorization": "Bearer " + os.environ["RAGPROOF_API_KEY"]}
        if os.environ.get("RAGPROOF_API_KEY") else None,
    )
    try:
        telemetry = RagProofTracer(
            tracer=provider.get_tracer("external-cuad-rag-application"),
            capture_mode=args.capture_mode,
        )
        run = run_scenario(
            "healthy", telemetry=telemetry, capture_mode=args.capture_mode
        )
        if not provider.force_flush(timeout_millis=10_000):
            raise RuntimeError("OTLP exporter did not flush before the timeout")
    finally:
        provider.shutdown()

    print(json.dumps({
        "status": "exported",
        "endpoint": args.endpoint,
        "capture_mode": args.capture_mode,
        "dataset": "CUAD v1" if run.source_documents else None,
        "ground_truth": "CUAD human annotation",
    }, indent=2))


if __name__ == "__main__":
    main()

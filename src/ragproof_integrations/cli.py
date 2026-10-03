from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .client import IntegrationError, MAX_BODY_BYTES, RAGProofClient
from .mapping import map_vendor_trace
from .vendors import fetch_langfuse_trace, fetch_langsmith_trace


def _read_json(path):
    with Path(path).open("rb") as stream:
        payload = stream.read(MAX_BODY_BYTES + 1)
    if len(payload) > MAX_BODY_BYTES:
        raise ValueError("Input JSON file exceeds 16 MiB")
    result = json.loads(payload)
    if not isinstance(result, dict):
        raise ValueError("Input JSON must be an object")
    return result


def run_cli(argv=None):
    parser = argparse.ArgumentParser(description="Import an actual trace into RAGProof with explicit lineage mappings")
    parser.add_argument("provider", choices=("canonical", "langfuse", "langsmith"))
    parser.add_argument("--endpoint", help="RAGProof URL; defaults to RAGPROOF_ENDPOINT or loopback")
    parser.add_argument("--file", help="Real canonical trace JSON (canonical provider)")
    parser.add_argument("--trace-id", help="Actual vendor trace or root run ID")
    parser.add_argument("--mapping", help="Declared field mapping JSON (vendor providers)")
    parser.add_argument("--from-time", help="Langfuse lower ISO-8601 timestamp bound")
    parser.add_argument("--to-time", help="Langfuse upper ISO-8601 timestamp bound")
    parser.add_argument("--allow-content", action="store_true", help="Explicitly permit transmission in full capture mode")
    parser.add_argument("--verify", action="store_true", help="Verify the stored answer immediately")
    args = parser.parse_args(argv)
    try:
        if args.provider == "canonical":
            if not args.file:
                raise ValueError("Canonical import requires --file with an actual trace")
            trace = _read_json(args.file)
        else:
            if not args.trace_id or not args.mapping:
                raise ValueError("Vendor import requires --trace-id and --mapping")
            mapping = _read_json(args.mapping)
            if args.provider == "langfuse":
                exported = fetch_langfuse_trace(args.trace_id, from_time=args.from_time, to_time=args.to_time)
            else:
                exported = fetch_langsmith_trace(args.trace_id)
            trace = map_vendor_trace(args.provider, args.trace_id, exported, mapping)
        with RAGProofClient(args.endpoint, allow_content=args.allow_content) as client:
            receipt = client.ingest(trace)
            result = {"trace_id": trace["trace_id"], "provider": args.provider,
                      "created": receipt.get("created"), "capture_mode": trace["privacy"]["capture_mode"]}
            if args.verify:
                verification = client.verify(trace["generation"]["response_id"])
                result["verdicts"] = [claim["verdict"] for claim in verification.get("claims", [])]
            print(json.dumps(result, indent=2))
        return 0
    except (IntegrationError, ValueError, OSError) as error:
        # Avoid arbitrary JSON decoder strings or OSError paths in CLI errors.
        message = "Invalid JSON input" if isinstance(error, json.JSONDecodeError) else str(error)
        print(json.dumps({"error": message}), file=sys.stderr)
        return 2


def main():
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()

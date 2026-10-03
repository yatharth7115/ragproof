from __future__ import annotations

import argparse
import json
from pathlib import Path

from ragproof_lab.scenarios import SCENARIOS

from .capture import capture_scenario


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture a CUAD laboratory run as a canonical OpenTelemetry trace"
    )
    parser.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    parser.add_argument(
        "--capture-mode",
        choices=("metadata_only", "redacted", "full"),
        default="metadata_only",
    )
    parser.add_argument("--output", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    captured = capture_scenario(args.scenario, capture_mode=args.capture_mode)
    payload = json.dumps(captured.canonical_trace, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        return
    print(payload, end="")


if __name__ == "__main__":
    main()

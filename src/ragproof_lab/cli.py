from __future__ import annotations

import argparse
import json

from .pipeline import run_scenario
from .scenarios import SCENARIOS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the controlled RAGProof laboratory")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario", choices=sorted(SCENARIOS))
    group.add_argument("--list", action="store_true", help="list available scenarios")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.list:
        print(json.dumps(sorted(SCENARIOS), indent=2))
        return
    result = run_scenario(args.scenario)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))

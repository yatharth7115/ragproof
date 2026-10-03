from __future__ import annotations

import argparse
import json

from ragproof_lab.scenarios import Scenario
from ragproof_store import TraceRepository

from .gate import GatePolicy, QualityGateRunner


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Run the RAGProof CI gate against confirmed CUAD regression cases."
    )
    command.add_argument("--candidate-name", default="ci-candidate")
    command.add_argument("--minimum-cases", type=int, default=1)
    command.add_argument("--minimum-pass-rate", type=float, default=1.0)
    command.add_argument("--required-category", action="append", default=[])
    command.add_argument("--parser-mode", default="healthy")
    command.add_argument("--chunker-mode", default="healthy")
    command.add_argument("--retriever-mode", default="healthy")
    command.add_argument("--reranker-mode", default="healthy")
    command.add_argument("--generator-mode", default="healthy")
    command.add_argument("--citation-mode", default="healthy")
    command.add_argument("--corpus-mode", default="current")
    command.add_argument("--full-report", action="store_true")
    return command


def run_cli(argv: list[str] | None = None, repository: TraceRepository | None = None) -> int:
    args = parser().parse_args(argv)
    store = repository or TraceRepository()
    store.initialize()
    candidate = Scenario(
        name=args.candidate_name,
        injected_fault=None,
        parser_mode=args.parser_mode,
        chunker_mode=args.chunker_mode,
        retriever_mode=args.retriever_mode,
        reranker_mode=args.reranker_mode,
        generator_mode=args.generator_mode,
        citation_mode=args.citation_mode,
        corpus_mode=args.corpus_mode,
    )
    result = QualityGateRunner(store).run(
        candidate,
        GatePolicy(
            minimum_cases=args.minimum_cases,
            minimum_pass_rate=args.minimum_pass_rate,
            required_categories=tuple(args.required_category),
        ),
    )
    print(json.dumps(result if args.full_report else {
        "gate_run_id": result["gate_run_id"],
        "candidate": result["candidate"]["name"],
        "outcome": result["outcome"],
        "summary": result["summary"],
    }, indent=2, sort_keys=True))
    return 0 if result["outcome"] == "PASS" else 1


def main() -> None:
    raise SystemExit(run_cli())

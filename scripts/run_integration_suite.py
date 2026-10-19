"""Run the real data-plane suite, fail on unintended skips, and save a summary."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import platform
import subprocess
import unittest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if os.getenv("RAGPROOF_RUN_STORAGE_INTEGRATION") != "1":
        parser.error("RAGPROOF_RUN_STORAGE_INTEGRATION=1 is required; data-plane checks must be enabled")

    root = Path(__file__).resolve().parents[1]
    suite = unittest.defaultTestLoader.discover(str(root / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    allowed_vendor_skips = {
        "test_external_client.LiveLangfuseReadTests.test_read_real_vendor_trace",
        "test_external_client.LiveLangsmithReadTests.test_read_real_vendor_trace",
    }
    skipped = [{"test": test.id(), "reason": reason} for test, reason in result.skipped]
    unexpected = [item for item in skipped if item["test"] not in allowed_vendor_skips]
    passed = result.testsRun - len(result.failures) - len(result.errors) - len(skipped)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True,
    ).stdout.strip()
    report = {
        "source_commit": commit,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "dependencies": dict(sorted((dist.metadata["Name"], dist.version) for dist in distributions())),
        "storage_integration_enabled": True,
        "tests_run": result.testsRun,
        "passed": passed,
        "failures": [test.id() for test, _ in result.failures],
        "errors": [test.id() for test, _ in result.errors],
        "skipped": skipped,
        "unexpected_skips": unexpected,
        "outcome": "PASS" if result.wasSuccessful() and not unexpected and result.testsRun else "FAIL",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if report["outcome"] == "PASS" else 1)


if __name__ == "__main__":
    main()

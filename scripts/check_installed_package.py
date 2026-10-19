"""Smoke an installed wheel from outside the checkout; never print generated keys."""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import distribution
from importlib.resources import files
import json
from pathlib import Path
import subprocess
import sys
import sysconfig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", action="store_true", help="Check commands supported without optional extras")
    args = parser.parse_args()
    dist = distribution("ragproof-mvp")
    prefix = Path(sys.prefix).resolve()
    package = Path(str(files("ragproof_resources"))).resolve()
    if not package.is_relative_to(prefix):
        raise RuntimeError("Expected a wheel installed in this environment, not an editable/source package")

    packaged_files = {str(path).replace("\\", "/") for path in dist.files or []}
    required = {
        "ragproof_resources/fixtures/cuad_eval_subset.json",
        "ragproof_resources/THIRD_PARTY_NOTICES.md",
        "ragproof_store/schema.sql",
        "ragproof_store/static/incident-dashboard.html",
    }
    for group in ("ragproof_resources/schemas/", "ragproof_store/migration_sql/"):
        if not any(path.startswith(group) for path in packaged_files):
            raise RuntimeError(f"Missing packaged resource group: {group}")
    if not required <= packaged_files:
        raise RuntimeError(f"Missing packaged resources: {sorted(required - packaged_files)}")

    from ragproof_resources import load_cuad_fixture, load_schema

    fixture = load_cuad_fixture()
    load_schema("canonical-trace.schema.json")

    scripts = Path(sysconfig.get_path("scripts"))
    suffix = ".exe" if sys.platform == "win32" else ""

    def run(name: str, *arguments: str) -> str:
        result = subprocess.run(
            [str(scripts / (name + suffix)), *arguments],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode:
            # Key-generation output must never be included in an exception/log.
            raise RuntimeError(f"Installed command failed: {name} (exit {result.returncode})")
        return result.stdout

    names = sorted(entry.name for entry in dist.entry_points if entry.group == "console_scripts")
    if args.base:
        names = ["ragproof-admin", "ragproof-capture", "ragproof-lab"]
    for name in names:
        run(name, "--help")
    run("ragproof-lab", "--list")
    run("ragproof-lab", "--scenario", "healthy")
    trace = json.loads(run("ragproof-capture", "--scenario", "healthy"))
    if trace["privacy"]["capture_mode"] != "metadata_only":
        raise RuntimeError("Default capture must remain metadata-only")

    result = subprocess.run(
        [str(scripts / ("ragproof-admin" + suffix)), "create-key", "--tenant-id", "cuad-public",
         "--project-id", "cuad-contract-review", "--role", "writer"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode:
        raise RuntimeError("Installed credential command failed")
    raw_key = result.stderr.splitlines()[-1]
    record = json.loads(result.stdout)[0]
    if (not raw_key.startswith("rpf_") or raw_key in result.stdout
            or record["key_hash"] != hashlib.sha256(raw_key.encode()).hexdigest()):
        raise RuntimeError("Installed credential output did not pass validation")

    evaluation = None
    if not args.base:
        evaluation = json.loads(run("ragproof-evaluate"))["summary"]
    print(json.dumps({
        "installation": "base" if args.base else "all-local-extras",
        "entry_points_checked": names,
        "resources": "present and loaded from installed wheel",
        "cuad_contracts": len(fixture["contracts"]),
        "credential_bootstrap": "passed (secret output discarded)",
        "evaluation": evaluation,
    }, indent=2))


if __name__ == "__main__":
    main()

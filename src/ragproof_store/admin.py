"""Local credential bootstrap. No database writes or application logging."""
from __future__ import annotations

import argparse
import json
import sys

from .credentials import ROLES, create_credential


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a project API key locally; stdout contains only its hashed JSON record."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-key", help="Generate a new key for one explicit project and role")
    create.add_argument("--tenant-id", required=True)
    create.add_argument("--project-id", required=True)
    create.add_argument("--role", required=True, choices=sorted(ROLES))
    args = parser.parse_args(argv)
    try:
        key, credential = create_credential(args.tenant_id, args.project_id, args.role)
    except ValueError as error:
        parser.error(str(error))
    print("Secret API key (shown once). Store it securely; do not share or log it:", file=sys.stderr)
    print(key, file=sys.stderr)
    print(json.dumps([credential], separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

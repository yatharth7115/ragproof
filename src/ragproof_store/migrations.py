"""Transactional migrations with a process-independent lock and drift checks."""

import argparse
import hashlib
from importlib.resources import files
import json
import re

import psycopg

from .config import StorageSettings


def migration_sources() -> list[tuple[str, str, str]]:
    package = files("ragproof_store")
    sources = [("0001_initial", package.joinpath("schema.sql").read_text(encoding="utf-8"))]
    directory = package.joinpath("migration_sql")
    if directory.is_dir():
        for resource in sorted(directory.iterdir(), key=lambda item: item.name):
            if not re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", resource.name):
                continue
            version = resource.name[:-4]
            if version <= "0001_initial":
                raise ValueError("Additional migrations must start at 0002")
            sources.append((version, resource.read_text(encoding="utf-8")))
    numbers = [version.split("_", 1)[0] for version, _ in sources]
    if len(set(numbers)) != len(numbers):
        raise ValueError("Migration numbers must be unique")
    return [
        (version, hashlib.sha256(sql.encode("utf-8")).hexdigest(), sql)
        for version, sql in sources
    ]


def apply_migrations(postgres_dsn: str) -> list[dict]:
    applied = []
    with psycopg.connect(postgres_dsn) as connection:
        # The lock lasts until commit, including DDL and history insertion.
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (726147001,))
        connection.execute("""
            CREATE TABLE IF NOT EXISTS ragproof_schema_migrations (
                version text PRIMARY KEY,
                checksum char(64) NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
        """)
        known = dict(connection.execute(
            "SELECT version, checksum FROM ragproof_schema_migrations"
        ).fetchall())
        sources = migration_sources()
        if set(known) - {version for version, _, _ in sources}:
            raise RuntimeError("Database contains migrations newer than this RAGProof build")
        for version, checksum, sql in sources:
            if version in known:
                if known[version] != checksum:
                    raise RuntimeError(f"Migration {version} changed after application; add a new migration")
                continue
            connection.execute(sql)
            connection.execute(
                "INSERT INTO ragproof_schema_migrations (version, checksum) VALUES (%s, %s)",
                (version, checksum),
            )
            applied.append({"version": version, "checksum": checksum})
    return applied


def migration_status(postgres_dsn: str) -> list[dict]:
    with psycopg.connect(postgres_dsn) as connection:
        exists = connection.execute(
            "SELECT to_regclass('ragproof_schema_migrations')"
        ).fetchone()[0]
        known = dict(connection.execute(
            "SELECT version, checksum FROM ragproof_schema_migrations"
        ).fetchall()) if exists else {}
    sources = migration_sources()
    result = [
        {"version": version, "checksum": checksum,
         "status": "pending" if version not in known else
         "applied" if known[version] == checksum else "drift"}
        for version, checksum, _ in sources
    ]
    result.extend(
        {"version": version, "checksum": known[version], "status": "unknown"}
        for version in sorted(set(known) - {version for version, _, _ in sources})
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply or inspect versioned RAGProof database migrations")
    parser.add_argument("--check", action="store_true", help="Read-only; exit 1 on pending migrations or drift")
    args = parser.parse_args()
    dsn = StorageSettings.from_env().postgres_dsn
    if args.check:
        result = migration_status(dsn)
        print(json.dumps({"migrations": result}, indent=2))
        raise SystemExit(0 if all(item["status"] == "applied" for item in result) else 1)
    result = apply_migrations(dsn)
    print(json.dumps({"applied": result}, indent=2))


if __name__ == "__main__":
    main()

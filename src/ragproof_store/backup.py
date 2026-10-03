"""Portable database, content and queue backups; restore only into empty targets."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import psycopg
from psycopg import sql
from redis import Redis

from .config import StorageSettings
from .migrations import apply_migrations, migration_status
from .object_store import ContentObjectStore


def _tables(connection) -> list[str]:
    names = {
        row[0] for row in connection.execute("""
            SELECT tablename FROM pg_tables WHERE schemaname = current_schema()
            AND tablename LIKE 'ragproof\\_%' ESCAPE '\\'
            AND tablename <> 'ragproof_schema_migrations'
        """).fetchall()
    }
    edges = connection.execute("""
        SELECT child.relname, parent.relname FROM pg_constraint fk
        JOIN pg_class child ON child.oid = fk.conrelid
        JOIN pg_class parent ON parent.oid = fk.confrelid
        JOIN pg_namespace n ON n.oid = child.relnamespace
        WHERE fk.contype = 'f' AND n.nspname = current_schema()
    """).fetchall()
    ordered = []
    while names:
        ready = sorted(name for name in names if not any(
            child == name and parent in names and parent != name for child, parent in edges
        ))
        if not ready:
            raise RuntimeError("Cannot back up a cyclic foreign-key graph automatically")
        ordered.extend(ready)
        names.difference_update(ready)
    return ordered


def _write(root: Path, relative: str, blocks) -> dict:
    destination = root / relative
    digest = hashlib.sha256()
    size = 0
    with destination.open("xb") as output:
        os.chmod(destination, 0o600)
        for block in blocks:
            output.write(block)
            digest.update(block)
            size += len(block)
    return {"file": relative, "sha256": digest.hexdigest(), "bytes": size}


def backup(settings: StorageSettings, destination: Path) -> dict:
    # Every caller must first stop application writers and worker processes.
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    objects = ContentObjectStore(settings)
    queue = Redis.from_url(settings.redis_url)
    migrations = migration_status(settings.postgres_dsn)
    if not migrations or any(item["status"] != "applied" for item in migrations):
        raise RuntimeError("Backup requires an up-to-date, non-drifted database")
    manifest = {
        "format": "ragproof-backup-v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "complete": False, "migrations": migrations, "tables": [], "objects": [], "redis": [],
    }
    with psycopg.connect(settings.postgres_dsn) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        for table in _tables(connection):
            with connection.cursor().copy(sql.SQL("COPY {} TO STDOUT (FORMAT BINARY)").format(sql.Identifier(table))) as copy:
                item = _write(destination, f"{table}.copy", copy)
            manifest["tables"].append({"table": table, **item})
    pages = objects.client.get_paginator("list_objects_v2").paginate(Bucket=objects.bucket)
    for page in pages:
        for item in page.get("Contents", []):
            body = objects.client.get_object(Bucket=objects.bucket, Key=item["Key"])["Body"]
            try:
                stored = _write(destination, f"object-{len(manifest['objects']):08d}.bin", body.iter_chunks(chunk_size=1024 * 1024))
            finally:
                body.close()
            manifest["objects"].append({"key": item["Key"], **stored})
    for index, key in enumerate(sorted(queue.scan_iter(match="ragproof:*"))):
        value = queue.dump(key)
        if value is None:
            raise RuntimeError("Queue changed during backup; stop all writers and retry")
        stored = _write(destination, f"redis-{index:08d}.bin", [value])
        manifest["redis"].append({"key": key.decode("utf-8"), "ttl_ms": max(0, queue.pttl(key)), **stored})
    manifest["complete"] = True
    _write(destination, "manifest.json", [(json.dumps(manifest, indent=2) + "\n").encode()])
    return {"directory": str(destination), "tables": len(manifest["tables"]), "objects": len(manifest["objects"]), "redis_keys": len(manifest["redis"])}


def _validate_backup(source: Path) -> dict:
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "ragproof-backup-v1" or manifest.get("complete") is not True:
        raise ValueError("Backup is incomplete or its format is unsupported")
    for item in manifest["tables"] + manifest["objects"] + manifest["redis"]:
        relative = item["file"]
        if Path(relative).name != relative or (source / relative).is_symlink():
            raise ValueError("Backup files must be plain local files")
        digest = hashlib.sha256()
        size = 0
        with (source / relative).open("rb") as content:
            for block in iter(lambda: content.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
        if digest.hexdigest() != item["sha256"] or size != item["bytes"]:
            raise ValueError(f"Backup checksum mismatch: {relative}")
    return manifest


def restore(settings: StorageSettings, source: Path, *, apply: bool = False) -> dict:
    manifest = _validate_backup(source)
    objects = ContentObjectStore(settings)
    queue = Redis.from_url(settings.redis_url)
    if queue.dbsize():
        raise ValueError("Restore requires a completely empty target Redis database")
    # Buckets must already exist, so restore never creates a misspelled target.
    if objects.client.list_objects_v2(Bucket=objects.bucket, MaxKeys=1).get("KeyCount", 0):
        raise ValueError("Restore requires an empty target content bucket")
    with psycopg.connect(settings.postgres_dsn) as connection:
        tables = _tables(connection)
        if tables:
            raise ValueError("Restore requires a fresh PostgreSQL database with no RAGProof tables")
    summary = {"validated": True, "apply": apply, "tables": len(manifest["tables"]), "objects": len(manifest["objects"]), "redis_keys": len(manifest["redis"])}
    if not apply:
        return summary
    apply_migrations(settings.postgres_dsn)
    migrations = migration_status(settings.postgres_dsn)
    if migrations != manifest["migrations"]:
        raise ValueError("Restore requires the exact same schema build as the backup; no records were imported")
    with psycopg.connect(settings.postgres_dsn) as connection:
        actual_tables = _tables(connection)
        if actual_tables != [item["table"] for item in manifest["tables"]]:
            raise ValueError("Backup table layout does not match this package")
        for item in manifest["tables"]:
            with connection.cursor().copy(sql.SQL("COPY {} FROM STDIN (FORMAT BINARY)").format(sql.Identifier(item["table"]))) as copy:
                with (source / item["file"]).open("rb") as content:
                    for block in iter(lambda: content.read(1024 * 1024), b""):
                        copy.write(block)
        # Content and queues are imported before SQL commits; if either fails,
        # SQL rolls back. Inspect the isolated targets before retrying.
        for item in manifest["objects"]:
            with (source / item["file"]).open("rb") as body:
                objects.client.put_object(Bucket=objects.bucket, Key=item["key"], Body=body)
        for item in manifest["redis"]:
            queue.restore(item["key"], item["ttl_ms"], (source / item["file"]).read_bytes())
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    save = commands.add_parser("backup")
    save.add_argument("--output", required=True, type=Path)
    save.add_argument("--apply", action="store_true")
    save.add_argument("--confirm-quiesced", action="store_true", help="Confirm API writers and workers are stopped")
    load = commands.add_parser("restore")
    load.add_argument("--source", required=True, type=Path)
    load.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.command == "backup":
        if args.apply and not args.confirm_quiesced:
            parser.error("A consistent backup requires --confirm-quiesced after stopping all writers")
        result = backup(StorageSettings.from_env(), args.output.resolve()) if args.apply else {
            "apply": False, "output": str(args.output.resolve()),
            "plan": "Snapshot versioned SQL tables, content objects, and ragproof:* Redis keys after writers stop",
        }
    else:
        # Restore has its own explicit target variables. It cannot silently use
        # the production/local application's default database, bucket or queue.
        names = ("POSTGRES_DSN", "REDIS_URL", "S3_ENDPOINT_URL", "S3_ACCESS_KEY", "S3_SECRET_KEY", "S3_BUCKET")
        missing = [name for name in names if not os.getenv(f"RAGPROOF_RESTORE_{name}")]
        if missing:
            parser.error("Set isolated RAGPROOF_RESTORE_ target variables: " + ", ".join(missing))
        settings = StorageSettings(**{name.lower(): os.environ[f"RAGPROOF_RESTORE_{name}"] for name in names})
        result = restore(settings, args.source.resolve(), apply=args.apply)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

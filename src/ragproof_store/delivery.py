"""Dispatch transactional events and optional signed release notifications."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

from .config import StorageSettings
from .events import IngestionEventPublisher


def dispatch(settings: StorageSettings, events=None, limit=100) -> str | None:
    events = events or IngestionEventPublisher(settings.redis_url)
    last_id = None
    with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as connection:
        rows = connection.execute("""
            SELECT id, payload FROM ragproof_event_outbox WHERE published_at IS NULL
            ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED
            """, (limit,)).fetchall()
        for row in rows:
            last_id = events.publish({**row["payload"], "event_id": str(row["id"])})
            connection.execute("UPDATE ragproof_event_outbox SET published_at=now() WHERE id=%s", (row["id"],))
    return last_id


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def notify_webhooks(settings: StorageSettings) -> int:
    endpoint = os.getenv("RAGPROOF_ALERT_WEBHOOK_URL", "")
    if not endpoint:
        return 0
    secret = os.getenv("RAGPROOF_ALERT_WEBHOOK_SECRET", "")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or not secret:
        raise ValueError("Webhook requires HTTPS and RAGPROOF_ALERT_WEBHOOK_SECRET")
    tenant = os.getenv("RAGPROOF_ALERT_TENANT_ID")
    project = os.getenv("RAGPROOF_ALERT_PROJECT_ID")
    if not tenant or not project:
        raise ValueError("Webhook requires explicit alert tenant and project IDs")
    delivered = 0
    with psycopg.connect(settings.postgres_dsn, row_factory=dict_row) as connection:
        connection.execute("""
            INSERT INTO ragproof_webhook_deliveries(outbox_id)
            SELECT id FROM ragproof_event_outbox WHERE payload->>'event'='quality_gate.completed'
            AND payload->>'outcome'='FAIL' AND payload->>'tenant_id'=%s AND payload->>'project_id'=%s
            ON CONFLICT DO NOTHING
            """, (tenant, project))
        rows = connection.execute("""
            SELECT d.outbox_id, d.attempts, o.payload FROM ragproof_webhook_deliveries d
            JOIN ragproof_event_outbox o ON o.id=d.outbox_id
            WHERE d.delivered_at IS NULL AND d.next_attempt_at<=now() AND d.attempts<8
            AND o.payload->>'tenant_id'=%s AND o.payload->>'project_id'=%s
            ORDER BY d.outbox_id LIMIT 10 FOR UPDATE OF d SKIP LOCKED
            """, (tenant, project)).fetchall()
        for row in rows:
            body = json.dumps(row["payload"], sort_keys=True, separators=(",", ":")).encode()
            stamp = str(int(time.time()))
            signature = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
            request = urllib.request.Request(endpoint, data=body, headers={
                "Content-Type": "application/json", "X-RAGProof-Event": str(row["outbox_id"]),
                "X-RAGProof-Timestamp": stamp, "X-RAGProof-Signature": f"sha256={signature}",
            })
            status = 0
            try:
                with urllib.request.build_opener(NoRedirect()).open(request, timeout=5) as response:
                    status = response.status
            except urllib.error.HTTPError as error:
                status = error.code
            except (urllib.error.URLError, TimeoutError):
                pass
            success = 200 <= status < 300
            connection.execute("""
                UPDATE ragproof_webhook_deliveries SET attempts=attempts+1, last_status=%s,
                  delivered_at=CASE WHEN %s THEN now() ELSE NULL END,
                  next_attempt_at=now() + make_interval(secs => %s)
                WHERE outbox_id=%s
                """, (status, success, min(3600, 2 ** (row["attempts"] + 1) * 15), row["outbox_id"]))
            delivered += int(success)
    return delivered


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = StorageSettings.from_env()
    while True:
        try:
            dispatch(settings)
            notify_webhooks(settings)
        except Exception as error:
            print(json.dumps({"status": "retry_pending", "error_type": type(error).__name__}), flush=True)
            if args.once:
                raise SystemExit(1)
        if args.once:
            return
        time.sleep(2)

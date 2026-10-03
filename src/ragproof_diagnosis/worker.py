from __future__ import annotations

import json
import socket

from redis.exceptions import ResponseError

from .diagnoser import RootCauseDiagnoser


class DiagnosisWorker:
    group_name = "ragproof-diagnosers"

    def __init__(
        self,
        diagnoser: RootCauseDiagnoser,
        consumer_name: str | None = None,
        group_name: str | None = None,
    ) -> None:
        self.diagnoser = diagnoser
        self.events = diagnoser.repository.events
        self.consumer_name = consumer_name or socket.gethostname()
        if group_name:
            self.group_name = group_name

    def ensure_group(self, start_id: str = "0") -> None:
        try:
            self.events.client.xgroup_create(
                self.events.stream_name, self.group_name, id=start_id, mkstream=True
            )
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise

    def process_next(self, block_ms: int = 1_000) -> dict | None:
        from ragproof_store.consumer import consume
        def handle(payload):
            if payload.get("event") == "verification.completed":
                trace = self.diagnoser.repository.get_trace(payload["trace_id"])
                if trace and any(key in trace.get("extensions", {}) for key in ("replay", "quality_gate")):
                    return None
                return self.diagnoser.diagnose_response(payload["response_id"])
        return consume(self.events, self.group_name, self.consumer_name, handle, block_ms)

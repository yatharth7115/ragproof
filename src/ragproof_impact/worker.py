from __future__ import annotations

import json
import socket

from redis.exceptions import ResponseError

from .regression import RegressionCaseBuilder


class RegressionWorker:
    group_name = "ragproof-regressions"

    def __init__(
        self,
        builder: RegressionCaseBuilder,
        consumer_name: str | None = None,
        group_name: str | None = None,
    ) -> None:
        self.builder = builder
        self.events = builder.repository.events
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
            if payload.get("event") == "replay.completed" and payload.get("outcome") == "CONFIRMED":
                return self.builder.create_from_replay(payload["replay_id"])
        return consume(self.events, self.group_name, self.consumer_name, handle, block_ms)

from __future__ import annotations

import json

from redis import Redis


class IngestionEventPublisher:
    stream_name = "ragproof:ingestion-events"

    def __init__(self, redis_url: str) -> None:
        self.client = Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=10)

    def ping(self) -> bool:
        return bool(self.client.ping())

    def publish(self, event: dict) -> str:
        return str(
            self.client.xadd(
                self.stream_name,
                {"event": event["event"], "payload": json.dumps(event, sort_keys=True)},
                maxlen=10_000,
                approximate=True,
            )
        )

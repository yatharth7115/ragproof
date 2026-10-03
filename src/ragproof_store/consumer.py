"""At-least-once stream consumption with stale-job reclaim and bounded retries."""
import json
import logging


def consume(events, group, consumer, handler, block_ms=1000, idle_ms=60000):
    reclaimed = events.client.xautoclaim(events.stream_name, group, consumer, idle_ms, start_id="0-0", count=50)
    entries = reclaimed[1]
    if not entries:
        batches = events.client.xreadgroup(group, consumer, {events.stream_name: ">"}, count=50, block=block_ms)
        entries = [entry for _, batch in batches for entry in batch]
    results = []
    for event_id, fields in entries:
        try:
            payload = json.loads(fields["payload"])
            result = handler(payload)
            if result is not None:
                results.append(result)
        except Exception as error:
            pending = events.client.xpending_range(events.stream_name, group, event_id, event_id, 1)
            attempts = pending[0]["times_delivered"] if pending else 1
            logging.getLogger("ragproof.worker").warning("%s event %s failed (%s); delivery %s", group, event_id, type(error).__name__, attempts)
            if attempts < 5:
                continue
            events.client.xadd("ragproof:dead-letters", {
                "group": group, "event_id": event_id,
                "payload": fields.get("payload", ""), "error_type": type(error).__name__,
            })
        events.client.xack(events.stream_name, group, event_id)
    return results[-1] if results else None

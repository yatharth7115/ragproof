from __future__ import annotations

import base64
from datetime import datetime
import json
import os
from uuid import UUID

from .client import IntegrationError, JsonTransport, validate_endpoint


def _required_env(name):
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Configure {name} locally before running this import")
    return value


def _decode_io(record, keys):
    # Langfuse v2 returns IO as raw JSON strings. Decode only valid JSON.
    result = dict(record)
    for key in keys:
        if isinstance(result.get(key), str):
            try:
                result[key] = json.loads(result[key])
            except json.JSONDecodeError:
                pass
    return result


def fetch_langfuse_trace(trace_id, *, from_time, to_time):
    """Read one bounded trace from the official observations v2 API."""
    try:
        start = datetime.fromisoformat(from_time.replace("Z", "+00:00"))
        end = datetime.fromisoformat(to_time.replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError("Langfuse import needs an ordered, timezone-aware time range") from error
    public = _required_env("LANGFUSE_PUBLIC_KEY")
    secret = _required_env("LANGFUSE_SECRET_KEY")
    endpoint = _required_env("LANGFUSE_BASE_URL")
    auth = base64.b64encode((public + ":" + secret).encode()).decode("ascii")
    params = {"traceId": trace_id, "fields": "core,basic,io,metadata,model",
              "limit": 100, "fromStartTime": from_time, "toStartTime": to_time}
    records, cursors = [], set()
    with JsonTransport(endpoint, headers={"Authorization": "Basic " + auth}) as transport:
        for _ in range(100):
            page = transport.request("GET", "/api/public/v2/observations", params=params)
            rows = page.get("data")
            if not isinstance(rows, list) or any(not isinstance(row, dict) or row.get("traceId") != trace_id for row in rows):
                raise IntegrationError("Langfuse returned an invalid or mismatched observation collection")
            records.extend(_decode_io(row, ("input", "output")) for row in rows)
            cursor = page.get("meta", {}).get("cursor")
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in cursors:
                raise IntegrationError("Langfuse pagination did not advance")
            cursors.add(cursor)
            params["cursor"] = cursor
        else:
            raise IntegrationError("Langfuse trace exceeds the 10,000 observation import limit")
    if len({row.get("id") for row in records}) != len(records):
        raise IntegrationError("Langfuse returned duplicate observations")
    roots = [row for row in records if row.get("isRootObservation") is True]
    if not roots:
        roots = [row for row in records if not row.get("parentObservationId")]
    if len(roots) != 1:
        raise IntegrationError("Langfuse trace needs exactly one completed root in the selected time range")
    return {"root": roots[0], "observations": records}


def fetch_langsmith_trace(run_id):
    """Use the supported SDK read API to hydrate an actual run and its children."""
    try:
        UUID(run_id)
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("LangSmith run ID must be a UUID") from error
    key = _required_env("LANGSMITH_API_KEY")
    endpoint = validate_endpoint(os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"))
    try:
        from langsmith import Client
    except ImportError as error:
        raise IntegrationError("Install the LangSmith read adapter: pip install 'ragproof-mvp[integrations,langsmith]'") from error
    client = Client(api_url=endpoint, api_key=key, auto_batch_tracing=False)
    try:
        run = client.read_run(run_id, load_child_runs=True)
        root = json.loads(run.json())
    except Exception as error:
        raise IntegrationError("LangSmith read failed; check credentials, workspace and run ID") from error
    finally:
        client.close()
    records = []
    pending = [root]
    seen = set()
    while pending:
        record = pending.pop()
        if not isinstance(record, dict) or not record.get("id") or record["id"] in seen:
            raise IntegrationError("LangSmith returned an invalid run tree")
        seen.add(record["id"])
        records.append(record)
        children = record.get("child_runs") or []
        if not isinstance(children, list):
            raise IntegrationError("LangSmith returned an invalid child-run collection")
        pending.extend(children)
        if len(records) + len(pending) > 10000:
            raise IntegrationError("LangSmith trace exceeds the 10,000 run import limit")
    return {"root": root, "observations": records}

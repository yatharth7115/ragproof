from __future__ import annotations

try:
    from fastapi import FastAPI, HTTPException
except ImportError as error:  # pragma: no cover - optional dependency boundary
    raise RuntimeError("Install the API dependencies with: pip install -e '.[api]'") from error

from .pipeline import run_scenario
from .scenarios import SCENARIOS


app = FastAPI(title="RAGProof Failure Laboratory", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/scenarios")
def list_scenarios() -> dict[str, list[str]]:
    return {"scenarios": sorted(SCENARIOS)}


@app.post("/v1/runs/{scenario}")
def execute_scenario(scenario: str) -> dict:
    try:
        return run_scenario(scenario).to_dict()
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

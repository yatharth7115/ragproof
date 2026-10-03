from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import time

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jsonschema import Draft202012Validator, FormatChecker
from starlette.concurrency import run_in_threadpool
from ragproof_resources import load_schema

from .repository import TraceRepository
from .security import AccessControl


def _validator() -> Draft202012Validator:
    schema = load_schema("canonical-trace.schema.json")
    return Draft202012Validator(schema, format_checker=FormatChecker())


def create_app(
    repository: TraceRepository | None = None,
    verifier=None,
    diagnoser=None,
    replay_engine=None,
    impact_analyzer=None,
    regression_builder=None,
    quality_gate=None,
    otlp_ingestor=None,
    access_control=None,
) -> FastAPI:
    store = repository or TraceRepository()
    if verifier is None:
        from ragproof_verifier.verifier import ClaimVerifier

        verifier = ClaimVerifier(store)
    if diagnoser is None:
        from ragproof_diagnosis import RootCauseDiagnoser

        diagnoser = RootCauseDiagnoser(store, verifier)
    if replay_engine is None:
        from ragproof_replay import DifferentialReplayEngine

        replay_engine = DifferentialReplayEngine(store, verifier, diagnoser)
    if impact_analyzer is None:
        from ragproof_impact import ChangeImpactAnalyzer

        impact_analyzer = ChangeImpactAnalyzer(store)
    if regression_builder is None:
        from ragproof_impact import RegressionCaseBuilder

        regression_builder = RegressionCaseBuilder(store)
    if quality_gate is None:
        from ragproof_gate import QualityGateRunner

        quality_gate = QualityGateRunner(store, verifier, diagnoser)
    if otlp_ingestor is None:
        from .otlp import OtlpTraceIngestor

        otlp_ingestor = OtlpTraceIngestor(store)
    validator = _validator()
    access = access_control or AccessControl()
    app = FastAPI(title="RAGProof Storage API", version="0.1.0")
    login_attempts = {}

    @app.middleware("http")
    async def project_access(request: Request, call_next):
        principal = access.resolve(request)
        request.state.principal = principal
        try:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                access.check_origin(request)
            if request.url.path.startswith("/v1/"):
                if principal is None:
                    raise HTTPException(401, "Sign in with a project API key")
                if request.method not in {"GET", "HEAD", "OPTIONS"} and not principal.can_write:
                    raise HTTPException(403, "A writer or admin credential is required")
                if access.mode == "token":
                    resource_patterns = [
                        (r"/v1/traces/([^/]+)$", "trace"),
                        (r"/v1/(?:verifications|diagnoses)/([^/]+)$", "response"),
                        (r"/v1/lineage/answers/([^/]+)$", "response"),
                        (r"/v1/replays/diagnoses/([^/]+)$", "diagnosis"),
                        (r"/v1/incidents/([^/]+)$", "diagnosis"),
                        (r"/v1/replays/([^/]+)$", "replay"),
                        (r"/v1/regressions/replays/([^/]+)$", "replay"),
                        (r"/v1/regressions/([^/]+)$", "regression"),
                        (r"/v1/impact/reports/([^/]+)$", "impact"),
                        (r"/v1/quality-gates/([^/]+)$", "gate"),
                    ]
                    for pattern, kind in resource_patterns:
                        match = re.fullmatch(pattern, request.url.path)
                        if match:
                            identity = await run_in_threadpool(store.resource_identity, kind, match[1])
                            if identity is None or (identity["tenant_id"], identity["project_id"]) != (principal.tenant_id, principal.project_id):
                                raise HTTPException(404, "Resource not found")
                            break
                    await run_in_threadpool(store.audit, principal, request.method, request.url.path, 0)
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
            response.headers["X-Frame-Options"] = "DENY"
            if request.url.path.startswith(("/v1/", "/auth/")):
                response.headers["Cache-Control"] = "no-store"
            return response
        except HTTPException as error:
            return JSONResponse({"detail": error.detail}, status_code=error.status_code)
        except Exception:
            logging.getLogger("ragproof").error("Request failed at %s; inspect service health", request.url.path)
            return JSONResponse({"detail": "Service unavailable; check local service health"}, status_code=503)

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        services = store.health()
        return JSONResponse({"status": "ready" if all(services.values()) else "unavailable", "services": services}, status_code=200 if all(services.values()) else 503)

    @app.get("/auth/status")
    def auth_status(request: Request):
        return {"required": access.mode == "token", "authenticated": request.state.principal is not None, "mode": access.mode}

    @app.post("/auth/session")
    def login(request: Request, body: dict):
        host = request.client.host if request.client else "unknown"
        now = time.monotonic()
        for key in list(login_attempts):
            if now - login_attempts[key][0] > 60:
                del login_attempts[key]
        if len(login_attempts) > 10000:
            raise HTTPException(429, "Try again later")
        started, count = login_attempts.get(host, (now, 0))
        login_attempts[host] = (started, count + 1)
        if count >= 10:
            raise HTTPException(429, "Too many sign-in attempts; wait one minute")
        key = body.get("api_key")
        if not isinstance(key, str) or len(key) > 1024:
            raise HTTPException(422, "api_key must be a string")
        principal = access.authenticate_key(key)
        if principal is None:
            raise HTTPException(401, "Invalid project API key")
        response = JSONResponse({"authenticated": True, "tenant_id": principal.tenant_id, "project_id": principal.project_id, "role": principal.role})
        response.set_cookie(access.cookie_name, access.session(principal), max_age=28800, httponly=True, secure=access.cookie_secure, samesite="strict", path="/")
        return response

    @app.delete("/auth/session")
    def logout():
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(access.cookie_name, path="/", secure=access.cookie_secure, httponly=True, samesite="strict")
        return response

    @app.get("/v1/workspace")
    def workspace(request: Request):
        principal = request.state.principal
        return {"mode": "local" if access.mode == "local" else "authenticated", "tenant_id": principal.tenant_id, "project_id": principal.project_id, "permissions": {"write": principal.can_write}, "services": store.health()}

    @app.get("/v1/traces")
    def trace_list(request: Request, limit: int = 30, offset: int = 0):
        principal = request.state.principal
        try:
            return store.list_traces(limit, offset, principal.tenant_id if access.mode != "local" else None, principal.project_id if access.mode != "local" else None)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/v1/quality-gates")
    def gate_list(request: Request, limit: int = 30, offset: int = 0):
        principal = request.state.principal
        try:
            return store.list_quality_gates(limit, offset, principal.tenant_id if access.mode != "local" else None, principal.project_id if access.mode != "local" else None)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
    static_directory = Path(__file__).resolve().parent / "static"
    app.mount(
        "/dashboard-assets",
        StaticFiles(directory=static_directory),
        name="dashboard-assets",
    )

    @app.get("/dashboard", include_in_schema=False)
    def incident_dashboard() -> FileResponse:
        return FileResponse(static_directory / "incident-dashboard.html")

    @app.post("/v1/traces", status_code=201)
    def ingest_trace(request: Request, trace: dict) -> dict:
        errors = sorted(validator.iter_errors(trace), key=lambda error: list(error.path))
        if errors:
            raise HTTPException(
                status_code=422,
                detail=[
                    {"path": list(error.absolute_path), "message": error.message}
                    for error in errors
                ],
            )
        access.check_trace(request.state.principal, trace)
        try:
            result = store.ingest(trace)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if not result["created"]:
            return result
        return result

    @app.post("/v1/otlp/traces", include_in_schema=True)
    async def ingest_otlp_traces(request: Request) -> Response:
        from google.protobuf.json_format import MessageToJson
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceResponse,
        )

        content_type = request.headers.get("content-type", "")
        content_encoding = request.headers.get("content-encoding", "").lower()
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > 16 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="OTLP request exceeds 16 MiB")
        payload = bytes(payload)
        if content_encoding:
            if content_encoding != "gzip":
                raise HTTPException(status_code=415, detail="Only gzip content encoding is supported")
            import gzip
            import io

            try:
                with gzip.GzipFile(fileobj=io.BytesIO(payload)) as zipped:
                    payload = zipped.read(16 * 1024 * 1024 + 1)
            except (OSError, EOFError) as error:
                raise HTTPException(status_code=400, detail="Invalid gzip payload") from error
            if len(payload) > 16 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="Expanded OTLP request exceeds 16 MiB")
        try:
            await run_in_threadpool(otlp_ingestor.ingest, payload, content_type, authorize=lambda trace: access.check_trace(request.state.principal, trace))
        except (ValueError, UnicodeDecodeError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        response = ExportTraceServiceResponse()
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type == "application/json":
            return Response(
                content=MessageToJson(response), media_type="application/json"
            )
        return Response(
            content=response.SerializeToString(), media_type="application/x-protobuf"
        )

    @app.get("/v1/traces/{trace_id}")
    def get_trace(trace_id: str) -> dict:
        trace = store.get_trace(trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="Trace not found")
        return trace

    @app.get("/v1/lineage/answers/{response_id}")
    def get_answer_lineage(response_id: str) -> dict:
        lineage = store.answer_lineage(response_id)
        if lineage is None:
            raise HTTPException(status_code=404, detail="Response not found")
        return lineage

    @app.get("/v1/impact/documents/{document_id}")
    def get_document_impact(
        request: Request, document_id: str, tenant_id: str, project_id: str, document_version: str
    ) -> dict:
        access.check_identity(request.state.principal, tenant_id, project_id)
        return store.document_impact(tenant_id, project_id, document_id, document_version)

    @app.post("/v1/verifications/{response_id}")
    def evaluate_response(response_id: str) -> dict:
        try:
            return verifier.evaluate_response(response_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/v1/verifications/{response_id}")
    def get_verification(response_id: str) -> dict:
        result = store.get_verification(response_id, verifier.name, verifier.version)
        if result is None:
            raise HTTPException(status_code=404, detail="Verification not found")
        return result

    @app.post("/v1/diagnoses/{response_id}")
    def diagnose_response(response_id: str) -> dict:
        try:
            return diagnoser.diagnose_response(response_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/v1/diagnoses/{response_id}")
    def get_diagnosis(response_id: str) -> dict:
        result = store.get_diagnosis(response_id, diagnoser.name, diagnoser.version)
        if result is None:
            raise HTTPException(status_code=404, detail="Diagnosis not found")
        return result

    @app.post("/v1/replays/diagnoses/{diagnosis_id}")
    def replay_diagnosis(diagnosis_id: str) -> dict:
        try:
            return replay_engine.replay_diagnosis(diagnosis_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/v1/replays/{replay_id}")
    def get_replay(replay_id: str) -> dict:
        result = store.get_replay(replay_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Replay not found")
        return result

    @app.post("/v1/impact/documents/{document_id}/analyze")
    def analyze_document_change(
        request: Request,
        document_id: str,
        tenant_id: str,
        project_id: str,
        from_version: str,
        to_version: str,
    ) -> dict:
        access.check_identity(request.state.principal, tenant_id, project_id)
        try:
            return impact_analyzer.analyze(
                tenant_id, project_id, document_id, from_version, to_version
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/v1/impact/reports/{impact_id}")
    def get_impact_report(impact_id: str) -> dict:
        result = store.get_impact(impact_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Impact report not found")
        return result

    @app.post("/v1/regressions/replays/{replay_id}")
    def create_regression_case(replay_id: str) -> dict:
        try:
            return regression_builder.create_from_replay(replay_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/v1/regressions/{case_id}")
    def get_regression_case(case_id: str) -> dict:
        result = store.get_regression(case_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Regression case not found")
        return result

    @app.post("/v1/quality-gates")
    def run_quality_gate(http_request: Request, request: dict) -> dict:
        from ragproof_gate import GatePolicy
        from ragproof_lab.scenarios import Scenario

        # The included release adapter operates the public CUAD project only.
        access.check_identity(http_request.state.principal, "cuad-public", "cuad-contract-review")
        if set(request) - {"candidate", "policy"}:
            raise HTTPException(422, "Unknown gate request fields")

        candidate = request.get("candidate", {})
        policy = request.get("policy", {})
        if not isinstance(candidate, dict) or not isinstance(policy, dict):
            raise HTTPException(422, "candidate and policy must be objects")
        if not isinstance(policy.get("required_categories", []), list):
            raise HTTPException(422, "required_categories must be an array")
        allowed_candidate_fields = {
            "name", "parser_mode", "chunker_mode", "retriever_mode",
            "reranker_mode", "generator_mode", "citation_mode", "corpus_mode",
        }
        unknown = set(candidate) - allowed_candidate_fields
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported candidate fields: {', '.join(sorted(unknown))}",
            )
        allowed_policy_fields = {
            "minimum_cases", "minimum_pass_rate", "required_categories"
        }
        unknown_policy = set(policy) - allowed_policy_fields
        if unknown_policy:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported policy fields: {', '.join(sorted(unknown_policy))}",
            )
        try:
            configuration = Scenario(
                name=candidate.get("name", "api-candidate"),
                injected_fault=None,
                parser_mode=candidate.get("parser_mode", "healthy"),
                chunker_mode=candidate.get("chunker_mode", "healthy"),
                retriever_mode=candidate.get("retriever_mode", "healthy"),
                reranker_mode=candidate.get("reranker_mode", "healthy"),
                generator_mode=candidate.get("generator_mode", "healthy"),
                citation_mode=candidate.get("citation_mode", "healthy"),
                corpus_mode=candidate.get("corpus_mode", "current"),
            )
            threshold = GatePolicy(
                minimum_cases=policy.get("minimum_cases", 1),
                minimum_pass_rate=policy.get("minimum_pass_rate", 1.0),
                required_categories=tuple(policy.get("required_categories", [])),
            )
            return quality_gate.run(configuration, threshold)
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/v1/quality-gates/{gate_run_id}")
    def get_quality_gate(gate_run_id: str) -> dict:
        result = store.get_quality_gate(gate_run_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Quality gate run not found")
        return result

    @app.get("/v1/incidents")
    def list_incidents(
        request: Request,
        status: str | None = None,
        category: str | None = None,
        limit: int = 100,
        offset: int = 0,
        q: str | None = None,
    ) -> dict:
        try:
            principal = request.state.principal
            return store.list_incidents(status, category, limit, offset, principal.tenant_id if access.mode != "local" else None, principal.project_id if access.mode != "local" else None, q)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/v1/incidents/{diagnosis_id}")
    def get_incident(diagnosis_id: str) -> dict:
        result = store.incident_detail(diagnosis_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        return result

    return app

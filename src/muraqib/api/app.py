"""FastAPI application.

Endpoints are deliberately small. The interesting parts are the cross-cutting
concerns: authentication, request-size limits, per-principal rate limiting,
correlation ids on every response, and an error handler that never leaks
internals to the caller while still logging them.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..agents.intake import IntakeBlocked
from ..config import get_settings
from ..corpus import Corpus
from ..graph.orchestrator import build_context, get_orchestrator, new_run_id
from ..models import AssessmentReport, Framework, PlatformConfig
from ..observability.logging_setup import configure_logging
from ..reporting.render import render_html, render_markdown, render_remediation_plan
from .auth import ROLE_ASSESSOR, ROLE_READER, Principal, current_principal, require_role

log = logging.getLogger("muraqib.api")

MAX_BODY_BYTES = 1_000_000
RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_S = 60


class AssessRequest(BaseModel):
    platform: PlatformConfig
    frameworks: list[Framework] | None = Field(default=None)
    include_markdown: bool = False


class AssessResponse(BaseModel):
    run_id: str
    report: AssessmentReport
    markdown: str | None = None
    ledger_verified: bool = True


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    settings = get_settings(refresh=True)
    configure_logging(settings.log_level, json_output=True)
    app.state.settings = settings
    app.state.corpus = Corpus.load(settings.corpus_dir)
    app.state.reports = {}
    app.state.rate = defaultdict(deque)
    log.info(
        "muraqib api starting",
        extra={
            "version": __version__,
            "controls": len(app.state.corpus.all_controls()),
            **settings.redacted(),
        },
    )
    if settings.auth_enabled and not (settings.oidc_jwks_uri or settings.dev_hs256_secret):
        raise RuntimeError(
            "MURAQIB_AUTH_ENABLED is on but no JWKS URI or dev secret is configured - refusing to start"
        )
    if not settings.auth_enabled:
        log.warning("authentication is DISABLED - do not expose this service to a network")
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Muraqib",
        version=__version__,
        description=(
            "AI governance readiness assessment. Produces gap analysis against NDMO, SDAIA, "
            "PDPL, EU AI Act, GDPR, NIST AI RMF and ISO/IEC 42001. Not a certification."
        ),
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):  # noqa: ANN001, ANN202
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        started = time.perf_counter()

        length = request.headers.get("content-length")
        if length and int(length) > MAX_BODY_BYTES:
            return JSONResponse(
                {"detail": "request body too large", "request_id": request_id},
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        key = request.headers.get("authorization", "")[-24:] or (
            request.client.host if request.client else "-"
        )
        now = time.time()
        bucket = request.app.state.rate[key]
        while bucket and now - bucket[0] > RATE_LIMIT_WINDOW_S:
            bucket.popleft()
        if request.url.path.startswith("/v1/assess") and len(bucket) >= RATE_LIMIT_REQUESTS:
            return JSONResponse(
                {"detail": "rate limit exceeded", "request_id": request_id},
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"Retry-After": str(RATE_LIMIT_WINDOW_S)},
            )
        bucket.append(now)

        try:
            response = await call_next(request)
        except Exception:
            log.exception(
                "unhandled error", extra={"request_id": request_id, "path": request.url.path}
            )
            return JSONResponse(
                {"detail": "internal error", "request_id": request_id},
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return response

    # ---------------- routes ----------------

    @app.get("/health", tags=["ops"])
    async def health(request: Request) -> dict[str, Any]:
        s = request.app.state.settings
        return {
            "status": "ok",
            "version": __version__,
            "controls": len(request.app.state.corpus.all_controls()),
            "frameworks": [f.value for f in request.app.state.corpus.frameworks],
            "auth_enabled": s.auth_enabled,
            "engine": f"{s.provider}:{s.model}",
        }

    @app.get("/v1/frameworks", tags=["corpus"])
    async def frameworks(
        request: Request, _: Principal = Depends(require_role(ROLE_READER))
    ) -> list[dict[str, Any]]:
        return [
            {
                "framework": p.framework.value,
                "official_name": p.official_name,
                "issuing_body": p.issuing_body,
                "jurisdiction": p.jurisdiction,
                "obligation": p.obligation.value,
                "status_note": p.status_note,
                "source_url": p.source_url,
                "licence_note": p.licence_note,
                "control_count": p.control_count,
            }
            for p in request.app.state.corpus.packs
        ]

    @app.get("/v1/controls/{control_id}", tags=["corpus"])
    async def control(
        control_id: str, request: Request, _: Principal = Depends(require_role(ROLE_READER))
    ) -> dict[str, Any]:
        found = request.app.state.corpus.control(control_id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "control not found")
        return found.model_dump(mode="json")

    @app.post("/v1/assess", response_model=AssessResponse, tags=["assessment"])
    async def assess(
        payload: AssessRequest,
        request: Request,
        principal: Principal = Depends(require_role(ROLE_ASSESSOR)),
    ) -> AssessResponse:
        settings = request.app.state.settings
        corpus = request.app.state.corpus
        run_id = new_run_id()
        ctx = build_context(settings, run_id=run_id, corpus=corpus)
        ctx.ledger.record("api_request", actor=principal.audit_identity(), run_id=run_id)
        try:
            report = get_orchestrator(ctx).run(
                payload.platform, payload.frameworks or corpus.frameworks, run_id=run_id
            )
        except IntakeBlocked as exc:
            raise HTTPException(
                getattr(
                    status, "HTTP_422_UNPROCESSABLE_CONTENT", status.HTTP_422_UNPROCESSABLE_ENTITY
                ),
                str(exc),
            ) from exc
        ok, _ = ctx.ledger.verify()
        request.app.state.reports[run_id] = report
        return AssessResponse(
            run_id=run_id,
            report=report,
            markdown=render_markdown(report) if payload.include_markdown else None,
            ledger_verified=ok,
        )

    # Registered BEFORE the bare "/v1/reports/{run_id}" route. Starlette matches
    # in registration order and "{run_id}" happily captures "MRQ-....html", so
    # with the bare route first this endpoint was unreachable: every HTML
    # request 404'd with run_id set to the id plus the extension.
    @app.get("/v1/reports/{run_id}.html", response_class=HTMLResponse, tags=["assessment"])
    async def get_report_html(
        run_id: str, request: Request, _: Principal = Depends(require_role(ROLE_READER))
    ) -> str:
        report = request.app.state.reports.get(run_id)
        if report is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found in this process")
        return render_html(report)

    @app.get("/v1/reports/{run_id}", tags=["assessment"])
    async def get_report(
        run_id: str, request: Request, _: Principal = Depends(require_role(ROLE_READER))
    ) -> AssessmentReport:
        report = request.app.state.reports.get(run_id)
        if report is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found in this process")
        return report

    @app.get(
        "/v1/reports/{run_id}/remediation", response_class=PlainTextResponse, tags=["assessment"]
    )
    async def get_remediation(
        run_id: str, request: Request, _: Principal = Depends(require_role(ROLE_READER))
    ) -> str:
        report = request.app.state.reports.get(run_id)
        if report is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found in this process")
        return render_remediation_plan(report, request.app.state.corpus)

    @app.get("/v1/whoami", tags=["ops"])
    async def whoami(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
        return {
            "subject": principal.subject,
            "issuer": principal.issuer,
            "roles": sorted(principal.effective_roles()),
            "anonymous": principal.anonymous,
        }

    return app


app = create_app()

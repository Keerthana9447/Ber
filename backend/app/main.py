from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import OperationalError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.api.v1.router import api_router
from app.config import Settings, get_settings
from app.db.session import create_database_engine, create_session_factory
from app.logging_config import configure_logging
from app.preflight import run_preflight
from app.resilience import retry_delay_seconds, should_log_traceback
from app.services.notifications import process_notification_batch

logger = logging.getLogger("berev.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or get_settings()
    configure_logging(configured.log_level)
    engine = create_database_engine(configured)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        stopping = asyncio.Event()
        mode = configured.preflight_mode
        if mode != "off":
            result = await asyncio.to_thread(run_preflight, engine)
            if result.ok:
                logger.info("startup_preflight_ok")
            else:
                for problem in result.problems:
                    logger.error("startup_preflight_failed [%s]: %s", problem.code, problem.message)
                if mode == "strict":
                    engine.dispose()
                    raise RuntimeError(
                        "Startup preflight failed: " + "; ".join(p.message for p in result.problems)
                    )

        app.state.scheduler_status = {"consecutive_failures": 0, "last_error_type": None}

        async def run_scheduler() -> None:
            failures = 0
            while not stopping.is_set():
                delay = configured.notification_interval_seconds
                try:
                    await asyncio.to_thread(
                        process_notification_batch,
                        app.state.session_factory,
                        configured,
                    )
                except Exception as error:
                    failures += 1
                    delay = retry_delay_seconds(configured.notification_interval_seconds, failures)
                    app.state.scheduler_status = {
                        "consecutive_failures": failures,
                        "last_error_type": type(error).__name__,
                    }
                    logger.error(
                        "notification_scheduler_failed: %s (consecutive failures: %d, retrying in %ds)",
                        type(error).__name__,
                        failures,
                        delay,
                        exc_info=should_log_traceback(failures),
                        extra={
                            "error_type": type(error).__name__,
                            "consecutive_failures": failures,
                            "retry_in_seconds": delay,
                        },
                    )
                else:
                    if failures:
                        logger.info(
                            "notification_scheduler_recovered",
                            extra={"consecutive_failures": failures},
                        )
                    failures = 0
                    app.state.scheduler_status = {"consecutive_failures": 0, "last_error_type": None}
                try:
                    await asyncio.wait_for(stopping.wait(), timeout=delay)
                except TimeoutError:
                    pass

        scheduler_task = asyncio.create_task(run_scheduler())
        app.state.notification_scheduler_task = scheduler_task
        try:
            yield
        finally:
            stopping.set()
            await scheduler_task
            engine.dispose()

    production = configured.environment == "production"
    app = FastAPI(
        title=configured.app_name,
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    if configured.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(configured.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Accept"],
        )
    app.state.settings = configured
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.include_router(api_router, prefix="/api/v1")

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        _: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        details = [
            {
                "loc": item.get("loc", ()),
                "msg": item.get("msg", "Invalid request"),
                "type": item.get("type", "value_error"),
            }
            for item in error.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": details},
        )

    @app.exception_handler(OperationalError)
    async def database_unavailable(_: Request, error: OperationalError) -> JSONResponse:
        logger.warning(
            "database_unavailable",
            extra={"status_code": status.HTTP_503_SERVICE_UNAVAILABLE},
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "Database is temporarily unavailable. Please try again shortly."},
            headers={"Retry-After": "5"},
        )

    @app.middleware("http")
    async def access_log(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        if production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        logger.info(
            "http_request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return response

    return app

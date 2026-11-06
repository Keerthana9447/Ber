from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.api.v1.router import api_router
from app.config import Settings, get_settings
from app.db.session import create_database_engine, create_session_factory
from app.logging_config import configure_logging

logger = logging.getLogger("berev.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or get_settings()
    configure_logging(configured.log_level)
    engine = create_database_engine(configured)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        yield
        engine.dispose()

    app = FastAPI(title=configured.app_name, lifespan=lifespan)
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

    @app.middleware("http")
    async def access_log(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
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

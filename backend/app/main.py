from __future__ import annotations

import importlib
import logging
import os
import pkgutil
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import ModuleType

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

import app.domains as domains_package
from app.core.db import engine
from app.core.errors import install_exception_handlers
from app.core.ids import new_id
from app.core.logging import configure_logging, log_event

logger = logging.getLogger(__name__)
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _router_from(module: ModuleType) -> APIRouter | None:
    candidate = getattr(module, "router", None)
    return candidate if isinstance(candidate, APIRouter) else None


def discover_domain_routers() -> list[tuple[str, APIRouter]]:
    """Import every existing app/domains/*/router.py without coupling domains."""

    discovered: list[tuple[str, APIRouter]] = []
    for item in sorted(
        pkgutil.iter_modules(domains_package.__path__), key=lambda value: value.name
    ):
        module_name = f"app.domains.{item.name}.router"
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                continue
            logger.warning(
                "domain router import failed",
                extra={"safe_fields": {"module": module_name, "error_type": type(exc).__name__}},
            )
            continue
        except Exception as exc:
            logger.warning(
                "domain router import failed",
                extra={"safe_fields": {"module": module_name, "error_type": type(exc).__name__}},
            )
            continue
        router = _router_from(module)
        if router is None:
            logger.warning(
                "domain router missing APIRouter", extra={"safe_fields": {"module": module_name}}
            )
            continue
        discovered.append((module_name, router))
    return discovered


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    configure_logging(os.getenv("LOG_LEVEL", "INFO"))
    application = FastAPI(
        title="HC Data Platform Backend",
        version="0.1.0",
        lifespan=lifespan,
    )
    install_exception_handlers(application)

    origins = [
        value.strip()
        for value in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
        if value.strip()
    ]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Organization-Id",
            "X-Client-Version",
            "X-Request-ID",
            "Accept-Language",
            "Idempotency-Key",
            "If-Match",
            "If-None-Match",
        ],
        expose_headers=["ETag", "Location", "X-Request-ID"],
    )

    @application.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        started = time.perf_counter()
        supplied = request.headers.get("X-Request-ID")
        request_id = (
            supplied if supplied and _SAFE_REQUEST_ID.fullmatch(supplied) else new_id("request")
        )
        request.state.request_id = request_id
        outcome = "ERROR"
        try:
            response = await call_next(request)
            outcome = str(response.status_code)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            log_event(
                logger,
                "request completed",
                request_id=request_id,
                actor=None,
                operation=f"{request.method} {request.url.path}",
                duration=round(time.perf_counter() - started, 6),
                outcome=outcome,
            )

    @application.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/readyz", include_in_schema=False)
    async def readyz() -> dict[str, str]:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return {"status": "ready"}

    for module_name, domain_router in discover_domain_routers():
        application.include_router(domain_router)
        logger.info("domain router loaded", extra={"safe_fields": {"module": module_name}})
    return application


app = create_app()

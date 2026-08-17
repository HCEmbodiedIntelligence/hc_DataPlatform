from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from http import HTTPStatus
from types import ModuleType
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

import hc_data_platform
from hc_data_platform.security.auth import AuthContext, JwtVerifier
from hc_data_platform.security.scope import ScopeGuard

from .config import Settings, get_settings
from .context import RequestContext, bind_request_context, reset_request_context
from .discovery import discover_module_routers
from .errors import ProblemDetails, ProblemException
from .health import (
    ReadinessProbe,
    ReadinessReport,
    check_readiness,
    default_readiness_probes,
    validate_readiness_probes,
)

logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str:
    supplied = request.headers.get("X-Request-ID", "")
    if supplied and len(supplied) <= 128 and all("!" <= char <= "~" for char in supplied):
        return supplied
    return str(uuid4())


def _problem_response(problem: ProblemDetails) -> JSONResponse:
    response = JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(mode="json", exclude_none=True),
        media_type="application/problem+json",
    )
    if problem.request_id is not None:
        response.headers["X-Request-ID"] = problem.request_id
    return response


def _http_status_title(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "HTTP request failed"


def _request_context_from_state(request: Request) -> RequestContext:
    context = getattr(request.state, "request_context", None)
    if isinstance(context, RequestContext):
        return context
    request_id = getattr(request.state, "request_id", None)
    return RequestContext(
        request_id=request_id if isinstance(request_id, str) else _request_id(request)
    )


def create_app(
    *,
    settings: Settings | None = None,
    readiness_probes: Mapping[str, ReadinessProbe] | None = None,
    module_package: ModuleType = hc_data_platform,
    jwt_verifier: JwtVerifier | None = None,
) -> FastAPI:
    """Create an independently testable API application with injectable dependency probes."""

    resolved_settings = settings if settings is not None else get_settings()
    probes = (
        dict(readiness_probes)
        if readiness_probes is not None
        else default_readiness_probes(resolved_settings)
    )
    validate_readiness_probes(probes)
    app = FastAPI(title="HC Data Platform Backend", version=hc_data_platform.__version__)
    app.state.settings = resolved_settings
    app.state.readiness_probes = probes
    verifier = jwt_verifier or _jwt_verifier_from_settings(resolved_settings)
    app.state.jwt_verifier = verifier
    if resolved_settings.runtime_backend == "production" and module_package is hc_data_platform:
        from hc_data_platform.runtime import build_runtime, configure_api

        runtime = build_runtime(resolved_settings)
        configure_api(runtime)
        app.state.runtime = runtime

    @app.middleware("http")
    async def request_context_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = _request_id(request)
        request.state.request_id = request_id
        request.state.auth_context = None
        try:
            auth = _authenticate_request(request, verifier)
            context = _request_context(request, request_id=request_id, auth=auth)
        except ProblemException as exc:
            problem = exc.problem.model_copy(
                update={
                    "request_id": exc.problem.request_id or request_id,
                    "instance": exc.problem.instance or request.url.path,
                }
            )
            auth_failure_response = _problem_response(problem)
            if problem.status == 401:
                auth_failure_response.headers["WWW-Authenticate"] = "Bearer"
            return auth_failure_response
        request.state.auth_context = auth
        request.state.request_context = context
        token = bind_request_context(context)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = context.request_id
            return response
        finally:
            reset_request_context(token)

    @app.exception_handler(ProblemException)
    async def handle_problem(request: Request, exc: ProblemException) -> JSONResponse:
        context = _request_context_from_state(request)
        problem = exc.problem
        if problem.request_id is None or problem.instance is None:
            problem = problem.model_copy(
                update={
                    "request_id": problem.request_id or context.request_id,
                    "instance": problem.instance or request.url.path,
                }
            )
        return _problem_response(problem)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        context = _request_context_from_state(request)
        return _problem_response(
            ProblemDetails(
                type="https://hc-data-platform.invalid/problems/request-validation",
                title="Request validation failed",
                status=422,
                detail="The request does not satisfy the API contract.",
                instance=request.url.path,
                code="REQUEST_VALIDATION_FAILED",
                request_id=context.request_id,
                details={"errors": jsonable_encoder(exc.errors())},
            )
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        context = _request_context_from_state(request)
        status = exc.status_code if 400 <= exc.status_code <= 599 else 500
        detail = exc.detail if isinstance(exc.detail, str) else _http_status_title(status)
        response = _problem_response(
            ProblemDetails(
                type=f"https://hc-data-platform.invalid/problems/http-{status}",
                title=_http_status_title(status),
                status=status,
                detail=detail,
                instance=request.url.path,
                code=f"HTTP_{status}",
                request_id=context.request_id,
            )
        )
        if exc.headers is not None:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        context = _request_context_from_state(request)
        logger.error(
            "Unhandled request exception",
            extra={
                "error_code": "INTERNAL_SERVER_ERROR",
                "exception_type": type(exc).__name__,
                "project_id": context.project_id,
                "request_id": context.request_id,
            },
        )
        return _problem_response(
            ProblemDetails(
                type="https://hc-data-platform.invalid/problems/internal-server-error",
                title="Internal Server Error",
                status=500,
                detail="The server could not complete the request.",
                instance=request.url.path,
                code="INTERNAL_SERVER_ERROR",
                request_id=context.request_id,
                retryable=True,
            )
        )

    @app.get("/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "live", "environment": resolved_settings.environment}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get(
        "/health/ready",
        tags=["health"],
        response_model=ReadinessReport,
        responses={503: {"model": ReadinessReport, "description": "A dependency is not ready"}},
    )
    async def ready() -> ReadinessReport | JSONResponse:
        report = await check_readiness(
            probes,
            timeout_seconds=resolved_settings.readiness_timeout_seconds,
        )
        if report.status != "ready":
            return JSONResponse(status_code=503, content=report.model_dump(mode="json"))
        return report

    for module in discover_module_routers(module_package):
        app.include_router(module.router)
    return app


def _jwt_verifier_from_settings(settings: Settings) -> JwtVerifier | None:
    if settings.jwt_jwks_url is not None:
        return JwtVerifier(
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            algorithms=settings.jwt_algorithms,
            jwks_url=settings.jwt_jwks_url,
        )
    if settings.jwt_signing_key is not None:
        return JwtVerifier(
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            algorithms=settings.jwt_algorithms,
            key=settings.jwt_signing_key.get_secret_value(),
        )
    return None


def _authenticate_request(request: Request, verifier: JwtVerifier | None) -> AuthContext | None:
    authorization = request.headers.get("Authorization")
    if authorization is None:
        return None
    scheme, separator, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not credentials.strip():
        raise ProblemException(
            ProblemDetails(
                type="https://hc-data-platform.invalid/problems/invalid-authorization-header",
                title="Invalid authorization header",
                status=401,
                detail="Authorization must contain one Bearer access token.",
                code="INVALID_AUTHORIZATION_HEADER",
            )
        )
    if verifier is None:
        raise ProblemException(
            ProblemDetails(
                type="https://hc-data-platform.invalid/problems/authentication-unavailable",
                title="Authentication unavailable",
                status=503,
                detail="The API has no JWT verification key source configured.",
                code="AUTHENTICATION_UNAVAILABLE",
                retryable=False,
            )
        )
    return verifier.verify(credentials.strip())


def _request_context(
    request: Request,
    *,
    request_id: str,
    auth: AuthContext | None,
) -> RequestContext:
    project_id = request.headers.get("X-Project-ID")
    region_code = request.headers.get("X-Region-Code")
    if auth is None:
        project_id = None
        region_code = None
    elif project_id is not None:
        ScopeGuard.require(auth, project_id, region_code)
    return RequestContext(
        request_id=request_id,
        project_id=project_id,
        subject_id=None if auth is None else auth.subject_id,
        region_code=region_code,
        roles=frozenset() if auth is None else auth.roles,
        service_identity=False if auth is None else auth.service_identity,
    )


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(create_app(settings=settings), host=settings.api_host, port=settings.api_port)

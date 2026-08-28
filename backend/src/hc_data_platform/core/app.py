from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from http import HTTPStatus
from types import ModuleType
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

import hc_data_platform
from hc_data_platform.security.abuse import InMemoryAbuseProtection, policy_from_settings
from hc_data_platform.security.access_repository import InMemoryAccessRepository
from hc_data_platform.security.access_service import AccessService
from hc_data_platform.security.admin_accounts import AdminAccountService
from hc_data_platform.security.auth import AuthContext, JwtVerifier
from hc_data_platform.security.challenge import challenge_verifier_from_settings
from hc_data_platform.security.passwords import PasswordHasher, PasswordPolicy, ScryptParameters
from hc_data_platform.security.recovery import (
    AccountRecoveryService,
    recovery_delivery_from_settings,
)
from hc_data_platform.security.scope import ScopeGuard

from .config import Settings, get_settings
from .context import RequestContext, bind_request_context, reset_request_context
from .discovery import discover_module_routers
from .errors import ProblemDetails, ProblemException
from .health import (
    ReadinessProbe,
    check_readiness,
    default_readiness_probes,
    validate_readiness_probes,
)

logger = logging.getLogger(__name__)

_SAFE_REQUEST_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_SAFE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_SAFE_VALIDATION_TYPE = re.compile(r"^[a-z][a-z0-9_.]{0,127}$")
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:authorization|cookie|password|secret|access[_-]?key|session[_-]?token|"
    r"postgres(?:ql)?(?:\+asyncpg)?://|s3://|minio://|x-amz-(?:signature|credential)|"
    r"traceback \(most recent call last\)|object[_-]?(?:key|path)|signed[_-]?url|dsn)"
)
_SENSITIVE_DETAIL_KEYS = re.compile(
    r"(?i)(?:authorization|cookie|password|secret|token|credential|dsn|url|uri|"
    r"object|manifest|bucket|path|stack|traceback|request_body)"
)
_UNTRUSTED_DETAIL_TEXT_KEYS = re.compile(
    r"(?i)^(?:detail|error|exception|message|msg|reason|stack|traceback)$"
)
_FORBIDDEN_METRIC_LABEL = re.compile(
    r"(?:^|[{,])\s*(?:principal|subject|user|project|tenant|region|resource|workflow|"
    r"dataset|profile|object|manifest|bucket|session|token|secret|key|dsn|url|uri)"
    r"(?:_[a-z0-9]+)*\s*="
)
_SAFE_HTTP_HEADERS = frozenset({"allow"})
_SAFE_EXCEPTION_TYPES = frozenset(
    {
        "AuthenticationDependencyError",
        "HTTPException",
        "ProblemException",
        "UnhandledException",
        "UnsafeHTTPResponse",
    }
)
_VALIDATION_SOURCES = frozenset({"body", "cookie", "header", "path", "query"})
_SAFE_DETAIL_TEXT_VALUES = frozenset({"Invalid value"})
_REDACTION_MARKER = "[REDACTED]"
_PUBLIC_API_OPERATIONS = frozenset(
    {
        ("/api/v1/auth/registrations", "post"),
        ("/api/v1/auth/sessions", "post"),
        ("/api/v1/auth/config", "get"),
        ("/api/v1/auth/password-recovery-requests", "post"),
        ("/api/v1/auth/password-recovery-confirmations", "post"),
        ("/api/v1/capabilities/auto-annotation", "get"),
        ("/api/v1/previews/sessions/{session_id}/media/index.m3u8", "get"),
    }
)
_SESSION_LOGOUT_OPERATION = ("/api/v1/auth/session:logout", "POST")
_OPERATION_ID_OVERRIDES = {
    ("/health/live", "get"): "getLiveness",
    ("/health/ready", "get"): "getReadiness",
    (
        "/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}/manifest",
        "get",
    ): "getUploadManifestDiscovery",
    (
        "/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}/parts",
        "get",
    ): "listUploadParts",
    (
        "/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}:commit-manifest",
        "post",
    ): "commitRolloutManifest",
    (
        "/api/v1/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}:renew",
        "post",
    ): "renewUploadPartAuthorizations",
    (
        "/api/v1/projects/{project_id}/datasets/{dataset_id}/lance-versions/{version}",
        "get",
    ): "getLanceCatalogVersionSnapshot",
    ("/api/v1/datasets/publication-preflight", "post"): "preflightDatasetPublication",
    ("/api/v1/datasets/publications", "post"): "publishDatasetVersion",
    (
        "/api/v1/datasets/{dataset_id}/versions/{dataset_version}",
        "get",
    ): "getPublishedDatasetVersion",
    (
        "/api/v1/datasets/{dataset_id}/versions/{dataset_version}/exports",
        "post",
    ): "exportPublishedDatasetVersion",
    (
        "/api/v1/datasets/{dataset_id}/versions/{dataset_version}/exports/{job_id}",
        "get",
    ): "getPublishedDatasetExportJob",
    (
        "/api/v1/datasets/{dataset_id}/versions/{dataset_version}/exports/{job_id}:cancel",
        "post",
    ): "cancelPublishedDatasetExportJob",
    (
        "/api/v1/datasets/{dataset_id}/versions/{dataset_version}/exports/{job_id}:retry",
        "post",
    ): "retryPublishedDatasetExportJob",
    (
        "/api/v1/datasets/{dataset_id}/versions/{dataset_version}/exports/{job_id}/download",
        "get",
    ): "authorizePublishedDatasetExportDownload",
    (
        "/api/v1/projects/{project_id}/quality-profiles",
        "post",
    ): "createQualityProfileVersion",
    (
        "/api/v1/projects/{project_id}/quality-profiles/{profile_id}/versions/{profile_version}",
        "get",
    ): "getQualityProfileVersion",
}


class PublicReadinessReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ready"] = "ready"


def _request_id(request: Request) -> str:
    supplied = request.headers.get("X-Request-ID", "").strip().lower()
    if _SAFE_REQUEST_ID.fullmatch(supplied):
        try:
            return str(UUID(supplied))
        except ValueError:
            pass
    return str(uuid4())


def _safe_business_text(value: object, *, fallback: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        return fallback
    if any(ord(character) < 32 and character not in "\t" for character in value):
        return fallback
    if _SENSITIVE_TEXT.search(value):
        return fallback
    return value


def _safe_details_value(value: object, *, depth: int = 0) -> object:
    if depth >= 5:
        return _REDACTION_MARKER
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_business_text(value, fallback=_REDACTION_MARKER)
    if isinstance(value, (list, tuple)):
        return [_safe_details_value(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for raw_key, item in list(value.items())[:50]:
            key = str(raw_key)[:128]
            if _SENSITIVE_DETAIL_KEYS.search(key):
                sanitized[key] = _REDACTION_MARKER
            elif _UNTRUSTED_DETAIL_TEXT_KEYS.fullmatch(key):
                sanitized[key] = (
                    item
                    if isinstance(item, str) and item in _SAFE_DETAIL_TEXT_VALUES
                    else _REDACTION_MARKER
                )
            else:
                sanitized[key] = _safe_details_value(item, depth=depth + 1)
        return sanitized
    return _REDACTION_MARKER


def _safe_route_locator(request: Request) -> str | None:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 256:
        return None
    return path


def _server_problem(
    *,
    request_id: str,
    instance: str | None = None,
    status: int = 500,
    retry_after_seconds: int | None = None,
) -> ProblemDetails:
    try:
        normalized_status = status if status >= 500 else 500
        title = HTTPStatus(normalized_status).phrase
    except ValueError:
        normalized_status = 500
        title = HTTPStatus.INTERNAL_SERVER_ERROR.phrase
    code = "INTERNAL_SERVER_ERROR" if normalized_status == 500 else f"HTTP_{normalized_status}"
    return ProblemDetails(
        type=f"https://hc-data-platform.invalid/problems/http-{normalized_status}",
        title=title,
        status=normalized_status,
        detail="The server could not complete the request.",
        instance=instance,
        code=code,
        request_id=request_id,
        retryable=True,
        retry_after_seconds=(retry_after_seconds if normalized_status == 503 else None),
    )


def _safe_problem(problem: ProblemDetails) -> ProblemDetails:
    # 501 is an intentionally declared product boundary, not an unexpected
    # server failure. Keep its sanitized contract code so clients can present
    # the approved "feature unavailable" state without guessing from HTTP.
    if problem.status >= 500 and problem.status != 501:
        return _server_problem(
            request_id=problem.request_id or str(uuid4()),
            instance=problem.instance,
            status=problem.status,
            retry_after_seconds=problem.retry_after_seconds,
        )
    return problem.model_copy(
        update={
            "title": _safe_business_text(
                problem.title, fallback=_http_status_title(problem.status)
            ),
            "detail": _safe_business_text(
                problem.detail,
                fallback="The request could not be completed.",
            ),
            "code": (
                problem.code if _SAFE_ERROR_CODE.fullmatch(problem.code) else "REQUEST_FAILED"
            ),
            "details": _safe_details_value(problem.details),
        }
    )


def _log_security_failure(
    request: Request,
    *,
    request_id: str,
    error_code: str,
    exception_type: str,
) -> None:
    # Every value is either server-generated or selected from a closed set.  In particular,
    # do not add exception messages, raw request paths, headers, bodies, or object locators.
    logger.error(
        "request_failed",
        extra={
            "error_code": (
                error_code if _SAFE_ERROR_CODE.fullmatch(error_code) else "INTERNAL_SERVER_ERROR"
            ),
            "exception_type": (
                exception_type if exception_type in _SAFE_EXCEPTION_TYPES else "UnhandledException"
            ),
            "request_id": request_id,
            "route": _safe_route_locator(request),
        },
    )


def _safe_metrics_payload() -> bytes:
    safe_lines: list[str] = []
    for line in generate_latest().decode("utf-8", errors="replace").splitlines(keepends=True):
        if not line.startswith("#") and (
            _FORBIDDEN_METRIC_LABEL.search(line) or _SENSITIVE_TEXT.search(line)
        ):
            continue
        safe_lines.append(line)
    return "".join(safe_lines).encode("utf-8")


def _problem_response(problem: ProblemDetails) -> JSONResponse:
    problem = _safe_problem(problem)
    response = JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(mode="json", exclude_none=True),
        media_type="application/problem+json",
    )
    if problem.request_id is not None:
        response.headers["X-Request-ID"] = problem.request_id
    if problem.retry_after_seconds is not None:
        response.headers["Retry-After"] = str(problem.retry_after_seconds)
    # Problems can contain scope-bound state (for example, whether a product
    # capability is currently approved), so never permit an intermediary to
    # reuse one across sessions.
    response.headers["Cache-Control"] = "no-store"
    return response


def _safe_retry_after_seconds(value: object) -> int | None:
    """Accept bounded RFC 9110 delay-seconds, never dates or signed/decimal values."""

    if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,5}", value) is None:
        return None
    seconds = int(value)
    return seconds if 1 <= seconds <= 86_400 else None


def _is_declared_feature_unavailable(
    request: Request,
    response: Response,
    *,
    marker: object,
) -> bool:
    """Trust only a 501 sanitized by the registered ProblemException handler."""

    content_type = response.headers.get("content-type", "").partition(";")[0].lower()
    return (
        getattr(request.state, "_declared_feature_unavailable_marker", None) is marker
        and response.status_code == 501
        and content_type == "application/problem+json"
    )


def _http_status_title(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "HTTP request failed"


def _safe_validation_errors(exc: RequestValidationError) -> list[dict[str, object]]:
    """Return useful locations and messages without echoing request inputs or validator context."""

    errors: list[dict[str, object]] = []
    for error in exc.errors()[:50]:
        raw_type = str(error.get("type", "validation_error"))
        error_type = raw_type if _SAFE_VALIDATION_TYPE.fullmatch(raw_type) else "validation_error"
        raw_location = error.get("loc", ())
        source = (
            str(raw_location[0])
            if isinstance(raw_location, (list, tuple)) and raw_location
            else "request"
        )
        errors.append(
            {
                "type": error_type,
                "loc": [source] if source in _VALIDATION_SOURCES else ["request"],
                "msg": "Invalid value",
            }
        )
    return errors


def _promote_inline_openapi_definitions(
    value: object,
    schemas: dict[str, Any],
) -> None:
    """Promote Pydantic inline $defs so every runtime-local $ref is resolvable."""

    if isinstance(value, list):
        for item in value:
            _promote_inline_openapi_definitions(item, schemas)
        return
    if not isinstance(value, dict):
        return

    definitions = value.pop("$defs", {})
    if isinstance(definitions, dict):
        for name, definition in definitions.items():
            if not isinstance(name, str) or not isinstance(definition, dict):
                continue
            _promote_inline_openapi_definitions(definition, schemas)
            schemas.setdefault(name, definition)

    reference = value.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        value["$ref"] = f"#/components/schemas/{reference.removeprefix('#/$defs/')}"
    for child in tuple(value.values()):
        _promote_inline_openapi_definitions(child, schemas)


def _normalize_runtime_openapi(document: dict[str, Any]) -> dict[str, Any]:
    components = document.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    if not isinstance(schemas, dict):
        raise RuntimeError("runtime OpenAPI components.schemas must be an object")

    problem_schema = ProblemDetails.model_json_schema(ref_template="#/components/schemas/{model}")
    _promote_inline_openapi_definitions(problem_schema, schemas)
    schemas.setdefault("ProblemDetails", problem_schema)
    _promote_inline_openapi_definitions(document, schemas)
    _promote_titled_operation_schemas(document, schemas)
    _normalize_operation_contracts(document)
    return document


def _promote_titled_operation_schemas(
    document: dict[str, Any],
    schemas: dict[str, Any],
) -> None:
    """Give Pydantic's titled inline bodies stable component references."""

    paths = document.get("paths", {})
    if not isinstance(paths, dict):
        return
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in {
                "get",
                "put",
                "post",
                "delete",
                "options",
                "head",
                "patch",
                "trace",
            } or not isinstance(operation, dict):
                continue
            request_body = operation.get("requestBody", {})
            if isinstance(request_body, dict):
                _promote_media_schema(request_body.get("content"), schemas)
            responses = operation.get("responses", {})
            if isinstance(responses, dict):
                for response in responses.values():
                    if isinstance(response, dict):
                        _promote_media_schema(response.get("content"), schemas)


def _promote_media_schema(content: object, schemas: dict[str, Any]) -> None:
    if not isinstance(content, dict):
        return
    for media in content.values():
        if not isinstance(media, dict):
            continue
        schema = media.get("schema")
        if not isinstance(schema, dict) or "$ref" in schema:
            continue
        title = schema.get("title")
        if (
            not isinstance(title, str)
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", title) is None
        ):
            continue
        definition = dict(schema)
        schemas.setdefault(title, definition)
        media["schema"] = {"$ref": f"#/components/schemas/{title}"}


def _normalize_operation_contracts(document: dict[str, Any]) -> None:
    """Stabilize public security and operation IDs across composed routers."""

    components = document.setdefault("components", {})
    if not isinstance(components, dict):
        raise RuntimeError("runtime OpenAPI components must be an object")
    security_schemes = components.setdefault("securitySchemes", {})
    if not isinstance(security_schemes, dict):
        raise RuntimeError("runtime OpenAPI securitySchemes must be an object")
    security_schemes.setdefault(
        "bearerAuth",
        {"type": "http", "scheme": "bearer", "bearerFormat": "JWT or opaque platform session"},
    )

    paths = document.get("paths", {})
    if not isinstance(paths, dict):
        raise RuntimeError("runtime OpenAPI paths must be an object")
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            normalized_method = method.lower()
            if normalized_method not in {
                "get",
                "put",
                "post",
                "delete",
                "options",
                "head",
                "patch",
                "trace",
            } or not isinstance(operation, dict):
                continue
            operation_key = (path, normalized_method)
            _normalize_error_response_contracts(operation)
            if path.startswith("/api/v1/"):
                operation["security"] = (
                    [] if operation_key in _PUBLIC_API_OPERATIONS else [{"bearerAuth": []}]
                )
            override = _OPERATION_ID_OVERRIDES.get(operation_key)
            if override is not None:
                operation["operationId"] = override
                continue
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str):
                continue
            suffix = re.sub(r"\W", "_", path.lstrip("/")) + f"_{normalized_method}"
            if not operation_id.endswith(f"_{suffix}"):
                continue
            endpoint_name = operation_id[: -(len(suffix) + 1)]
            words = endpoint_name.split("_")
            operation["operationId"] = words[0] + "".join(
                word[:1].upper() + word[1:] for word in words[1:]
            )


def _normalize_error_response_contracts(operation: dict[str, Any]) -> None:
    responses = operation.get("responses", {})
    if not isinstance(responses, dict):
        return
    for raw_status, response in responses.items():
        try:
            status = int(str(raw_status))
        except ValueError:
            continue
        if status < 400 or not isinstance(response, dict):
            continue
        content = response.get("content", {})
        if not isinstance(content, dict):
            continue
        schema: object | None = None
        for media in content.values():
            if isinstance(media, dict) and isinstance(media.get("schema"), dict):
                schema = media["schema"]
                break
        if not isinstance(schema, dict):
            continue
        reference = schema.get("$ref")
        if reference not in {
            "#/components/schemas/HTTPValidationError",
            "#/components/schemas/ProblemDetails",
        }:
            continue
        response["content"] = {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        }


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
    access_service: AccessService | None = None,
    admin_account_service: AdminAccountService | None = None,
    account_recovery_service: AccountRecoveryService | None = None,
) -> FastAPI:
    """Create an independently testable API application with injectable dependency probes."""

    resolved_settings = settings if settings is not None else get_settings()
    probes = (
        dict(readiness_probes)
        if readiness_probes is not None
        else default_readiness_probes(resolved_settings)
    )
    validate_readiness_probes(probes)
    docs_enabled = resolved_settings.api_docs_enabled
    app = FastAPI(
        title="HC Data Platform Backend",
        version=hc_data_platform.__version__,
        openapi_url="/openapi.json" if docs_enabled else None,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
    )
    app.state.settings = resolved_settings
    app.state.readiness_probes = probes
    verifier = jwt_verifier or _jwt_verifier_from_settings(resolved_settings)
    app.state.jwt_verifier = verifier
    resolved_access_service = access_service
    resolved_admin_account_service = admin_account_service
    resolved_recovery_service = account_recovery_service
    if resolved_settings.runtime_backend == "production" and module_package is hc_data_platform:
        from hc_data_platform.runtime import build_runtime, configure_api

        runtime = build_runtime(resolved_settings)
        configure_api(runtime)
        app.state.runtime = runtime
        resolved_access_service = resolved_access_service or runtime.access
        resolved_admin_account_service = resolved_admin_account_service or runtime.admin_accounts
        resolved_recovery_service = resolved_recovery_service or runtime.recovery
    composed_password_hasher = PasswordHasher(
        ScryptParameters(
            n=resolved_settings.password_scrypt_n,
            r=resolved_settings.password_scrypt_r,
            p=resolved_settings.password_scrypt_p,
        )
    )
    composed_password_policy = PasswordPolicy(
        min_length=resolved_settings.password_min_length,
        max_length=resolved_settings.password_max_length,
    )
    composed_abuse_protection = None
    if resolved_settings.auth_abuse_enabled:
        hmac_secret = resolved_settings.auth_abuse_hmac_secret
        if hmac_secret is None:  # Settings validates this invariant before composition.
            raise RuntimeError("auth abuse HMAC secret is not configured")
        composed_abuse_protection = InMemoryAbuseProtection(
            hmac_secret=hmac_secret.get_secret_value(),
            policy=policy_from_settings(resolved_settings),
        )
    composed_challenge_verifier = challenge_verifier_from_settings(resolved_settings)
    if resolved_access_service is None:
        resolved_access_service = AccessService(
            InMemoryAccessRepository(
                session_idle_ttl_seconds=resolved_settings.session_idle_ttl_seconds,
                session_absolute_ttl_seconds=resolved_settings.session_absolute_ttl_seconds,
                session_touch_interval_seconds=resolved_settings.session_touch_interval_seconds,
                max_active_sessions=resolved_settings.max_active_sessions,
            ),
            password_hasher=composed_password_hasher,
            password_policy=composed_password_policy,
            abuse_protection=composed_abuse_protection,
            challenge_verifier=composed_challenge_verifier,
        )
    if resolved_recovery_service is None:
        resolved_recovery_service = AccountRecoveryService(
            resolved_access_service.repository,
            delivery=recovery_delivery_from_settings(resolved_settings),
            password_hasher=composed_password_hasher,
            password_policy=composed_password_policy,
            abuse_protection=composed_abuse_protection,
            challenge_verifier=composed_challenge_verifier,
            email_verification_ttl_seconds=(
                resolved_settings.auth_recovery_email_verification_ttl_seconds
            ),
            password_recovery_ttl_seconds=resolved_settings.auth_recovery_token_ttl_seconds,
        )
    if resolved_admin_account_service is None:
        resolved_admin_account_service = AdminAccountService(
            resolved_access_service.repository,
            password_hasher=composed_password_hasher,
            password_policy=composed_password_policy,
        )
    app.state.access_service = resolved_access_service
    app.state.admin_account_service = resolved_admin_account_service
    app.state.account_recovery_service = resolved_recovery_service
    declared_feature_unavailable_marker = object()

    @app.middleware("http")
    async def request_context_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = _request_id(request)
        request.state.request_id = request_id
        request.state.auth_context = None
        request.state._declared_feature_unavailable_marker = None
        try:
            auth = _authenticate_request(request, verifier, resolved_access_service)
            context = _request_context(request, request_id=request_id, auth=auth)
        except ProblemException as exc:
            if exc.problem.status >= 500:
                _log_security_failure(
                    request,
                    request_id=request_id,
                    error_code="INTERNAL_SERVER_ERROR",
                    exception_type="ProblemException",
                )
            problem = exc.problem.model_copy(
                update={
                    "request_id": request_id,
                    "instance": None,
                }
            )
            auth_failure_response = _problem_response(problem)
            if problem.status == 401:
                auth_failure_response.headers["WWW-Authenticate"] = "Bearer"
            return auth_failure_response
        except Exception:
            _log_security_failure(
                request,
                request_id=request_id,
                error_code="INTERNAL_SERVER_ERROR",
                exception_type="AuthenticationDependencyError",
            )
            return _problem_response(_server_problem(request_id=request_id))
        request.state.auth_context = auth
        request.state.request_context = context
        token = bind_request_context(context)
        try:
            response = await call_next(request)
            raw_retry_after = response.headers.get("Retry-After")
            response_retry_after = (
                _safe_retry_after_seconds(raw_retry_after)
                if response.status_code in {429, 503}
                else None
            )
            if raw_retry_after is not None:
                del response.headers["Retry-After"]
            if response_retry_after is not None:
                response.headers["Retry-After"] = str(response_retry_after)
            if response.status_code >= 500 and not _is_declared_feature_unavailable(
                request,
                response,
                marker=declared_feature_unavailable_marker,
            ):
                _log_security_failure(
                    request,
                    request_id=context.request_id,
                    error_code="INTERNAL_SERVER_ERROR",
                    exception_type="UnsafeHTTPResponse",
                )
                response = _problem_response(
                    _server_problem(
                        request_id=context.request_id,
                        instance=_safe_route_locator(request),
                        status=response.status_code,
                        retry_after_seconds=response_retry_after,
                    )
                )
            if auth is not None and response.status_code >= 400:
                response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Request-ID"] = context.request_id
            return response
        finally:
            reset_request_context(token)

    @app.exception_handler(ProblemException)
    async def handle_problem(request: Request, exc: ProblemException) -> JSONResponse:
        context = _request_context_from_state(request)
        problem = exc.problem.model_copy(
            update={
                "request_id": context.request_id,
                "instance": _safe_route_locator(request),
            }
        )
        if problem.status >= 500 and problem.status != 501:
            _log_security_failure(
                request,
                request_id=context.request_id,
                error_code="INTERNAL_SERVER_ERROR",
                exception_type="ProblemException",
            )
        safe_problem = _safe_problem(problem)
        if safe_problem.status == 501:
            request.state._declared_feature_unavailable_marker = declared_feature_unavailable_marker
        return _problem_response(safe_problem)

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
                instance=_safe_route_locator(request),
                code="REQUEST_VALIDATION_FAILED",
                request_id=context.request_id,
                retryable=False,
                details={"errors": _safe_validation_errors(exc)},
            )
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        context = _request_context_from_state(request)
        status = exc.status_code if 400 <= exc.status_code <= 599 else 500
        retry_after_header = next(
            (value for name, value in (exc.headers or {}).items() if name.lower() == "retry-after"),
            None,
        )
        retry_after_seconds = (
            _safe_retry_after_seconds(retry_after_header) if status in {429, 503} else None
        )
        if status >= 500:
            _log_security_failure(
                request,
                request_id=context.request_id,
                error_code="INTERNAL_SERVER_ERROR",
                exception_type="HTTPException",
            )
        detail = _http_status_title(status)
        response = _problem_response(
            ProblemDetails(
                type=f"https://hc-data-platform.invalid/problems/http-{status}",
                title=_http_status_title(status),
                status=status,
                detail=detail,
                instance=_safe_route_locator(request),
                code=f"HTTP_{status}",
                request_id=context.request_id,
                retryable=retry_after_seconds is not None,
                retry_after_seconds=retry_after_seconds,
            )
        )
        if exc.headers is not None and status < 500:
            for name, value in exc.headers.items():
                normalized_name = name.lower()
                safe_allow = normalized_name == "allow" and bool(
                    re.fullmatch(r"[A-Z]+(?:,\s*[A-Z]+)*", value)
                )
                if normalized_name in _SAFE_HTTP_HEADERS and safe_allow:
                    response.headers[name] = value
        return response

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        context = _request_context_from_state(request)
        del exc
        _log_security_failure(
            request,
            request_id=context.request_id,
            error_code="INTERNAL_SERVER_ERROR",
            exception_type="UnhandledException",
        )
        return _problem_response(
            _server_problem(
                request_id=context.request_id,
                instance=_safe_route_locator(request),
            )
        )

    @app.get("/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(content=_safe_metrics_payload(), media_type=CONTENT_TYPE_LATEST)

    @app.get(
        "/health/ready",
        tags=["health"],
        response_model=PublicReadinessReport,
        responses={503: {"model": ProblemDetails, "description": "A dependency is not ready"}},
    )
    async def ready(request: Request) -> PublicReadinessReport | JSONResponse:
        report = await check_readiness(
            probes,
            timeout_seconds=resolved_settings.readiness_timeout_seconds,
        )
        if report.status != "ready":
            context = _request_context_from_state(request)
            return _problem_response(
                ProblemDetails(
                    type="https://hc-data-platform.invalid/problems/service-not-ready",
                    title="Service Unavailable",
                    status=503,
                    detail="The server could not complete the request.",
                    instance=_safe_route_locator(request),
                    code="SERVICE_NOT_READY",
                    request_id=context.request_id,
                    retryable=True,
                )
            )
        return PublicReadinessReport()

    for module in discover_module_routers(module_package):
        app.include_router(module.router)
    original_openapi = app.openapi

    def normalized_openapi() -> dict[str, Any]:
        return _normalize_runtime_openapi(original_openapi())

    app.openapi = normalized_openapi  # type: ignore[method-assign]
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


def _authenticate_request(
    request: Request,
    verifier: JwtVerifier | None,
    access_service: AccessService,
) -> AuthContext | None:
    if (request.url.path, request.method.lower()) in _PUBLIC_API_OPERATIONS:
        return None
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
                retryable=False,
            )
        )
    token = credentials.strip()
    if token.startswith(AccessService.TOKEN_PREFIX):
        auth = access_service.authenticate_access_token(
            token,
            request_id=str(request.state.request_id),
        )
        if auth is None:
            if (request.url.path, request.method.upper()) == _SESSION_LOGOUT_OPERATION:
                # A retry after a successful logout must remain a non-enumerating 204.  Only
                # this exact operation may receive a revoked opaque credential without an
                # authenticated request context; its route still requires Bearer syntax.
                return None
            raise ProblemException(
                ProblemDetails(
                    type="https://hc-data-platform.invalid/problems/session-invalid",
                    title="Session invalid",
                    status=401,
                    detail="The session is revoked or no longer valid.",
                    code="SESSION_INVALID",
                    retryable=False,
                )
            )
        return auth
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
    return verifier.verify(token)


def _request_context(
    request: Request,
    *,
    request_id: str,
    auth: AuthContext | None,
) -> RequestContext:
    organization_id = request.headers.get("X-Organization-Id")
    project_id = request.headers.get("X-Project-ID")
    region_code = request.headers.get("X-Region-Code")
    if auth is None:
        organization_id = None
        project_id = None
        region_code = None
    elif project_id is not None:
        ScopeGuard.require(auth, project_id, region_code, organization_id)
    return RequestContext(
        request_id=request_id,
        organization_id=organization_id,
        project_id=project_id,
        subject_id=None if auth is None else auth.subject_id,
        region_code=region_code,
        roles=frozenset() if auth is None else auth.roles,
        service_identity=False if auth is None else auth.service_identity,
        platform_admin=False if auth is None else auth.is_platform_admin,
    )


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(create_app(settings=settings), host=settings.api_host, port=settings.api_port)

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class DomainError(Exception):
    """Base for every browser-visible domain failure."""

    def __init__(
        self,
        code: str,
        http_status: int,
        message: str,
        field_errors: list[dict[str, Any]] | None = None,
        operation_errors: list[dict[str, Any]] | None = None,
        blocked_reasons: list[dict[str, Any]] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.message = message
        self.field_errors = field_errors or []
        self.operation_errors = operation_errors or []
        self.blocked_reasons = blocked_reasons or []
        self.retryable = retryable


class _TypedDomainError(DomainError):
    http_status = 500
    default_code = "SERVER_ERROR"
    default_message = "The request could not be completed."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        field_errors: list[dict[str, Any]] | None = None,
        operation_errors: list[dict[str, Any]] | None = None,
        blocked_reasons: list[dict[str, Any]] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(
            code or self.default_code,
            self.http_status,
            message or self.default_message,
            field_errors,
            operation_errors,
            blocked_reasons,
            retryable,
        )


class ValidationError(_TypedDomainError):
    http_status = 422
    default_code = "VALIDATION_ERROR"
    default_message = "The request is invalid."


class NotFoundError(_TypedDomainError):
    http_status = 404
    default_code = "NOT_FOUND"
    default_message = "The requested resource was not found."


class ForbiddenError(_TypedDomainError):
    http_status = 403
    default_code = "FORBIDDEN"
    default_message = "The requested action is not permitted."


class UnauthenticatedError(_TypedDomainError):
    http_status = 401
    default_code = "UNAUTHENTICATED"
    default_message = "Authentication is required."


class VersionConflictError(_TypedDomainError):
    http_status = 409
    default_code = "VERSION_CONFLICT"
    default_message = "The resource was changed by another operation."


class PreconditionFailedError(_TypedDomainError):
    http_status = 412
    default_code = "PRECONDITION_FAILED"
    default_message = "The request precondition did not match the current resource."


class GoneError(_TypedDomainError):
    http_status = 410
    default_code = "GONE"
    default_message = "The requested resource is no longer available."


class RateLimitedError(_TypedDomainError):
    http_status = 429
    default_code = "RATE_LIMITED"
    default_message = "Too many requests."

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("retryable", True)
        super().__init__(message, **kwargs)


class ServerError(_TypedDomainError):
    http_status = 500
    default_code = "SERVER_ERROR"
    default_message = "An internal server error occurred."


def error_envelope(exc: DomainError, request_id: str) -> dict[str, Any]:
    return {
        "error": {
            "code": exc.code,
            "message": exc.message,
            "field_errors": exc.field_errors,
            "operation_errors": exc.operation_errors,
            "blocked_reasons": exc.blocked_reasons,
            "request_id": request_id,
            "retryable": exc.retryable,
        }
    }


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "request-unknown")
    headers = {"X-Request-ID": request_id}
    if exc.http_status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        status_code=exc.http_status,
        content=error_envelope(exc, request_id),
        headers=headers,
        media_type="application/problem+json",
    )


async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    field_errors = []
    for item in exc.errors():
        loc = [str(part) for part in item.get("loc", ()) if part not in {"body", "query"}]
        field_errors.append(
            {
                "path": "/" + "/".join(loc),
                "code": str(item.get("type", "INVALID_VALUE")).upper().replace(".", "_"),
                "message": item.get("msg", "Invalid value"),
            }
        )
    return await domain_error_handler(
        request,
        ValidationError(field_errors=field_errors),
    )


async def unhandled_error_handler(request: Request, _exc: Exception) -> JSONResponse:
    return await domain_error_handler(request, ServerError())


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, domain_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)

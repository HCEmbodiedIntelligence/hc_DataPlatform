from __future__ import annotations

from fastapi import Request

from app.core.errors import PreconditionFailedError, ValidationError


def compute_etag(resource_version: int | str) -> str:
    return f'"rv-{resource_version}"'


def check_if_match(request: Request, current_etag: str) -> None:
    supplied = request.headers.get("If-Match")
    if supplied is None:
        raise ValidationError(
            code="IF_MATCH_REQUIRED",
            field_errors=[
                {"path": "/If-Match", "code": "REQUIRED", "message": "If-Match is required."}
            ],
        )
    candidates = {part.strip() for part in supplied.split(",")}
    if "*" not in candidates and current_etag not in candidates:
        raise PreconditionFailedError(
            code="PRECONDITION_FAILED",
            message="If-Match does not match the current resource version.",
            operation_errors=[
                {
                    "code": "ETAG_MISMATCH",
                    "message": "Refresh the resource before retrying.",
                    "retryable": False,
                }
            ],
        )

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProblemDetails(BaseModel):
    """RFC 9457 problem response plus stable platform error metadata."""

    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    status: int = Field(ge=400, le=599)
    detail: str
    instance: str | None = None
    code: str
    request_id: str | None = None
    retryable: bool
    retry_after_seconds: int | None = Field(default=None, ge=1, le=86_400)
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_retry_after_status(self) -> ProblemDetails:
        if self.retry_after_seconds is not None and self.status not in {429, 503}:
            raise ValueError("retry_after_seconds is only valid for status 429 or 503")
        return self


class ProblemException(Exception):
    def __init__(self, problem: ProblemDetails) -> None:
        # Tracebacks and JUnit reporters render ``Exception.args``.  Keep the stable code
        # there instead of a detail that may contain dependency or object-store context.
        super().__init__(problem.code)
        self.problem = problem


def problem(
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    type: str = "about:blank",
    instance: str | None = None,
    request_id: str | None = None,
    retryable: bool = False,
    retry_after_seconds: int | None = None,
    details: dict[str, Any] | None = None,
) -> ProblemException:
    return ProblemException(
        ProblemDetails(
            type=type,
            status=status,
            code=code,
            title=title,
            detail=detail,
            instance=instance,
            request_id=request_id,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
            details=details or {},
        )
    )

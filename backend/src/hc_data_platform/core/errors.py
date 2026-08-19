from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProblemDetails(BaseModel):
    """RFC 9457 problem response plus stable platform error metadata."""

    model_config = ConfigDict(extra="forbid")

    type: str = "about:blank"
    title: str
    status: int = Field(ge=400, le=599)
    detail: str
    instance: str | None = None
    code: str
    request_id: str | None = None
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


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
            details=details or {},
        )
    )

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Request-scoped identity and correlation metadata.

    Authentication code may enrich the optional subject and scope fields. The core HTTP
    middleware always supplies a request ID, including for unauthenticated health routes.
    """

    project_id: str | None = None
    subject_id: str | None = None
    region_code: str | None = None
    roles: frozenset[str] = field(default_factory=frozenset)
    request_id: str = field(default_factory=lambda: str(uuid4()))
    service_identity: bool = False

    def has_role(self, *allowed: str) -> bool:
        return bool(self.roles.intersection(allowed))


_current_request_context: ContextVar[RequestContext | None] = ContextVar(
    "hc_request_context",
    default=None,
)


def bind_request_context(context: RequestContext) -> Token[RequestContext | None]:
    return _current_request_context.set(context)


def reset_request_context(token: Token[RequestContext | None]) -> None:
    _current_request_context.reset(token)


def current_request_context() -> RequestContext:
    context = _current_request_context.get()
    if context is None:
        raise RuntimeError("no RequestContext is bound to the current execution context")
    return context


def select_request_scope(project_id: str, region_code: str | None = None) -> None:
    """Select the verified tenant scope consumed by synchronous DB adapters."""

    context = _current_request_context.get() or RequestContext()
    _current_request_context.set(
        RequestContext(
            project_id=project_id,
            subject_id=context.subject_id,
            region_code=region_code,
            roles=context.roles,
            request_id=context.request_id,
            service_identity=context.service_identity,
        )
    )

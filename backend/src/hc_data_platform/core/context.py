from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Request-scoped identity and correlation metadata.

    Authentication code may enrich the optional subject and scope fields. The core HTTP
    middleware always supplies a request ID, including for unauthenticated health routes.
    """

    organization_id: str | None = None
    project_id: str | None = None
    subject_id: str | None = None
    region_code: str | None = None
    request_id: str = field(default_factory=lambda: str(uuid4()))
    service_identity: bool = False
    platform_admin: bool = False


_current_request_context: ContextVar[RequestContext | None] = ContextVar(
    "hc_request_context",
    default=None,
)
_current_writer_permit: ContextVar[UUID | None] = ContextVar(
    "hc_writer_permit",
    default=None,
)
_current_platform_task_lease: ContextVar[UUID | None] = ContextVar(
    "hc_platform_task_lease",
    default=None,
)
_session_mutations_allowed: ContextVar[bool] = ContextVar(
    "hc_session_mutations_allowed",
    default=True,
)


@dataclass(slots=True)
class _WriterPermitRetention:
    seconds: int | None = None


_writer_permit_retention: ContextVar[_WriterPermitRetention | None] = ContextVar(
    "hc_writer_permit_retention",
    default=None,
)


def bind_request_context(context: RequestContext) -> Token[RequestContext | None]:
    return _current_request_context.set(context)


def reset_request_context(token: Token[RequestContext | None]) -> None:
    _current_request_context.reset(token)


def clear_request_context() -> None:
    """Remove any ambient scope before starting unrelated durable work."""
    _current_request_context.set(None)
    _current_writer_permit.set(None)
    _current_platform_task_lease.set(None)
    _session_mutations_allowed.set(True)
    _writer_permit_retention.set(None)


def bind_writer_permit(permit_id: UUID) -> Token[UUID | None]:
    return _current_writer_permit.set(permit_id)


def reset_writer_permit(token: Token[UUID | None]) -> None:
    _current_writer_permit.reset(token)


def current_writer_permit() -> UUID | None:
    return _current_writer_permit.get()


def bind_platform_task_lease(lease_id: UUID) -> Token[UUID | None]:
    return _current_platform_task_lease.set(lease_id)


def reset_platform_task_lease(token: Token[UUID | None]) -> None:
    _current_platform_task_lease.reset(token)


def current_platform_task_lease() -> UUID | None:
    return _current_platform_task_lease.get()


def bind_writer_permit_retention() -> Token[_WriterPermitRetention | None]:
    return _writer_permit_retention.set(_WriterPermitRetention())


def reset_writer_permit_retention(token: Token[_WriterPermitRetention | None]) -> None:
    _writer_permit_retention.reset(token)


def retain_current_writer_permit(seconds: int) -> None:
    """Keep an API permit active while a returned external write grant is valid."""

    if seconds < 1:
        raise ValueError("writer permit retention must be positive")
    if current_writer_permit() is None:
        return
    retention = _writer_permit_retention.get()
    if retention is None:
        return
    retention.seconds = seconds if retention.seconds is None else max(retention.seconds, seconds)


def current_writer_permit_retention_seconds() -> int | None:
    retention = _writer_permit_retention.get()
    return None if retention is None else retention.seconds


def bind_session_mutations_allowed(allowed: bool) -> Token[bool]:
    return _session_mutations_allowed.set(allowed)


def reset_session_mutations_allowed(token: Token[bool]) -> None:
    _session_mutations_allowed.reset(token)


def session_mutations_allowed() -> bool:
    return _session_mutations_allowed.get()


def current_request_context() -> RequestContext:
    context = _current_request_context.get()
    if context is None:
        raise RuntimeError("no RequestContext is bound to the current execution context")
    return context


def select_request_scope(
    project_id: str,
    region_code: str | None = None,
    *,
    organization_id: str | None = None,
) -> None:
    """Select the verified tenant scope consumed by synchronous DB adapters."""

    context = _current_request_context.get() or RequestContext()
    _current_request_context.set(
        RequestContext(
            # Preserve the verified organization boundary when a router narrows
            # the request to a project or region.
            organization_id=(
                context.organization_id if organization_id is None else organization_id
            ),
            project_id=project_id,
            subject_id=context.subject_id,
            region_code=region_code,
            request_id=context.request_id,
            service_identity=context.service_identity,
            platform_admin=context.platform_admin,
        )
    )


def select_organization_scope(organization_id: str) -> None:
    """Select an organization-wide business scope without inventing a project.

    Organization-owned registries such as robot assets are shared by every
    project in the tenant.  Keeping ``project_id`` unset is intentional: it
    prevents an implementation detail from becoming part of the resource
    identity while still installing the verified organization boundary for
    PostgreSQL RLS.
    """

    if not organization_id:
        raise ValueError("organization_id must not be empty")
    context = _current_request_context.get() or RequestContext()
    _current_request_context.set(
        RequestContext(
            organization_id=organization_id,
            project_id=None,
            subject_id=context.subject_id,
            region_code=None,
            request_id=context.request_id,
            service_identity=context.service_identity,
            platform_admin=context.platform_admin,
        )
    )

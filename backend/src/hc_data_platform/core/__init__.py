"""Shared application contracts and runtime wiring."""

from typing import TYPE_CHECKING, Any

from .context import RequestContext, current_request_context
from .errors import ProblemDetails, ProblemException
from .etag import ETag, etag_matches, make_etag, require_if_match
from .events import DomainEventEnvelope
from .pagination import CursorCodec, PageInfo

if TYPE_CHECKING:
    from .discovery import ModuleRouter as ModuleRouter


def __getattr__(name: str) -> Any:
    # Data/error contracts must not load FastAPI through package initialization.
    if name == "ModuleRouter":
        from .discovery import ModuleRouter

        return ModuleRouter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CursorCodec",
    "DomainEventEnvelope",
    "ETag",
    "ModuleRouter",
    "PageInfo",
    "ProblemDetails",
    "ProblemException",
    "RequestContext",
    "current_request_context",
    "etag_matches",
    "make_etag",
    "require_if_match",
]

"""Shared application contracts and runtime wiring."""

from .context import RequestContext, current_request_context
from .discovery import ModuleRouter
from .errors import ProblemDetails, ProblemException
from .etag import ETag, etag_matches, make_etag, require_if_match
from .events import DomainEventEnvelope
from .pagination import CursorCodec, PageInfo

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

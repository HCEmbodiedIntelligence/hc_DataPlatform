from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Generic, Protocol, TypeVar

from hc_data_platform.core.errors import problem

from .auth import AuthContext


@dataclass(frozen=True, slots=True)
class ScopeSelection:
    project_id: str
    region_code: str | None = None

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("project_id must not be empty")
        if self.region_code == "":
            raise ValueError("region_code must be non-empty when supplied")


@dataclass(frozen=True, slots=True)
class ScopedResource:
    project_id: str
    region_code: str | None = None


class ScopeGuard:
    @staticmethod
    def require(auth: AuthContext, project_id: str, region_code: str | None = None) -> None:
        # Exact project scope is mandatory for humans and workers alike.  In particular, the
        # string role "admin" is never a cross-tenant bypass.
        if auth.scope_pairs:
            allowed = (region_code is None and (project_id, None) in auth.scope_pairs) or (
                region_code is not None
                and (
                    (project_id, region_code) in auth.scope_pairs
                    or (not auth.service_identity and (project_id, None) in auth.scope_pairs)
                )
            )
            if allowed:
                return
            selected_project_exists = any(
                scoped_project == project_id for scoped_project, _ in auth.scope_pairs
            )
            code = (
                "SERVICE_SCOPE_REQUIRED"
                if auth.service_identity
                else (
                    "REGION_SCOPE_DENIED"
                    if region_code is not None and selected_project_exists
                    else "PROJECT_SCOPE_DENIED"
                )
            )
            title = (
                "Explicit worker scope required"
                if auth.service_identity
                else (
                    "Region access denied"
                    if code == "REGION_SCOPE_DENIED"
                    else "Project access denied"
                )
            )
            raise problem(
                status=403,
                code=code,
                title=title,
                detail="The subject was not issued this exact project and region scope.",
            )

        # Legacy JWTs expose independent project_ids and region_codes arrays.  With more than
        # one project there is no trustworthy way to know which region belongs to which project;
        # treating the arrays as a Cartesian product is an IDOR risk.  Fail closed until the
        # issuer provides explicit paired scopes (as platform sessions already do).
        if region_code is not None and len(auth.project_ids) > 1:
            raise problem(
                status=403,
                code="REGION_SCOPE_AMBIGUOUS",
                title="Explicit project-region scope required",
                detail="A region-scoped request requires an explicit project-region binding.",
            )

        if project_id not in auth.project_ids:
            raise problem(
                status=403,
                code="SERVICE_SCOPE_REQUIRED" if auth.service_identity else "PROJECT_SCOPE_DENIED",
                title=(
                    "Explicit worker scope required"
                    if auth.service_identity
                    else "Project access denied"
                ),
                detail="The subject cannot access this project.",
            )
        if region_code is not None and region_code not in auth.region_codes:
            raise problem(
                status=403,
                code="SERVICE_SCOPE_REQUIRED" if auth.service_identity else "REGION_SCOPE_DENIED",
                title=(
                    "Explicit worker scope required"
                    if auth.service_identity
                    else "Region access denied"
                ),
                detail="The subject cannot access this region.",
            )

    @staticmethod
    def select(auth: AuthContext, selection: ScopeSelection) -> ScopeSelection:
        ScopeGuard.require(auth, selection.project_id, selection.region_code)
        return selection


T = TypeVar("T", bound=ScopedResource)


class ScopedRepository(Protocol, Generic[T]):
    def get(self, auth: AuthContext, resource_id: str) -> T | None: ...

    def add(self, auth: AuthContext, resource_id: str, resource: T) -> None: ...


class InMemoryScopedRepository(Generic[T]):
    """Thread-safe fake that applies the same read and write scope checks as PostgreSQL."""

    def __init__(self) -> None:
        self._items: dict[str, T] = {}
        self._lock = RLock()

    def get(self, auth: AuthContext, resource_id: str) -> T | None:
        with self._lock:
            item = self._items.get(resource_id)
            if item is not None:
                ScopeGuard.require(auth, item.project_id, item.region_code)
            return item

    def add(self, auth: AuthContext, resource_id: str, resource: T) -> None:
        ScopeGuard.require(auth, resource.project_id, resource.region_code)
        with self._lock:
            self._items[resource_id] = resource

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
        # Human administrators may deliberately select any tenant. Service identities never
        # inherit that bypass: a worker must be issued the exact project/region scope it uses.
        if auth.service_identity:
            if project_id not in auth.project_ids:
                raise problem(
                    status=403,
                    code="SERVICE_SCOPE_REQUIRED",
                    title="Explicit worker scope required",
                    detail="The service identity was not issued this project scope.",
                )
            if region_code is not None and region_code not in auth.region_codes:
                raise problem(
                    status=403,
                    code="SERVICE_SCOPE_REQUIRED",
                    title="Explicit worker scope required",
                    detail="The service identity was not issued this region scope.",
                )
            return

        if "admin" not in auth.roles and project_id not in auth.project_ids:
            raise problem(
                status=403,
                code="PROJECT_SCOPE_DENIED",
                title="Project access denied",
                detail="The subject cannot access this project.",
            )
        if (
            region_code is not None
            and "admin" not in auth.roles
            and region_code not in auth.region_codes
        ):
            raise problem(
                status=403,
                code="REGION_SCOPE_DENIED",
                title="Region access denied",
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

"""Platform-admin project directory and bootstrap provisioning."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from threading import RLock
from typing import Annotated, Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from hc_data_platform.core.dbapi import normalize_postgres_dsn

StableScopeId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$",
    ),
]


class PlatformProjectError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PlatformProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: StableScopeId
    organization_name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
    ]
    project_id: StableScopeId


class PlatformProject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: StableScopeId
    organization_name: str = Field(min_length=1, max_length=256)
    project_id: StableScopeId
    project_name: str = Field(min_length=1, max_length=256)


class PlatformProjectPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: str = "hc-platform-project-directory/v1"
    count: int = Field(ge=0)
    items: tuple[PlatformProject, ...]


class PlatformProjectRepository(Protocol):
    def list_projects(self) -> tuple[PlatformProject, ...]: ...

    def create_project(self, command: PlatformProjectCreate) -> PlatformProject: ...


class InMemoryPlatformProjectRepository:
    def __init__(self, projects: tuple[PlatformProject, ...] = ()) -> None:
        self._projects = {
            (project.organization_id, project.project_id): project for project in projects
        }
        self._lock = RLock()

    def list_projects(self) -> tuple[PlatformProject, ...]:
        with self._lock:
            return tuple(
                self._projects[key]
                for key in sorted(self._projects, key=lambda item: (item[0], item[1]))
            )

    def create_project(self, command: PlatformProjectCreate) -> PlatformProject:
        key = (command.organization_id, command.project_id)
        with self._lock:
            if key in self._projects:
                raise PlatformProjectError(
                    "PLATFORM_PROJECT_ALREADY_EXISTS",
                    "the organization project already exists",
                )
            project = PlatformProject(
                organization_id=command.organization_id,
                organization_name=command.organization_name,
                project_id=command.project_id,
                project_name=command.project_id,
            )
            self._projects[key] = project
            return project


class DbApiCursor(Protocol):
    def execute(self, query: str, parameters: object = ...) -> None: ...

    def fetchone(self) -> Mapping[str, Any] | None: ...

    def fetchall(self) -> list[Mapping[str, Any]]: ...


class DbApiConnection(Protocol):
    def __enter__(self) -> DbApiConnection: ...

    def __exit__(self, *args: object) -> None: ...

    def cursor(self) -> DbApiCursor: ...


def _project_from_row(row: Mapping[str, Any]) -> PlatformProject:
    project_id = str(row["project_id"])
    return PlatformProject(
        organization_id=str(row["organization_id"]),
        organization_name=str(row["organization_name"]),
        project_id=project_id,
        project_name=project_id,
    )


class PostgresPlatformProjectRepository:
    def __init__(self, connection_factory: Callable[[], DbApiConnection]) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(cls, dsn: str) -> PostgresPlatformProjectRepository:
        normalized = normalize_postgres_dsn(dsn)

        def connect() -> DbApiConnection:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(normalized, row_factory=dict_row)  # type: ignore[return-value]

        return cls(connect)

    @staticmethod
    def _activate_platform_admin(cursor: DbApiCursor) -> None:
        # The HTTP boundary has already verified the exact global platform.admin
        # grant. This transaction-local marker admits the global directory query
        # through the registry table's FORCE RLS policy.
        cursor.execute("SELECT set_config('app.platform_admin', 'true', true)")

    def list_projects(self) -> tuple[PlatformProject, ...]:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            self._activate_platform_admin(cursor)
            cursor.execute(
                """
                SELECT project.organization_id,
                       COALESCE(directory.display_name, project.organization_id)
                           AS organization_name,
                       project.project_id
                FROM registry.organization_projects project
                LEFT JOIN access_control.organization_join_codes directory
                  ON directory.organization_id = project.organization_id
                ORDER BY project.organization_id, project.project_id
                """
            )
            rows = cursor.fetchall()
        return tuple(_project_from_row(row) for row in rows)

    def create_project(self, command: PlatformProjectCreate) -> PlatformProject:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            self._activate_platform_admin(cursor)
            cursor.execute(
                """
                INSERT INTO access_control.organization_join_codes (
                    organization_id, display_name, join_code
                )
                VALUES (%s, %s, %s)
                ON CONFLICT (organization_id) DO NOTHING
                """,
                (
                    command.organization_id,
                    command.organization_name,
                    f"bootstrap-{uuid4().hex}",
                ),
            )
            cursor.execute(
                """
                INSERT INTO registry.organization_projects (organization_id, project_id)
                VALUES (%s, %s)
                ON CONFLICT (organization_id, project_id) DO NOTHING
                RETURNING organization_id, project_id
                """,
                (command.organization_id, command.project_id),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                raise PlatformProjectError(
                    "PLATFORM_PROJECT_ALREADY_EXISTS",
                    "the organization project already exists",
                )
            cursor.execute(
                """
                SELECT project.organization_id,
                       COALESCE(directory.display_name, project.organization_id)
                           AS organization_name,
                       project.project_id
                FROM registry.organization_projects project
                LEFT JOIN access_control.organization_join_codes directory
                  ON directory.organization_id = project.organization_id
                WHERE project.organization_id = %s AND project.project_id = %s
                """,
                (command.organization_id, command.project_id),
            )
            row = cursor.fetchone()
            if row is None:  # Defensive: the insert and read share one transaction.
                raise RuntimeError("created platform project was not readable")
        return _project_from_row(row)


class PlatformProjectService:
    def __init__(self, repository: PlatformProjectRepository) -> None:
        self.repository = repository

    def list_projects(self) -> PlatformProjectPage:
        projects = self.repository.list_projects()
        return PlatformProjectPage(count=len(projects), items=projects)

    def create_project(self, command: PlatformProjectCreate) -> PlatformProject:
        return self.repository.create_project(command)

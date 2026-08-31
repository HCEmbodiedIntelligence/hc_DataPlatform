"""Scoped P02 data-source persistence and encrypted credential storage."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Any, Protocol, cast
from uuid import uuid4

from hc_data_platform.security.versioning import ResourceVersion

from .models import (
    DataSourceAsyncJob,
    DataSourceConnectionTestJob,
    DataSourceDetail,
    DataSourceScope,
)


@dataclass(frozen=True, slots=True, repr=False)
class CredentialMaterial:
    """One transient secret write; callers must never persist or log ``secret``."""

    kind: str
    secret: str
    masked_hint: str


@dataclass(frozen=True, slots=True)
class DataSourceAuditEvent:
    project_id: str
    region_code: str
    actor_id: str
    action: str
    resource_id: str
    request_id: str
    outcome: str
    occurred_at: datetime
    before_hash: str | None = None
    after_hash: str | None = None
    details: Mapping[str, object] | None = None


class DataSourceDuplicateError(RuntimeError):
    pass


class DataSourceVersionConflict(RuntimeError):
    pass


class CredentialVaultUnavailable(RuntimeError):
    pass


class DataSourceRepository(Protocol):
    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool: ...

    def robot_display_name(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        robot_id: str,
    ) -> str | None: ...

    def list_sources(
        self,
        *,
        scope: DataSourceScope,
        query: str | None,
        source_types: tuple[str, ...],
        administrative_states: tuple[str, ...],
        connectivity_states: tuple[str, ...],
        credential_states: tuple[str, ...],
    ) -> tuple[DataSourceDetail, ...]: ...

    def get_source(self, *, scope: DataSourceScope, source_id: str) -> DataSourceDetail | None: ...

    def create_source(
        self,
        *,
        source: DataSourceDetail,
        credential: CredentialMaterial | None,
        credential_key: str,
        audit_event: DataSourceAuditEvent,
    ) -> DataSourceDetail: ...

    def save_source(
        self,
        *,
        source: DataSourceDetail,
        expected_version: int,
        audit_event: DataSourceAuditEvent,
    ) -> DataSourceDetail: ...

    def rotate_credential(
        self,
        *,
        source: DataSourceDetail,
        expected_version: int,
        credential: CredentialMaterial,
        credential_key: str,
        audit_event: DataSourceAuditEvent,
    ) -> DataSourceDetail: ...

    def save_connection_test(
        self,
        *,
        source: DataSourceDetail,
        expected_version: int,
        connection_test: DataSourceConnectionTestJob,
        async_job: DataSourceAsyncJob,
        audit_event: DataSourceAuditEvent,
    ) -> DataSourceDetail: ...

    def credential_is_usable(
        self,
        *,
        scope: DataSourceScope,
        source_id: str,
        credential_version: str,
        credential_key: str,
    ) -> bool: ...

    def get_connection_test_job(
        self,
        *,
        scope: DataSourceScope,
        job_id: str,
    ) -> tuple[DataSourceConnectionTestJob, DataSourceAsyncJob] | None: ...

    def append_audit(self, event: DataSourceAuditEvent) -> None: ...


class InMemoryDataSourceRepository:
    """Thread-safe reference repository with the same scoped checks as PostgreSQL."""

    def __init__(
        self,
        *,
        organization_projects: tuple[tuple[str, str], ...] = (),
        robots: tuple[tuple[str, str, str, str, str], ...] = (),
        sources: tuple[DataSourceDetail, ...] = (),
    ) -> None:
        self._organization_projects = set(organization_projects)
        self._organization_projects.update(
            (source.scope.organization_id, source.scope.project_id) for source in sources
        )
        self._robots = {
            (organization_id, project_id, region_code, robot_id): display_name
            for organization_id, project_id, region_code, robot_id, display_name in robots
        }
        self._sources = {_source_key(source.scope, source.id): source for source in sources}
        self._credentials: dict[tuple[str, str, str, str, str], str] = {}
        self._connection_tests: dict[
            tuple[str, str, str, str], tuple[DataSourceConnectionTestJob, DataSourceAsyncJob]
        ] = {}
        self.audit_events: list[DataSourceAuditEvent] = []
        self._lock = RLock()

    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool:
        with self._lock:
            return (organization_id, project_id) in self._organization_projects

    def robot_display_name(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        robot_id: str,
    ) -> str | None:
        with self._lock:
            return self._robots.get((organization_id, project_id, region_code, robot_id))

    def list_sources(
        self,
        *,
        scope: DataSourceScope,
        query: str | None,
        source_types: tuple[str, ...],
        administrative_states: tuple[str, ...],
        connectivity_states: tuple[str, ...],
        credential_states: tuple[str, ...],
    ) -> tuple[DataSourceDetail, ...]:
        needle = query.casefold().strip() if query else ""
        with self._lock:
            values = [
                source
                for source in self._sources.values()
                if source.scope == scope
                and (
                    not needle or needle in source.id.casefold() or needle in source.name.casefold()
                )
                and (not source_types or source.source_type in source_types)
                and (
                    not administrative_states
                    or source.administrative_state in administrative_states
                )
                and (not connectivity_states or source.connectivity.state in connectivity_states)
                and (not credential_states or source.credential.state in credential_states)
            ]
        return tuple(values)

    def get_source(self, *, scope: DataSourceScope, source_id: str) -> DataSourceDetail | None:
        with self._lock:
            return self._sources.get(_source_key(scope, source_id))

    def create_source(
        self,
        *,
        source: DataSourceDetail,
        credential: CredentialMaterial | None,
        credential_key: str,
        audit_event: DataSourceAuditEvent,
    ) -> DataSourceDetail:
        key = _source_key(source.scope, source.id)
        with self._lock:
            if key in self._sources or self._name_exists(source=source):
                raise DataSourceDuplicateError
            self._sources[key] = source
            if credential is not None:
                self._credentials[(*key, source.credential.version)] = _credential_digest(
                    credential, credential_key
                )
            self.audit_events.append(audit_event)
        return source

    def save_source(
        self,
        *,
        source: DataSourceDetail,
        expected_version: int,
        audit_event: DataSourceAuditEvent,
    ) -> DataSourceDetail:
        key = _source_key(source.scope, source.id)
        with self._lock:
            current = self._sources.get(key)
            if current is None or _version(current) != expected_version:
                raise DataSourceVersionConflict
            if self._name_exists(source=source, exclude_id=source.id):
                raise DataSourceDuplicateError
            self._sources[key] = source
            self.audit_events.append(audit_event)
        return source

    def rotate_credential(
        self,
        *,
        source: DataSourceDetail,
        expected_version: int,
        credential: CredentialMaterial,
        credential_key: str,
        audit_event: DataSourceAuditEvent,
    ) -> DataSourceDetail:
        key = _source_key(source.scope, source.id)
        with self._lock:
            current = self._sources.get(key)
            if current is None or _version(current) != expected_version:
                raise DataSourceVersionConflict
            self._sources[key] = source
            self._credentials[(*key, source.credential.version)] = _credential_digest(
                credential, credential_key
            )
            self.audit_events.append(audit_event)
        return source

    def save_connection_test(
        self,
        *,
        source: DataSourceDetail,
        expected_version: int,
        connection_test: DataSourceConnectionTestJob,
        async_job: DataSourceAsyncJob,
        audit_event: DataSourceAuditEvent,
    ) -> DataSourceDetail:
        key = _source_key(source.scope, source.id)
        with self._lock:
            current = self._sources.get(key)
            if current is None or _version(current) != expected_version:
                raise DataSourceVersionConflict
            self._sources[key] = source
            self._connection_tests[_job_key(source.scope, connection_test.id)] = (
                connection_test,
                async_job,
            )
            self.audit_events.append(audit_event)
        return source

    def credential_is_usable(
        self,
        *,
        scope: DataSourceScope,
        source_id: str,
        credential_version: str,
        credential_key: str,
    ) -> bool:
        del credential_key
        with self._lock:
            return (*_source_key(scope, source_id), credential_version) in self._credentials

    def get_connection_test_job(
        self,
        *,
        scope: DataSourceScope,
        job_id: str,
    ) -> tuple[DataSourceConnectionTestJob, DataSourceAsyncJob] | None:
        with self._lock:
            return self._connection_tests.get(_job_key(scope, job_id))

    def append_audit(self, event: DataSourceAuditEvent) -> None:
        with self._lock:
            self.audit_events.append(event)

    def _name_exists(self, *, source: DataSourceDetail, exclude_id: str | None = None) -> bool:
        folded = source.name.casefold()
        return any(
            item.scope == source.scope and item.id != exclude_id and item.name.casefold() == folded
            for item in self._sources.values()
        )


class DbApiCursor(Protocol):
    description: Sequence[Sequence[Any]] | None

    def execute(self, query: str, params: Sequence[object] = ()) -> object: ...

    def fetchone(self) -> object | None: ...

    def fetchall(self) -> Sequence[object]: ...

    def close(self) -> None: ...


class DbApiConnection(Protocol):
    def cursor(self) -> DbApiCursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[], DbApiConnection]


def _source_key(scope: DataSourceScope, source_id: str) -> tuple[str, str, str, str]:
    return (scope.organization_id, scope.project_id, scope.region_code, source_id)


def _job_key(scope: DataSourceScope, job_id: str) -> tuple[str, str, str, str]:
    return (scope.organization_id, scope.project_id, scope.region_code, job_id)


def _version(source: DataSourceDetail) -> int:
    return ResourceVersion.from_etag(source.etag).value


def _credential_digest(credential: CredentialMaterial, credential_key: str) -> str:
    # The in-memory implementation intentionally retains neither plaintext nor an encryption key.
    import hashlib

    return hashlib.sha256(
        f"{credential.kind}\0{credential_key}\0{credential.secret}".encode()
    ).hexdigest()


def _row(cursor: DbApiCursor, raw: object) -> dict[str, object]:
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items()}
    if cursor.description is None:
        raise RuntimeError("database cursor did not describe its result columns")
    return dict(
        zip(
            (str(column[0]) for column in cursor.description),
            cast(Sequence[object], raw),
            strict=True,
        )
    )


def _decode_json(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


def _source_from_document(value: object) -> DataSourceDetail:
    return DataSourceDetail.model_validate(_decode_json(value))


def _job_documents(
    value: object,
) -> tuple[DataSourceConnectionTestJob, DataSourceAsyncJob]:
    document = _decode_json(value)
    if not isinstance(document, Mapping):
        raise RuntimeError("connection-test document must be an object")
    return (
        DataSourceConnectionTestJob.model_validate(document["connection_test"]),
        DataSourceAsyncJob.model_validate(document["async_job"]),
    )


def _document(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = cast(Any, value).model_dump(mode="json")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )


def _json_default(value: object) -> object:
    if hasattr(value, "model_dump"):
        return cast(Any, value).model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"{value.__class__.__name__} is not JSON serializable")


class PostgresDataSourceRepository:
    """RLS-scoped P02 source facts, encrypted credentials, jobs, and audit records."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT 1
                  FROM registry.organization_projects
                 WHERE organization_id = %s AND project_id = %s
                """,
                (organization_id, project_id),
            )
            return cursor.fetchone() is not None
        finally:
            cursor.close()
            connection.close()

    def robot_display_name(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        robot_id: str,
    ) -> str | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT robot.display_name
                  FROM robotics.project_robot_assignments assignment
                  JOIN robotics.robot_assets robot
                    ON robot.organization_id = assignment.organization_id
                   AND robot.robot_id = assignment.robot_id
                 WHERE assignment.organization_id = %s
                   AND assignment.project_id = %s
                   AND assignment.region_code = %s
                   AND assignment.robot_id = %s
                   AND assignment.active
                """,
                (organization_id, project_id, region_code, robot_id),
            )
            raw = cursor.fetchone()
            return None if raw is None else str(_row(cursor, raw)["display_name"])
        finally:
            cursor.close()
            connection.close()

    def list_sources(
        self,
        *,
        scope: DataSourceScope,
        query: str | None,
        source_types: tuple[str, ...],
        administrative_states: tuple[str, ...],
        connectivity_states: tuple[str, ...],
        credential_states: tuple[str, ...],
    ) -> tuple[DataSourceDetail, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT source_document
                  FROM ingest.data_sources
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND (
                        %s::text IS NULL
                        OR name ILIKE '%%' || %s || '%%'
                        OR source_id ILIKE '%%' || %s || '%%'
                   )
                   AND (cardinality(%s::text[]) = 0 OR source_type = ANY(%s::text[]))
                   AND (
                        cardinality(%s::text[]) = 0
                        OR administrative_state = ANY(%s::text[])
                   )
                   AND (
                        cardinality(%s::text[]) = 0
                        OR connectivity_state = ANY(%s::text[])
                   )
                   AND (
                        cardinality(%s::text[]) = 0
                        OR credential_state = ANY(%s::text[])
                   )
                """,
                (
                    scope.organization_id,
                    scope.project_id,
                    scope.region_code,
                    query,
                    query,
                    query,
                    list(source_types),
                    list(source_types),
                    list(administrative_states),
                    list(administrative_states),
                    list(connectivity_states),
                    list(connectivity_states),
                    list(credential_states),
                    list(credential_states),
                ),
            )
            return tuple(
                _source_from_document(_row(cursor, raw)["source_document"])
                for raw in cursor.fetchall()
            )
        finally:
            cursor.close()
            connection.close()

    def get_source(self, *, scope: DataSourceScope, source_id: str) -> DataSourceDetail | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT source_document
                  FROM ingest.data_sources
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND source_id = %s
                """,
                (*scope.model_dump().values(), source_id),
            )
            raw = cursor.fetchone()
            return (
                None if raw is None else _source_from_document(_row(cursor, raw)["source_document"])
            )
        finally:
            cursor.close()
            connection.close()

    def create_source(
        self,
        *,
        source: DataSourceDetail,
        credential: CredentialMaterial | None,
        credential_key: str,
        audit_event: DataSourceAuditEvent,
    ) -> DataSourceDetail:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO ingest.data_sources (
                    organization_id, project_id, region_code, source_id, name, source_type,
                    source_format, administrative_state, connectivity_state, credential_state,
                    version, source_document, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING source_id
                """,
                (*_source_values(source),),
            )
            if cursor.fetchone() is None:
                raise DataSourceDuplicateError
            if credential is not None:
                self._store_credential(cursor, source, credential, credential_key)
            self._insert_audit(cursor, audit_event)
            connection.commit()
            return source
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def save_source(
        self,
        *,
        source: DataSourceDetail,
        expected_version: int,
        audit_event: DataSourceAuditEvent,
    ) -> DataSourceDetail:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._update_source(cursor, source=source, expected_version=expected_version)
            self._insert_audit(cursor, audit_event)
            connection.commit()
            return source
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def rotate_credential(
        self,
        *,
        source: DataSourceDetail,
        expected_version: int,
        credential: CredentialMaterial,
        credential_key: str,
        audit_event: DataSourceAuditEvent,
    ) -> DataSourceDetail:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._update_source(cursor, source=source, expected_version=expected_version)
            self._store_credential(cursor, source, credential, credential_key)
            self._insert_audit(cursor, audit_event)
            connection.commit()
            return source
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def save_connection_test(
        self,
        *,
        source: DataSourceDetail,
        expected_version: int,
        connection_test: DataSourceConnectionTestJob,
        async_job: DataSourceAsyncJob,
        audit_event: DataSourceAuditEvent,
    ) -> DataSourceDetail:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._update_source(cursor, source=source, expected_version=expected_version)
            cursor.execute(
                """
                INSERT INTO ingest.data_source_connection_test_jobs (
                    organization_id, project_id, region_code, job_id, source_id, status,
                    resource_version, job_document, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                """,
                (
                    source.scope.organization_id,
                    source.scope.project_id,
                    source.scope.region_code,
                    connection_test.id,
                    source.id,
                    connection_test.status,
                    int(async_job.resource_version),
                    _document(
                        {
                            "connection_test": connection_test,
                            "async_job": async_job,
                        }
                    ),
                    connection_test.created_at,
                    connection_test.updated_at,
                ),
            )
            self._insert_audit(cursor, audit_event)
            connection.commit()
            return source
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def credential_is_usable(
        self,
        *,
        scope: DataSourceScope,
        source_id: str,
        credential_version: str,
        credential_key: str,
    ) -> bool:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            # Decryption occurs inside PostgreSQL and only this Boolean fact leaves the vault.
            cursor.execute(
                """
                SELECT length(pgp_sym_decrypt(secret_ciphertext, %s::text)) > 0 AS usable
                  FROM ingest.data_source_credentials
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND source_id = %s
                   AND credential_version = %s
                """,
                (credential_key, *_source_key(scope, source_id), int(credential_version)),
            )
            raw = cursor.fetchone()
            return raw is not None and bool(_row(cursor, raw)["usable"])
        except Exception as exc:
            if getattr(exc, "sqlstate", None) in {"39000", "XX000"}:
                raise CredentialVaultUnavailable from exc
            raise
        finally:
            cursor.close()
            connection.close()

    def get_connection_test_job(
        self,
        *,
        scope: DataSourceScope,
        job_id: str,
    ) -> tuple[DataSourceConnectionTestJob, DataSourceAsyncJob] | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT job_document
                  FROM ingest.data_source_connection_test_jobs
                 WHERE organization_id = %s
                   AND project_id = %s
                   AND region_code = %s
                   AND job_id = %s
                """,
                (*scope.model_dump().values(), job_id),
            )
            raw = cursor.fetchone()
            return None if raw is None else _job_documents(_row(cursor, raw)["job_document"])
        finally:
            cursor.close()
            connection.close()

    def append_audit(self, event: DataSourceAuditEvent) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._insert_audit(cursor, event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def _update_source(
        self,
        cursor: DbApiCursor,
        *,
        source: DataSourceDetail,
        expected_version: int,
    ) -> None:
        cursor.execute(
            """
            UPDATE ingest.data_sources
               SET name = %s,
                   source_type = %s,
                   source_format = %s,
                   administrative_state = %s,
                   connectivity_state = %s,
                   credential_state = %s,
                   version = %s,
                   source_document = %s::jsonb,
                   updated_at = %s
             WHERE organization_id = %s
               AND project_id = %s
               AND region_code = %s
               AND source_id = %s
               AND version = %s
             RETURNING source_id
            """,
            (
                source.name,
                source.source_type,
                source.source_format,
                source.administrative_state,
                source.connectivity.state,
                source.credential.state,
                _version(source),
                _document(source),
                source.updated_at,
                *_source_key(source.scope, source.id),
                expected_version,
            ),
        )
        if cursor.fetchone() is None:
            raise DataSourceVersionConflict

    @staticmethod
    def _store_credential(
        cursor: DbApiCursor,
        source: DataSourceDetail,
        credential: CredentialMaterial,
        credential_key: str,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO ingest.data_source_credentials (
                organization_id, project_id, region_code, source_id, credential_version,
                credential_kind, secret_ciphertext, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s,
                      pgp_sym_encrypt(%s::text, %s::text,
                          'cipher-algo=aes256,compress-algo=0'),
                      %s)
            """,
            (
                *_source_key(source.scope, source.id),
                int(source.credential.version),
                credential.kind,
                credential.secret,
                credential_key,
                source.credential.updated_at or source.updated_at,
            ),
        )

    @staticmethod
    def _insert_audit(cursor: DbApiCursor, event: DataSourceAuditEvent) -> None:
        cursor.execute(
            """
            INSERT INTO core.audit_events (
                audit_id, project_id, region_code, actor_id, action, resource_type,
                resource_id, request_id, before_hash, after_hash, details, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, 'DATA_SOURCE', %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                str(uuid4()),
                event.project_id,
                event.region_code,
                event.actor_id,
                event.action,
                event.resource_id,
                event.request_id,
                event.before_hash,
                event.after_hash,
                _document({"outcome": event.outcome, **(event.details or {})}),
                event.occurred_at,
            ),
        )


def _source_values(source: DataSourceDetail) -> tuple[object, ...]:
    return (
        source.scope.organization_id,
        source.scope.project_id,
        source.scope.region_code,
        source.id,
        source.name,
        source.source_type,
        source.source_format,
        source.administrative_state,
        source.connectivity.state,
        source.credential.state,
        _version(source),
        _document(source),
        source.created_at,
        source.updated_at,
    )

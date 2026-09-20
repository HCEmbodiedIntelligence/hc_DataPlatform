"""Encrypted, page-managed object-store configuration.

Object-store credentials are deliberately kept out of the generic runtime-config
snapshot because that channel is readable, revisioned operational metadata.  This
module exposes only a redacted status while encrypting credential material in
PostgreSQL with the process credential-encryption key.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import TYPE_CHECKING, Any, Literal, Protocol
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from hc_data_platform.core.dbapi import normalize_postgres_dsn

if TYPE_CHECKING:
    from hc_data_platform.core.config import Settings


_UNCONFIGURED_VALUES = frozenset(
    {
        "",
        "hc-unconfigured",
        "hc-data-local",
        "minio",
        "minio-local-only",
        "unconfigured-access-key",
        "unconfigured-secret-key",
        "replace-with-your-oss-bucket",
        "replace-with-your-access-key-id",
        "replace-with-your-access-key-secret",
        "https://object-store-unconfigured.invalid",
    }
)


class ObjectStoreConfigurationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _endpoint(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("OSS endpoints must be origin-only HTTPS URLs")
    return normalized


def _masked_access_key(value: str) -> str:
    suffix = value[-4:] if len(value) >= 4 else value
    return f"••••{suffix}"


class ObjectStoreConfigurationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    provider: Literal["oss"] = "oss"
    endpoint: str = Field(min_length=1, max_length=2_048)
    public_endpoint: str = Field(min_length=1, max_length=2_048)
    bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
    region: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    access_key: SecretStr | None = Field(default=None, min_length=1, max_length=512)
    secret_key: SecretStr | None = Field(default=None, min_length=1, max_length=2_048)

    @field_validator("endpoint", "public_endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        return _endpoint(value)

    @model_validator(mode="after")
    def require_credential_pair(self) -> ObjectStoreConfigurationUpdate:
        if (self.access_key is None) != (self.secret_key is None):
            raise ValueError("access_key and secret_key must be supplied together")
        return self


class ObjectStoreConfigurationStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal["hc-object-store-config/v1"] = "hc-object-store-config/v1"
    environment_id: str
    revision: int = Field(ge=0)
    source: Literal["unconfigured", "environment", "database"]
    configured: bool
    provider: Literal["oss", "s3"]
    endpoint: str
    public_endpoint: str
    bucket: str
    region: str
    access_key_configured: bool
    access_key_hint: str | None = None
    activation_required: bool = False
    updated_at: datetime | None = None


class ObjectStoreLocationStatus(BaseModel):
    """Authenticated, credential-free storage location for the shell selector."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal["hc-object-store-location/v1"] = "hc-object-store-location/v1"
    configured: bool
    provider: Literal["oss", "s3"]
    public_endpoint: str
    region: str


@dataclass(frozen=True, slots=True, repr=False)
class StoredObjectStoreConfiguration:
    environment_id: str
    revision: int
    provider: Literal["oss"]
    endpoint: str
    public_endpoint: str
    bucket: str
    region: str
    access_key: str
    secret_key: str
    access_key_hint: str
    updated_at: datetime | None = None

    def settings_updates(self) -> dict[str, str]:
        return {
            "object_store_provider": self.provider,
            "object_store_endpoint": self.endpoint,
            "object_store_public_endpoint": self.public_endpoint,
            "object_store_bucket": self.bucket,
            "object_store_region": self.region,
            "object_store_access_key": self.access_key,
            "object_store_secret_key": self.secret_key,
        }


class ObjectStoreConfigurationRepository(Protocol):
    def current(self, environment_id: str) -> StoredObjectStoreConfiguration | None: ...

    def save(
        self,
        environment_id: str,
        *,
        expected_revision: int,
        command: ObjectStoreConfigurationUpdate,
        actor_id: str,
        request_id: str,
    ) -> StoredObjectStoreConfiguration: ...


class InMemoryObjectStoreConfigurationRepository:
    def __init__(self) -> None:
        self._values: dict[str, StoredObjectStoreConfiguration] = {}
        self._lock = RLock()

    def current(self, environment_id: str) -> StoredObjectStoreConfiguration | None:
        with self._lock:
            return self._values.get(environment_id)

    def save(
        self,
        environment_id: str,
        *,
        expected_revision: int,
        command: ObjectStoreConfigurationUpdate,
        actor_id: str,
        request_id: str,
    ) -> StoredObjectStoreConfiguration:
        del actor_id, request_id
        with self._lock:
            current = self._values.get(environment_id)
            revision = current.revision if current is not None else 0
            if revision != expected_revision:
                raise ObjectStoreConfigurationError(
                    "PLATFORM_OBJECT_STORE_CONFIG_REVISION_CONFLICT",
                    "object-store configuration expected revision is stale",
                )
            access_key, secret_key = _resolved_credentials(current, command)
            stored = StoredObjectStoreConfiguration(
                environment_id=environment_id,
                revision=revision + 1,
                provider=command.provider,
                endpoint=command.endpoint,
                public_endpoint=command.public_endpoint,
                bucket=command.bucket,
                region=command.region,
                access_key=access_key,
                secret_key=secret_key,
                access_key_hint=_masked_access_key(access_key),
            )
            self._values[environment_id] = stored
            return stored


class DbApiCursor(Protocol):
    def execute(self, query: str, parameters: object = ...) -> None: ...

    def fetchone(self) -> Mapping[str, Any] | None: ...


class DbApiConnection(Protocol):
    def __enter__(self) -> DbApiConnection: ...

    def __exit__(self, *args: object) -> None: ...

    def cursor(self) -> DbApiCursor: ...


def _stored_from_row(row: Mapping[str, Any]) -> StoredObjectStoreConfiguration:
    return StoredObjectStoreConfiguration(
        environment_id=str(row["environment_id"]),
        revision=int(row["revision"]),
        provider="oss",
        endpoint=str(row["endpoint"]),
        public_endpoint=str(row["public_endpoint"]),
        bucket=str(row["bucket"]),
        region=str(row["region"]),
        access_key=str(row["access_key"]),
        secret_key=str(row["secret_key"]),
        access_key_hint=str(row["access_key_hint"]),
        updated_at=row.get("updated_at"),
    )


class PostgresObjectStoreConfigurationRepository:
    def __init__(
        self,
        connection_factory: Callable[[], DbApiConnection],
        *,
        encryption_key: str,
    ) -> None:
        if not encryption_key:
            raise ValueError("object-store configuration encryption key is required")
        self._connection_factory = connection_factory
        self._encryption_key = encryption_key

    @classmethod
    def from_dsn(
        cls, dsn: str, *, encryption_key: str
    ) -> PostgresObjectStoreConfigurationRepository:
        normalized = normalize_postgres_dsn(dsn)

        def connect() -> DbApiConnection:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(normalized, row_factory=dict_row)  # type: ignore[return-value]

        return cls(connect, encryption_key=encryption_key)

    def current(self, environment_id: str) -> StoredObjectStoreConfiguration | None:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT environment_id, revision, provider, endpoint, public_endpoint,
                       bucket, region, access_key_hint, updated_at,
                       pgp_sym_decrypt(access_key_ciphertext, %s::text)::text AS access_key,
                       pgp_sym_decrypt(secret_key_ciphertext, %s::text)::text AS secret_key
                FROM platform.platform_object_store_configurations
                WHERE environment_id = %s
                """,
                (self._encryption_key, self._encryption_key, environment_id),
            )
            row = cursor.fetchone()
        return None if row is None else _stored_from_row(row)

    def save(
        self,
        environment_id: str,
        *,
        expected_revision: int,
        command: ObjectStoreConfigurationUpdate,
        actor_id: str,
        request_id: str,
    ) -> StoredObjectStoreConfiguration:
        current = self.current(environment_id)
        access_key, secret_key = _resolved_credentials(current, command)
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE platform.platform_object_store_configurations
                SET revision = revision + 1,
                    provider = %s,
                    endpoint = %s,
                    public_endpoint = %s,
                    bucket = %s,
                    region = %s,
                    access_key_ciphertext = pgp_sym_encrypt(
                        %s::text, %s::text, 'cipher-algo=aes256,compress-algo=0'
                    ),
                    secret_key_ciphertext = pgp_sym_encrypt(
                        %s::text, %s::text, 'cipher-algo=aes256,compress-algo=0'
                    ),
                    access_key_hint = %s,
                    updated_by = %s,
                    request_id = %s,
                    updated_at = statement_timestamp()
                WHERE environment_id = %s AND revision = %s
                RETURNING environment_id, revision, provider, endpoint, public_endpoint,
                          bucket, region, access_key_hint, updated_at,
                          pgp_sym_decrypt(access_key_ciphertext, %s::text)::text AS access_key,
                          pgp_sym_decrypt(secret_key_ciphertext, %s::text)::text AS secret_key
                """,
                (
                    command.provider,
                    command.endpoint,
                    command.public_endpoint,
                    command.bucket,
                    command.region,
                    access_key,
                    self._encryption_key,
                    secret_key,
                    self._encryption_key,
                    _masked_access_key(access_key),
                    actor_id,
                    request_id,
                    environment_id,
                    expected_revision,
                    self._encryption_key,
                    self._encryption_key,
                ),
            )
            row = cursor.fetchone()
            if row is None and expected_revision == 0:
                cursor.execute(
                    """
                    INSERT INTO platform.platform_object_store_configurations (
                        environment_id, revision, provider, endpoint, public_endpoint,
                        bucket, region, access_key_ciphertext, secret_key_ciphertext,
                        access_key_hint, updated_by, request_id
                    )
                    VALUES (
                        %s, 1, %s, %s, %s, %s, %s,
                        pgp_sym_encrypt(
                            %s::text, %s::text, 'cipher-algo=aes256,compress-algo=0'
                        ),
                        pgp_sym_encrypt(
                            %s::text, %s::text, 'cipher-algo=aes256,compress-algo=0'
                        ),
                        %s, %s, %s
                    )
                    ON CONFLICT (environment_id) DO NOTHING
                    RETURNING environment_id, revision, provider, endpoint, public_endpoint,
                              bucket, region, access_key_hint, updated_at,
                              pgp_sym_decrypt(access_key_ciphertext, %s::text)::text AS access_key,
                              pgp_sym_decrypt(secret_key_ciphertext, %s::text)::text AS secret_key
                    """,
                    (
                        environment_id,
                        command.provider,
                        command.endpoint,
                        command.public_endpoint,
                        command.bucket,
                        command.region,
                        access_key,
                        self._encryption_key,
                        secret_key,
                        self._encryption_key,
                        _masked_access_key(access_key),
                        actor_id,
                        request_id,
                        self._encryption_key,
                        self._encryption_key,
                    ),
                )
                row = cursor.fetchone()
        if row is None:
            raise ObjectStoreConfigurationError(
                "PLATFORM_OBJECT_STORE_CONFIG_REVISION_CONFLICT",
                "object-store configuration expected revision is stale",
            )
        return _stored_from_row(row)


def _resolved_credentials(
    current: StoredObjectStoreConfiguration | None,
    command: ObjectStoreConfigurationUpdate,
) -> tuple[str, str]:
    if command.access_key is not None and command.secret_key is not None:
        return (
            command.access_key.get_secret_value(),
            command.secret_key.get_secret_value(),
        )
    if current is None:
        raise ObjectStoreConfigurationError(
            "PLATFORM_OBJECT_STORE_CONFIG_CREDENTIALS_REQUIRED",
            "credentials are required for the first object-store configuration",
        )
    return current.access_key, current.secret_key


def _configured_value(value: str) -> bool:
    return value.strip() not in _UNCONFIGURED_VALUES and not value.startswith("replace-with-")


def settings_object_store_configured(settings: Settings) -> bool:
    # The bundled local MinIO credentials are intentional, usable configuration.
    # Treating them as placeholders silently redirects the API to a saved OSS account.
    if settings.environment in {"local", "test"} and settings.object_store_provider == "s3":
        return all(
            _configured_value(value) or value in {"hc-data-local", "minio", "minio-local-only"}
            for value in (
                settings.object_store_endpoint,
                settings.object_store_public_endpoint,
                settings.object_store_bucket,
                settings.object_store_access_key,
                settings.object_store_secret_key,
            )
        )
    endpoints = (settings.object_store_endpoint, settings.object_store_public_endpoint)
    return (
        all(_configured_value(value) for value in endpoints)
        and _configured_value(settings.object_store_bucket)
        and _configured_value(settings.object_store_access_key)
        and _configured_value(settings.object_store_secret_key)
    )


def _status_from_stored(
    stored: StoredObjectStoreConfiguration,
    *,
    active_revision: int,
) -> ObjectStoreConfigurationStatus:
    return ObjectStoreConfigurationStatus(
        environment_id=stored.environment_id,
        revision=stored.revision,
        source="database",
        configured=True,
        provider=stored.provider,
        endpoint=stored.endpoint,
        public_endpoint=stored.public_endpoint,
        bucket=stored.bucket,
        region=stored.region,
        access_key_configured=True,
        access_key_hint=stored.access_key_hint,
        activation_required=stored.revision != active_revision,
        updated_at=stored.updated_at,
    )


class ObjectStoreConfigurationService:
    def __init__(
        self,
        repository: ObjectStoreConfigurationRepository,
        settings: Settings,
        *,
        active_revision: int = 0,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.environment_id = settings.platform_environment_id
        self.active_revision = active_revision

    def current(self) -> ObjectStoreConfigurationStatus:
        local_minio = (
            self.settings.environment in {"local", "test"}
            and self.settings.object_store_provider == "s3"
            and settings_object_store_configured(self.settings)
        )
        stored = None if local_minio else self.repository.current(self.environment_id)
        if stored is not None:
            return _status_from_stored(stored, active_revision=self.active_revision)
        configured = settings_object_store_configured(self.settings)
        return ObjectStoreConfigurationStatus(
            environment_id=self.environment_id,
            revision=0,
            source="environment" if configured else "unconfigured",
            configured=configured,
            provider=self.settings.object_store_provider,
            endpoint=(
                self.settings.object_store_endpoint
                if _configured_value(self.settings.object_store_endpoint)
                else ""
            ),
            public_endpoint=(
                self.settings.object_store_public_endpoint
                if _configured_value(self.settings.object_store_public_endpoint)
                else ""
            ),
            bucket=(self.settings.object_store_bucket if configured else ""),
            region=self.settings.object_store_region,
            access_key_configured=configured,
            access_key_hint=(
                _masked_access_key(self.settings.object_store_access_key) if configured else None
            ),
        )

    def save(
        self,
        command: ObjectStoreConfigurationUpdate,
        *,
        actor_id: str,
        request_id: str,
    ) -> ObjectStoreConfigurationStatus:
        stored = self.repository.save(
            self.environment_id,
            expected_revision=command.expected_revision,
            command=command,
            actor_id=actor_id,
            request_id=request_id,
        )
        return _status_from_stored(stored, active_revision=self.active_revision)

    def location(self) -> ObjectStoreLocationStatus:
        status = self.current()
        return ObjectStoreLocationStatus(
            configured=status.configured,
            provider=status.provider,
            public_endpoint=status.public_endpoint if status.configured else "",
            region=status.region if status.configured else "",
        )


def load_persisted_object_store_settings(settings: Settings) -> tuple[Settings, int]:
    """Load page-managed credentials only when boot environment values are incomplete.

    A database/configuration outage must not recreate the original startup deadlock: the
    API needs to stay alive so an operator can reach the configuration page.
    """

    if settings.runtime_backend != "production" or settings_object_store_configured(settings):
        return settings, 0
    try:
        repository = PostgresObjectStoreConfigurationRepository.from_dsn(
            settings.postgres_dsn,
            encryption_key=settings.data_source_credential_key.get_secret_value(),
        )
        stored = repository.current(settings.platform_environment_id)
    except Exception:
        return settings, 0
    if stored is None:
        return settings, 0
    return settings.model_copy(update=stored.settings_updates()), stored.revision

from __future__ import annotations

import ipaddress
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCAL_CURSOR_SECRET = "local-cursor-secret-change-me"
_LOCAL_OBJECT_STORE_SECRET = "minio-local-only"


class Settings(BaseSettings):
    """Validated process configuration loaded from ``HC_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="HC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    environment: Literal["local", "test", "staging", "production"] = "local"
    runtime_backend: Literal["memory", "production"] = "production"
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65535)
    postgres_dsn: str = "postgresql+asyncpg://hc:hc@localhost:5432/hc_data"
    temporal_target: str = "localhost:7233"
    outbox_scopes: tuple[str, ...] = ()
    outbox_poll_interval_seconds: float = Field(default=0.5, gt=0, le=60)
    outbox_batch_size: int = Field(default=32, ge=1, le=1000)
    object_store_endpoint: str = Field(default="http://localhost:9000", repr=False)
    object_store_public_endpoint: str | None = Field(default=None, repr=False)
    object_store_bucket: str = Field(default="hc-data-local", min_length=3, max_length=63)
    object_store_access_key: str = Field(default="minio", min_length=1, repr=False)
    object_store_secret_key: str = Field(
        default=_LOCAL_OBJECT_STORE_SECRET,
        min_length=8,
        repr=False,
    )
    object_store_region: str = Field(default="us-east-1", min_length=1)
    lance_root_uri: str | None = None
    alignment_staging_root: str = "/tmp/hc-data/alignment"
    preview_cache_root: str = "/tmp/hc-data/previews"
    artifact_prefix: str = "artifacts"
    mcap_decoder_factory: str | None = None
    jwt_issuer: str = "https://identity.example.invalid/"
    jwt_audience: str = Field(default="hc-data-platform", min_length=1)
    jwt_algorithms: list[str] = Field(default_factory=lambda: ["RS256"], min_length=1)
    jwt_jwks_url: str | None = None
    jwt_signing_key: SecretStr | None = Field(default=None, repr=False)
    cursor_secret: str = Field(default=_LOCAL_CURSOR_SECRET, min_length=16, repr=False)
    readiness_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    enforce_schema_migrations: bool = False
    api_docs_enabled: bool = False

    @field_validator("postgres_dsn")
    @classmethod
    def validate_postgres_dsn(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"postgres", "postgresql", "postgresql+asyncpg"}:
            raise ValueError("must use a PostgreSQL DSN")
        if not parsed.hostname or not parsed.path or parsed.path == "/":
            raise ValueError("must include a PostgreSQL host and database name")
        return value

    @field_validator("temporal_target")
    @classmethod
    def validate_temporal_target(cls, value: str) -> str:
        parsed = urlparse(f"//{value}")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("must contain a valid host:port") from exc
        if not parsed.hostname or port is None or not 1 <= port <= 65535:
            raise ValueError("must contain a valid host:port")
        return value

    @field_validator("outbox_scopes")
    @classmethod
    def validate_outbox_scopes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(
            not item
            or item.count("/") != 1
            or not all(part for part in item.split("/", maxsplit=1))
            for item in normalized
        ):
            raise ValueError("must contain project_id/region_code scope pairs")
        if len(normalized) != len(set(normalized)):
            raise ValueError("must not contain duplicate scope pairs")
        return normalized

    @field_validator("object_store_endpoint", mode="before")
    @classmethod
    def validate_object_store_endpoint(cls, value: object) -> str:
        return _validated_object_store_endpoint(value)

    @field_validator("object_store_public_endpoint", mode="before")
    @classmethod
    def validate_object_store_public_endpoint(cls, value: object) -> str | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return _validated_object_store_endpoint(value)

    @field_validator("jwt_issuer")
    @classmethod
    def validate_jwt_issuer(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("must be an absolute HTTP(S) URL")
        if parsed.query or parsed.fragment:
            raise ValueError("must not contain a query or fragment")
        # The OIDC issuer is an exact identifier. A trailing slash is significant to
        # JWT `iss` validation and therefore must not be normalized away.
        return value

    @field_validator("jwt_jwks_url", mode="before")
    @classmethod
    def validate_optional_https_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("must be an absolute HTTPS URL")
        return value.rstrip("/")

    @field_validator("jwt_signing_key", mode="before")
    @classmethod
    def normalize_optional_signing_key(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("jwt_algorithms")
    @classmethod
    def validate_jwt_algorithms(cls, value: list[str]) -> list[str]:
        normalized = [algorithm.strip() for algorithm in value]
        if any(not algorithm or algorithm.lower() == "none" for algorithm in normalized):
            raise ValueError("must contain only named, signed JWT algorithms")
        if len(normalized) != len(set(normalized)):
            raise ValueError("must not contain duplicate JWT algorithms")
        return normalized

    @model_validator(mode="after")
    def reject_local_secrets_outside_local_environments(self) -> Settings:
        if self.object_store_public_endpoint is None and self.environment in {"local", "test"}:
            self.object_store_public_endpoint = self.object_store_endpoint

        if self.environment in {"staging", "production"}:
            insecure: list[str] = []
            public_endpoint = self.object_store_public_endpoint
            if public_endpoint is None or not _is_safe_public_object_store_endpoint(
                public_endpoint
            ):
                insecure.append("HC_OBJECT_STORE_PUBLIC_ENDPOINT")
            if self.cursor_secret == _LOCAL_CURSOR_SECRET:
                insecure.append("HC_CURSOR_SECRET")
            if self.object_store_secret_key == _LOCAL_OBJECT_STORE_SECRET:
                insecure.append("HC_OBJECT_STORE_SECRET_KEY")
            issuer_host = urlparse(self.jwt_issuer).hostname or ""
            if issuer_host.endswith(".invalid"):
                insecure.append("HC_JWT_ISSUER")
            if not self.enforce_schema_migrations:
                insecure.append("HC_ENFORCE_SCHEMA_MIGRATIONS")
            if self.jwt_jwks_url is None and self.jwt_signing_key is None:
                insecure.append("HC_JWT_JWKS_URL or HC_JWT_SIGNING_KEY")
            if self.api_docs_enabled:
                insecure.append("HC_API_DOCS_ENABLED")
            if insecure:
                joined = ", ".join(insecure)
                raise ValueError(f"insecure local values are forbidden: {joined}")
        return self


def _validated_object_store_endpoint(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("must be an absolute HTTP(S) URL")
    normalized = value.strip()
    parsed = urlparse(normalized)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("must contain a valid HTTP(S) host and port") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.params
        or parsed.query
        or parsed.fragment
        or any(character.isspace() for character in normalized)
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("must be an absolute HTTP(S) URL without userinfo, query, or fragment")
    return normalized.rstrip("/")


def _is_safe_public_object_store_endpoint(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname is None:
        return False
    hostname = parsed.hostname.rstrip(".").lower()
    if (
        hostname == "localhost"
        or hostname.endswith(".localhost")
        or hostname.endswith(".local")
        or hostname.endswith(".internal")
        or hostname.endswith(".svc")
        or hostname.endswith(".cluster.local")
    ):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return "." in hostname
    return address.is_global


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

from __future__ import annotations

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
    )

    environment: Literal["local", "test", "staging", "production"] = "local"
    runtime_backend: Literal["memory", "production"] = "production"
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65535)
    postgres_dsn: str = "postgresql+asyncpg://hc:hc@localhost:5432/hc_data"
    temporal_target: str = "localhost:7233"
    object_store_endpoint: str = "http://localhost:9000"
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

    @field_validator("object_store_endpoint")
    @classmethod
    def validate_object_store_endpoint(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("must be an absolute HTTP(S) URL")
        return value.rstrip("/")

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
        if self.environment in {"staging", "production"}:
            insecure: list[str] = []
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
            if insecure:
                joined = ", ".join(insecure)
                raise ValueError(f"insecure local values are forbidden: {joined}")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

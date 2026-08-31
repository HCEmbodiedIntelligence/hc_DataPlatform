from __future__ import annotations

import ipaddress
import re
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCAL_CURSOR_SECRET = "local-cursor-secret-change-me"
_LOCAL_OBJECT_STORE_SECRET = "minio-local-only"
_LOCAL_DATA_SOURCE_CREDENTIAL_KEY = "local-data-source-credential-key-change-me"
_UNCONFIGURED_OBJECT_STORE_ENDPOINT = "https://object-store-unconfigured.invalid"


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
    platform_environment_id: str = Field(default="local", min_length=1, max_length=128)
    secret_bundle_revision: str = "unversioned"
    runtime_backend: Literal["memory", "production"] = "production"
    release_id: str = "unreleased"
    platform_version: str = "0.1.0"
    git_commit: str = "unknown"
    chart_version: str = "0.1.0"
    release_manifest_digest: str = "unreleased"
    migration_manifest_digest: str = "unreleased"
    component_role: Literal["api", "worker", "media-worker", "migration", "frontend"] = "api"
    component_image_digest: str = "unreleased"
    instance_id: UUID | None = None
    node_name: str | None = Field(default=None, min_length=1, max_length=253)
    pod_name: str | None = Field(default=None, min_length=1, max_length=253)
    kubernetes_node_name: str | None = Field(default=None, min_length=1, max_length=253)
    kubernetes_zone: str | None = Field(default=None, min_length=1, max_length=253)
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65535)
    postgres_dsn: str = "postgresql+asyncpg://hc:hc@localhost:5432/hc_data"
    temporal_target: str = "localhost:7233"
    temporal_worker_build_id: str | None = Field(default=None, min_length=1, max_length=127)
    worker_graceful_shutdown_seconds: int = Field(default=60, ge=1, le=600)
    worker_max_concurrent_workflow_tasks: int = Field(default=4, ge=1, le=1_000)
    worker_max_concurrent_activities: int = Field(default=2, ge=1, le=128)
    worker_max_cached_workflows: int = Field(default=16, ge=1, le=10_000)
    outbox_scopes: tuple[str, ...] = ()
    outbox_poll_interval_seconds: float = Field(default=0.5, gt=0, le=60)
    outbox_batch_size: int = Field(default=32, ge=1, le=1000)
    storage_inventory_scopes: tuple[str, ...] = ()
    storage_inventory_interval_seconds: float = Field(default=3_600, ge=10, le=86_400)
    object_store_provider: Literal["s3", "oss"] = "s3"
    object_store_endpoint: str = Field(default="http://localhost:9000", repr=False)
    object_store_public_endpoint: str | None = Field(default=None, repr=False)
    object_store_bucket: str = Field(default="hc-data-local", max_length=63)
    object_store_access_key: str = Field(default="minio", max_length=512, repr=False)
    object_store_secret_key: str = Field(
        default=_LOCAL_OBJECT_STORE_SECRET,
        max_length=2_048,
        repr=False,
    )
    object_store_region: str = Field(default="us-east-1", max_length=63)
    ingest_part_authorization_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    lance_root_uri: str | None = None
    alignment_staging_root: str = "/tmp/hc-data/alignment"
    aligned_media_staging_root: str = "/tmp/hc-data/aligned-media"
    media_temporal_task_queue: str = Field(default="hc-media-pipeline", min_length=1)
    aligned_media_staging_ttl_hours: int = Field(default=24, ge=1, le=168)
    aligned_media_publication_orphan_ttl_minutes: int = Field(default=30, ge=10, le=10_080)
    media_maintenance_interval_seconds: float = Field(default=300, ge=10, le=86_400)
    aligned_media_allowed_profiles: tuple[str, ...] = ("canonical-h264-crf20-v1",)
    media_max_concurrent_generations: int = Field(default=2, ge=1, le=128)
    media_global_max_concurrent_generations: int = Field(default=4, ge=1, le=128)
    media_ffmpeg_threads: int = Field(default=2, ge=1, le=64)
    artifact_prefix: str = "artifacts"
    auto_annotation_provider_name: str = Field(default="vlm", min_length=1, max_length=128)
    auto_annotation_provider_endpoint: str | None = None
    auto_annotation_provider_models: tuple[str, ...] = ()
    auto_annotation_provider_api_key: SecretStr | None = Field(default=None, repr=False)
    auto_annotation_provider_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    auto_annotation_max_concurrent_jobs: int = Field(default=4, ge=1, le=1000)
    auto_annotation_max_jobs_per_hour: int = Field(default=60, ge=1, le=100_000)
    auto_annotation_daily_cost_limit_micros: int = Field(default=5_000_000, ge=1)
    mcap_decoder_factory: str | None = None
    jwt_issuer: str = "https://identity.example.invalid/"
    jwt_audience: str = Field(default="hc-data-platform", min_length=1)
    jwt_algorithms: list[str] = Field(default_factory=lambda: ["RS256"], min_length=1)
    jwt_jwks_url: str | None = None
    jwt_signing_key: SecretStr | None = Field(default=None, repr=False)
    cursor_secret: str = Field(default=_LOCAL_CURSOR_SECRET, min_length=16, repr=False)
    data_source_credential_key: SecretStr = Field(
        default=SecretStr(_LOCAL_DATA_SOURCE_CREDENTIAL_KEY),
        min_length=24,
        repr=False,
    )
    password_min_length: int = Field(default=6, ge=6, le=128)
    password_max_length: int = Field(default=128, ge=64, le=128)
    password_scrypt_n: int = Field(default=2**15, ge=2**14, le=2**18)
    password_scrypt_r: int = Field(default=8, ge=1, le=32)
    password_scrypt_p: int = Field(default=1, ge=1, le=8)
    session_idle_ttl_seconds: int = Field(default=1_800, ge=1, le=31_536_000)
    session_absolute_ttl_seconds: int = Field(default=86_400, ge=1, le=31_536_000)
    session_touch_interval_seconds: int = Field(default=60, ge=1, le=31_536_000)
    max_active_sessions: int = Field(default=5, ge=1, le=100)
    auth_abuse_enabled: bool = False
    auth_abuse_hmac_secret: SecretStr | None = Field(default=None, repr=False)
    auth_client_ip_mode: Literal["peer", "trusted_proxy"] = "peer"
    auth_trusted_proxy_cidrs: tuple[str, ...] = ()
    auth_login_failure_window_seconds: int = Field(default=900, ge=1, le=86_400)
    auth_login_challenge_after_failures: int = Field(default=3, ge=1, le=100)
    auth_login_delay_after_failures: int = Field(default=5, ge=1, le=100)
    auth_login_delay_initial_seconds: int = Field(default=30, ge=1, le=3_600)
    auth_login_delay_max_seconds: int = Field(default=600, ge=1, le=86_400)
    auth_login_lock_after_failures: int = Field(default=10, ge=1, le=100)
    auth_login_lock_seconds: int = Field(default=3_600, ge=1, le=86_400)
    auth_login_source_rate_limit: int = Field(default=30, ge=1, le=100_000)
    auth_login_source_rate_window_seconds: int = Field(default=60, ge=1, le=86_400)
    auth_login_subject_rate_limit: int = Field(default=20, ge=1, le=100_000)
    auth_login_subject_rate_window_seconds: int = Field(default=900, ge=1, le=86_400)
    auth_registration_source_rate_limit: int = Field(default=5, ge=1, le=100_000)
    auth_registration_source_rate_window_seconds: int = Field(default=600, ge=1, le=86_400)
    auth_registration_subject_rate_limit: int = Field(default=3, ge=1, le=100_000)
    auth_registration_subject_rate_window_seconds: int = Field(default=3_600, ge=1, le=86_400)
    auth_global_rate_limit: int = Field(default=1_000, ge=1, le=1_000_000)
    auth_global_rate_window_seconds: int = Field(default=60, ge=1, le=86_400)
    auth_challenge_provider: Literal["disabled", "turnstile"] = "disabled"
    auth_turnstile_site_key: str | None = Field(default=None, min_length=1, max_length=256)
    auth_turnstile_secret: SecretStr | None = Field(default=None, repr=False)
    auth_turnstile_expected_hostnames: tuple[str, ...] = ()
    auth_turnstile_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    auth_turnstile_read_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    auth_recovery_enabled: bool = False
    auth_recovery_email_verification_ttl_seconds: int = Field(default=600, ge=60, le=86_400)
    auth_recovery_token_ttl_seconds: int = Field(default=900, ge=60, le=86_400)
    auth_recovery_public_base_url: str | None = None
    auth_recovery_email_from: str | None = None
    auth_smtp_host: str | None = None
    auth_smtp_port: int = Field(default=587, ge=1, le=65_535)
    auth_smtp_username: str | None = None
    auth_smtp_password: SecretStr | None = Field(default=None, repr=False)
    auth_smtp_starttls: bool = True
    auth_smtp_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    readiness_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    observability_log_query_url: str | None = Field(default=None, repr=False)
    observability_log_query_bearer_token: SecretStr | None = Field(default=None, repr=False)
    release_feed_trusted_public_keys: tuple[str, ...] = ()
    enforce_schema_migrations: bool = False
    api_docs_enabled: bool = False

    @field_validator("release_id")
    @classmethod
    def validate_release_id(cls, value: str) -> str:
        normalized = value.strip()
        if (
            re.fullmatch(r"(?:unreleased|platform-v[A-Za-z0-9][A-Za-z0-9._-]{0,119})", normalized)
            is None
        ):
            raise ValueError("must be unreleased or a platform-v release identifier")
        return normalized

    @field_validator("platform_environment_id")
    @classmethod
    def validate_platform_environment_id(cls, value: str) -> str:
        normalized = value.strip()
        if (
            re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9._:/-]{0,126}[A-Za-z0-9])?",
                normalized,
            )
            is None
        ):
            raise ValueError("must be an explicit safe environment identifier")
        return normalized

    @field_validator("secret_bundle_revision")
    @classmethod
    def validate_secret_bundle_revision(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != "unversioned" and re.fullmatch(r"sha256:[0-9a-f]{64}", normalized) is None:
            raise ValueError("must be unversioned or a lowercase sha256 digest")
        return normalized

    @field_validator("platform_version", "chart_version")
    @classmethod
    def validate_release_semver(cls, value: str) -> str:
        normalized = value.strip()
        if (
            re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", normalized)
            is None
        ):
            raise ValueError("must be a three-component semantic version")
        return normalized

    @field_validator("git_commit")
    @classmethod
    def validate_git_commit(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != "unknown" and re.fullmatch(r"[0-9a-f]{40}", normalized) is None:
            raise ValueError("must be unknown or a full lowercase Git SHA-1")
        return normalized

    @field_validator(
        "release_manifest_digest",
        "migration_manifest_digest",
        "component_image_digest",
    )
    @classmethod
    def validate_release_digest(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != "unreleased" and re.fullmatch(r"sha256:[0-9a-f]{64}", normalized) is None:
            raise ValueError("must be unreleased or a lowercase sha256 digest")
        return normalized

    @field_validator(
        "node_name",
        "pod_name",
        "kubernetes_node_name",
        "kubernetes_zone",
        mode="before",
    )
    @classmethod
    def validate_optional_instance_metadata(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            return None
        if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._:/-]*[A-Za-z0-9])?", normalized) is None:
            raise ValueError("must contain only safe node metadata characters")
        return normalized

    @field_validator("temporal_worker_build_id")
    @classmethod
    def validate_temporal_worker_build_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._:/-]{0,125}[A-Za-z0-9])?", normalized) is None:
            raise ValueError("must contain only safe Temporal build-ID characters")
        return normalized

    @field_validator("release_feed_trusted_public_keys")
    @classmethod
    def validate_release_feed_public_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("release-feed trusted public keys must be unique")
        if any(re.fullmatch(r"[A-Za-z0-9_-]{43}", item) is None for item in value):
            raise ValueError("release-feed trusted public keys must be raw Ed25519 base64url")
        return value

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

    @field_validator("observability_log_query_url")
    @classmethod
    def validate_observability_log_query_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        parsed = urlparse(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path != "/loki/api/v1/query_range"
        ):
            raise ValueError(
                "must be an HTTP(S) Loki endpoint ending in /loki/api/v1/query_range "
                "without credentials, query, or fragment"
            )
        return normalized

    @field_validator("outbox_scopes", "storage_inventory_scopes")
    @classmethod
    def validate_worker_scopes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(
            not item
            or item.count("/") != 2
            or not all(part for part in item.split("/", maxsplit=2))
            for item in normalized
        ):
            raise ValueError("must contain organization_id/project_id/region_code scope triples")
        if len(normalized) != len(set(normalized)):
            raise ValueError("must not contain duplicate scope triples")
        return normalized

    @field_validator("aligned_media_allowed_profiles", mode="before")
    @classmethod
    def normalize_aligned_media_profiles(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @field_validator("object_store_endpoint", mode="before")
    @classmethod
    def validate_object_store_endpoint(cls, value: object) -> str:
        if isinstance(value, str) and not value.strip():
            return _UNCONFIGURED_OBJECT_STORE_ENDPOINT
        return _validated_object_store_endpoint(value)

    @field_validator("object_store_public_endpoint", mode="before")
    @classmethod
    def validate_object_store_public_endpoint(cls, value: object) -> str | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return _validated_object_store_endpoint(value)

    @field_validator("auto_annotation_provider_endpoint", mode="before")
    @classmethod
    def normalize_optional_auto_annotation_endpoint(cls, value: object) -> object:
        """Treat Compose's empty optional-provider value as not configured.

        The provider remains deliberately unavailable until both endpoint and
        models are configured; an empty environment value must not prevent an
        unrelated worker (such as ingest) from starting.
        """
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("auto_annotation_provider_api_key", mode="before")
    @classmethod
    def normalize_optional_auto_annotation_api_key(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

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

    @field_validator("auth_abuse_hmac_secret", mode="before")
    @classmethod
    def normalize_optional_auth_abuse_hmac_secret(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("auth_turnstile_site_key", mode="before")
    @classmethod
    def normalize_optional_turnstile_site_key(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("auth_turnstile_secret", mode="before")
    @classmethod
    def normalize_optional_turnstile_secret(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "auth_recovery_public_base_url",
        "auth_recovery_email_from",
        "auth_smtp_host",
        "auth_smtp_username",
        mode="before",
    )
    @classmethod
    def normalize_optional_recovery_text(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("auth_smtp_password", mode="before")
    @classmethod
    def normalize_optional_smtp_password(cls, value: object) -> object:
        if isinstance(value, str) and not value:
            return None
        return value

    @field_validator("auth_turnstile_expected_hostnames")
    @classmethod
    def validate_turnstile_expected_hostnames(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for item in value:
            hostname = item.strip().rstrip(".").lower()
            parsed = urlparse(f"//{hostname}")
            if (
                not hostname
                or parsed.hostname != hostname
                or any(character.isspace() for character in hostname)
                or "/" in hostname
                or ":" in hostname
                or hostname.count(".") < 1
            ):
                raise ValueError("must contain fully qualified hostnames without ports or paths")
            normalized.append(hostname)
        if len(normalized) != len(set(normalized)):
            raise ValueError("must not contain duplicate hostnames")
        return tuple(normalized)

    @field_validator("auth_trusted_proxy_cidrs")
    @classmethod
    def validate_auth_trusted_proxy_cidrs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for item in value:
            try:
                network = ipaddress.ip_network(item.strip(), strict=False)
            except ValueError as exc:
                raise ValueError("must contain valid IPv4 or IPv6 CIDR values") from exc
            normalized.append(str(network))
        if len(normalized) != len(set(normalized)):
            raise ValueError("must not contain duplicate CIDR values")
        return tuple(normalized)

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
        if self.worker_max_cached_workflows < self.worker_max_concurrent_workflow_tasks:
            raise ValueError(
                "HC_WORKER_MAX_CACHED_WORKFLOWS must be greater than or equal to "
                "HC_WORKER_MAX_CONCURRENT_WORKFLOW_TASKS"
            )
        alignment_root = PurePosixPath(self.alignment_staging_root)
        media_root = PurePosixPath(self.aligned_media_staging_root)
        if (
            alignment_root == media_root
            or alignment_root in media_root.parents
            or media_root in alignment_root.parents
        ):
            raise ValueError("alignment and aligned-media staging roots must not overlap")
        if self.password_min_length > self.password_max_length:
            raise ValueError("HC_PASSWORD_MIN_LENGTH must not exceed HC_PASSWORD_MAX_LENGTH")
        if self.password_scrypt_n & (self.password_scrypt_n - 1):
            raise ValueError("HC_PASSWORD_SCRYPT_N must be a power of two")
        estimated_scrypt_bytes = 128 * self.password_scrypt_n * self.password_scrypt_r
        if estimated_scrypt_bytes > 1024 * 1024 * 1024:
            raise ValueError("configured scrypt memory cost exceeds 1 GiB")
        if self.session_touch_interval_seconds >= self.session_idle_ttl_seconds:
            raise ValueError(
                "HC_SESSION_TOUCH_INTERVAL_SECONDS must be less than HC_SESSION_IDLE_TTL_SECONDS"
            )
        if self.session_idle_ttl_seconds > self.session_absolute_ttl_seconds:
            raise ValueError(
                "HC_SESSION_IDLE_TTL_SECONDS must not exceed HC_SESSION_ABSOLUTE_TTL_SECONDS"
            )
        if not (
            self.auth_login_challenge_after_failures
            <= self.auth_login_delay_after_failures
            <= self.auth_login_lock_after_failures
        ):
            raise ValueError(
                "auth login challenge, delay, and lock thresholds must be nondecreasing"
            )
        if self.auth_login_delay_initial_seconds > self.auth_login_delay_max_seconds:
            raise ValueError(
                "HC_AUTH_LOGIN_DELAY_INITIAL_SECONDS must not exceed "
                "HC_AUTH_LOGIN_DELAY_MAX_SECONDS"
            )
        if self.auth_abuse_enabled:
            secret = self.auth_abuse_hmac_secret
            if secret is None or len(secret.get_secret_value()) < 32:
                raise ValueError(
                    "HC_AUTH_ABUSE_HMAC_SECRET must contain at least 32 characters when "
                    "HC_AUTH_ABUSE_ENABLED is true"
                )
            if self.auth_client_ip_mode == "trusted_proxy" and not self.auth_trusted_proxy_cidrs:
                raise ValueError(
                    "HC_AUTH_TRUSTED_PROXY_CIDRS is required in trusted_proxy client IP mode"
                )
        if self.auth_challenge_provider == "turnstile":
            if not self.auth_abuse_enabled:
                raise ValueError(
                    "HC_AUTH_CHALLENGE_PROVIDER=turnstile requires HC_AUTH_ABUSE_ENABLED"
                )
            if self.auth_turnstile_site_key is None:
                raise ValueError("HC_AUTH_TURNSTILE_SITE_KEY is required for Turnstile")
            if self.auth_turnstile_secret is None:
                raise ValueError("HC_AUTH_TURNSTILE_SECRET is required for Turnstile")
            if not self.auth_turnstile_expected_hostnames:
                raise ValueError("HC_AUTH_TURNSTILE_EXPECTED_HOSTNAMES is required for Turnstile")
        if self.auth_recovery_enabled:
            if self.auth_recovery_public_base_url is None:
                raise ValueError(
                    "HC_AUTH_RECOVERY_PUBLIC_BASE_URL is required when account recovery is enabled"
                )
            recovery_url = urlparse(self.auth_recovery_public_base_url)
            if (
                recovery_url.scheme not in {"http", "https"}
                or not recovery_url.hostname
                or recovery_url.username is not None
                or recovery_url.password is not None
                or recovery_url.query
                or recovery_url.fragment
            ):
                raise ValueError("HC_AUTH_RECOVERY_PUBLIC_BASE_URL must be an absolute HTTP(S) URL")
            if self.environment in {"staging", "production"} and recovery_url.scheme != "https":
                raise ValueError(
                    "HC_AUTH_RECOVERY_PUBLIC_BASE_URL must use HTTPS outside local/test"
                )
            if self.auth_recovery_email_from is None or "@" not in self.auth_recovery_email_from:
                raise ValueError(
                    "HC_AUTH_RECOVERY_EMAIL_FROM is required when account recovery is enabled"
                )
            if self.auth_smtp_host is None:
                raise ValueError("HC_AUTH_SMTP_HOST is required when account recovery is enabled")
            if (self.auth_smtp_username is None) != (self.auth_smtp_password is None):
                raise ValueError(
                    "HC_AUTH_SMTP_USERNAME and HC_AUTH_SMTP_PASSWORD must be configured together"
                )
            if self.environment in {"staging", "production"} and not self.auth_smtp_starttls:
                raise ValueError("HC_AUTH_SMTP_STARTTLS must be enabled outside local/test")
        if self.object_store_public_endpoint is None and self.environment in {"local", "test"}:
            self.object_store_public_endpoint = self.object_store_endpoint
        if self.object_store_provider == "oss":
            # An OSS deployment may be configured after boot from the platform page.
            # Canonical non-secret sentinels keep dependency composition lazy while
            # readiness reports the object store as unconfigured.
            self.object_store_bucket = self.object_store_bucket.strip() or "hc-unconfigured"
            self.object_store_access_key = (
                self.object_store_access_key.strip() or "unconfigured-access-key"
            )
            self.object_store_secret_key = (
                self.object_store_secret_key.strip() or "unconfigured-secret-key"
            )
            self.object_store_region = self.object_store_region.strip() or "cn-hangzhou"
            if self.object_store_public_endpoint is None:
                self.object_store_public_endpoint = self.object_store_endpoint
            endpoints = tuple(
                endpoint
                for endpoint in (self.object_store_endpoint, self.object_store_public_endpoint)
                if endpoint
            )
            if any(urlparse(endpoint).scheme != "https" for endpoint in endpoints):
                raise ValueError("Alibaba Cloud OSS endpoints must use HTTPS")
        provider_endpoint = self.auto_annotation_provider_endpoint
        if provider_endpoint is not None:
            provider_url = urlparse(provider_endpoint)
            if (
                provider_url.scheme not in {"http", "https"}
                or not provider_url.hostname
                or provider_url.username is not None
                or provider_url.password is not None
                or provider_url.fragment
                or not self.auto_annotation_provider_models
            ):
                raise ValueError(
                    "HC_AUTO_ANNOTATION_PROVIDER_ENDPOINT requires an absolute HTTP(S) URL "
                    "and HC_AUTO_ANNOTATION_PROVIDER_MODELS"
                )
            if self.environment in {"staging", "production"} and provider_url.scheme != "https":
                raise ValueError(
                    "HC_AUTO_ANNOTATION_PROVIDER_ENDPOINT must use HTTPS outside local/test"
                )
        elif self.auto_annotation_provider_models or self.auto_annotation_provider_api_key:
            raise ValueError(
                "automatic annotation models/API key require HC_AUTO_ANNOTATION_PROVIDER_ENDPOINT"
            )

        if self.environment in {"staging", "production"}:
            insecure: list[str] = []
            for path, environment_name in (
                (self.alignment_staging_root, "HC_ALIGNMENT_STAGING_ROOT"),
                (self.aligned_media_staging_root, "HC_ALIGNED_MEDIA_STAGING_ROOT"),
            ):
                if not _is_pod_ephemeral_path(path):
                    insecure.append(environment_name)
            if self.release_id == "unreleased":
                insecure.append("HC_RELEASE_ID")
            if self.secret_bundle_revision in {
                "unversioned",
                f"sha256:{'0' * 64}",
            }:
                insecure.append("HC_SECRET_BUNDLE_REVISION")
            if self.git_commit == "unknown":
                insecure.append("HC_GIT_COMMIT")
            for field_name, environment_name in (
                (self.release_manifest_digest, "HC_RELEASE_MANIFEST_DIGEST"),
                (self.migration_manifest_digest, "HC_MIGRATION_MANIFEST_DIGEST"),
                (self.component_image_digest, "HC_COMPONENT_IMAGE_DIGEST"),
            ):
                if field_name == "unreleased" or field_name == f"sha256:{'0' * 64}":
                    insecure.append(environment_name)
            page_managed_object_store_pending = self.object_store_provider == "oss" and not all(
                value
                not in {
                    "",
                    "hc-unconfigured",
                    "unconfigured-access-key",
                    "unconfigured-secret-key",
                    _UNCONFIGURED_OBJECT_STORE_ENDPOINT,
                }
                for value in (
                    self.object_store_endpoint,
                    self.object_store_public_endpoint or "",
                    self.object_store_bucket,
                    self.object_store_access_key,
                    self.object_store_secret_key,
                )
            )
            public_endpoint = self.object_store_public_endpoint
            if not page_managed_object_store_pending and (
                public_endpoint is None
                or not _is_safe_public_object_store_endpoint(public_endpoint)
            ):
                insecure.append("HC_OBJECT_STORE_PUBLIC_ENDPOINT")
            if self.cursor_secret == _LOCAL_CURSOR_SECRET:
                insecure.append("HC_CURSOR_SECRET")
            if (
                self.data_source_credential_key.get_secret_value()
                == _LOCAL_DATA_SOURCE_CREDENTIAL_KEY
            ):
                insecure.append("HC_DATA_SOURCE_CREDENTIAL_KEY")
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
            if not self.auth_abuse_enabled:
                insecure.append("HC_AUTH_ABUSE_ENABLED")
            if self.auth_abuse_hmac_secret is None:
                insecure.append("HC_AUTH_ABUSE_HMAC_SECRET")
            if self.auth_challenge_provider != "turnstile":
                insecure.append("HC_AUTH_CHALLENGE_PROVIDER=turnstile")
            if self.auth_turnstile_site_key is None:
                insecure.append("HC_AUTH_TURNSTILE_SITE_KEY")
            if self.auth_turnstile_secret is None:
                insecure.append("HC_AUTH_TURNSTILE_SECRET")
            if not self.auth_turnstile_expected_hostnames:
                insecure.append("HC_AUTH_TURNSTILE_EXPECTED_HOSTNAMES")
            if insecure:
                joined = ", ".join(insecure)
                raise ValueError(f"insecure local values are forbidden: {joined}")
        return self


def require_durable_runtime(settings: Settings) -> None:
    """Reject test-only process state for a normal staging/production process."""

    if (
        settings.environment in {"staging", "production"}
        and settings.runtime_backend != "production"
    ):
        raise RuntimeError("staging and production processes require the durable runtime backend")


def _is_pod_ephemeral_path(value: str) -> bool:
    path = PurePosixPath(value)
    root = PurePosixPath("/tmp/hc-data")
    return path.is_absolute() and root in path.parents and str(path) == value


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

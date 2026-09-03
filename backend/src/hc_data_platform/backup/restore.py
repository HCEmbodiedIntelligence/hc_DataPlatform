"""Read-only whole-platform restore planning and target preflight.

The planner deliberately has no restore mutation port.  It authenticates and binds
repository metadata first, then consumes read-only target observations and emits a
deterministic checkpoint.  Object payload download and restore execution belong to
RST3-02.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, cast
from urllib.parse import urlsplit

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from hc_data_platform.backup.contracts import (
    Identifier,
    KmsKeyReference,
    ReleaseIdentityV1,
    RestoreTargetV1,
    SecretDependencyV1,
    Sha256,
    VerifiedManifestV1,
    bind_verified_manifest_to_target,
    canonical_json_bytes,
    find_plaintext_secret_material,
)
from hc_data_platform.backup.postgresql import PostgresEndpoint

RESTORE_PLAN_REQUEST_FORMAT: Literal["hc-platform-restore-plan-request/v1"] = (
    "hc-platform-restore-plan-request/v1"
)
RESTORE_PLAN_FORMAT: Literal["hc-platform-restore-plan/v1"] = "hc-platform-restore-plan/v1"
RESTORE_CAPACITY_HEADROOM_PERCENT: Literal[30] = 30
RestoreObjectMode = Literal["reuse_external", "copy_referenced", "portable"]
MAX_RESTORE_PLAN_REQUEST_BYTES = 4 * 1024 * 1024
MAX_RESTORE_PLAN_BYTES = 4 * 1024 * 1024

ObjectPrefix = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,510}[A-Za-z0-9._-])?$"),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RestorePlanError(RuntimeError):
    """Stable redacted failure raised before any restore target mutation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class RestoreTemporalReadinessV1(_StrictModel):
    cluster_reference: str = Field(min_length=1, max_length=255)
    namespace: Identifier
    service_version: str = Field(min_length=1, max_length=63)
    ready: bool
    evidence_sha256: Sha256


class RestoreReadinessEvidenceV1(_StrictModel):
    target_instance_id: Identifier
    target_environment_id: Identifier
    observed_at: AwareDatetime
    valid_until: AwareDatetime
    evidence_reference: str = Field(min_length=1, max_length=512)
    evidence_sha256: Sha256
    postgresql_available_bytes: int = Field(ge=0)
    object_store_available_bytes: int = Field(ge=0)
    deployed_release: ReleaseIdentityV1
    available_kms_key_references: frozenset[KmsKeyReference]
    temporal: RestoreTemporalReadinessV1
    external_dependencies: tuple[SecretDependencyV1, ...]
    external_dependencies_ready: bool
    api_writes_disabled: bool
    worker_replicas_zero: bool
    domain_names_ready: bool
    certificates_ready: bool

    @field_validator("observed_at", "valid_until")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("restore readiness timestamps must be UTC")
        return value

    @field_validator("evidence_reference")
    @classmethod
    def require_safe_evidence_reference(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"evidence", "gitops", "provider"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path in {"", "/"}
            or any(part in {".", ".."} for part in parsed.path.split("/"))
        ):
            raise ValueError("restore readiness evidence reference is unsafe")
        return value

    @model_validator(mode="after")
    def validate_readiness(self) -> RestoreReadinessEvidenceV1:
        if self.valid_until <= self.observed_at:
            raise ValueError("restore readiness evidence validity is empty")
        dependencies = [
            (
                item.environment_variable,
                item.secret_reference,
                item.secret_key_name,
                item.version,
            )
            for item in self.external_dependencies
        ]
        if dependencies != sorted(dependencies) or len(dependencies) != len(set(dependencies)):
            raise ValueError("restore external dependencies must be sorted and unique")
        return self


class RestorePlanRequestV1(_StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        title="hc-platform-restore-plan-request/v1",
        json_schema_extra={
            "$id": (
                "https://hc-data-platform.invalid/contracts/"
                "hc-platform-restore-plan-request-v1.schema.json"
            ),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    format_version: Literal["hc-platform-restore-plan-request/v1"] = RESTORE_PLAN_REQUEST_FORMAT
    request_id: Identifier
    requested_at: AwareDatetime
    target: RestoreTargetV1
    target_postgresql_database: Identifier
    object_restore_mode: RestoreObjectMode = "reuse_external"
    target_object_store_bucket_reference: str = Field(min_length=1, max_length=255)
    target_object_store_prefix: ObjectPrefix
    readiness: RestoreReadinessEvidenceV1
    capacity_headroom_percent: Literal[30] = RESTORE_CAPACITY_HEADROOM_PERCENT

    @field_validator("requested_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("restore request timestamp must be UTC")
        return value

    @field_validator("target_object_store_prefix")
    @classmethod
    def require_isolated_prefix(cls, value: str) -> str:
        if (
            value.startswith("/")
            or value.endswith("/")
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or any(character in value for character in ("*", "?", "$", "{", "}"))
        ):
            raise ValueError("restore object prefix must be explicit and normalized")
        return value

    @model_validator(mode="after")
    def bind_target_and_evidence(self) -> RestorePlanRequestV1:
        if (
            self.readiness.target_instance_id != self.target.target_instance_id
            or self.readiness.target_environment_id != self.target.target_environment_id
        ):
            raise ValueError("restore readiness evidence is for another target")
        if not self.readiness.observed_at <= self.requested_at <= self.readiness.valid_until:
            raise ValueError("restore request is outside readiness evidence validity")
        return self


class PostgreSQLTargetInspectionV1(_StrictModel):
    database_name: Identifier
    server_major: int = Field(ge=14, le=99)
    user_object_count: int = Field(ge=0)
    inspection_sha256: Sha256


class ObjectStoreTargetInspectionV1(_StrictModel):
    bucket_reference: str = Field(min_length=1, max_length=255)
    prefix: ObjectPrefix
    versioning_enabled: bool
    object_count: int = Field(ge=0)
    inspection_sha256: Sha256


class StagingTargetInspectionV1(_StrictModel):
    available_bytes: int = Field(ge=0)
    inspection_sha256: Sha256


class RestoreTargetInspectionV1(_StrictModel):
    postgresql: PostgreSQLTargetInspectionV1
    object_store: ObjectStoreTargetInspectionV1
    staging: StagingTargetInspectionV1


class RestoreCapacityCheckV1(_StrictModel):
    domain: Literal["postgresql", "object_store", "staging"]
    source_bytes: int = Field(ge=0)
    headroom_percent: Literal[30] = RESTORE_CAPACITY_HEADROOM_PERCENT
    required_bytes: int = Field(ge=0)
    available_bytes: int = Field(ge=0)
    passed: Literal[True] = True


class RestorePreflightCheckV1(_StrictModel):
    name: Literal[
        "backup_signature",
        "source_target_identity",
        "target_fence",
        "exact_release",
        "kms_versions",
        "postgresql_empty",
        "object_store_mode_ready",
        "object_store_versioning",
        "capacity",
        "temporal_identity",
        "external_dependencies",
        "domains_and_certificates",
    ]
    passed: Literal[True] = True
    evidence_sha256: Sha256


class RestorePlanV1(_StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        title="hc-platform-restore-plan/v1",
        json_schema_extra={
            "$id": (
                "https://hc-data-platform.invalid/contracts/hc-platform-restore-plan-v1.schema.json"
            ),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )

    format_version: Literal["hc-platform-restore-plan/v1"] = RESTORE_PLAN_FORMAT
    plan_id: Identifier
    request_id: Identifier
    operation_id: Identifier
    planned_at: AwareDatetime
    status: Literal["PREFLIGHT_PASSED"] = "PREFLIGHT_PASSED"
    dry_run: Literal[True] = True
    writes_enabled: Literal[False] = False
    payloads_downloaded: Literal[False] = False
    backup_id: Identifier
    source_platform_id: Identifier
    source_environment_id: Identifier
    target_instance_id: Identifier
    target_environment_id: Identifier
    target_postgresql_database: Identifier
    object_restore_mode: RestoreObjectMode
    target_object_store_bucket_reference: str = Field(min_length=1, max_length=255)
    target_object_store_prefix: ObjectPrefix
    manifest_sha256: Sha256
    signature_sha256: Sha256
    backup_signing_key_sha256: Sha256
    exact_release_sha256: Sha256
    readiness_evidence_sha256: Sha256
    capacities: tuple[RestoreCapacityCheckV1, RestoreCapacityCheckV1, RestoreCapacityCheckV1]
    checks: tuple[RestorePreflightCheckV1, ...] = Field(min_length=12, max_length=12)
    checkpoint_sha256: Sha256
    next_action: Literal["restore_execute_requires_signed_approval"] = (
        "restore_execute_requires_signed_approval"
    )

    @field_validator("planned_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError("restore plan timestamp must be UTC")
        return value

    @model_validator(mode="after")
    def require_complete_sorted_checks(self) -> RestorePlanV1:
        domains = [item.domain for item in self.capacities]
        if domains != ["postgresql", "object_store", "staging"]:
            raise ValueError("restore capacity checks are incomplete or unsorted")
        names = [item.name for item in self.checks]
        expected = [
            "backup_signature",
            "source_target_identity",
            "target_fence",
            "exact_release",
            "kms_versions",
            "postgresql_empty",
            "object_store_mode_ready",
            "object_store_versioning",
            "capacity",
            "temporal_identity",
            "external_dependencies",
            "domains_and_certificates",
        ]
        if names != expected:
            raise ValueError("restore preflight checks are incomplete or out of order")
        return self


class RestoreTargetInspector(Protocol):
    """Read-only infrastructure inspection port; it has no mutation methods."""

    def inspect(self, request: RestorePlanRequestV1) -> RestoreTargetInspectionV1: ...


class PostgreSQLRestoreTargetAdapter:
    """Inspect a named PostgreSQL database in a read-only transaction."""

    _USER_OBJECT_COUNT = """
        SELECT
            (SELECT count(*) FROM pg_class AS c JOIN pg_namespace AS n
             ON n.oid = c.relnamespace
             WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_')
          + (SELECT count(*) FROM pg_proc AS p JOIN pg_namespace AS n
             ON n.oid = p.pronamespace
             WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_')
          + (SELECT count(*) FROM pg_type AS t JOIN pg_namespace AS n
             ON n.oid = t.typnamespace
             WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
               AND t.typtype <> 'b')
          + (SELECT count(*) FROM pg_extension WHERE extname <> 'plpgsql')
    """

    def __init__(
        self,
        endpoint: PostgresEndpoint,
        *,
        connection_factory: Callable[[PostgresEndpoint], Any] | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._connection_factory = connection_factory or self._connect

    @staticmethod
    def _connect(endpoint: PostgresEndpoint) -> Any:
        import psycopg

        connect = cast(Any, psycopg.connect)
        return connect(**endpoint.connection_kwargs())

    def inspect(self, expected_database: str) -> PostgreSQLTargetInspectionV1:
        if self._endpoint.database != expected_database:
            raise RestorePlanError(
                "RESTORE_POSTGRES_TARGET_MISMATCH",
                "the configured PostgreSQL database differs from the explicit target",
            )
        try:
            with self._connection_factory(self._endpoint) as connection:
                connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY, DEFERRABLE"
                )
                identity = connection.execute(
                    "SELECT current_database(), current_setting('server_version_num')::integer"
                ).fetchone()
                count = connection.execute(self._USER_OBJECT_COUNT).fetchone()
        except RestorePlanError:
            raise
        except Exception as exc:
            raise RestorePlanError(
                "RESTORE_POSTGRES_PREFLIGHT_FAILED",
                "the PostgreSQL target could not be inspected read-only",
            ) from exc
        if identity is None or count is None:
            raise RestorePlanError(
                "RESTORE_POSTGRES_PREFLIGHT_INVALID",
                "the PostgreSQL target returned incomplete identity evidence",
            )
        database_name = str(identity[0])
        server_major = int(identity[1]) // 10000
        user_object_count = int(count[0])
        material = {
            "database_name": database_name,
            "server_major": server_major,
            "user_object_count": user_object_count,
        }
        return PostgreSQLTargetInspectionV1(
            **material,
            inspection_sha256=_sha256_json(material),
        )


class S3RestoreTargetClient(Protocol):
    def get_bucket_versioning(self, **kwargs: object) -> Mapping[str, Any]: ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, Any]: ...


class S3RestoreTargetAdapter:
    """Inspect an exact non-root object prefix without creating or deleting objects."""

    def __init__(
        self,
        client: S3RestoreTargetClient,
        *,
        bucket: str,
        bucket_reference: str,
        prefix: str,
    ) -> None:
        if not bucket or len(bucket) > 255:
            raise ValueError("restore object-store bucket is invalid")
        self._client = client
        self._bucket = bucket
        self._bucket_reference = bucket_reference
        self._prefix = prefix

    def inspect(self) -> ObjectStoreTargetInspectionV1:
        try:
            versioning = self._client.get_bucket_versioning(Bucket=self._bucket)
            listing = self._client.list_objects_v2(
                Bucket=self._bucket,
                Prefix=f"{self._prefix}/",
                MaxKeys=1,
            )
        except Exception as exc:
            raise RestorePlanError(
                "RESTORE_OBJECT_STORE_PREFLIGHT_FAILED",
                "the object-store target could not be inspected read-only",
            ) from exc
        contents = listing.get("Contents", ())
        if not isinstance(contents, (list, tuple)):
            raise RestorePlanError(
                "RESTORE_OBJECT_STORE_PREFLIGHT_INVALID",
                "the object-store target returned malformed inventory evidence",
            )
        material = {
            "bucket_reference": self._bucket_reference,
            "prefix": self._prefix,
            "versioning_enabled": versioning.get("Status") == "Enabled",
            "object_count": len(contents),
        }
        return ObjectStoreTargetInspectionV1(
            **material,
            inspection_sha256=_sha256_json(material),
        )


class CompositeRestoreTargetInspector:
    def __init__(
        self,
        postgresql: PostgreSQLRestoreTargetAdapter,
        object_store: S3RestoreTargetAdapter,
        *,
        staging_directory: Path,
    ) -> None:
        self._postgresql = postgresql
        self._object_store = object_store
        self._staging_directory = staging_directory

    def inspect(self, request: RestorePlanRequestV1) -> RestoreTargetInspectionV1:
        return RestoreTargetInspectionV1(
            postgresql=self._postgresql.inspect(request.target_postgresql_database),
            object_store=self._object_store.inspect(),
            staging=_inspect_staging(self._staging_directory),
        )


def create_restore_plan(
    authenticated: VerifiedManifestV1,
    request: RestorePlanRequestV1,
    *,
    inspector: RestoreTargetInspector,
    clock: Callable[[], datetime] | None = None,
) -> RestorePlanV1:
    """Create a deterministic successful plan, or fail before any restore mutation."""

    planned_at = (clock or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc)
    target = request.target
    if not target.target_is_empty or target.temporary_restore_authorization_id is not None:
        raise RestorePlanError(
            "RESTORE_TARGET_NOT_EMPTY",
            "RST3-01 only accepts an explicitly empty isolated target",
        )
    try:
        verified = bind_verified_manifest_to_target(authenticated, target=target)
    except Exception as exc:
        if hasattr(exc, "code"):
            raise RestorePlanError(cast(Any, exc).code, "backup target binding failed") from exc
        raise
    manifest = verified.manifest
    if manifest.postgresql.database_size_bytes is None:
        raise RestorePlanError(
            "RESTORE_CAPACITY_EVIDENCE_MISSING",
            "the backup predates exact PostgreSQL capacity evidence",
        )
    postgresql_source_bytes = manifest.postgresql.database_size_bytes
    if manifest.postgresql.database_name is None:
        raise RestorePlanError(
            "RESTORE_SOURCE_DATABASE_IDENTITY_MISSING",
            "the backup predates exact source database identity evidence",
        )
    same_object_location = (
        request.target_object_store_bucket_reference == manifest.object_store.bucket_reference
        and request.target_object_store_prefix.strip("/") == manifest.object_store.prefix.strip("/")
    )
    if request.object_restore_mode == "reuse_external" and not same_object_location:
        raise RestorePlanError(
            "RESTORE_EXTERNAL_OBJECT_IDENTITY_MISMATCH",
            "reuse_external must retain the signed source bucket and prefix",
        )
    if request.object_restore_mode != "reuse_external" and same_object_location:
        raise RestorePlanError(
            "RESTORE_OBJECT_STORE_TARGET_SOURCE_COLLISION",
            "the object-store restore target resolves to the recorded source prefix",
        )
    readiness = request.readiness
    if not readiness.observed_at <= planned_at <= readiness.valid_until:
        raise RestorePlanError(
            "RESTORE_READINESS_EVIDENCE_EXPIRED",
            "target readiness evidence is not valid at planning time",
        )
    if readiness.deployed_release != manifest.release:
        raise RestorePlanError(
            "BACKUP_RELEASE_MISMATCH",
            "the deployed target release differs from the exact backup-recorded release",
        )
    if not readiness.api_writes_disabled or not readiness.worker_replicas_zero:
        raise RestorePlanError(
            "RESTORE_TARGET_FENCE_NOT_READY",
            "target API writes must be disabled and Worker replicas must be zero",
        )
    required_keys = {
        manifest.secrets_and_kms.repository_key_reference,
        manifest.secrets_and_kms.signing_key_reference,
        *manifest.secrets_and_kms.portable_recipient_key_references,
    }
    if not required_keys.issubset(readiness.available_kms_key_references):
        raise RestorePlanError(
            "BACKUP_KMS_KEY_UNAVAILABLE",
            "one or more exact backup key versions lack provider readiness evidence",
        )
    if not readiness.temporal.ready:
        raise RestorePlanError(
            "RESTORE_TEMPORAL_NOT_READY", "the target Temporal namespace is not ready"
        )
    temporal_identity = (
        readiness.temporal.cluster_reference,
        readiness.temporal.namespace,
        readiness.temporal.service_version,
    )
    if temporal_identity != (
        manifest.temporal.cluster_reference,
        manifest.temporal.namespace,
        manifest.temporal.service_version,
    ):
        raise RestorePlanError(
            "RESTORE_TEMPORAL_TARGET_MISMATCH",
            "the target Temporal identity differs from the backup",
        )
    expected_dependencies = tuple(
        sorted(
            manifest.secrets_and_kms.dependencies,
            key=lambda item: (
                item.environment_variable,
                item.secret_reference,
                item.secret_key_name,
                item.version,
            ),
        )
    )
    if not readiness.external_dependencies_ready or readiness.external_dependencies != (
        expected_dependencies
    ):
        raise RestorePlanError(
            "RESTORE_EXTERNAL_DEPENDENCY_MISMATCH",
            "required external dependency versions are unavailable or do not match",
        )
    if not readiness.domain_names_ready or not readiness.certificates_ready:
        raise RestorePlanError(
            "RESTORE_ENDPOINT_READINESS_FAILED",
            "required target domain names or certificates are not ready",
        )

    inspection = inspector.inspect(request)
    if (
        inspection.postgresql.database_name != request.target_postgresql_database
        or inspection.postgresql.server_major != manifest.postgresql.major_version
    ):
        raise RestorePlanError(
            "BACKUP_POSTGRESQL_MAJOR_MISMATCH",
            "the exact named PostgreSQL target is incompatible with the backup",
        )
    if inspection.postgresql.user_object_count != 0:
        raise RestorePlanError(
            "RESTORE_POSTGRES_TARGET_NOT_EMPTY",
            "the named PostgreSQL target contains user objects",
        )
    if (
        inspection.object_store.bucket_reference != request.target_object_store_bucket_reference
        or inspection.object_store.prefix != request.target_object_store_prefix
    ):
        raise RestorePlanError(
            "RESTORE_OBJECT_STORE_TARGET_MISMATCH",
            "the inspected object target differs from the explicit target",
        )
    if (
        request.object_restore_mode != "reuse_external"
        and inspection.object_store.object_count != 0
    ):
        raise RestorePlanError(
            "RESTORE_OBJECT_STORE_TARGET_NOT_EMPTY",
            "the exact object-store target prefix is not empty",
        )
    if not inspection.object_store.versioning_enabled:
        raise RestorePlanError(
            "RESTORE_OBJECT_STORE_VERSIONING_REQUIRED",
            "the restore object-store target must have versioning enabled",
        )

    capacities = (
        _capacity(
            "postgresql",
            postgresql_source_bytes,
            readiness.postgresql_available_bytes,
        ),
        _capacity(
            "object_store",
            (
                0
                if request.object_restore_mode == "reuse_external"
                else manifest.object_store.total_bytes
            ),
            readiness.object_store_available_bytes,
        ),
        _capacity(
            "staging",
            sum(artifact.size_bytes for artifact in manifest.artifacts),
            inspection.staging.available_bytes,
        ),
    )
    failed = next((item for item in capacities if item.required_bytes > item.available_bytes), None)
    if failed is not None:
        raise RestorePlanError(
            "RESTORE_CAPACITY_INSUFFICIENT",
            f"the {failed.domain} restore capacity is below the required headroom",
        )

    release_sha = _sha256_json(manifest.release.model_dump(mode="json"))
    evidence = {
        "request": request.model_dump(mode="json"),
        "manifest_sha256": authenticated.manifest_sha256,
        "signature_sha256": authenticated.signature_sha256,
        "inspection": inspection.model_dump(mode="json"),
        "capacities": [item.model_dump(mode="json") for item in capacities],
    }
    checkpoint_sha = _sha256_json(evidence)
    checks = (
        _check("backup_signature", authenticated.signature_sha256),
        _check("source_target_identity", authenticated.manifest_sha256),
        _check("target_fence", _sha256_json(target.model_dump(mode="json"))),
        _check("exact_release", release_sha),
        _check("kms_versions", _sha256_json(sorted(required_keys))),
        _check("postgresql_empty", inspection.postgresql.inspection_sha256),
        _check("object_store_mode_ready", inspection.object_store.inspection_sha256),
        _check("object_store_versioning", inspection.object_store.inspection_sha256),
        _check("capacity", _sha256_json([item.model_dump(mode="json") for item in capacities])),
        _check("temporal_identity", readiness.temporal.evidence_sha256),
        _check("external_dependencies", readiness.evidence_sha256),
        _check("domains_and_certificates", readiness.evidence_sha256),
    )
    plan_id = f"restore-plan-{checkpoint_sha[:24]}"
    return RestorePlanV1(
        plan_id=plan_id,
        request_id=request.request_id,
        operation_id=target.operation_id,
        planned_at=planned_at,
        backup_id=manifest.backup_id,
        source_platform_id=manifest.source_platform_id,
        source_environment_id=manifest.source_environment_id,
        target_instance_id=target.target_instance_id,
        target_environment_id=target.target_environment_id,
        target_postgresql_database=request.target_postgresql_database,
        object_restore_mode=request.object_restore_mode,
        target_object_store_bucket_reference=request.target_object_store_bucket_reference,
        target_object_store_prefix=request.target_object_store_prefix,
        manifest_sha256=authenticated.manifest_sha256,
        signature_sha256=authenticated.signature_sha256,
        backup_signing_key_sha256=manifest.signature.public_key_sha256,
        exact_release_sha256=release_sha,
        readiness_evidence_sha256=readiness.evidence_sha256,
        capacities=capacities,
        checks=checks,
        checkpoint_sha256=checkpoint_sha,
    )


def load_restore_plan_request(path: Path) -> RestorePlanRequestV1:
    payload = _read_private_file(path, maximum_bytes=MAX_RESTORE_PLAN_REQUEST_BYTES)
    try:
        value = json.loads(payload, object_pairs_hook=_unique_object)
        if not isinstance(value, dict):
            raise ValueError("top-level value is not an object")
        if find_plaintext_secret_material(value):
            raise RestorePlanError(
                "RESTORE_PLAN_PLAINTEXT_SECRET",
                "restore plan input contains Secret-shaped material",
            )
        return RestorePlanRequestV1.model_validate(value)
    except RestorePlanError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise RestorePlanError(
            "RESTORE_PLAN_REQUEST_INVALID", "the restore plan request is invalid"
        ) from exc


def load_restore_plan(path: Path) -> RestorePlanV1:
    payload = _read_private_file(path, maximum_bytes=MAX_RESTORE_PLAN_BYTES)
    try:
        value = json.loads(payload, object_pairs_hook=_unique_object)
        if not isinstance(value, dict):
            raise ValueError("top-level value is not an object")
        if find_plaintext_secret_material(value):
            raise RestorePlanError(
                "RESTORE_PLAN_PLAINTEXT_SECRET",
                "restore plan contains Secret-shaped material",
            )
        return RestorePlanV1.model_validate(value)
    except RestorePlanError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise RestorePlanError("RESTORE_PLAN_INVALID", "the restore plan is invalid") from exc


def _capacity(
    domain: Literal["postgresql", "object_store", "staging"],
    source_bytes: int,
    available_bytes: int,
) -> RestoreCapacityCheckV1:
    required = (source_bytes * (100 + RESTORE_CAPACITY_HEADROOM_PERCENT) + 99) // 100
    # Construct a passed result only after comparing; callers map failure to a stable code.
    return RestoreCapacityCheckV1(
        domain=domain,
        source_bytes=source_bytes,
        required_bytes=required,
        available_bytes=available_bytes,
    )


def _check(name: Any, evidence_sha256: str) -> RestorePreflightCheckV1:
    return RestorePreflightCheckV1(name=name, evidence_sha256=evidence_sha256)


def _inspect_staging(path: Path) -> StagingTargetInspectionV1:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
        filesystem = os.statvfs(resolved)
    except (FileNotFoundError, OSError) as exc:
        raise RestorePlanError(
            "RESTORE_STAGING_PREFLIGHT_FAILED",
            "the restore staging directory could not be inspected",
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise RestorePlanError(
            "RESTORE_STAGING_UNSAFE",
            "the restore staging directory must be owner-only",
        )
    available = filesystem.f_bavail * filesystem.f_frsize
    material = {
        "device": info.st_dev,
        "available_bytes": available,
    }
    return StagingTargetInspectionV1(
        available_bytes=available,
        inspection_sha256=_sha256_json(material),
    )


def _read_private_file(path: Path, *, maximum_bytes: int) -> bytes:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as exc:
        raise RestorePlanError(
            "RESTORE_PLAN_REQUEST_INVALID", "the restore plan input is unavailable"
        ) from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or not 0 < info.st_size <= maximum_bytes
    ):
        raise RestorePlanError(
            "RESTORE_PLAN_REQUEST_INVALID",
            "the restore plan input violates its owner-only bounded file contract",
        )
    return resolved.read_bytes()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()

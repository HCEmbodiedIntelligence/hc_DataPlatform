"""Fenced PostgreSQL logical backup and isolated-restore verification.

The adapter intentionally treats PostgreSQL client programs as an external Job
dependency.  It never places a DSN or password in a command argument, requires
the client and server major versions to match, and binds ``pg_dump`` to the
same exported snapshot used to calculate the verification evidence.

This module records a WAL anchor but does not claim that an anchor alone is a
physical backup.  A recoverable baseline and retained WAL are separate
provider responsibilities.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from hc_data_platform.backup.contracts import Sha256

SafeName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,126}[A-Za-z0-9])?$",
    ),
]
SafeHost = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9_.:-]{0,253}[A-Za-z0-9])?$",
    ),
]
WalLsn = Annotated[str, StringConstraints(pattern=r"^[0-9A-F]+/[0-9A-F]+$")]
_TOOL_VERSION = re.compile(r"\(PostgreSQL\)\s+(?P<major>[0-9]+)(?:\.[0-9]+)?")
_CONTROL_SYSTEM_IDENTIFIER = re.compile(
    rb"^Database system identifier:\s*(?P<value>[0-9]+)\s*$", re.MULTILINE
)
_CONTROL_TIMELINE = re.compile(
    rb"^Latest checkpoint's TimeLineID:\s*(?P<value>[0-9]+)\s*$", re.MULTILINE
)
_USER_SCHEMA_PREDICATE = "n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PostgresBackupError(RuntimeError):
    """Stable, redacted PostgreSQL backup failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class PostgresEndpoint(_StrictModel):
    """Structured libpq endpoint; its password is never rendered into argv."""

    host: SafeHost
    port: int = Field(default=5432, ge=1, le=65535)
    database: SafeName
    username: SafeName
    password: SecretStr
    sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = (
        "verify-full"
    )

    @field_validator("password")
    @classmethod
    def reject_invalid_password(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw or "\n" in raw or "\r" in raw or "\x00" in raw:
            raise ValueError("PostgreSQL password contains forbidden characters")
        return value

    def connection_kwargs(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.username,
            "password": self.password.get_secret_value(),
            "sslmode": self.sslmode,
        }

    def command_environment(self, *, passfile: Path) -> dict[str, str]:
        return {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "PGCONNECT_TIMEOUT": "10",
            "PGDATABASE": self.database,
            "PGHOST": self.host,
            "PGPASSFILE": str(passfile),
            "PGPORT": str(self.port),
            "PGSSLMODE": self.sslmode,
            "PGUSER": self.username,
        }


class MaintenanceBackupLease(_StrictModel):
    environment_id: SafeName
    operation_id: SafeName
    owner_instance_id: UUID
    fencing_token: int = Field(gt=0)


class PostgresToolchain(_StrictModel):
    """Argument prefixes allow a dedicated external Job or test container."""

    pg_dump: tuple[str, ...] = ("pg_dump",)
    pg_restore: tuple[str, ...] = ("pg_restore",)
    pg_basebackup: tuple[str, ...] = ("pg_basebackup",)
    pg_controldata: tuple[str, ...] = ("pg_controldata",)
    pg_verifybackup: tuple[str, ...] = ("pg_verifybackup",)

    @field_validator("pg_dump", "pg_restore", "pg_basebackup", "pg_controldata", "pg_verifybackup")
    @classmethod
    def require_bounded_argv_prefix(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) > 32:
            raise ValueError("PostgreSQL tool argv prefix must contain 1..32 arguments")
        if any(not item or "\x00" in item or "\n" in item or "\r" in item for item in value):
            raise ValueError("PostgreSQL tool argv prefix contains invalid text")
        return value


def compose_functional_postgres_toolchain(
    staging_directory: Path,
    *,
    run_id: str,
) -> PostgresToolchain:
    """Use the pinned Compose PostgreSQL image for host-side functional CLI tools."""

    if re.fullmatch(r"[a-z0-9][a-z0-9-]{5,63}", run_id) is None:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_CONFIGURATION_INVALID",
            "the functional PostgreSQL tool run ID is invalid",
        )
    staging = _assert_secure_directory(staging_directory)
    common = (
        "docker",
        "run",
        "--rm",
        "--network",
        "host",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--label",
        f"hc.migration.run_id={run_id}",
        "--volume",
        f"{staging}:{staging}",
        "--env",
        "LANG",
        "--env",
        "LC_ALL",
        "--env",
        "PGCONNECT_TIMEOUT",
        "--env",
        "PGDATABASE",
        "--env",
        "PGHOST",
        "--env",
        "PGPASSFILE",
        "--env",
        "PGPORT",
        "--env",
        "PGSSLMODE",
        "--env",
        "PGUSER",
        (
            "postgres:16.10-bookworm@sha256:"
            "61c57e5eeda4d69232c97cb23ae5d95d170a1a2fd0b40a6b4f34fb56819b056d"
        ),
    )
    return PostgresToolchain(
        pg_dump=(*common, "pg_dump"),
        pg_restore=(*common, "pg_restore"),
    )


class PostgresWalAnchor(_StrictModel):
    format_version: Literal["postgresql-wal-anchor/v1"] = "postgresql-wal-anchor/v1"
    system_identifier: int = Field(gt=0)
    timeline_id: int = Field(gt=0)
    wal_lsn: WalLsn
    server_version: str = Field(min_length=1, max_length=128)
    server_major: int = Field(ge=14, le=99)
    database_name: SafeName
    in_recovery: bool
    physical_recovery_ready: Literal[False] = False

    def manifest_coordinate(self) -> str:
        return (
            "postgresql-wal-anchor/v1:"
            f"system={self.system_identifier};timeline={self.timeline_id};lsn={self.wal_lsn}"
        )


class TableContentDigest(_StrictModel):
    schema_name: str = Field(min_length=1, max_length=128)
    table_name: str = Field(min_length=1, max_length=128)
    row_count: int = Field(ge=0)
    content_sha256: Sha256


class DatabaseVerificationEvidence(_StrictModel):
    database_size_bytes: int = Field(ge=0)
    migration_count: int = Field(gt=0)
    migration_sha256: Sha256
    schema_object_count: int = Field(ge=0)
    schema_sha256: Sha256
    constraint_count: int = Field(ge=0)
    constraint_sha256: Sha256
    sequence_count: int = Field(ge=0)
    sequence_sha256: Sha256
    tables: tuple[TableContentDigest, ...]
    aggregate_sha256: Sha256

    @model_validator(mode="after")
    def require_sorted_unique_tables(self) -> DatabaseVerificationEvidence:
        names = [(table.schema_name, table.table_name) for table in self.tables]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("table evidence must be sorted and unique")
        return self


class LogicalDumpArtifact(_StrictModel):
    path: Path
    size_bytes: int = Field(gt=0)
    sha256: Sha256
    archive_format: Literal["postgresql_custom"] = "postgresql_custom"
    server_major: int = Field(ge=14, le=99)
    wal_anchor: PostgresWalAnchor
    source_evidence: DatabaseVerificationEvidence


class ManifestLogicalDumpArtifact(_StrictModel):
    """Logical dump evidence reconstructed only from an authenticated manifest."""

    path: Path
    size_bytes: int = Field(gt=0)
    sha256: Sha256
    archive_format: Literal["postgresql_custom"] = "postgresql_custom"
    server_major: int = Field(ge=14, le=99)
    server_version: str = Field(min_length=1, max_length=63)
    source_database: SafeName
    source_evidence: DatabaseVerificationEvidence


class RestoreVerificationReport(_StrictModel):
    target_database: SafeName
    server_major: int = Field(ge=14, le=99)
    migration_match: Literal[True] = True
    schema_match: Literal[True] = True
    constraints_match: Literal[True] = True
    row_count_match: Literal[True] = True
    content_hash_match: Literal[True] = True
    aggregate_sha256: Sha256


class PhysicalBackupFile(_StrictModel):
    relative_path: str = Field(
        min_length=1,
        max_length=1024,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,1023}$",
    )
    size_bytes: int = Field(ge=0)
    sha256: Sha256

    @field_validator("relative_path")
    @classmethod
    def require_normalized_path(cls, value: str) -> str:
        if any(part in {"", ".", ".."} for part in value.split("/")):
            raise ValueError("physical backup file path is not normalized")
        return value


class PhysicalBaselineArtifact(_StrictModel):
    path: Path
    server_major: int = Field(ge=14, le=99)
    system_identifier: int = Field(gt=0)
    timeline_id: int = Field(gt=0)
    start_lsn: WalLsn
    end_lsn: WalLsn
    manifest_sha256: Sha256
    total_bytes: int = Field(gt=0)
    files: tuple[PhysicalBackupFile, ...] = Field(min_length=1)
    aggregate_sha256: Sha256
    baseline_recovery_ready: Literal[True] = True

    @model_validator(mode="after")
    def require_sorted_unique_files(self) -> PhysicalBaselineArtifact:
        paths = [item.relative_path for item in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("physical backup files must be sorted and unique")
        return self


class WalArchiveReceipt(_StrictModel):
    format_version: Literal["postgresql-wal-archive-receipt/v1"] = (
        "postgresql-wal-archive-receipt/v1"
    )
    archive_reference: str = Field(pattern=r"^wal-archive://[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    system_identifier: int = Field(gt=0)
    timeline_id: int = Field(gt=0)
    continuous_from_lsn: WalLsn
    continuous_through_lsn: WalLsn
    immutable: Literal[True] = True
    verified_at: AwareDatetime
    retained_until: AwareDatetime
    verification_sha256: Sha256


class PostgresPITREvidence(_StrictModel):
    format_version: Literal["postgresql-pitr-evidence/v1"] = "postgresql-pitr-evidence/v1"
    baseline: PhysicalBaselineArtifact
    wal_archive: WalArchiveReceipt
    recovery_target_lsn: WalLsn
    physical_recovery_ready: Literal[True] = True


class WalArchiveVerificationPort(Protocol):
    """External immutable WAL provider verification boundary."""

    def verify_for_baseline(self, baseline: PhysicalBaselineArtifact) -> WalArchiveReceipt: ...


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""


class CommandRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str],
        cwd: Path,
        timeout_seconds: int,
        capture_stdout: bool = False,
    ) -> CommandResult: ...


class SubprocessCommandRunner:
    """No shell, no ambient environment, and no database stderr disclosure."""

    def run(
        self,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str],
        cwd: Path,
        timeout_seconds: int,
        capture_stdout: bool = False,
    ) -> CommandResult:
        try:
            completed = subprocess.run(
                list(argv),
                cwd=cwd,
                env=dict(environment),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=timeout_seconds,
            )
        except FileNotFoundError as exc:
            raise PostgresBackupError(
                "BACKUP_POSTGRES_TOOL_NOT_FOUND",
                "a required PostgreSQL client program is unavailable",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise PostgresBackupError(
                "BACKUP_POSTGRES_TOOL_TIMEOUT",
                "a PostgreSQL client program exceeded its deadline",
            ) from exc
        stdout = completed.stdout or b""
        if len(stdout) > 4096:
            raise PostgresBackupError(
                "BACKUP_POSTGRES_TOOL_OUTPUT_INVALID",
                "PostgreSQL client identity output exceeded its bound",
            )
        return CommandResult(returncode=completed.returncode, stdout=stdout)


ConnectionFactory = Callable[[PostgresEndpoint], Any]


def _default_connection_factory(endpoint: PostgresEndpoint) -> Any:
    import psycopg

    return psycopg.connect(
        host=endpoint.host,
        port=endpoint.port,
        dbname=endpoint.database,
        user=endpoint.username,
        password=endpoint.password.get_secret_value(),
        sslmode=endpoint.sslmode,
    )


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _escape_pgpass(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:")


@contextmanager
def _pgpass_file(endpoint: PostgresEndpoint, directory: Path) -> Iterator[Path]:
    path = directory / f".pgpass-{uuid4().hex}"
    lines = []
    for database in dict.fromkeys((endpoint.database, "replication")):
        lines.append(
            ":".join(
                _escape_pgpass(value)
                for value in (
                    endpoint.host,
                    str(endpoint.port),
                    database,
                    endpoint.username,
                    endpoint.password.get_secret_value(),
                )
            )
        )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(lines))
            stream.write("\n")
        yield path
    finally:
        with suppress(FileNotFoundError):
            path.unlink()


def _assert_secure_directory(directory: Path) -> Path:
    resolved = directory.resolve(strict=True)
    info = resolved.stat()
    if not stat.S_ISDIR(info.st_mode) or directory.is_symlink():
        raise PostgresBackupError(
            "BACKUP_POSTGRES_STAGING_UNSAFE", "the staging path is not a real directory"
        )
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_STAGING_UNSAFE",
            "the staging directory must be owner-only",
        )
    return resolved


def _server_identity(connection: Any) -> tuple[int, str, int, int, str, bool, int]:
    row = connection.execute(
        """
        SELECT
            current_setting('server_version_num')::integer,
            current_setting('server_version'),
            (SELECT system_identifier FROM pg_control_system()),
            (SELECT timeline_id FROM pg_control_checkpoint()),
            pg_current_wal_lsn()::text,
            pg_is_in_recovery(),
            (SELECT oid::bigint FROM pg_database WHERE datname = current_database())
        """
    ).fetchone()
    if row is None:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_IDENTITY_UNAVAILABLE",
            "PostgreSQL did not return its recovery identity",
        )
    version_num = int(row[0])
    return (
        version_num // 10000,
        str(row[1]),
        int(row[2]),
        int(row[3]),
        str(row[4]),
        bool(row[5]),
        int(row[6]),
    )


def _assert_backup_lease(connection: Any, lease: MaintenanceBackupLease) -> None:
    row = connection.execute(
        """
        SELECT 1
        FROM platform.maintenance_operations AS operation
        JOIN platform.environment_fences AS fence USING (environment_id)
        WHERE operation.operation_id = %s
          AND operation.environment_id = %s
          AND operation.operation_kind = 'BACKUP'
          AND operation.state = 'EXECUTING'
          AND operation.owner_instance_id = %s
          AND operation.fencing_token = %s
          AND operation.lease_until > statement_timestamp()
          AND fence.mode = 'READ_ONLY_MAINTENANCE'
          AND fence.fencing_token = operation.fencing_token
        """,
        (
            lease.operation_id,
            lease.environment_id,
            lease.owner_instance_id,
            lease.fencing_token,
        ),
    ).fetchone()
    if row is None:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_FENCE_REJECTED",
            "the backup Job does not own a current fenced maintenance operation",
        )


def assert_current_backup_lease(
    endpoint: PostgresEndpoint,
    lease: MaintenanceBackupLease,
    *,
    connection_factory: ConnectionFactory | None = None,
) -> None:
    """Verify an exact backup lease without accepting a password-bearing DSN."""

    factory = connection_factory or _default_connection_factory
    try:
        with factory(endpoint) as connection:
            _assert_backup_lease(connection, lease)
    except PostgresBackupError:
        raise
    except Exception as exc:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_CONNECTION_FAILED",
            "the PostgreSQL maintenance fence could not be verified",
        ) from exc


def _migration_rows(connection: Any) -> list[tuple[str, str]]:
    exists = connection.execute(
        "SELECT to_regclass('core.schema_migrations') IS NOT NULL"
    ).fetchone()
    if exists is None or not bool(exists[0]):
        raise PostgresBackupError(
            "BACKUP_POSTGRES_MIGRATION_LEDGER_MISSING",
            "the source database has no migration ledger",
        )
    rows = connection.execute(
        """
        SELECT version, checksum_sha256
        FROM core.schema_migrations
        ORDER BY version COLLATE "C"
        """
    ).fetchall()
    result = [(str(row[0]), str(row[1])) for row in rows]
    if not result:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_MIGRATION_LEDGER_EMPTY",
            "the source database migration ledger is empty",
        )
    return result


def _schema_rows(connection: Any) -> list[tuple[object, ...]]:
    statements = (
        """
        SELECT 'schema', n.nspname, pg_get_userbyid(n.nspowner)
        FROM pg_namespace AS n
        WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
        ORDER BY n.nspname COLLATE "C"
        """,
        f"""
        SELECT 'relation', n.nspname, c.relname, c.relkind, c.relpersistence,
               c.relrowsecurity, c.relforcerowsecurity,
               CASE WHEN c.relkind IN ('v', 'm') THEN pg_get_viewdef(c.oid, true) ELSE NULL END,
               pg_get_userbyid(c.relowner)
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE {_USER_SCHEMA_PREDICATE}
          AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend AS d
              WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e'
          )
        ORDER BY n.nspname COLLATE "C", c.relname COLLATE "C"
        """,
        f"""
        SELECT 'column', n.nspname, c.relname, a.attname, a.attnum,
               format_type(a.atttypid, a.atttypmod), a.attnotnull,
               a.attidentity, a.attgenerated,
               COALESCE(pg_get_expr(ad.adbin, ad.adrelid, true), ''),
               COALESCE(coll.collname, '')
        FROM pg_attribute AS a
        JOIN pg_class AS c ON c.oid = a.attrelid
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef AS ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
        LEFT JOIN pg_collation AS coll ON coll.oid = a.attcollation
        WHERE {_USER_SCHEMA_PREDICATE}
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY n.nspname COLLATE "C", c.relname COLLATE "C", a.attnum
        """,
        f"""
        SELECT 'index', n.nspname, c.relname, i.relname, pg_get_indexdef(i.oid)
        FROM pg_index AS x
        JOIN pg_class AS c ON c.oid = x.indrelid
        JOIN pg_class AS i ON i.oid = x.indexrelid
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE {_USER_SCHEMA_PREDICATE}
        ORDER BY n.nspname COLLATE "C", c.relname COLLATE "C", i.relname COLLATE "C"
        """,
        f"""
        SELECT 'function', n.nspname, p.proname, p.prokind,
               pg_get_function_identity_arguments(p.oid),
               CASE WHEN p.prokind IN ('f', 'p') THEN pg_get_functiondef(p.oid) ELSE NULL END,
               p.prosrc, p.probin, p.provolatile, p.proparallel, p.prosecdef,
               p.proleakproof, p.proisstrict, p.proretset,
               pg_get_userbyid(p.proowner),
               (
                   SELECT jsonb_agg(
                       jsonb_build_array(
                           CASE acl.grantee
                               WHEN 0 THEN 'PUBLIC'
                               ELSE pg_get_userbyid(acl.grantee)
                           END,
                           pg_get_userbyid(acl.grantor),
                           acl.privilege_type,
                           acl.is_grantable
                       )
                       ORDER BY acl.grantee, acl.grantor, acl.privilege_type
                   )
                   FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) AS acl
               )
        FROM pg_proc AS p
        JOIN pg_namespace AS n ON n.oid = p.pronamespace
        WHERE {_USER_SCHEMA_PREDICATE}
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend AS d
              WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'
          )
        ORDER BY n.nspname COLLATE "C", p.proname COLLATE "C",
                 pg_get_function_identity_arguments(p.oid) COLLATE "C"
        """,
        f"""
        SELECT 'policy', n.nspname, c.relname, p.polname, p.polcmd,
               p.polpermissive, pg_get_expr(p.polqual, p.polrelid, true),
               pg_get_expr(p.polwithcheck, p.polrelid, true)
        FROM pg_policy AS p
        JOIN pg_class AS c ON c.oid = p.polrelid
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE {_USER_SCHEMA_PREDICATE}
        ORDER BY n.nspname COLLATE "C", c.relname COLLATE "C", p.polname COLLATE "C"
        """,
        f"""
        SELECT 'trigger', n.nspname, c.relname, t.tgname, pg_get_triggerdef(t.oid, true)
        FROM pg_trigger AS t
        JOIN pg_class AS c ON c.oid = t.tgrelid
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE {_USER_SCHEMA_PREDICATE} AND NOT t.tgisinternal
        ORDER BY n.nspname COLLATE "C", c.relname COLLATE "C", t.tgname COLLATE "C"
        """,
        """
        SELECT 'extension', e.extname, e.extversion, n.nspname,
               pg_get_userbyid(e.extowner)
        FROM pg_extension AS e
        JOIN pg_namespace AS n ON n.oid = e.extnamespace
        WHERE e.extname <> 'plpgsql'
        ORDER BY e.extname COLLATE "C"
        """,
        f"""
        WITH securable AS (
            SELECT 'schema'::text AS object_kind, n.nspname AS schema_name,
                   ''::text AS object_name, n.nspowner AS owner_oid,
                   n.nspacl AS acl, 'n'::"char" AS acl_kind
            FROM pg_namespace AS n
            WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_'
            UNION ALL
            SELECT CASE WHEN c.relkind = 'S' THEN 'sequence' ELSE 'relation' END,
                   n.nspname, c.relname, c.relowner, c.relacl,
                   CASE WHEN c.relkind = 'S' THEN 'S'::"char" ELSE 'r'::"char" END
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE {_USER_SCHEMA_PREDICATE}
              AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
              AND NOT EXISTS (
                  SELECT 1 FROM pg_depend AS d
                  WHERE d.classid = 'pg_class'::regclass
                    AND d.objid = c.oid AND d.deptype = 'e'
              )
            UNION ALL
            SELECT 'function', n.nspname,
                   p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
                   p.proowner, p.proacl, 'f'::"char"
            FROM pg_proc AS p
            JOIN pg_namespace AS n ON n.oid = p.pronamespace
            WHERE {_USER_SCHEMA_PREDICATE}
              AND NOT EXISTS (
                  SELECT 1 FROM pg_depend AS d
                  WHERE d.classid = 'pg_proc'::regclass
                    AND d.objid = p.oid AND d.deptype = 'e'
              )
        )
        SELECT 'acl', object_kind, schema_name, object_name,
               CASE exploded.grantee
                   WHEN 0 THEN 'PUBLIC'
                   ELSE pg_get_userbyid(exploded.grantee)
               END,
               pg_get_userbyid(exploded.grantor),
               exploded.privilege_type, exploded.is_grantable
        FROM securable
        CROSS JOIN LATERAL aclexplode(
            COALESCE(securable.acl, acldefault(securable.acl_kind, securable.owner_oid))
        ) AS exploded
        ORDER BY object_kind COLLATE "C", schema_name COLLATE "C",
                 object_name COLLATE "C", exploded.grantee,
                 exploded.grantor, exploded.privilege_type COLLATE "C"
        """,
    )
    result: list[tuple[object, ...]] = []
    for statement in statements:
        result.extend(tuple(row) for row in connection.execute(statement).fetchall())
    return result


def _constraint_rows(connection: Any) -> list[tuple[object, ...]]:
    rows = connection.execute(
        f"""
        SELECT n.nspname, c.relname, x.conname, x.contype,
               x.condeferrable, x.condeferred, x.convalidated,
               pg_get_constraintdef(x.oid, true)
        FROM pg_constraint AS x
        JOIN pg_class AS c ON c.oid = x.conrelid
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE {_USER_SCHEMA_PREDICATE}
        ORDER BY n.nspname COLLATE "C", c.relname COLLATE "C", x.conname COLLATE "C"
        """
    ).fetchall()
    return [tuple(row) for row in rows]


def _sequence_rows(connection: Any) -> list[tuple[object, ...]]:
    from psycopg import sql

    definitions = connection.execute(
        f"""
        SELECT n.nspname, c.relname, s.seqstart, s.seqincrement,
               s.seqmax, s.seqmin, s.seqcache, s.seqcycle
        FROM pg_sequence AS s
        JOIN pg_class AS c ON c.oid = s.seqrelid
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE {_USER_SCHEMA_PREDICATE}
        ORDER BY n.nspname COLLATE "C", c.relname COLLATE "C"
        """
    ).fetchall()
    result: list[tuple[object, ...]] = []
    for definition in definitions:
        schema_name, sequence_name = str(definition[0]), str(definition[1])
        state = connection.execute(
            sql.SQL("SELECT last_value, is_called FROM {}.{}").format(
                sql.Identifier(schema_name), sql.Identifier(sequence_name)
            )
        ).fetchone()
        if state is None:
            raise PostgresBackupError(
                "BACKUP_POSTGRES_SEQUENCE_UNREADABLE",
                "a database sequence did not return its state",
            )
        result.append((*tuple(definition), *tuple(state)))
    return result


def _table_digests(connection: Any) -> tuple[TableContentDigest, ...]:
    from psycopg import sql

    tables = connection.execute(
        f"""
        SELECT n.nspname, c.relname
        FROM pg_class AS c
        JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE {_USER_SCHEMA_PREDICATE} AND c.relkind = 'r'
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend AS d
              WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e'
          )
        ORDER BY n.nspname COLLATE "C", c.relname COLLATE "C"
        """
    ).fetchall()
    result: list[TableContentDigest] = []
    for index, row in enumerate(tables):
        schema_name, table_name = str(row[0]), str(row[1])
        digest = hashlib.sha256()
        row_count = 0
        cursor = connection.cursor(name=f"backup_hash_{index}")
        try:
            cursor.execute(
                sql.SQL(
                    "SELECT to_jsonb(value)::text FROM ONLY {}.{} AS value "
                    'ORDER BY to_jsonb(value)::text COLLATE "C"'
                ).format(sql.Identifier(schema_name), sql.Identifier(table_name))
            )
            while True:
                batch = cursor.fetchmany(1000)
                if not batch:
                    break
                for value_row in batch:
                    encoded = str(value_row[0]).encode("utf-8")
                    digest.update(len(encoded).to_bytes(8, "big"))
                    digest.update(encoded)
                    row_count += 1
        finally:
            cursor.close()
        result.append(
            TableContentDigest(
                schema_name=schema_name,
                table_name=table_name,
                row_count=row_count,
                content_sha256=digest.hexdigest(),
            )
        )
    return tuple(result)


def collect_database_evidence(connection: Any) -> DatabaseVerificationEvidence:
    """Collect deterministic, content-free restore evidence in the current snapshot."""

    migrations = _migration_rows(connection)
    schema = _schema_rows(connection)
    constraints = _constraint_rows(connection)
    sequences = _sequence_rows(connection)
    tables = _table_digests(connection)
    size_row = connection.execute("SELECT pg_database_size(current_database())::bigint").fetchone()
    if size_row is None or int(size_row[0]) < 0:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_DATABASE_SIZE_INVALID",
            "the source database size could not be measured",
        )
    components = {
        "migrations": migrations,
        "schema": schema,
        "constraints": constraints,
        "sequences": sequences,
        "tables": [table.model_dump(mode="json") for table in tables],
    }
    return DatabaseVerificationEvidence(
        database_size_bytes=int(size_row[0]),
        migration_count=len(migrations),
        migration_sha256=_canonical_sha256(migrations),
        schema_object_count=len(schema),
        schema_sha256=_canonical_sha256(schema),
        constraint_count=len(constraints),
        constraint_sha256=_canonical_sha256(constraints),
        sequence_count=len(sequences),
        sequence_sha256=_canonical_sha256(sequences),
        tables=tables,
        aggregate_sha256=_canonical_sha256(components),
    )


def _reject_duplicate_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PostgresBackupError(
                "BACKUP_POSTGRES_MANIFEST_INVALID",
                "the PostgreSQL backup manifest contains duplicate keys",
            )
        result[key] = value
    return result


def _read_physical_manifest(directory: Path) -> tuple[int, int, str, str, str]:
    manifest_path = directory / "backup_manifest"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise PostgresBackupError(
            "BACKUP_POSTGRES_MANIFEST_MISSING",
            "pg_basebackup did not produce a regular backup manifest",
        )
    size = manifest_path.stat().st_size
    if size <= 0 or size > 64 * 1024 * 1024:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_MANIFEST_INVALID",
            "the PostgreSQL backup manifest size is invalid",
        )
    try:
        manifest = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_pairs,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_MANIFEST_INVALID",
            "the PostgreSQL backup manifest is malformed",
        ) from exc
    if not isinstance(manifest, dict):
        raise PostgresBackupError(
            "BACKUP_POSTGRES_MANIFEST_INVALID",
            "the PostgreSQL backup manifest has an invalid root",
        )
    if manifest.get("PostgreSQL-Backup-Manifest-Version") not in {1, 2}:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_MANIFEST_VERSION_UNSUPPORTED",
            "the PostgreSQL backup manifest version is unsupported",
        )
    try:
        raw_system_identifier = manifest.get("System-Identifier")
        system_identifier = int(raw_system_identifier) if raw_system_identifier is not None else 0
        ranges = manifest["WAL-Ranges"]
    except (KeyError, TypeError, ValueError) as exc:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_MANIFEST_INVALID",
            "the PostgreSQL backup manifest lacks recovery coordinates",
        ) from exc
    if not isinstance(ranges, list) or not ranges:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_MANIFEST_INVALID",
            "the PostgreSQL backup manifest has no WAL range",
        )
    parsed_ranges: list[tuple[int, str, str]] = []
    try:
        for item in ranges:
            if not isinstance(item, dict):
                raise TypeError
            timeline = int(item["Timeline"])
            start = str(item["Start-LSN"])
            end = str(item["End-LSN"])
            if not re.fullmatch(r"[0-9A-F]+/[0-9A-F]+", start):
                raise ValueError
            if not re.fullmatch(r"[0-9A-F]+/[0-9A-F]+", end):
                raise ValueError
            parsed_ranges.append((timeline, start, end))
    except (KeyError, TypeError, ValueError) as exc:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_MANIFEST_INVALID",
            "the PostgreSQL backup WAL range is malformed",
        ) from exc
    timelines = {item[0] for item in parsed_ranges}
    if len(timelines) != 1:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_TIMELINE_AMBIGUOUS",
            "one physical baseline must resolve to exactly one timeline",
        )
    timeline_id = next(iter(timelines))
    start_lsn = min((item[1] for item in parsed_ranges), key=_lsn_integer)
    end_lsn = max((item[2] for item in parsed_ranges), key=_lsn_integer)
    if _lsn_integer(end_lsn) < _lsn_integer(start_lsn):
        raise PostgresBackupError(
            "BACKUP_POSTGRES_MANIFEST_INVALID", "the PostgreSQL WAL range is reversed"
        )
    return system_identifier, timeline_id, start_lsn, end_lsn, _file_sha256(manifest_path)


def _physical_files(directory: Path) -> tuple[tuple[PhysicalBackupFile, ...], int, str]:
    files: list[PhysicalBackupFile] = []
    total_bytes = 0
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise PostgresBackupError(
                "BACKUP_POSTGRES_PHYSICAL_SYMLINK_UNSUPPORTED",
                "physical backup tablespace symlinks require an explicit mapping",
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise PostgresBackupError(
                "BACKUP_POSTGRES_PHYSICAL_FILE_INVALID",
                "the physical baseline contains a non-regular entry",
            )
        relative = path.relative_to(directory).as_posix()
        size = path.stat().st_size
        total_bytes += size
        files.append(
            PhysicalBackupFile(
                relative_path=relative,
                size_bytes=size,
                sha256=_file_sha256(path),
            )
        )
    if not files or total_bytes <= 0:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_PHYSICAL_EMPTY", "the physical baseline is empty"
        )
    aggregate = _canonical_sha256([item.model_dump(mode="json") for item in files])
    return tuple(files), total_bytes, aggregate


def _lsn_integer(value: str) -> int:
    high, low = value.split("/", 1)
    return (int(high, 16) << 32) + int(low, 16)


def _parse_control_identity(output: bytes) -> tuple[int, int]:
    system_match = _CONTROL_SYSTEM_IDENTIFIER.search(output)
    timeline_match = _CONTROL_TIMELINE.search(output)
    if system_match is None or timeline_match is None:
        raise PostgresBackupError(
            "BACKUP_POSTGRES_CONTROL_IDENTITY_INVALID",
            "pg_controldata did not return a bounded physical identity",
        )
    return int(system_match.group("value")), int(timeline_match.group("value"))


class PostgresLogicalBackupAdapter:
    def __init__(
        self,
        *,
        toolchain: PostgresToolchain | None = None,
        command_runner: CommandRunner | None = None,
        connection_factory: ConnectionFactory | None = None,
        command_timeout_seconds: int = 7200,
    ) -> None:
        if command_timeout_seconds < 1 or command_timeout_seconds > 86400:
            raise ValueError("command timeout must be between 1 and 86400 seconds")
        self._toolchain = toolchain or PostgresToolchain()
        self._command_runner = command_runner or SubprocessCommandRunner()
        self._connection_factory = connection_factory or _default_connection_factory
        self._command_timeout_seconds = command_timeout_seconds

    def _run_tool(
        self,
        argv: Sequence[str],
        *,
        endpoint: PostgresEndpoint,
        passfile: Path,
        cwd: Path,
        capture_stdout: bool = False,
    ) -> CommandResult:
        result = self._command_runner.run(
            argv,
            environment=endpoint.command_environment(passfile=passfile),
            cwd=cwd,
            timeout_seconds=self._command_timeout_seconds,
            capture_stdout=capture_stdout,
        )
        if result.returncode != 0:
            raise PostgresBackupError(
                "BACKUP_POSTGRES_TOOL_FAILED",
                "a PostgreSQL client program failed without producing trusted output",
            )
        return result

    def _tool_major(
        self,
        prefix: tuple[str, ...],
        *,
        endpoint: PostgresEndpoint,
        passfile: Path,
        cwd: Path,
    ) -> int:
        result = self._run_tool(
            (*prefix, "--version"),
            endpoint=endpoint,
            passfile=passfile,
            cwd=cwd,
            capture_stdout=True,
        )
        match = _TOOL_VERSION.search(result.stdout.decode("ascii", errors="replace"))
        if match is None:
            raise PostgresBackupError(
                "BACKUP_POSTGRES_TOOL_VERSION_INVALID",
                "the PostgreSQL client version could not be authenticated",
            )
        return int(match.group("major"))

    def create_dump(
        self,
        source: PostgresEndpoint,
        *,
        lease: MaintenanceBackupLease,
        staging_directory: Path,
        artifact_name: str = "postgresql.dump",
    ) -> LogicalDumpArtifact:
        staging = _assert_secure_directory(staging_directory)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", artifact_name):
            raise PostgresBackupError(
                "BACKUP_POSTGRES_ARTIFACT_NAME_INVALID", "the dump artifact name is invalid"
            )
        final_path = staging / artifact_name
        if final_path.exists() or final_path.is_symlink():
            raise PostgresBackupError(
                "BACKUP_POSTGRES_ARTIFACT_EXISTS", "the dump artifact already exists"
            )
        temporary_path = staging / f".{artifact_name}.{uuid4().hex}.partial"
        try:
            with _pgpass_file(source, staging) as passfile:
                with self._connection_factory(source) as connection:
                    connection.execute(
                        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY, DEFERRABLE"
                    )
                    _assert_backup_lease(connection, lease)
                    (
                        server_major,
                        server_version,
                        system_identifier,
                        timeline_id,
                        wal_lsn,
                        in_recovery,
                        _database_oid,
                    ) = _server_identity(connection)
                    if in_recovery:
                        raise PostgresBackupError(
                            "BACKUP_POSTGRES_RECOVERY_SOURCE_REJECTED",
                            "logical backup requires the configured writable primary",
                        )
                    tool_major = self._tool_major(
                        self._toolchain.pg_dump,
                        endpoint=source,
                        passfile=passfile,
                        cwd=staging,
                    )
                    if tool_major != server_major:
                        raise PostgresBackupError(
                            "BACKUP_POSTGRES_MAJOR_MISMATCH",
                            "pg_dump and the PostgreSQL server must have the same major version",
                        )
                    snapshot_row = connection.execute("SELECT pg_export_snapshot()").fetchone()
                    if snapshot_row is None:
                        raise PostgresBackupError(
                            "BACKUP_POSTGRES_SNAPSHOT_UNAVAILABLE",
                            "PostgreSQL did not export a consistent snapshot",
                        )
                    snapshot_id = str(snapshot_row[0])
                    source_evidence = collect_database_evidence(connection)
                    self._run_tool(
                        (
                            *self._toolchain.pg_dump,
                            "--format=custom",
                            "--file",
                            str(temporary_path),
                            "--snapshot",
                            snapshot_id,
                        ),
                        endpoint=source,
                        passfile=passfile,
                        cwd=staging,
                    )
                with self._connection_factory(source) as fresh_connection:
                    _assert_backup_lease(fresh_connection, lease)
                if not temporary_path.exists() or temporary_path.is_symlink():
                    raise PostgresBackupError(
                        "BACKUP_POSTGRES_ARCHIVE_MISSING",
                        "pg_dump did not create a regular archive",
                    )
                info = temporary_path.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
                    raise PostgresBackupError(
                        "BACKUP_POSTGRES_ARCHIVE_INVALID",
                        "pg_dump did not create a non-empty regular archive",
                    )
                temporary_path.chmod(0o600)
                self._run_tool(
                    (*self._toolchain.pg_restore, "--list", str(temporary_path)),
                    endpoint=source,
                    passfile=passfile,
                    cwd=staging,
                )
                _fsync_file(temporary_path)
                temporary_path.replace(final_path)
                _fsync_directory(staging)
                return LogicalDumpArtifact(
                    path=final_path,
                    size_bytes=info.st_size,
                    sha256=_file_sha256(final_path),
                    server_major=server_major,
                    wal_anchor=PostgresWalAnchor(
                        system_identifier=system_identifier,
                        timeline_id=timeline_id,
                        wal_lsn=wal_lsn,
                        server_version=server_version,
                        server_major=server_major,
                        database_name=source.database,
                        in_recovery=in_recovery,
                    ),
                    source_evidence=source_evidence,
                )
        except BaseException:
            with suppress(FileNotFoundError):
                temporary_path.unlink()
            raise

    def restore_and_verify(
        self,
        artifact: LogicalDumpArtifact,
        target: PostgresEndpoint,
        *,
        staging_directory: Path,
    ) -> RestoreVerificationReport:
        return self._restore_exact_and_verify(
            path=artifact.path,
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
            server_major=artifact.server_major,
            source_database=artifact.wal_anchor.database_name,
            source_system_identifier=artifact.wal_anchor.system_identifier,
            source_evidence=artifact.source_evidence,
            target=target,
            staging_directory=staging_directory,
            allow_verified_existing=False,
        )

    def restore_manifest_dump_and_verify(
        self,
        artifact: ManifestLogicalDumpArtifact,
        target: PostgresEndpoint,
        *,
        staging_directory: Path,
    ) -> RestoreVerificationReport:
        return self._restore_exact_and_verify(
            path=artifact.path,
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
            server_major=artifact.server_major,
            source_database=artifact.source_database,
            source_system_identifier=None,
            source_evidence=artifact.source_evidence,
            target=target,
            staging_directory=staging_directory,
            allow_verified_existing=True,
        )

    def _restore_exact_and_verify(
        self,
        *,
        path: Path,
        size_bytes: int,
        sha256: str,
        server_major: int,
        source_database: str,
        source_system_identifier: int | None,
        source_evidence: DatabaseVerificationEvidence,
        target: PostgresEndpoint,
        staging_directory: Path,
        allow_verified_existing: bool,
    ) -> RestoreVerificationReport:
        staging = _assert_secure_directory(staging_directory)
        archive = path.resolve(strict=True)
        if not archive.is_relative_to(staging) or path.is_symlink():
            raise PostgresBackupError(
                "BACKUP_POSTGRES_ARCHIVE_PATH_INVALID",
                "the archive is outside the exact staging directory",
            )
        parent = archive.parent
        while True:
            parent_info = parent.stat()
            if (
                parent.is_symlink()
                or not stat.S_ISDIR(parent_info.st_mode)
                or parent_info.st_uid != os.getuid()
                or stat.S_IMODE(parent_info.st_mode) & 0o077
            ):
                raise PostgresBackupError(
                    "BACKUP_POSTGRES_ARCHIVE_PATH_INVALID",
                    "the archive parent path is unsafe",
                )
            if parent == staging:
                break
            parent = parent.parent
        info = archive.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_size != size_bytes
        ):
            raise PostgresBackupError(
                "BACKUP_POSTGRES_ARCHIVE_INVALID", "the archive metadata changed"
            )
        if _file_sha256(archive) != sha256:
            raise PostgresBackupError(
                "BACKUP_POSTGRES_ARCHIVE_HASH_MISMATCH", "the archive digest changed"
            )
        restored: DatabaseVerificationEvidence | None = None
        with _pgpass_file(target, staging) as passfile:
            with self._connection_factory(target) as connection:
                connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY, DEFERRABLE"
                )
                target_major, _version, system_id, _timeline, _lsn, _recovery, db_oid = (
                    _server_identity(connection)
                )
                source_same_cluster = (
                    source_system_identifier is not None and system_id == source_system_identifier
                )
                source_database_row = connection.execute(
                    "SELECT oid::bigint FROM pg_database WHERE datname = %s",
                    (source_database,),
                ).fetchone()
                if (
                    source_same_cluster
                    and source_database_row is not None
                    and int(source_database_row[0]) == db_oid
                ):
                    raise PostgresBackupError(
                        "BACKUP_POSTGRES_TARGET_SOURCE_COLLISION",
                        "restore target resolves to the source database",
                    )
                if target_major != server_major:
                    raise PostgresBackupError(
                        "BACKUP_POSTGRES_MAJOR_MISMATCH",
                        "the restore target must use the recorded PostgreSQL major version",
                    )
                object_count = connection.execute(
                    f"""
                    SELECT
                        (SELECT count(*) FROM pg_class AS c JOIN pg_namespace AS n
                         ON n.oid = c.relnamespace WHERE {_USER_SCHEMA_PREDICATE})
                      + (SELECT count(*) FROM pg_proc AS p JOIN pg_namespace AS n
                         ON n.oid = p.pronamespace WHERE {_USER_SCHEMA_PREDICATE})
                      + (SELECT count(*) FROM pg_type AS t JOIN pg_namespace AS n
                         ON n.oid = t.typnamespace WHERE {_USER_SCHEMA_PREDICATE}
                           AND t.typtype <> 'b')
                      + (SELECT count(*) FROM pg_extension WHERE extname <> 'plpgsql')
                    """
                ).fetchone()
                if object_count is None:
                    raise PostgresBackupError(
                        "BACKUP_POSTGRES_TARGET_NOT_EMPTY",
                        "restore requires a database created empty from template0",
                    )
                if int(object_count[0]) != 0:
                    if not allow_verified_existing:
                        raise PostgresBackupError(
                            "BACKUP_POSTGRES_TARGET_NOT_EMPTY",
                            "restore requires a database created empty from template0",
                        )
                    restored = collect_database_evidence(connection)
            if restored is None:
                tool_major = self._tool_major(
                    self._toolchain.pg_restore,
                    endpoint=target,
                    passfile=passfile,
                    cwd=staging,
                )
                if tool_major != server_major:
                    raise PostgresBackupError(
                        "BACKUP_POSTGRES_MAJOR_MISMATCH",
                        "pg_restore and the archive server must have the same major version",
                    )
                self._run_tool(
                    (
                        *self._toolchain.pg_restore,
                        "--exit-on-error",
                        "--single-transaction",
                        "--dbname",
                        target.database,
                        str(archive),
                    ),
                    endpoint=target,
                    passfile=passfile,
                    cwd=staging,
                )
        if restored is None:
            with self._connection_factory(target) as connection:
                connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY, DEFERRABLE"
                )
                restored = collect_database_evidence(connection)
        expected = source_evidence
        if (
            restored.migration_count != expected.migration_count
            or restored.migration_sha256 != expected.migration_sha256
        ):
            raise PostgresBackupError(
                "BACKUP_POSTGRES_MIGRATION_MISMATCH", "restored migration evidence differs"
            )
        if (
            restored.schema_object_count != expected.schema_object_count
            or restored.schema_sha256 != expected.schema_sha256
        ):
            raise PostgresBackupError(
                "BACKUP_POSTGRES_SCHEMA_MISMATCH", "restored schema evidence differs"
            )
        if (
            restored.constraint_count != expected.constraint_count
            or restored.constraint_sha256 != expected.constraint_sha256
        ):
            raise PostgresBackupError(
                "BACKUP_POSTGRES_CONSTRAINT_MISMATCH", "restored constraints differ"
            )
        if (
            restored.sequence_count != expected.sequence_count
            or restored.sequence_sha256 != expected.sequence_sha256
        ):
            raise PostgresBackupError(
                "BACKUP_POSTGRES_SEQUENCE_MISMATCH", "restored sequence state differs"
            )
        expected_counts = {
            (table.schema_name, table.table_name): table.row_count for table in expected.tables
        }
        restored_counts = {
            (table.schema_name, table.table_name): table.row_count for table in restored.tables
        }
        if restored_counts != expected_counts:
            raise PostgresBackupError(
                "BACKUP_POSTGRES_ROW_COUNT_MISMATCH", "restored table row counts differ"
            )
        expected_hashes = {
            (table.schema_name, table.table_name): table.content_sha256 for table in expected.tables
        }
        restored_hashes = {
            (table.schema_name, table.table_name): table.content_sha256 for table in restored.tables
        }
        if (
            restored_hashes != expected_hashes
            or restored.aggregate_sha256 != expected.aggregate_sha256
        ):
            raise PostgresBackupError(
                "BACKUP_POSTGRES_CONTENT_HASH_MISMATCH", "restored content hashes differ"
            )
        return RestoreVerificationReport(
            target_database=target.database,
            server_major=server_major,
            aggregate_sha256=restored.aggregate_sha256,
        )


class PostgresPhysicalBackupAdapter(PostgresLogicalBackupAdapter):
    """Create a checksummed physical baseline for an external WAL archive."""

    def create_physical_baseline(
        self,
        source: PostgresEndpoint,
        *,
        lease: MaintenanceBackupLease,
        staging_directory: Path,
        artifact_directory_name: str = "postgresql-physical",
    ) -> PhysicalBaselineArtifact:
        staging = _assert_secure_directory(staging_directory)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", artifact_directory_name):
            raise PostgresBackupError(
                "BACKUP_POSTGRES_ARTIFACT_NAME_INVALID",
                "the physical artifact directory name is invalid",
            )
        final_path = staging / artifact_directory_name
        if final_path.exists() or final_path.is_symlink():
            raise PostgresBackupError(
                "BACKUP_POSTGRES_ARTIFACT_EXISTS",
                "the physical baseline artifact already exists",
            )
        temporary_path = staging / f".{artifact_directory_name}.{uuid4().hex}.partial"
        temporary_path.mkdir(mode=0o700)
        try:
            with _pgpass_file(source, staging) as passfile:
                with self._connection_factory(source) as connection:
                    _assert_backup_lease(connection, lease)
                    (
                        server_major,
                        _server_version,
                        system_identifier,
                        timeline_id,
                        _wal_lsn,
                        in_recovery,
                        _database_oid,
                    ) = _server_identity(connection)
                if in_recovery:
                    raise PostgresBackupError(
                        "BACKUP_POSTGRES_RECOVERY_SOURCE_REJECTED",
                        "physical backup requires the configured writable primary",
                    )
                basebackup_major = self._tool_major(
                    self._toolchain.pg_basebackup,
                    endpoint=source,
                    passfile=passfile,
                    cwd=staging,
                )
                verifybackup_major = self._tool_major(
                    self._toolchain.pg_verifybackup,
                    endpoint=source,
                    passfile=passfile,
                    cwd=staging,
                )
                controldata_major = self._tool_major(
                    self._toolchain.pg_controldata,
                    endpoint=source,
                    passfile=passfile,
                    cwd=staging,
                )
                if (
                    basebackup_major != server_major
                    or verifybackup_major != server_major
                    or controldata_major != server_major
                ):
                    raise PostgresBackupError(
                        "BACKUP_POSTGRES_MAJOR_MISMATCH",
                        "physical backup tools and server must have the same major version",
                    )
                self._run_tool(
                    (
                        *self._toolchain.pg_basebackup,
                        "--pgdata",
                        str(temporary_path),
                        "--format=plain",
                        "--wal-method=stream",
                        "--checkpoint=fast",
                        "--manifest-checksums=SHA256",
                        "--label",
                        f"hc-platform:{lease.operation_id}",
                        "--no-password",
                    ),
                    endpoint=source,
                    passfile=passfile,
                    cwd=staging,
                )
                control_identity = _parse_control_identity(
                    self._run_tool(
                        (*self._toolchain.pg_controldata, str(temporary_path)),
                        endpoint=source,
                        passfile=passfile,
                        cwd=staging,
                        capture_stdout=True,
                    ).stdout
                )
                self._run_tool(
                    (
                        *self._toolchain.pg_verifybackup,
                        "--exit-on-error",
                        "--quiet",
                        str(temporary_path),
                    ),
                    endpoint=source,
                    passfile=passfile,
                    cwd=staging,
                )
                (
                    manifest_system_identifier,
                    manifest_timeline_id,
                    start_lsn,
                    end_lsn,
                    manifest_sha256,
                ) = _read_physical_manifest(temporary_path)
                if (
                    manifest_system_identifier not in {0, system_identifier}
                    or manifest_timeline_id != timeline_id
                    or control_identity != (system_identifier, timeline_id)
                ):
                    raise PostgresBackupError(
                        "BACKUP_POSTGRES_PHYSICAL_IDENTITY_MISMATCH",
                        "the physical baseline does not belong to the fenced primary",
                    )
                files, total_bytes, aggregate_sha256 = _physical_files(temporary_path)
                with self._connection_factory(source) as fresh_connection:
                    _assert_backup_lease(fresh_connection, lease)
                    fresh_identity = _server_identity(fresh_connection)
                if (
                    fresh_identity[0] != server_major
                    or fresh_identity[2] != system_identifier
                    or fresh_identity[3] != timeline_id
                    or fresh_identity[5]
                ):
                    raise PostgresBackupError(
                        "BACKUP_POSTGRES_PHYSICAL_IDENTITY_CHANGED",
                        "the PostgreSQL primary identity changed during physical backup",
                    )
            artifact = PhysicalBaselineArtifact(
                path=final_path,
                server_major=server_major,
                system_identifier=system_identifier,
                timeline_id=timeline_id,
                start_lsn=start_lsn,
                end_lsn=end_lsn,
                manifest_sha256=manifest_sha256,
                total_bytes=total_bytes,
                files=files,
                aggregate_sha256=aggregate_sha256,
            )
            temporary_path.replace(final_path)
            _fsync_directory(staging)
            return artifact
        except BaseException:
            if temporary_path.parent == staging and temporary_path.name.startswith("."):
                shutil.rmtree(temporary_path, ignore_errors=True)
            raise

    @staticmethod
    def bind_verified_wal_archive(
        baseline: PhysicalBaselineArtifact,
        verifier: WalArchiveVerificationPort,
    ) -> PostgresPITREvidence:
        receipt = verifier.verify_for_baseline(baseline)
        now = datetime.now(timezone.utc)
        if receipt.system_identifier != baseline.system_identifier:
            raise PostgresBackupError(
                "BACKUP_POSTGRES_WAL_SYSTEM_MISMATCH",
                "the WAL archive belongs to a different PostgreSQL system",
            )
        if receipt.timeline_id != baseline.timeline_id:
            raise PostgresBackupError(
                "BACKUP_POSTGRES_WAL_TIMELINE_MISMATCH",
                "the WAL archive belongs to a different PostgreSQL timeline",
            )
        if _lsn_integer(receipt.continuous_from_lsn) > _lsn_integer(baseline.start_lsn):
            raise PostgresBackupError(
                "BACKUP_POSTGRES_WAL_GAP",
                "the WAL archive starts after the physical baseline requires",
            )
        if _lsn_integer(receipt.continuous_through_lsn) < _lsn_integer(baseline.end_lsn):
            raise PostgresBackupError(
                "BACKUP_POSTGRES_WAL_GAP",
                "the WAL archive ends before the physical baseline completes",
            )
        if receipt.verified_at > now or receipt.retained_until <= now:
            raise PostgresBackupError(
                "BACKUP_POSTGRES_WAL_RETENTION_INVALID",
                "the WAL archive receipt has no current retained recovery window",
            )
        return PostgresPITREvidence(
            baseline=baseline,
            wal_archive=receipt,
            recovery_target_lsn=receipt.continuous_through_lsn,
        )

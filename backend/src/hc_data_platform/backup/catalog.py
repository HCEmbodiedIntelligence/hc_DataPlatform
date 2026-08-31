"""Rebuildable, append-only catalog for authenticated platform backups."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from threading import RLock
from typing import Annotated, Any, Literal, Protocol
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from hc_data_platform.backup.contracts import (
    BackupManifestV1,
    Sha256,
    VerificationFactV1,
    VerifiedManifestV1,
    parse_signature_envelope,
    verify_signed_manifest,
)
from hc_data_platform.backup.repository import BackupManifestRepository
from hc_data_platform.core.dbapi import normalize_postgres_dsn

CatalogStatus = Literal[
    "CREATING",
    "CREATED",
    "INTEGRITY_VERIFIED",
    "RESTORE_VERIFIED",
    "FAILED",
    "CORRUPT",
    "EXPIRED",
]
CatalogSourceKind = Literal["CREATE", "REBUILD", "VERIFY", "RESTORE", "LIFECYCLE"]
SafeIdentifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$",
    ),
]
SafeActor = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]
LogicalRepositoryUri = Annotated[
    str,
    StringConstraints(
        pattern=(
            r"^backup-repository://[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}/"
            r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}/[a-zA-Z0-9._/-]+$"
        )
    ),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BackupCatalogEntry(_StrictModel):
    backup_id: SafeIdentifier
    format_version: Literal["hc-platform-backup/v1"]
    mode: Literal["portable", "snapshot"]
    source_platform_id: SafeIdentifier
    source_environment_id: SafeIdentifier
    repository_id: SafeIdentifier
    manifest_uri: LogicalRepositoryUri
    manifest_sha256: Sha256
    signature_sha256: Sha256
    signer_public_key_sha256: Sha256
    release_manifest_sha256: Sha256
    migration_manifest_sha256: Sha256
    backup_created_at: datetime
    backup_completed_at: datetime
    cataloged_at: datetime


class BackupCatalogStatusFact(_StrictModel):
    status_event_id: int = Field(gt=0)
    backup_id: SafeIdentifier
    status: CatalogStatus
    source_kind: CatalogSourceKind
    operation_id: SafeIdentifier
    evidence_uri: LogicalRepositoryUri | None
    evidence_sha256: Sha256 | None
    failure_code: str | None
    recorded_by: SafeActor
    request_id: SafeActor
    occurred_at: datetime


class BackupCatalogRecord(_StrictModel):
    entry: BackupCatalogEntry
    current_status: CatalogStatus
    status_facts: tuple[BackupCatalogStatusFact, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def current_status_matches_latest_fact(self) -> BackupCatalogRecord:
        if self.current_status != self.status_facts[-1].status:
            raise ValueError("current catalog status must match the latest append-only fact")
        if any(fact.backup_id != self.entry.backup_id for fact in self.status_facts):
            raise ValueError("catalog status facts must belong to the entry")
        return self


class BackupCatalogListItem(_StrictModel):
    """Redacted immutable catalog projection safe for the online API."""

    backup_id: SafeIdentifier
    format_version: Literal["hc-platform-backup/v1"]
    mode: Literal["portable", "snapshot"]
    status: CatalogStatus
    source_environment_id: SafeIdentifier
    repository_id: SafeIdentifier
    release_manifest_sha256: Sha256
    backup_created_at: datetime
    backup_completed_at: datetime
    status_occurred_at: datetime


class BackupCatalogPage(_StrictModel):
    format_version: Literal["hc-platform-backup-catalog-page/v1"] = (
        "hc-platform-backup-catalog-page/v1"
    )
    observed_at: datetime
    count: int = Field(ge=0)
    items: tuple[BackupCatalogListItem, ...]
    next_cursor: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def require_page_count(self) -> BackupCatalogPage:
        if self.count != len(self.items):
            raise ValueError("backup catalog page count differs from its item count")
        return self


class BackupCatalogAuditContext(_StrictModel):
    actor_id: SafeActor
    request_id: SafeActor


class BackupCatalogAuditEvent(_StrictModel):
    actor_id: SafeActor
    request_id: SafeActor
    action: Literal[
        "platform.backup.catalog.created",
        "platform.backup.catalog.rebuilt",
        "platform.backup.catalog.verified",
    ]
    resource_type: Literal["platform_backup"] = "platform_backup"
    resource_id: SafeIdentifier
    outcome: Literal["SUCCEEDED"] = "SUCCEEDED"
    safe_details: Mapping[str, object]


class BackupCatalogError(ValueError):
    """Stable fail-closed catalog error without repository or key details."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class BackupCatalogRepository(Protocol):
    def import_created_manifest(
        self,
        verified: VerifiedManifestV1,
        *,
        creation_operation_id: str,
        verification_fact: VerificationFactV1,
        audit: BackupCatalogAuditContext,
    ) -> BackupCatalogRecord: ...

    def import_rebuilt_manifest(
        self,
        verified: VerifiedManifestV1,
        *,
        verification_fact: VerificationFactV1,
        audit: BackupCatalogAuditContext,
    ) -> BackupCatalogRecord: ...

    def get(self, backup_id: str) -> BackupCatalogRecord | None: ...

    def list_page(
        self,
        *,
        source_environment_id: str,
        status: CatalogStatus | None,
        cursor: str | None,
        limit: int,
    ) -> BackupCatalogPage: ...

    def append_integrity_verification(
        self,
        backup_id: str,
        *,
        operation_id: str,
        evidence_uri: str,
        evidence_sha256: str,
        verifier_identity: str,
        verified_at: datetime,
        audit: BackupCatalogAuditContext,
    ) -> BackupCatalogRecord: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")


def _created_operation_id(operation_id: str) -> str:
    candidate = f"{operation_id}:created"
    if _SAFE_IDENTIFIER.fullmatch(operation_id) is None or len(candidate) > 128:
        raise BackupCatalogError(
            "BACKUP_CATALOG_OPERATION_INVALID",
            "the backup creation operation identifier is invalid",
        )
    return candidate


def _encode_cursor(item: BackupCatalogListItem) -> str:
    payload = json.dumps(
        {
            "backup_completed_at": item.backup_completed_at.isoformat(),
            "backup_id": item.backup_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if cursor is None:
        return None
    if not cursor or len(cursor) > 1024 or re.fullmatch(r"[A-Za-z0-9_-]+", cursor) is None:
        raise BackupCatalogError("BACKUP_CATALOG_CURSOR_INVALID", "the catalog cursor is invalid")
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"backup_completed_at", "backup_id"}:
            raise ValueError("cursor shape differs")
        completed_at = datetime.fromisoformat(value["backup_completed_at"])
        backup_id = value["backup_id"]
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise BackupCatalogError(
            "BACKUP_CATALOG_CURSOR_INVALID", "the catalog cursor is invalid"
        ) from exc
    if (
        completed_at.tzinfo is None
        or completed_at.utcoffset() != timezone.utc.utcoffset(completed_at)
        or not isinstance(backup_id, str)
        or _SAFE_IDENTIFIER.fullmatch(backup_id) is None
    ):
        raise BackupCatalogError("BACKUP_CATALOG_CURSOR_INVALID", "the catalog cursor is invalid")
    return completed_at, backup_id


def _manifest_uri(manifest: BackupManifestV1) -> str:
    return (
        f"backup-repository://{manifest.backup_repository.repository_id}/"
        f"{manifest.backup_id}/manifest.json"
    )


def _evidence_uri(manifest: BackupManifestV1, fact: VerificationFactV1) -> str:
    evidence_path = sorted(fact.evidence_artifacts)[0]
    return (
        f"backup-repository://{manifest.backup_repository.repository_id}/"
        f"{manifest.backup_id}/{evidence_path}"
    )


def _verification_fact_for_status(manifest: BackupManifestV1) -> VerificationFactV1:
    expected_kind = "restore" if manifest.status == "RESTORE_VERIFIED" else "integrity"
    candidates = [fact for fact in manifest.verification_facts if fact.kind == expected_kind]
    if not candidates:
        raise BackupCatalogError(
            "BACKUP_CATALOG_VERIFICATION_FACT_MISSING",
            "the authenticated manifest has no fact for its catalog status",
        )
    return max(candidates, key=lambda fact: (fact.verified_at, fact.operation_id))


def _entry_identity(entry: BackupCatalogEntry) -> dict[str, object]:
    return entry.model_dump(exclude={"cataloged_at"})


def _record(
    entry: BackupCatalogEntry, facts: Sequence[BackupCatalogStatusFact]
) -> BackupCatalogRecord:
    ordered = tuple(sorted(facts, key=lambda fact: fact.status_event_id))
    return BackupCatalogRecord(
        entry=entry,
        current_status=ordered[-1].status,
        status_facts=ordered,
    )


def _entry_from_verified(
    verified: VerifiedManifestV1,
    *,
    cataloged_at: datetime,
) -> BackupCatalogEntry:
    manifest = verified.manifest
    return BackupCatalogEntry(
        backup_id=manifest.backup_id,
        format_version=manifest.format_version,
        mode=manifest.mode,
        source_platform_id=manifest.source_platform_id,
        source_environment_id=manifest.source_environment_id,
        repository_id=manifest.backup_repository.repository_id,
        manifest_uri=_manifest_uri(manifest),
        manifest_sha256=verified.manifest_sha256,
        signature_sha256=verified.signature_sha256,
        signer_public_key_sha256=manifest.signature.public_key_sha256,
        release_manifest_sha256=manifest.release.release_manifest_sha256,
        migration_manifest_sha256=manifest.release.migration_manifest_sha256,
        backup_created_at=manifest.created_at,
        backup_completed_at=manifest.completed_at,
        cataloged_at=cataloged_at,
    )


def _status_fact_from_verified(
    verified: VerifiedManifestV1,
    verification_fact: VerificationFactV1,
    *,
    audit: BackupCatalogAuditContext,
    status_event_id: int,
) -> BackupCatalogStatusFact:
    manifest = verified.manifest
    return BackupCatalogStatusFact(
        status_event_id=status_event_id,
        backup_id=manifest.backup_id,
        status=manifest.status,
        source_kind="REBUILD",
        operation_id=verification_fact.operation_id,
        evidence_uri=_evidence_uri(manifest, verification_fact),
        evidence_sha256=verified.manifest_sha256,
        failure_code=None,
        recorded_by=audit.actor_id,
        request_id=audit.request_id,
        occurred_at=verification_fact.verified_at,
    )


def _create_status_facts(
    verified: VerifiedManifestV1,
    verification_fact: VerificationFactV1,
    *,
    creation_operation_id: str,
    audit: BackupCatalogAuditContext,
    first_status_event_id: int,
) -> tuple[BackupCatalogStatusFact, BackupCatalogStatusFact, BackupCatalogStatusFact]:
    manifest = verified.manifest
    if manifest.status != "INTEGRITY_VERIFIED" or verification_fact.kind != "integrity":
        raise BackupCatalogError(
            "BACKUP_CATALOG_CREATE_STATUS_INVALID",
            "a create flow may import only an integrity-verified manifest",
        )
    created_operation_id = _created_operation_id(creation_operation_id)
    evidence_uri = _evidence_uri(manifest, verification_fact)
    common = {
        "backup_id": manifest.backup_id,
        "recorded_by": audit.actor_id,
        "request_id": audit.request_id,
    }
    return (
        BackupCatalogStatusFact(
            status_event_id=first_status_event_id,
            status="CREATING",
            source_kind="CREATE",
            operation_id=creation_operation_id,
            evidence_uri=None,
            evidence_sha256=None,
            failure_code=None,
            occurred_at=manifest.created_at,
            **common,
        ),
        BackupCatalogStatusFact(
            status_event_id=first_status_event_id + 1,
            status="CREATED",
            source_kind="CREATE",
            operation_id=created_operation_id,
            evidence_uri=None,
            evidence_sha256=None,
            failure_code=None,
            occurred_at=manifest.completed_at,
            **common,
        ),
        BackupCatalogStatusFact(
            status_event_id=first_status_event_id + 2,
            status="INTEGRITY_VERIFIED",
            source_kind="VERIFY",
            operation_id=verification_fact.operation_id,
            evidence_uri=evidence_uri,
            evidence_sha256=verified.manifest_sha256,
            failure_code=None,
            occurred_at=verification_fact.verified_at,
            **common,
        ),
    )


def _list_item(record: BackupCatalogRecord) -> BackupCatalogListItem:
    entry = record.entry
    latest = record.status_facts[-1]
    return BackupCatalogListItem(
        backup_id=entry.backup_id,
        format_version=entry.format_version,
        mode=entry.mode,
        status=record.current_status,
        source_environment_id=entry.source_environment_id,
        repository_id=entry.repository_id,
        release_manifest_sha256=entry.release_manifest_sha256,
        backup_created_at=entry.backup_created_at,
        backup_completed_at=entry.backup_completed_at,
        status_occurred_at=latest.occurred_at,
    )


def _audit_event(
    verified: VerifiedManifestV1,
    *,
    audit: BackupCatalogAuditContext,
    action: Literal[
        "platform.backup.catalog.created",
        "platform.backup.catalog.rebuilt",
        "platform.backup.catalog.verified",
    ] = "platform.backup.catalog.rebuilt",
) -> BackupCatalogAuditEvent:
    manifest = verified.manifest
    return BackupCatalogAuditEvent(
        actor_id=audit.actor_id,
        request_id=audit.request_id,
        action=action,
        resource_id=manifest.backup_id,
        safe_details={
            "format_version": manifest.format_version,
            "repository_id": manifest.backup_repository.repository_id,
            "source_environment_id": manifest.source_environment_id,
            "status": manifest.status,
        },
    )


def _verification_audit_event(
    record: BackupCatalogRecord,
    *,
    audit: BackupCatalogAuditContext,
) -> BackupCatalogAuditEvent:
    return BackupCatalogAuditEvent(
        actor_id=audit.actor_id,
        request_id=audit.request_id,
        action="platform.backup.catalog.verified",
        resource_id=record.entry.backup_id,
        safe_details={
            "format_version": record.entry.format_version,
            "repository_id": record.entry.repository_id,
            "source_environment_id": record.entry.source_environment_id,
            "status": "INTEGRITY_VERIFIED",
        },
    )


class BackupCatalogService:
    """Authenticates independent-repository artifacts before catalog import."""

    def __init__(
        self,
        repository: BackupCatalogRepository,
        *,
        trusted_signing_key_sha256: frozenset[str],
        trusted_repository_ids: frozenset[str],
    ) -> None:
        if not trusted_signing_key_sha256:
            raise ValueError("at least one pinned backup signing key is required")
        if not trusted_repository_ids:
            raise ValueError("at least one trusted backup repository is required")
        self._repository = repository
        self._trusted_signing_key_sha256 = trusted_signing_key_sha256
        self._trusted_repository_ids = trusted_repository_ids

    def import_created_manifest(
        self,
        verified: VerifiedManifestV1,
        *,
        creation_operation_id: str,
        audit: BackupCatalogAuditContext,
    ) -> BackupCatalogRecord:
        manifest = verified.manifest
        if manifest.signature.public_key_sha256 not in self._trusted_signing_key_sha256:
            raise BackupCatalogError(
                "BACKUP_CATALOG_SIGNER_UNTRUSTED",
                "the authenticated manifest signer is not pinned for catalog creation",
            )
        if manifest.backup_repository.repository_id not in self._trusted_repository_ids:
            raise BackupCatalogError(
                "BACKUP_CATALOG_REPOSITORY_UNTRUSTED",
                "the authenticated manifest names an untrusted backup repository",
            )
        verification_fact = _verification_fact_for_status(manifest)
        return self._repository.import_created_manifest(
            verified,
            creation_operation_id=creation_operation_id,
            verification_fact=verification_fact,
            audit=audit,
        )

    def rebuild_from_repository_artifacts(
        self,
        manifest_bytes: bytes,
        signature_bytes: bytes,
        *,
        public_key: Ed25519PublicKey,
        audit: BackupCatalogAuditContext,
    ) -> BackupCatalogRecord:
        verified = verify_signed_manifest(
            manifest_bytes,
            signature_bytes,
            public_key=public_key,
        )
        manifest = verified.manifest
        if manifest.signature.public_key_sha256 not in self._trusted_signing_key_sha256:
            raise BackupCatalogError(
                "BACKUP_CATALOG_SIGNER_UNTRUSTED",
                "the authenticated manifest signer is not pinned for catalog rebuild",
            )
        if manifest.backup_repository.repository_id not in self._trusted_repository_ids:
            raise BackupCatalogError(
                "BACKUP_CATALOG_REPOSITORY_UNTRUSTED",
                "the authenticated manifest names an untrusted backup repository",
            )
        return self._repository.import_rebuilt_manifest(
            verified,
            verification_fact=_verification_fact_for_status(manifest),
            audit=audit,
        )

    def get(self, backup_id: str) -> BackupCatalogRecord | None:
        return self._repository.get(backup_id)

    def list_page(
        self,
        *,
        source_environment_id: str,
        status: CatalogStatus | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> BackupCatalogPage:
        if _SAFE_IDENTIFIER.fullmatch(source_environment_id) is None or not 1 <= limit <= 100:
            raise BackupCatalogError(
                "BACKUP_CATALOG_QUERY_INVALID", "the backup catalog query is invalid"
            )
        return self._repository.list_page(
            source_environment_id=source_environment_id,
            status=status,
            cursor=cursor,
            limit=limit,
        )

    def append_integrity_verification(
        self,
        backup_id: str,
        *,
        operation_id: str,
        evidence_uri: str,
        evidence_sha256: str,
        verifier_identity: str,
        verified_at: datetime,
        audit: BackupCatalogAuditContext,
    ) -> BackupCatalogRecord:
        return self._repository.append_integrity_verification(
            backup_id,
            operation_id=operation_id,
            evidence_uri=evidence_uri,
            evidence_sha256=evidence_sha256,
            verifier_identity=verifier_identity,
            verified_at=verified_at,
            audit=audit,
        )

    def rebuild_repository(
        self,
        manifest_repository: BackupManifestRepository,
        *,
        public_keys_by_sha256: Mapping[str, Ed25519PublicKey],
        audit: BackupCatalogAuditContext,
    ) -> tuple[BackupCatalogRecord, ...]:
        if manifest_repository.repository_id not in self._trusted_repository_ids:
            raise BackupCatalogError(
                "BACKUP_CATALOG_REPOSITORY_UNTRUSTED",
                "the repository is not trusted for catalog rebuild",
            )
        records: list[BackupCatalogRecord] = []
        for artifacts in manifest_repository.list_signed_manifests():
            envelope = parse_signature_envelope(artifacts.signature_bytes)
            public_key = public_keys_by_sha256.get(envelope.public_key_sha256)
            if public_key is None:
                raise BackupCatalogError(
                    "BACKUP_CATALOG_SIGNER_UNTRUSTED",
                    "the repository manifest signer is not pinned for catalog rebuild",
                )
            verified = verify_signed_manifest(
                artifacts.manifest_bytes,
                artifacts.signature_bytes,
                public_key=public_key,
            )
            manifest = verified.manifest
            if manifest.signature.public_key_sha256 not in self._trusted_signing_key_sha256:
                raise BackupCatalogError(
                    "BACKUP_CATALOG_SIGNER_UNTRUSTED",
                    "the authenticated manifest signer is not pinned for catalog rebuild",
                )
            if (
                artifacts.repository_id != manifest.backup_repository.repository_id
                or artifacts.backup_id != manifest.backup_id
            ):
                raise BackupCatalogError(
                    "BACKUP_CATALOG_REPOSITORY_PATH_MISMATCH",
                    "the signed manifest identity does not match its repository path",
                )
            record = self._repository.import_rebuilt_manifest(
                verified,
                verification_fact=_verification_fact_for_status(manifest),
                audit=audit,
            )
            records.append(record)
        return tuple(records)


class InMemoryBackupCatalogRepository:
    """Deterministic catalog adapter for contract and idempotency tests."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._entries: dict[str, BackupCatalogEntry] = {}
        self._facts: dict[str, list[BackupCatalogStatusFact]] = {}
        self._lock = RLock()
        self.platform_audit_events: list[BackupCatalogAuditEvent] = []

    def import_created_manifest(
        self,
        verified: VerifiedManifestV1,
        *,
        creation_operation_id: str,
        verification_fact: VerificationFactV1,
        audit: BackupCatalogAuditContext,
    ) -> BackupCatalogRecord:
        with self._lock:
            candidate = _entry_from_verified(verified, cataloged_at=self._clock())
            existing = self._entries.get(candidate.backup_id)
            if existing is not None and _entry_identity(existing) != _entry_identity(candidate):
                raise BackupCatalogError(
                    "BACKUP_CATALOG_IDENTITY_CONFLICT",
                    "the backup ID is already bound to different immutable manifest facts",
                )
            if existing is None:
                self._entries[candidate.backup_id] = candidate
                existing = candidate
            facts = self._facts.setdefault(candidate.backup_id, [])
            expected = _create_status_facts(
                verified,
                verification_fact,
                creation_operation_id=creation_operation_id,
                audit=audit,
                first_status_event_id=1,
            )
            if facts:
                if len(facts) < 3 or facts[:3] != list(expected):
                    raise BackupCatalogError(
                        "BACKUP_CATALOG_OPERATION_CONFLICT",
                        "the backup creation operations are bound to different catalog facts",
                    )
                return _record(existing, facts)
            facts.extend(expected)
            self.platform_audit_events.append(
                _audit_event(
                    verified,
                    audit=audit,
                    action="platform.backup.catalog.created",
                )
            )
            return _record(existing, facts)

    def import_rebuilt_manifest(
        self,
        verified: VerifiedManifestV1,
        *,
        verification_fact: VerificationFactV1,
        audit: BackupCatalogAuditContext,
    ) -> BackupCatalogRecord:
        with self._lock:
            candidate = _entry_from_verified(verified, cataloged_at=self._clock())
            existing = self._entries.get(candidate.backup_id)
            if existing is not None and _entry_identity(existing) != _entry_identity(candidate):
                raise BackupCatalogError(
                    "BACKUP_CATALOG_IDENTITY_CONFLICT",
                    "the backup ID is already bound to different immutable manifest facts",
                )
            if existing is None:
                self._entries[candidate.backup_id] = candidate
                existing = candidate

            facts = self._facts.setdefault(candidate.backup_id, [])
            matching = next(
                (fact for fact in facts if fact.operation_id == verification_fact.operation_id),
                None,
            )
            candidate_fact = _status_fact_from_verified(
                verified,
                verification_fact,
                audit=audit,
                status_event_id=len(facts) + 1,
            )
            if matching is not None:
                if matching.model_dump(exclude={"status_event_id"}) != candidate_fact.model_dump(
                    exclude={"status_event_id"}
                ):
                    raise BackupCatalogError(
                        "BACKUP_CATALOG_OPERATION_CONFLICT",
                        "the verification operation is already bound to different catalog facts",
                    )
                return _record(existing, facts)

            if facts and facts[-1].status == candidate_fact.status:
                raise BackupCatalogError(
                    "BACKUP_CATALOG_TRANSITION_INVALID",
                    "the catalog status cannot be appended twice",
                )
            if facts and facts[-1].status == "RESTORE_VERIFIED":
                raise BackupCatalogError(
                    "BACKUP_CATALOG_TRANSITION_INVALID",
                    "a restored manifest cannot replace immutable catalog identity",
                )
            facts.append(candidate_fact)
            self.platform_audit_events.append(_audit_event(verified, audit=audit))
            return _record(existing, facts)

    def get(self, backup_id: str) -> BackupCatalogRecord | None:
        with self._lock:
            entry = self._entries.get(backup_id)
            if entry is None:
                return None
            return _record(entry, self._facts[backup_id])

    def list_page(
        self,
        *,
        source_environment_id: str,
        status: CatalogStatus | None,
        cursor: str | None,
        limit: int,
    ) -> BackupCatalogPage:
        boundary = _decode_cursor(cursor)
        with self._lock:
            items = [
                _list_item(_record(entry, self._facts[backup_id]))
                for backup_id, entry in self._entries.items()
                if entry.source_environment_id == source_environment_id
            ]
        items = [item for item in items if status is None or item.status == status]
        items.sort(key=lambda item: (item.backup_completed_at, item.backup_id), reverse=True)
        if boundary is not None:
            items = [
                item for item in items if (item.backup_completed_at, item.backup_id) < boundary
            ]
        selected = tuple(items[: limit + 1])
        page_items = selected[:limit]
        next_cursor = (
            _encode_cursor(page_items[-1]) if len(selected) > limit and page_items else None
        )
        return BackupCatalogPage(
            observed_at=self._clock(),
            count=len(page_items),
            items=page_items,
            next_cursor=next_cursor,
        )

    def append_integrity_verification(
        self,
        backup_id: str,
        *,
        operation_id: str,
        evidence_uri: str,
        evidence_sha256: str,
        verifier_identity: str,
        verified_at: datetime,
        audit: BackupCatalogAuditContext,
    ) -> BackupCatalogRecord:
        if (
            _SAFE_IDENTIFIER.fullmatch(operation_id) is None
            or not verifier_identity
            or len(verifier_identity) > 255
        ):
            raise BackupCatalogError(
                "BACKUP_CATALOG_VERIFICATION_INVALID",
                "the catalog verification fact is invalid",
            )
        with self._lock:
            entry = self._entries.get(backup_id)
            if entry is None:
                raise BackupCatalogError(
                    "BACKUP_CATALOG_NOT_FOUND", "the backup catalog entry was not found"
                )
            facts = self._facts[backup_id]
            candidate = BackupCatalogStatusFact(
                status_event_id=len(facts) + 1,
                backup_id=backup_id,
                status="INTEGRITY_VERIFIED",
                source_kind="VERIFY",
                operation_id=operation_id,
                evidence_uri=evidence_uri,
                evidence_sha256=evidence_sha256,
                failure_code=None,
                recorded_by=verifier_identity,
                request_id=audit.request_id,
                occurred_at=verified_at,
            )
            matching = next((fact for fact in facts if fact.operation_id == operation_id), None)
            if matching is not None:
                if matching.model_dump(exclude={"status_event_id"}) != candidate.model_dump(
                    exclude={"status_event_id"}
                ):
                    raise BackupCatalogError(
                        "BACKUP_CATALOG_OPERATION_CONFLICT",
                        "the verification operation is bound to different catalog facts",
                    )
                return _record(entry, facts)
            if facts[-1].status not in {"CREATED", "INTEGRITY_VERIFIED"}:
                raise BackupCatalogError(
                    "BACKUP_CATALOG_TRANSITION_INVALID",
                    "the current backup state cannot accept integrity verification",
                )
            facts.append(candidate)
            record = _record(entry, facts)
            self.platform_audit_events.append(_verification_audit_event(record, audit=audit))
            return record


class DbApiCursor(Protocol):
    description: Sequence[Sequence[object] | object] | None

    def execute(self, query: str, params: Sequence[object] | None = None) -> object: ...

    def fetchone(self) -> Mapping[str, Any] | None: ...

    def fetchall(self) -> Sequence[Mapping[str, Any]]: ...


class DbApiConnection(Protocol):
    def __enter__(self) -> DbApiConnection: ...

    def __exit__(self, *args: object) -> None: ...

    def cursor(self) -> DbApiCursor: ...


class PostgresBackupCatalogRepository:
    """Global PostgreSQL catalog adapter with same-transaction P19 audit."""

    def __init__(self, connection_factory: Callable[[], DbApiConnection]) -> None:
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(cls, dsn: str) -> PostgresBackupCatalogRepository:
        normalized = normalize_postgres_dsn(dsn)

        def connect() -> DbApiConnection:
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(normalized, row_factory=dict_row)  # type: ignore[return-value]

        return cls(connect)

    def import_created_manifest(
        self,
        verified: VerifiedManifestV1,
        *,
        creation_operation_id: str,
        verification_fact: VerificationFactV1,
        audit: BackupCatalogAuditContext,
    ) -> BackupCatalogRecord:
        candidate = _entry_from_verified(verified, cataloged_at=_utc_now())
        expected = _create_status_facts(
            verified,
            verification_fact,
            creation_operation_id=creation_operation_id,
            audit=audit,
            first_status_event_id=1,
        )
        try:
            with self._connection_factory() as connection:
                cursor = connection.cursor()
                self._insert_entry(cursor, candidate)
                entry = self._lock_entry(cursor, candidate)
                cursor.execute(
                    """
                    SELECT * FROM platform.backup_catalog_status_facts
                    WHERE backup_id = %s
                    ORDER BY status_event_id
                    """,
                    (candidate.backup_id,),
                )
                existing = tuple(
                    BackupCatalogStatusFact.model_validate(row) for row in cursor.fetchall()
                )
                if existing:
                    comparable_existing = tuple(
                        fact.model_dump(exclude={"status_event_id"}) for fact in existing[:3]
                    )
                    comparable_expected = tuple(
                        fact.model_dump(exclude={"status_event_id"}) for fact in expected
                    )
                    if len(existing) < 3 or comparable_existing != comparable_expected:
                        raise BackupCatalogError(
                            "BACKUP_CATALOG_OPERATION_CONFLICT",
                            "the backup creation operations are bound to different catalog facts",
                        )
                    return _record(entry, existing)
                for fact in expected:
                    self._insert_status_fact(cursor, fact)
                self._insert_platform_audit(
                    cursor,
                    _audit_event(
                        verified,
                        audit=audit,
                        action="platform.backup.catalog.created",
                    ),
                )
                return self._record_from_cursor(cursor, entry)
        except BackupCatalogError:
            raise
        except Exception as exc:
            code = _database_contract_code(exc)
            if code is not None:
                raise BackupCatalogError(code, "the backup catalog rejected the append") from exc
            raise

    def import_rebuilt_manifest(
        self,
        verified: VerifiedManifestV1,
        *,
        verification_fact: VerificationFactV1,
        audit: BackupCatalogAuditContext,
    ) -> BackupCatalogRecord:
        candidate = _entry_from_verified(verified, cataloged_at=_utc_now())
        try:
            with self._connection_factory() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    INSERT INTO platform.backup_catalog_entries (
                        backup_id, format_version, mode, source_platform_id,
                        source_environment_id, repository_id, manifest_uri,
                        manifest_sha256, signature_sha256, signer_public_key_sha256,
                        release_manifest_sha256, migration_manifest_sha256,
                        backup_created_at, backup_completed_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (backup_id) DO NOTHING
                    """,
                    (
                        candidate.backup_id,
                        candidate.format_version,
                        candidate.mode,
                        candidate.source_platform_id,
                        candidate.source_environment_id,
                        candidate.repository_id,
                        candidate.manifest_uri,
                        candidate.manifest_sha256,
                        candidate.signature_sha256,
                        candidate.signer_public_key_sha256,
                        candidate.release_manifest_sha256,
                        candidate.migration_manifest_sha256,
                        candidate.backup_created_at,
                        candidate.backup_completed_at,
                    ),
                )
                cursor.execute(
                    """
                    SELECT * FROM platform.backup_catalog_entries
                    WHERE backup_id = %s
                    FOR UPDATE
                    """,
                    (candidate.backup_id,),
                )
                entry_row = cursor.fetchone()
                if entry_row is None:
                    raise RuntimeError("backup catalog insert returned no durable entry")
                entry = BackupCatalogEntry.model_validate(entry_row)
                if _entry_identity(entry) != _entry_identity(candidate):
                    raise BackupCatalogError(
                        "BACKUP_CATALOG_IDENTITY_CONFLICT",
                        "the backup ID is already bound to different immutable manifest facts",
                    )

                cursor.execute(
                    """
                    SELECT * FROM platform.backup_catalog_status_facts
                    WHERE backup_id = %s AND operation_id = %s
                    """,
                    (candidate.backup_id, verification_fact.operation_id),
                )
                existing_fact_row = cursor.fetchone()
                candidate_fact = _status_fact_from_verified(
                    verified,
                    verification_fact,
                    audit=audit,
                    status_event_id=1,
                )
                if existing_fact_row is not None:
                    existing_fact = BackupCatalogStatusFact.model_validate(existing_fact_row)
                    if existing_fact.model_dump(exclude={"status_event_id"}) != (
                        candidate_fact.model_dump(exclude={"status_event_id"})
                    ):
                        raise BackupCatalogError(
                            "BACKUP_CATALOG_OPERATION_CONFLICT",
                            "the verification operation is bound to different catalog facts",
                        )
                    return self._record_from_cursor(cursor, entry)

                cursor.execute(
                    """
                    INSERT INTO platform.backup_catalog_status_facts (
                        backup_id, status, source_kind, operation_id, evidence_uri,
                        evidence_sha256, failure_code, recorded_by, request_id, occurred_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s, %s)
                    RETURNING status_event_id
                    """,
                    (
                        candidate_fact.backup_id,
                        candidate_fact.status,
                        candidate_fact.source_kind,
                        candidate_fact.operation_id,
                        candidate_fact.evidence_uri,
                        candidate_fact.evidence_sha256,
                        candidate_fact.recorded_by,
                        candidate_fact.request_id,
                        candidate_fact.occurred_at,
                    ),
                )
                if cursor.fetchone() is None:
                    raise RuntimeError("backup catalog status append returned no event")
                self._insert_platform_audit(cursor, _audit_event(verified, audit=audit))
                return self._record_from_cursor(cursor, entry)
        except BackupCatalogError:
            raise
        except Exception as exc:
            code = _database_contract_code(exc)
            if code is not None:
                raise BackupCatalogError(code, "the backup catalog rejected the append") from exc
            raise

    def get(self, backup_id: str) -> BackupCatalogRecord | None:
        with self._connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT * FROM platform.backup_catalog_entries WHERE backup_id = %s",
                (backup_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._record_from_cursor(cursor, BackupCatalogEntry.model_validate(row))

    def list_page(
        self,
        *,
        source_environment_id: str,
        status: CatalogStatus | None,
        cursor: str | None,
        limit: int,
    ) -> BackupCatalogPage:
        boundary = _decode_cursor(cursor)
        with self._connection_factory() as connection:
            cursor_handle = connection.cursor()
            cursor_handle.execute("SELECT statement_timestamp() AS observed_at")
            observed_row = cursor_handle.fetchone()
            if observed_row is None:
                raise RuntimeError("backup catalog database clock returned no value")
            clauses = ["source_environment_id = %s"]
            parameters: list[object] = [source_environment_id]
            if status is not None:
                clauses.append("status = %s")
                parameters.append(status)
            if boundary is not None:
                clauses.append("(backup_completed_at, backup_id) < (%s, %s)")
                parameters.extend(boundary)
            parameters.append(limit + 1)
            cursor_handle.execute(
                f"""
                SELECT backup_id, format_version, mode, status,
                       source_environment_id, repository_id, release_manifest_sha256,
                       backup_created_at, backup_completed_at, status_occurred_at
                FROM platform.backup_catalog_current
                WHERE {" AND ".join(clauses)}
                ORDER BY backup_completed_at DESC, backup_id DESC
                LIMIT %s
                """,
                parameters,
            )
            selected = tuple(
                BackupCatalogListItem.model_validate(row) for row in cursor_handle.fetchall()
            )
        page_items = selected[:limit]
        next_cursor = (
            _encode_cursor(page_items[-1]) if len(selected) > limit and page_items else None
        )
        return BackupCatalogPage(
            observed_at=observed_row["observed_at"],
            count=len(page_items),
            items=page_items,
            next_cursor=next_cursor,
        )

    def append_integrity_verification(
        self,
        backup_id: str,
        *,
        operation_id: str,
        evidence_uri: str,
        evidence_sha256: str,
        verifier_identity: str,
        verified_at: datetime,
        audit: BackupCatalogAuditContext,
    ) -> BackupCatalogRecord:
        try:
            with self._connection_factory() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    "SELECT * FROM platform.backup_catalog_entries WHERE backup_id = %s FOR UPDATE",
                    (backup_id,),
                )
                entry_row = cursor.fetchone()
                if entry_row is None:
                    raise BackupCatalogError(
                        "BACKUP_CATALOG_NOT_FOUND", "the backup catalog entry was not found"
                    )
                entry = BackupCatalogEntry.model_validate(entry_row)
                candidate = BackupCatalogStatusFact(
                    status_event_id=1,
                    backup_id=backup_id,
                    status="INTEGRITY_VERIFIED",
                    source_kind="VERIFY",
                    operation_id=operation_id,
                    evidence_uri=evidence_uri,
                    evidence_sha256=evidence_sha256,
                    failure_code=None,
                    recorded_by=verifier_identity,
                    request_id=audit.request_id,
                    occurred_at=verified_at,
                )
                cursor.execute(
                    """
                    SELECT * FROM platform.backup_catalog_status_facts
                    WHERE backup_id = %s AND operation_id = %s
                    """,
                    (backup_id, operation_id),
                )
                existing_row = cursor.fetchone()
                if existing_row is not None:
                    existing = BackupCatalogStatusFact.model_validate(existing_row)
                    if existing.model_dump(exclude={"status_event_id"}) != candidate.model_dump(
                        exclude={"status_event_id"}
                    ):
                        raise BackupCatalogError(
                            "BACKUP_CATALOG_OPERATION_CONFLICT",
                            "the verification operation is bound to different catalog facts",
                        )
                    return self._record_from_cursor(cursor, entry)
                self._insert_status_fact(cursor, candidate)
                record = self._record_from_cursor(cursor, entry)
                self._insert_platform_audit(
                    cursor,
                    _verification_audit_event(record, audit=audit),
                )
                return record
        except BackupCatalogError:
            raise
        except Exception as exc:
            code = _database_contract_code(exc)
            if code is not None:
                raise BackupCatalogError(code, "the backup catalog rejected the append") from exc
            raise

    @staticmethod
    def _insert_entry(cursor: DbApiCursor, candidate: BackupCatalogEntry) -> None:
        cursor.execute(
            """
            INSERT INTO platform.backup_catalog_entries (
                backup_id, format_version, mode, source_platform_id,
                source_environment_id, repository_id, manifest_uri,
                manifest_sha256, signature_sha256, signer_public_key_sha256,
                release_manifest_sha256, migration_manifest_sha256,
                backup_created_at, backup_completed_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (backup_id) DO NOTHING
            """,
            (
                candidate.backup_id,
                candidate.format_version,
                candidate.mode,
                candidate.source_platform_id,
                candidate.source_environment_id,
                candidate.repository_id,
                candidate.manifest_uri,
                candidate.manifest_sha256,
                candidate.signature_sha256,
                candidate.signer_public_key_sha256,
                candidate.release_manifest_sha256,
                candidate.migration_manifest_sha256,
                candidate.backup_created_at,
                candidate.backup_completed_at,
            ),
        )

    @staticmethod
    def _lock_entry(cursor: DbApiCursor, candidate: BackupCatalogEntry) -> BackupCatalogEntry:
        cursor.execute(
            "SELECT * FROM platform.backup_catalog_entries WHERE backup_id = %s FOR UPDATE",
            (candidate.backup_id,),
        )
        entry_row = cursor.fetchone()
        if entry_row is None:
            raise RuntimeError("backup catalog insert returned no durable entry")
        entry = BackupCatalogEntry.model_validate(entry_row)
        if _entry_identity(entry) != _entry_identity(candidate):
            raise BackupCatalogError(
                "BACKUP_CATALOG_IDENTITY_CONFLICT",
                "the backup ID is already bound to different immutable manifest facts",
            )
        return entry

    @staticmethod
    def _insert_status_fact(cursor: DbApiCursor, fact: BackupCatalogStatusFact) -> None:
        cursor.execute(
            """
            INSERT INTO platform.backup_catalog_status_facts (
                backup_id, status, source_kind, operation_id, evidence_uri,
                evidence_sha256, failure_code, recorded_by, request_id, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING status_event_id
            """,
            (
                fact.backup_id,
                fact.status,
                fact.source_kind,
                fact.operation_id,
                fact.evidence_uri,
                fact.evidence_sha256,
                fact.failure_code,
                fact.recorded_by,
                fact.request_id,
                fact.occurred_at,
            ),
        )
        if cursor.fetchone() is None:
            raise RuntimeError("backup catalog status append returned no event")

    @staticmethod
    def _record_from_cursor(
        cursor: DbApiCursor,
        entry: BackupCatalogEntry,
    ) -> BackupCatalogRecord:
        cursor.execute(
            """
            SELECT * FROM platform.backup_catalog_status_facts
            WHERE backup_id = %s
            ORDER BY status_event_id
            """,
            (entry.backup_id,),
        )
        facts = tuple(BackupCatalogStatusFact.model_validate(row) for row in cursor.fetchall())
        return _record(entry, facts)

    @staticmethod
    def _insert_platform_audit(cursor: DbApiCursor, event: BackupCatalogAuditEvent) -> None:
        cursor.execute(
            """
            INSERT INTO access_control.audit_events (
                event_id, scope_kind, organization_id, project_id, actor_id, action,
                resource_type, resource_id, request_id, outcome, safe_details
            ) VALUES (
                %s, 'PLATFORM', NULL, NULL, %s, %s, %s, %s, %s, %s, %s::jsonb
            )
            """,
            (
                uuid4(),
                event.actor_id,
                event.action,
                event.resource_type,
                event.resource_id,
                event.request_id,
                event.outcome,
                json.dumps(dict(event.safe_details), ensure_ascii=False, sort_keys=True),
            ),
        )


def _database_contract_code(error: BaseException) -> str | None:
    value = str(error).partition("\n")[0].strip()
    if value.startswith("PLATFORM_BACKUP_") and value.replace("_", "").isalnum():
        return value
    return None

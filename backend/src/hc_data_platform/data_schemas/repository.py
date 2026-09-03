from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock
from typing import Any, Protocol, cast
from uuid import uuid4

from hc_data_platform.core.etag import make_etag

from .models import (
    CreateStreamSchemaRequest,
    DataSchemaDatasetReference,
    DataSchemaPublishPreflight,
    DataSchemaValidationReport,
    DataSchemaVersionRecord,
    SchemaBlockedReason,
    SchemaHash,
    StreamSchemaDefinition,
    UpdateStreamSchemaDraftRequest,
)


@dataclass(frozen=True, slots=True)
class SchemaRouteFact:
    organization_id: str
    project_id: str
    region_code: str
    component_id: str
    schema_id: str
    schema_version: str
    relation_revision: str


@dataclass(frozen=True, slots=True)
class DataSchemaVersionWindow:
    """One bounded keyset window, returned in canonical ascending order."""

    items: tuple[DataSchemaVersionRecord, ...]
    has_more: bool


@dataclass(frozen=True, slots=True)
class DataSchemaAuditEvent:
    project_id: str
    region_code: str | None
    actor_id: str
    action: str
    resource_id: str
    request_id: str
    outcome: str
    occurred_at: datetime


class DataSchemaRepository(Protocol):
    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool: ...
    def list_versions(
        self,
        *,
        organization_id: str,
        project_id: str,
        query: str | None,
        status: str | None,
        logical_type: str | None,
        after: tuple[str, str, int] | None,
        before: tuple[str, str, int] | None,
        limit: int,
    ) -> DataSchemaVersionWindow: ...
    def get_version(
        self, *, organization_id: str, project_id: str, schema_id: str, schema_version: str
    ) -> DataSchemaVersionRecord | None: ...
    def get_previous_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
    ) -> DataSchemaVersionRecord | None: ...
    def list_dataset_references(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        schema_id: str,
        schema_version: str,
    ) -> tuple[DataSchemaDatasetReference, ...]: ...
    def associate_dataset_reference(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        dataset_id: str,
        dataset_version_id: str,
        actor_id: str,
        idempotency_key: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaDatasetReference: ...
    def resolve_route(
        self,
        *,
        project_id: str,
        region_code: str,
        component_id: str,
        schema_id: str,
        schema_version: str,
    ) -> tuple[SchemaRouteFact, ...]: ...
    def create_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        command: CreateStreamSchemaRequest,
        source: str,
        idempotency_key: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaVersionRecord: ...
    def update_draft(
        self,
        *,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        command: UpdateStreamSchemaDraftRequest,
        idempotency_key: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaVersionRecord: ...
    def validate_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        report: DataSchemaValidationReport,
        idempotency_key: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaValidationReport: ...
    def preflight_publish(
        self,
        *,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        expected_hash: str,
        validation_report_id: str,
        compatibility_check_id: str,
        idempotency_key: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaPublishPreflight: ...
    def publish_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        preflight_token: str,
        idempotency_key: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaVersionRecord: ...
    def append_audit(self, event: DataSchemaAuditEvent) -> None: ...


def _canonical_definition(
    definition: StreamSchemaDefinition | Mapping[str, object],
) -> dict[str, object]:
    value = (
        definition.model_dump(mode="json")
        if isinstance(definition, StreamSchemaDefinition)
        else dict(definition)
    )
    json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return value


def _content_hash(definition: StreamSchemaDefinition | Mapping[str, object]) -> str:
    payload = json.dumps(
        _canonical_definition(definition),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _version_etag(
    *, schema_id: str, schema_version: str, revision: int, content_hash: str, status: str
) -> str:
    return str(
        make_etag(
            {
                "schema_id": schema_id,
                "schema_version": schema_version,
                "revision": revision,
                "content_hash": content_hash,
                "status": status,
            }
        )
    )


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class InMemoryDataSchemaRepository:
    def __init__(
        self,
        *,
        organization_projects: tuple[tuple[str, str], ...] = (),
        versions: tuple[tuple[str, DataSchemaVersionRecord], ...] = (),
        route_facts: tuple[SchemaRouteFact, ...] = (),
        dataset_versions: tuple[tuple[str, str, str, str, str, str], ...] = (),
    ) -> None:
        self._organization_projects = frozenset(organization_projects)
        self._versions = {
            (organization, item.schema_id, item.schema_version): item
            for organization, item in versions
        }
        self._route_facts = tuple(route_facts)
        self._dataset_versions = frozenset(dataset_versions)
        self._dataset_references: dict[
            tuple[str, str, str, str, str, str, str], DataSchemaDatasetReference
        ] = {}
        self.audit_events: list[DataSchemaAuditEvent] = []
        self._command_receipts: dict[tuple[str, str, str, str, str], tuple[str, object]] = {}
        self._revisions: dict[tuple[str, str, str], int] = {}
        self._validation_reports: dict[tuple[str, str, str], DataSchemaValidationReport] = {}
        self._preflights: dict[
            tuple[str, str, str], tuple[DataSchemaPublishPreflight, str, str]
        ] = {}
        self._lock = RLock()

    def has_organization_project(self, *, organization_id: str, project_id: str) -> bool:
        return (organization_id, project_id) in self._organization_projects

    def list_versions(
        self,
        *,
        organization_id: str,
        project_id: str,
        query: str | None,
        status: str | None,
        logical_type: str | None,
        after: tuple[str, str, int] | None,
        before: tuple[str, str, int] | None,
        limit: int,
    ) -> DataSchemaVersionWindow:
        if not self.has_organization_project(
            organization_id=organization_id, project_id=project_id
        ):
            return DataSchemaVersionWindow(items=(), has_more=False)
        needle = query.casefold().strip() if query else ""
        with self._lock:
            values = [
                item
                for (organization, _, _), item in self._versions.items()
                if organization == organization_id
                and (
                    not needle
                    or needle in item.display_name.casefold()
                    or needle in item.schema_id.casefold()
                )
                and (status is None or item.status == status)
                and (logical_type is None or item.logical_type == logical_type)
            ]
        ordered = sorted(
            values,
            key=lambda item: (
                item.display_name.casefold(),
                item.schema_id,
                int(item.schema_version),
            ),
        )
        if after is not None:
            ordered = [
                item
                for item in ordered
                if (item.display_name.casefold(), item.schema_id, int(item.schema_version)) > after
            ]
        elif before is not None:
            ordered = [
                item
                for item in ordered
                if (item.display_name.casefold(), item.schema_id, int(item.schema_version)) < before
            ]
            ordered = ordered[-(limit + 1) :]
        has_more = len(ordered) > limit
        if before is not None:
            items = tuple(ordered[1:] if has_more else ordered)
        else:
            items = tuple(ordered[:limit])
        return DataSchemaVersionWindow(items=items, has_more=has_more)

    def get_version(
        self, *, organization_id: str, project_id: str, schema_id: str, schema_version: str
    ) -> DataSchemaVersionRecord | None:
        if not self.has_organization_project(
            organization_id=organization_id, project_id=project_id
        ):
            return None
        with self._lock:
            return self._versions.get((organization_id, schema_id, schema_version))

    def get_previous_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
    ) -> DataSchemaVersionRecord | None:
        if not self.has_organization_project(
            organization_id=organization_id, project_id=project_id
        ):
            return None
        target_version = int(schema_version)
        with self._lock:
            versions = (
                item
                for (organization, item_schema_id, _), item in self._versions.items()
                if organization == organization_id
                and item_schema_id == schema_id
                and int(item.schema_version) < target_version
            )
            return max(versions, key=lambda item: int(item.schema_version), default=None)

    def list_dataset_references(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        schema_id: str,
        schema_version: str,
    ) -> tuple[DataSchemaDatasetReference, ...]:
        if not self.has_organization_project(
            organization_id=organization_id, project_id=project_id
        ):
            return ()
        with self._lock:
            values = [
                reference
                for (
                    organization,
                    project,
                    region,
                    reference_schema_id,
                    reference_schema_version,
                    _,
                    _,
                ), reference in self._dataset_references.items()
                if (
                    organization,
                    project,
                    region,
                    reference_schema_id,
                    reference_schema_version,
                )
                == (organization_id, project_id, region_code, schema_id, schema_version)
            ]
        return tuple(
            sorted(
                values,
                key=lambda reference: (
                    reference.associated_at,
                    reference.dataset_id,
                    reference.dataset_version_id,
                ),
                reverse=True,
            )
        )

    def associate_dataset_reference(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        dataset_id: str,
        dataset_version_id: str,
        actor_id: str,
        idempotency_key: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaDatasetReference:
        resource_id = (
            f"{region_code}:{schema_id}:{schema_version}:{dataset_id}:{dataset_version_id}"
        )
        fingerprint = _fingerprint(
            {
                "expected_etag": expected_etag,
                "dataset_id": dataset_id,
                "dataset_version_id": dataset_version_id,
            }
        )
        association_key = (
            organization_id,
            project_id,
            region_code,
            schema_id,
            schema_version,
            dataset_id,
            dataset_version_id,
        )
        with self._lock:
            replay = self._receipt(
                organization_id=organization_id,
                project_id=project_id,
                resource_id=resource_id,
                operation="ASSOCIATE_DATASET",
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                if not isinstance(replay, DataSchemaDatasetReference):
                    raise RuntimeError("dataset reference receipt is malformed")
                return replay
            version = self._versions.get((organization_id, schema_id, schema_version))
            if version is None:
                raise KeyError("schema version")
            if version.etag != expected_etag:
                raise RuntimeError("etag")
            if version.status != "PUBLISHED":
                raise PermissionError("dataset reference requires published schema")
            if (
                organization_id,
                project_id,
                region_code,
                dataset_id,
                dataset_version_id,
                "READY",
            ) not in self._dataset_versions:
                raise KeyError("dataset reference")
            existing = self._dataset_references.get(association_key)
            if existing is not None:
                self._store_receipt(
                    organization_id=organization_id,
                    project_id=project_id,
                    resource_id=resource_id,
                    operation="ASSOCIATE_DATASET",
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                    response=existing,
                )
                return existing
            reference = DataSchemaDatasetReference(
                schema_id=schema_id,
                schema_version=schema_version,
                dataset_id=dataset_id,
                dataset_version_id=dataset_version_id,
                associated_by=actor_id,
                associated_at=occurred_at,
            )
            self._dataset_references[association_key] = reference
            self._store_receipt(
                organization_id=organization_id,
                project_id=project_id,
                resource_id=resource_id,
                operation="ASSOCIATE_DATASET",
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                response=reference,
            )
            self._append_mutation_audit(
                project_id=project_id,
                actor_id=actor_id,
                action="data_schema.dataset_reference.created",
                resource_id=resource_id,
                request_id=request_id,
                occurred_at=occurred_at,
            )
            return reference

    def resolve_route(
        self,
        *,
        project_id: str,
        region_code: str,
        component_id: str,
        schema_id: str,
        schema_version: str,
    ) -> tuple[SchemaRouteFact, ...]:
        return tuple(
            item
            for item in self._route_facts
            if (
                item.project_id,
                item.region_code,
                item.component_id,
                item.schema_id,
                item.schema_version,
            )
            == (project_id, region_code, component_id, schema_id, schema_version)
        )

    def append_audit(self, event: DataSchemaAuditEvent) -> None:
        with self._lock:
            self.audit_events.append(event)

    def _receipt(
        self,
        *,
        organization_id: str,
        project_id: str,
        resource_id: str,
        operation: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> object | None:
        receipt = self._command_receipts.get(
            (organization_id, project_id, resource_id, operation, idempotency_key)
        )
        if receipt is None:
            return None
        stored_fingerprint, response = receipt
        if stored_fingerprint != fingerprint:
            raise ValueError("idempotency key already has a different request")
        return response

    def _store_receipt(
        self,
        *,
        organization_id: str,
        project_id: str,
        resource_id: str,
        operation: str,
        idempotency_key: str,
        fingerprint: str,
        response: object,
    ) -> None:
        self._command_receipts[
            (organization_id, project_id, resource_id, operation, idempotency_key)
        ] = (fingerprint, response)

    def _append_mutation_audit(
        self,
        *,
        project_id: str,
        actor_id: str,
        action: str,
        resource_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> None:
        self.audit_events.append(
            DataSchemaAuditEvent(
                project_id=project_id,
                region_code=None,
                actor_id=actor_id,
                action=action,
                resource_id=resource_id,
                request_id=request_id,
                outcome="SUCCEEDED",
                occurred_at=occurred_at,
            )
        )

    def _advance_revision(
        self, *, organization_id: str, schema_id: str, schema_version: str
    ) -> int:
        key = (organization_id, schema_id, schema_version)
        revision = self._revisions.get(key, 1) + 1
        self._revisions[key] = revision
        return revision

    def create_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        command: CreateStreamSchemaRequest,
        source: str,
        idempotency_key: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaVersionRecord:
        fingerprint = _fingerprint({"command": command.model_dump(mode="json"), "source": source})
        resource_id = command.schema_id
        with self._lock:
            replay = self._receipt(
                organization_id=organization_id,
                project_id=project_id,
                resource_id=resource_id,
                operation="IMPORT" if source == "IMPORT" else "CREATE",
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return cast(DataSchemaVersionRecord, replay)
            existing = tuple(
                item
                for (organization, schema_id, _), item in self._versions.items()
                if organization == organization_id and schema_id == command.schema_id
            )
            if existing and any(item.family_id != command.family_id for item in existing):
                raise ValueError("schema_id is already bound to another family")
            next_version = str(max((int(item.schema_version) for item in existing), default=0) + 1)
            definition = _canonical_definition(command.schema_definition)
            content_hash = _content_hash(command.schema_definition)
            record = DataSchemaVersionRecord(
                schema_id=command.schema_id,
                family_id=command.family_id,
                schema_version=next_version,
                display_name=command.display_name,
                logical_type=command.logical_type,
                status="DRAFT",
                compatibility_mode=command.compatibility_mode,
                compatibility_result=None,
                schema_hash=SchemaHash(
                    algorithm="SHA-256",
                    canonicalization_version="schema-c14n-v1",
                    value=content_hash,
                ),
                schema_definition=definition,
                etag=_version_etag(
                    schema_id=command.schema_id,
                    schema_version=next_version,
                    revision=1,
                    content_hash=content_hash,
                    status="DRAFT",
                ),
                allowed_actions=("VIEW", "EDIT", "VALIDATE", "PUBLISH"),
            )
            self._versions[(organization_id, command.schema_id, next_version)] = record
            self._revisions[(organization_id, command.schema_id, next_version)] = 1
            self._store_receipt(
                organization_id=organization_id,
                project_id=project_id,
                resource_id=resource_id,
                operation="IMPORT" if source == "IMPORT" else "CREATE",
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                response=record,
            )
            self._append_mutation_audit(
                project_id=project_id,
                actor_id=actor_id,
                action="data_schema.import.committed"
                if source == "IMPORT"
                else "data_schema.draft.created",
                resource_id=f"{command.schema_id}:{next_version}",
                request_id=request_id,
                occurred_at=occurred_at,
            )
            return record

    def update_draft(
        self,
        *,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        command: UpdateStreamSchemaDraftRequest,
        idempotency_key: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaVersionRecord:
        fingerprint = _fingerprint(
            {"expected_etag": expected_etag, "command": command.model_dump(mode="json")}
        )
        resource_id = f"{schema_id}:{schema_version}"
        with self._lock:
            replay = self._receipt(
                organization_id=organization_id,
                project_id=project_id,
                resource_id=resource_id,
                operation="UPDATE",
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return cast(DataSchemaVersionRecord, replay)
            existing = self._versions.get((organization_id, schema_id, schema_version))
            if existing is None:
                raise KeyError("schema version")
            if existing.etag != expected_etag:
                raise RuntimeError("etag")
            if existing.status != "DRAFT":
                raise PermissionError("immutable")
            definition = _canonical_definition(
                command.schema_definition or existing.schema_definition
            )
            content_hash = _content_hash(definition)
            revision = self._advance_revision(
                organization_id=organization_id,
                schema_id=schema_id,
                schema_version=schema_version,
            )
            updated = existing.model_copy(
                update={
                    "display_name": command.display_name or existing.display_name,
                    "logical_type": command.logical_type or existing.logical_type,
                    "compatibility_mode": command.compatibility_mode or existing.compatibility_mode,
                    "compatibility_result": None,
                    "schema_hash": SchemaHash(
                        algorithm="SHA-256",
                        canonicalization_version="schema-c14n-v1",
                        value=content_hash,
                    ),
                    "schema_definition": definition,
                    "etag": _version_etag(
                        schema_id=schema_id,
                        schema_version=schema_version,
                        revision=revision,
                        content_hash=content_hash,
                        status="DRAFT",
                    ),
                }
            )
            self._versions[(organization_id, schema_id, schema_version)] = updated
            self._store_receipt(
                organization_id=organization_id,
                project_id=project_id,
                resource_id=resource_id,
                operation="UPDATE",
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                response=updated,
            )
            self._append_mutation_audit(
                project_id=project_id,
                actor_id=actor_id,
                action="data_schema.draft.updated",
                resource_id=resource_id,
                request_id=request_id,
                occurred_at=occurred_at,
            )
            return updated

    def validate_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        report: DataSchemaValidationReport,
        idempotency_key: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaValidationReport:
        fingerprint = _fingerprint(
            {"expected_etag": expected_etag, "report": report.model_dump(mode="json")}
        )
        resource_id = f"{schema_id}:{schema_version}"
        with self._lock:
            replay = self._receipt(
                organization_id=organization_id,
                project_id=project_id,
                resource_id=resource_id,
                operation="VALIDATE",
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return cast(DataSchemaValidationReport, replay)
            current = self._versions.get((organization_id, schema_id, schema_version))
            if current is None:
                raise KeyError("schema version")
            if current.etag != expected_etag:
                raise RuntimeError("etag")
            if current.status != "DRAFT":
                raise PermissionError("immutable")
            if current.schema_hash is None or current.schema_hash.value != report.content_hash:
                raise RuntimeError("content hash")
            self._validation_reports[(organization_id, project_id, report.id)] = report
            self._store_receipt(
                organization_id=organization_id,
                project_id=project_id,
                resource_id=resource_id,
                operation="VALIDATE",
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                response=report,
            )
            self._append_mutation_audit(
                project_id=project_id,
                actor_id=actor_id,
                action="data_schema.validation.completed",
                resource_id=resource_id,
                request_id=request_id,
                occurred_at=occurred_at,
            )
            return report

    def preflight_publish(
        self,
        *,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        expected_hash: str,
        validation_report_id: str,
        compatibility_check_id: str,
        idempotency_key: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaPublishPreflight:
        fingerprint = _fingerprint(
            {
                "expected_etag": expected_etag,
                "expected_hash": expected_hash,
                "validation_report_id": validation_report_id,
                "compatibility_check_id": compatibility_check_id,
            }
        )
        resource_id = f"{schema_id}:{schema_version}"
        with self._lock:
            replay = self._receipt(
                organization_id=organization_id,
                project_id=project_id,
                resource_id=resource_id,
                operation="PREFLIGHT",
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return cast(DataSchemaPublishPreflight, replay)
            current = self._versions.get((organization_id, schema_id, schema_version))
            report = self._validation_reports.get(
                (organization_id, project_id, validation_report_id)
            )
            if current is None or report is None:
                raise KeyError("schema version or report")
            if (
                current.etag != expected_etag
                or current.schema_hash is None
                or current.schema_hash.value != expected_hash
            ):
                raise RuntimeError("etag")
            if (
                current.status != "DRAFT"
                or report.status != "PASSED"
                or report.compatibility_check_id != compatibility_check_id
            ):
                raise PermissionError("preflight")
            token = secrets.token_urlsafe(32)
            result = DataSchemaPublishPreflight(
                allowed=True,
                preflight_token=token,
                expires_at=occurred_at + timedelta(minutes=5),
                resource_revision=current.etag,
            )
            self._preflights[(organization_id, project_id, token)] = (
                result,
                resource_id,
                expected_hash,
            )
            self._store_receipt(
                organization_id=organization_id,
                project_id=project_id,
                resource_id=resource_id,
                operation="PREFLIGHT",
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                response=result,
            )
            self._append_mutation_audit(
                project_id=project_id,
                actor_id=actor_id,
                action="data_schema.version.publish_preflighted",
                resource_id=resource_id,
                request_id=request_id,
                occurred_at=occurred_at,
            )
            return result

    def publish_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        preflight_token: str,
        idempotency_key: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaVersionRecord:
        fingerprint = _fingerprint(
            {"expected_etag": expected_etag, "preflight_token": preflight_token}
        )
        resource_id = f"{schema_id}:{schema_version}"
        with self._lock:
            replay = self._receipt(
                organization_id=organization_id,
                project_id=project_id,
                resource_id=resource_id,
                operation="PUBLISH",
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
            if replay is not None:
                return cast(DataSchemaVersionRecord, replay)
            current = self._versions.get((organization_id, schema_id, schema_version))
            preflight = self._preflights.pop((organization_id, project_id, preflight_token), None)
            if current is None or preflight is None:
                raise KeyError("preflight")
            issued, preflight_resource, expected_hash = preflight
            if (
                issued.expires_at is None
                or issued.expires_at <= occurred_at
                or preflight_resource != resource_id
                or current.etag != expected_etag
                or current.schema_hash is None
                or current.schema_hash.value != expected_hash
                or current.status != "DRAFT"
            ):
                raise PermissionError("preflight")
            revision = self._advance_revision(
                organization_id=organization_id,
                schema_id=schema_id,
                schema_version=schema_version,
            )
            published = current.model_copy(
                update={
                    "status": "PUBLISHED",
                    "etag": _version_etag(
                        schema_id=schema_id,
                        schema_version=schema_version,
                        revision=revision,
                        content_hash=current.schema_hash.value,
                        status="PUBLISHED",
                    ),
                    "allowed_actions": ("VIEW",),
                }
            )
            self._versions[(organization_id, schema_id, schema_version)] = published
            self._store_receipt(
                organization_id=organization_id,
                project_id=project_id,
                resource_id=resource_id,
                operation="PUBLISH",
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
                response=published,
            )
            self._append_mutation_audit(
                project_id=project_id,
                actor_id=actor_id,
                action="data_schema.version.published",
                resource_id=resource_id,
                request_id=request_id,
                occurred_at=occurred_at,
            )
            return published


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


def _items(value: object) -> list[object]:
    decoded = json.loads(value) if isinstance(value, str) else value
    return decoded if isinstance(decoded, list) else []


def _version(row: Mapping[str, object]) -> DataSchemaVersionRecord:
    schema_hash = (
        None
        if row["hash_value"] is None
        else SchemaHash(
            algorithm=str(row["hash_algorithm"]),
            canonicalization_version=str(row["canonicalization_version"]),
            value=str(row["hash_value"]),
        )
    )
    definition = (
        json.loads(row["schema_definition"])
        if isinstance(row["schema_definition"], str)
        else row["schema_definition"]
    )
    return DataSchemaVersionRecord(
        schema_id=str(row["schema_id"]),
        family_id=str(row["family_id"]),
        schema_version=str(row["schema_version"]),
        display_name=str(row["display_name"]),
        logical_type=str(row["logical_type"]),
        status=str(row["status"]),
        compatibility_mode=str(row["compatibility_mode"]),
        compatibility_result=None
        if row["compatibility_result"] is None
        else str(row["compatibility_result"]),
        schema_hash=schema_hash,
        schema_definition=cast(dict[str, Any], definition),
        etag=str(row["etag"]),
        allowed_actions=tuple(str(item) for item in _items(row["allowed_actions"])),
        blocked_reasons=tuple(
            SchemaBlockedReason.model_validate(item) for item in _items(row["blocked_reasons"])
        ),
    )


def _dataset_reference(row: Mapping[str, object]) -> DataSchemaDatasetReference:
    return DataSchemaDatasetReference(
        schema_id=str(row["schema_id"]),
        schema_version=str(row["schema_version"]),
        dataset_id=str(row["dataset_id"]),
        dataset_version_id=str(row["dataset_version_id"]),
        associated_by=str(row["associated_by"]),
        associated_at=cast(datetime, row["associated_at"]),
    )


class PostgresDataSchemaRepository:
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

    def preflight_publish(
        self,
        *,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        expected_hash: str,
        validation_report_id: str,
        compatibility_check_id: str,
        idempotency_key: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaPublishPreflight:
        resource_id = f"{schema_id}:{schema_version}"
        fingerprint = _fingerprint(
            {
                "expected_etag": expected_etag,
                "expected_hash": expected_hash,
                "validation_report_id": validation_report_id,
                "compatibility_check_id": compatibility_check_id,
            }
        )
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:

            def run() -> DataSchemaPublishPreflight:
                replay = self._receipt(
                    cursor,
                    organization_id=organization_id,
                    project_id=project_id,
                    resource_id=resource_id,
                    operation="PREFLIGHT",
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                )
                if replay is not None:
                    return DataSchemaPublishPreflight.model_validate(replay)
                locked = self._locked_version(
                    cursor,
                    organization_id=organization_id,
                    schema_id=schema_id,
                    schema_version=schema_version,
                )
                if locked is None:
                    raise KeyError("schema version")
                current, _revision = locked
                if (
                    current.etag != expected_etag
                    or current.schema_hash is None
                    or current.schema_hash.value != expected_hash
                ):
                    raise RuntimeError("etag")
                cursor.execute(
                    """
                    SELECT content_hash, compatibility_check_id, compatibility_result, status
                      FROM data_schemas.schema_validation_reports
                     WHERE organization_id = %s AND project_id = %s AND report_id = %s
                       AND schema_id = %s AND schema_version = %s::bigint
                     FOR KEY SHARE
                    """,
                    (
                        organization_id,
                        project_id,
                        validation_report_id,
                        schema_id,
                        schema_version,
                    ),
                )
                raw_report = cursor.fetchone()
                if raw_report is None:
                    raise KeyError("validation report")
                report = _row(cursor, raw_report)
                if (
                    current.status != "DRAFT"
                    or str(report["content_hash"]) != expected_hash
                    or str(report["compatibility_check_id"]) != compatibility_check_id
                    or str(report["compatibility_result"]) != "PASSED"
                    or str(report["status"]) != "PASSED"
                ):
                    raise PermissionError("preflight")
                token = secrets.token_urlsafe(32)
                preflight_id = str(uuid4())
                expires_at = occurred_at + timedelta(minutes=5)
                cursor.execute(
                    """
                    INSERT INTO data_schemas.schema_publish_preflights (
                        organization_id, project_id, preflight_id, schema_id, schema_version,
                        idempotency_key, expected_etag, expected_hash, validation_report_id,
                        compatibility_check_id, token_hash, allowed, status, expires_at,
                        created_by, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s::bigint, %s, %s, %s, %s, %s, %s, true,
                        'ISSUED', %s, %s, %s
                    )
                    """,
                    (
                        organization_id,
                        project_id,
                        preflight_id,
                        schema_id,
                        schema_version,
                        idempotency_key,
                        expected_etag,
                        expected_hash,
                        validation_report_id,
                        compatibility_check_id,
                        hashlib.sha256(token.encode()).hexdigest(),
                        expires_at,
                        actor_id,
                        occurred_at,
                    ),
                )
                result = DataSchemaPublishPreflight(
                    allowed=True,
                    preflight_token=token,
                    expires_at=expires_at,
                    resource_revision=current.etag,
                )
                self._store_receipt(
                    cursor,
                    organization_id=organization_id,
                    project_id=project_id,
                    resource_id=resource_id,
                    operation="PREFLIGHT",
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                    response=result.model_dump(mode="json"),
                    occurred_at=occurred_at,
                )
                self._insert_mutation_audit(
                    cursor,
                    project_id=project_id,
                    actor_id=actor_id,
                    action="data_schema.version.publish_preflighted",
                    resource_id=resource_id,
                    request_id=request_id,
                    occurred_at=occurred_at,
                )
                return result

            return cast(DataSchemaPublishPreflight, self._commit_or_rollback(connection, run))
        finally:
            cursor.close()
            connection.close()

    def publish_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        preflight_token: str,
        idempotency_key: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaVersionRecord:
        resource_id = f"{schema_id}:{schema_version}"
        fingerprint = _fingerprint(
            {"expected_etag": expected_etag, "preflight_token": preflight_token}
        )
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:

            def run() -> DataSchemaVersionRecord:
                replay = self._receipt(
                    cursor,
                    organization_id=organization_id,
                    project_id=project_id,
                    resource_id=resource_id,
                    operation="PUBLISH",
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                )
                if replay is not None:
                    return DataSchemaVersionRecord.model_validate(replay)
                locked = self._locked_version(
                    cursor,
                    organization_id=organization_id,
                    schema_id=schema_id,
                    schema_version=schema_version,
                )
                if locked is None:
                    raise KeyError("schema version")
                current, revision = locked
                cursor.execute(
                    """
                    SELECT preflight_id, expected_etag, expected_hash, status, expires_at
                      FROM data_schemas.schema_publish_preflights
                     WHERE organization_id = %s AND project_id = %s AND schema_id = %s
                       AND schema_version = %s::bigint AND token_hash = %s
                     FOR UPDATE
                    """,
                    (
                        organization_id,
                        project_id,
                        schema_id,
                        schema_version,
                        hashlib.sha256(preflight_token.encode()).hexdigest(),
                    ),
                )
                raw_preflight = cursor.fetchone()
                if raw_preflight is None:
                    raise KeyError("preflight")
                preflight = _row(cursor, raw_preflight)
                if (
                    current.status != "DRAFT"
                    or current.etag != expected_etag
                    or current.schema_hash is None
                    or current.schema_hash.value != str(preflight["expected_hash"])
                    or expected_etag != str(preflight["expected_etag"])
                    or str(preflight["status"]) != "ISSUED"
                    or cast(datetime, preflight["expires_at"]) <= occurred_at
                ):
                    raise PermissionError("preflight")
                next_revision = revision + 1
                etag = _version_etag(
                    schema_id=schema_id,
                    schema_version=schema_version,
                    revision=next_revision,
                    content_hash=current.schema_hash.value,
                    status="PUBLISHED",
                )
                cursor.execute(
                    """
                    UPDATE data_schemas.stream_schema_versions
                       SET status = 'PUBLISHED', etag = %s, revision = %s,
                           allowed_actions = '["VIEW"]'::jsonb, published_by = %s,
                           published_at = %s, updated_at = %s
                     WHERE organization_id = %s AND schema_id = %s AND schema_version = %s::bigint
                    """,
                    (
                        etag,
                        next_revision,
                        actor_id,
                        occurred_at,
                        occurred_at,
                        organization_id,
                        schema_id,
                        schema_version,
                    ),
                )
                cursor.execute(
                    """
                    UPDATE data_schemas.schema_publish_preflights
                       SET status = 'CONSUMED', consumed_at = %s
                     WHERE organization_id = %s AND project_id = %s AND preflight_id = %s
                    """,
                    (occurred_at, organization_id, project_id, str(preflight["preflight_id"])),
                )
                published = current.model_copy(
                    update={"status": "PUBLISHED", "etag": etag, "allowed_actions": ("VIEW",)}
                )
                self._store_receipt(
                    cursor,
                    organization_id=organization_id,
                    project_id=project_id,
                    resource_id=resource_id,
                    operation="PUBLISH",
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                    response=published.model_dump(mode="json"),
                    occurred_at=occurred_at,
                )
                self._insert_mutation_audit(
                    cursor,
                    project_id=project_id,
                    actor_id=actor_id,
                    action="data_schema.version.published",
                    resource_id=resource_id,
                    request_id=request_id,
                    occurred_at=occurred_at,
                )
                return published

            return cast(DataSchemaVersionRecord, self._commit_or_rollback(connection, run))
        finally:
            cursor.close()
            connection.close()

    def update_draft(
        self,
        *,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        command: UpdateStreamSchemaDraftRequest,
        idempotency_key: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaVersionRecord:
        resource_id = f"{schema_id}:{schema_version}"
        fingerprint = _fingerprint(
            {"expected_etag": expected_etag, "command": command.model_dump(mode="json")}
        )
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:

            def run() -> DataSchemaVersionRecord:
                replay = self._receipt(
                    cursor,
                    organization_id=organization_id,
                    project_id=project_id,
                    resource_id=resource_id,
                    operation="UPDATE",
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                )
                if replay is not None:
                    return DataSchemaVersionRecord.model_validate(replay)
                locked = self._locked_version(
                    cursor,
                    organization_id=organization_id,
                    schema_id=schema_id,
                    schema_version=schema_version,
                )
                if locked is None:
                    raise KeyError("schema version")
                current, revision = locked
                if current.etag != expected_etag:
                    raise RuntimeError("etag")
                if current.status != "DRAFT":
                    raise PermissionError("immutable")
                definition = _canonical_definition(
                    command.schema_definition or current.schema_definition
                )
                content_hash = _content_hash(definition)
                next_revision = revision + 1
                etag = _version_etag(
                    schema_id=schema_id,
                    schema_version=schema_version,
                    revision=next_revision,
                    content_hash=content_hash,
                    status="DRAFT",
                )
                cursor.execute(
                    """
                    UPDATE data_schemas.stream_schema_versions
                       SET display_name = %s, logical_type = %s, compatibility_mode = %s,
                           compatibility_result = NULL, hash_algorithm = 'SHA-256',
                           canonicalization_version = 'schema-c14n-v1', hash_value = %s,
                           schema_definition = %s::jsonb, etag = %s, revision = %s,
                           updated_at = %s
                     WHERE organization_id = %s AND schema_id = %s AND schema_version = %s::bigint
                    """,
                    (
                        command.display_name or current.display_name,
                        command.logical_type or current.logical_type,
                        command.compatibility_mode or current.compatibility_mode,
                        content_hash,
                        json.dumps(definition, ensure_ascii=False),
                        etag,
                        next_revision,
                        occurred_at,
                        organization_id,
                        schema_id,
                        schema_version,
                    ),
                )
                updated = current.model_copy(
                    update={
                        "display_name": command.display_name or current.display_name,
                        "logical_type": command.logical_type or current.logical_type,
                        "compatibility_mode": command.compatibility_mode
                        or current.compatibility_mode,
                        "compatibility_result": None,
                        "schema_hash": SchemaHash(
                            algorithm="SHA-256",
                            canonicalization_version="schema-c14n-v1",
                            value=content_hash,
                        ),
                        "schema_definition": definition,
                        "etag": etag,
                    }
                )
                self._store_receipt(
                    cursor,
                    organization_id=organization_id,
                    project_id=project_id,
                    resource_id=resource_id,
                    operation="UPDATE",
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                    response=updated.model_dump(mode="json"),
                    occurred_at=occurred_at,
                )
                self._insert_mutation_audit(
                    cursor,
                    project_id=project_id,
                    actor_id=actor_id,
                    action="data_schema.draft.updated",
                    resource_id=resource_id,
                    request_id=request_id,
                    occurred_at=occurred_at,
                )
                return updated

            return cast(DataSchemaVersionRecord, self._commit_or_rollback(connection, run))
        finally:
            cursor.close()
            connection.close()

    def validate_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        report: DataSchemaValidationReport,
        idempotency_key: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaValidationReport:
        resource_id = f"{schema_id}:{schema_version}"
        fingerprint = _fingerprint(
            {"expected_etag": expected_etag, "report": report.model_dump(mode="json")}
        )
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:

            def run() -> DataSchemaValidationReport:
                replay = self._receipt(
                    cursor,
                    organization_id=organization_id,
                    project_id=project_id,
                    resource_id=resource_id,
                    operation="VALIDATE",
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                )
                if replay is not None:
                    return DataSchemaValidationReport.model_validate(replay)
                locked = self._locked_version(
                    cursor,
                    organization_id=organization_id,
                    schema_id=schema_id,
                    schema_version=schema_version,
                )
                if locked is None:
                    raise KeyError("schema version")
                current, _revision = locked
                if current.etag != expected_etag:
                    raise RuntimeError("etag")
                if current.status != "DRAFT":
                    raise PermissionError("immutable")
                if current.schema_hash is None or current.schema_hash.value != report.content_hash:
                    raise RuntimeError("content hash")
                cursor.execute(
                    """
                    INSERT INTO data_schemas.schema_validation_reports (
                        organization_id, project_id, report_id, schema_id, schema_version,
                        content_hash, compatibility_check_id, compatibility_result, status,
                        findings, checked_by, checked_at
                    ) VALUES (%s, %s, %s, %s, %s::bigint, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    """,
                    (
                        organization_id,
                        project_id,
                        report.id,
                        schema_id,
                        schema_version,
                        report.content_hash,
                        report.compatibility_check_id,
                        report.compatibility_result,
                        report.status,
                        json.dumps([item.model_dump(mode="json") for item in report.findings]),
                        actor_id,
                        occurred_at,
                    ),
                )
                self._store_receipt(
                    cursor,
                    organization_id=organization_id,
                    project_id=project_id,
                    resource_id=resource_id,
                    operation="VALIDATE",
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                    response=report.model_dump(mode="json"),
                    occurred_at=occurred_at,
                )
                self._insert_mutation_audit(
                    cursor,
                    project_id=project_id,
                    actor_id=actor_id,
                    action="data_schema.validation.completed",
                    resource_id=resource_id,
                    request_id=request_id,
                    occurred_at=occurred_at,
                )
                return report

            return cast(DataSchemaValidationReport, self._commit_or_rollback(connection, run))
        finally:
            cursor.close()
            connection.close()

    def list_versions(
        self,
        *,
        organization_id: str,
        project_id: str,
        query: str | None,
        status: str | None,
        logical_type: str | None,
        after: tuple[str, str, int] | None,
        before: tuple[str, str, int] | None,
        limit: int,
    ) -> DataSchemaVersionWindow:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            if after is not None and before is not None:
                raise ValueError("only one schema page boundary is allowed")
            boundary = after or before
            comparison = ">" if after is not None else "<"
            ordering = "ASC" if before is None else "DESC"
            cursor.execute(
                f"""
                SELECT schema_id, family_id, schema_version, display_name, logical_type, status,
                       compatibility_mode, compatibility_result, hash_algorithm,
                       canonicalization_version, hash_value, schema_definition, etag,
                       allowed_actions, blocked_reasons
                  FROM data_schemas.stream_schema_versions version
                  JOIN registry.organization_projects membership
                    ON membership.organization_id = version.organization_id
                 WHERE version.organization_id = %s AND membership.project_id = %s
                   AND (
                       %s::text IS NULL
                       OR version.display_name ILIKE '%%' || %s || '%%'
                       OR version.schema_id ILIKE '%%' || %s || '%%'
                   )
                   AND (%s::text IS NULL OR version.status = %s)
                   AND (%s::text IS NULL OR version.logical_type = %s)
                   AND (
                       %s::text IS NULL
                       OR (lower(version.display_name), version.schema_id, version.schema_version)
                          {comparison} (%s::text, %s::text, %s::bigint)
                   )
                 ORDER BY lower(version.display_name) {ordering}, version.schema_id {ordering},
                          version.schema_version {ordering}
                 LIMIT %s
            """,
                (
                    organization_id,
                    project_id,
                    query,
                    query,
                    query,
                    status,
                    status,
                    logical_type,
                    logical_type,
                    None if boundary is None else boundary[0],
                    None if boundary is None else boundary[0],
                    None if boundary is None else boundary[1],
                    None if boundary is None else boundary[2],
                    limit + 1,
                ),
            )
            items = tuple(_version(_row(cursor, raw)) for raw in cursor.fetchall())
            has_more = len(items) > limit
            bounded = items[:limit]
            if before is not None:
                bounded = tuple(reversed(bounded))
            return DataSchemaVersionWindow(items=bounded, has_more=has_more)
        finally:
            cursor.close()
            connection.close()

    def get_version(
        self, *, organization_id: str, project_id: str, schema_id: str, schema_version: str
    ) -> DataSchemaVersionRecord | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT schema_id, family_id, schema_version, display_name, logical_type, status,
                       compatibility_mode, compatibility_result, hash_algorithm,
                       canonicalization_version, hash_value, schema_definition, etag,
                       allowed_actions, blocked_reasons
                  FROM data_schemas.stream_schema_versions version
                  JOIN registry.organization_projects membership
                    ON membership.organization_id = version.organization_id
                 WHERE version.organization_id = %s AND membership.project_id = %s
                   AND version.schema_id = %s AND version.schema_version = %s::bigint
            """,
                (organization_id, project_id, schema_id, schema_version),
            )
            raw = cursor.fetchone()
            return None if raw is None else _version(_row(cursor, raw))
        finally:
            cursor.close()
            connection.close()

    def get_previous_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
    ) -> DataSchemaVersionRecord | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT schema_id, family_id, schema_version, display_name, logical_type, status,
                       compatibility_mode, compatibility_result, hash_algorithm,
                       canonicalization_version, hash_value, schema_definition, etag,
                       allowed_actions, blocked_reasons
                  FROM data_schemas.stream_schema_versions version
                  JOIN registry.organization_projects membership
                    ON membership.organization_id = version.organization_id
                 WHERE version.organization_id = %s AND membership.project_id = %s
                   AND version.schema_id = %s
                   AND version.schema_version < %s::bigint
                 ORDER BY version.schema_version DESC
                 LIMIT 1
                """,
                (organization_id, project_id, schema_id, schema_version),
            )
            raw = cursor.fetchone()
            return None if raw is None else _version(_row(cursor, raw))
        finally:
            cursor.close()
            connection.close()

    def list_dataset_references(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        schema_id: str,
        schema_version: str,
    ) -> tuple[DataSchemaDatasetReference, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT schema_id, schema_version, dataset_id, dataset_version_id,
                       associated_by, associated_at
                  FROM data_schemas.stream_schema_dataset_version_references
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND schema_id = %s AND schema_version = %s::bigint
                 ORDER BY associated_at DESC, dataset_id DESC, dataset_version_id DESC
                """,
                (organization_id, project_id, region_code, schema_id, schema_version),
            )
            return tuple(_dataset_reference(_row(cursor, raw)) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def associate_dataset_reference(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        dataset_id: str,
        dataset_version_id: str,
        actor_id: str,
        idempotency_key: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaDatasetReference:
        resource_id = (
            f"{region_code}:{schema_id}:{schema_version}:{dataset_id}:{dataset_version_id}"
        )
        fingerprint = _fingerprint(
            {
                "expected_etag": expected_etag,
                "dataset_id": dataset_id,
                "dataset_version_id": dataset_version_id,
            }
        )
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:

            def run() -> DataSchemaDatasetReference:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (
                        "data-schema-dataset-reference:"
                        f"{organization_id}:{project_id}:{region_code}:{resource_id}",
                    ),
                )
                replay = self._receipt(
                    cursor,
                    organization_id=organization_id,
                    project_id=project_id,
                    resource_id=resource_id,
                    operation="ASSOCIATE_DATASET",
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                )
                if replay is not None:
                    return DataSchemaDatasetReference.model_validate(replay)
                locked = self._locked_version(
                    cursor,
                    organization_id=organization_id,
                    schema_id=schema_id,
                    schema_version=schema_version,
                )
                if locked is None:
                    raise KeyError("schema version")
                version, _revision = locked
                if version.etag != expected_etag:
                    raise RuntimeError("etag")
                if version.status != "PUBLISHED":
                    raise PermissionError("dataset reference requires published schema")
                cursor.execute(
                    """
                    SELECT 1
                      FROM dataset_registry.dataset_versions
                     WHERE organization_id = %s AND project_id = %s AND region_code = %s
                       AND dataset_id = %s AND version_id = %s AND version_status = 'READY'
                     FOR KEY SHARE
                    """,
                    (
                        organization_id,
                        project_id,
                        region_code,
                        dataset_id,
                        dataset_version_id,
                    ),
                )
                if cursor.fetchone() is None:
                    raise KeyError("dataset reference")
                cursor.execute(
                    """
                    INSERT INTO data_schemas.stream_schema_dataset_version_references (
                        organization_id, project_id, region_code, schema_id, schema_version,
                        dataset_id, dataset_version_id, associated_by, associated_at
                    ) VALUES (%s, %s, %s, %s, %s::bigint, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    RETURNING schema_id, schema_version, dataset_id, dataset_version_id,
                              associated_by, associated_at
                    """,
                    (
                        organization_id,
                        project_id,
                        region_code,
                        schema_id,
                        schema_version,
                        dataset_id,
                        dataset_version_id,
                        actor_id,
                        occurred_at,
                    ),
                )
                raw_reference = cursor.fetchone()
                created = raw_reference is not None
                if raw_reference is None:
                    cursor.execute(
                        """
                        SELECT schema_id, schema_version, dataset_id, dataset_version_id,
                               associated_by, associated_at
                          FROM data_schemas.stream_schema_dataset_version_references
                         WHERE organization_id = %s AND project_id = %s AND region_code = %s
                           AND schema_id = %s AND schema_version = %s::bigint
                           AND dataset_id = %s AND dataset_version_id = %s
                        """,
                        (
                            organization_id,
                            project_id,
                            region_code,
                            schema_id,
                            schema_version,
                            dataset_id,
                            dataset_version_id,
                        ),
                    )
                    raw_reference = cursor.fetchone()
                if raw_reference is None:
                    raise RuntimeError("dataset reference insert did not persist")
                reference = _dataset_reference(_row(cursor, raw_reference))
                self._store_receipt(
                    cursor,
                    organization_id=organization_id,
                    project_id=project_id,
                    resource_id=resource_id,
                    operation="ASSOCIATE_DATASET",
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                    response=reference.model_dump(mode="json"),
                    occurred_at=occurred_at,
                )
                if created:
                    self._insert_mutation_audit(
                        cursor,
                        project_id=project_id,
                        region_code=region_code,
                        actor_id=actor_id,
                        action="data_schema.dataset_reference.created",
                        resource_id=resource_id,
                        request_id=request_id,
                        occurred_at=occurred_at,
                    )
                return reference

            return cast(DataSchemaDatasetReference, self._commit_or_rollback(connection, run))
        finally:
            cursor.close()
            connection.close()

    def resolve_route(
        self,
        *,
        project_id: str,
        region_code: str,
        component_id: str,
        schema_id: str,
        schema_version: str,
    ) -> tuple[SchemaRouteFact, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT membership.organization_id, channel.project_id, channel.region_code,
                       channel.component_id, channel.schema_id, channel.schema_version,
                       component.robot_id || ':' || channel.channel_id || ':' || version.etag
                           AS relation_revision
                  FROM robotics.component_channels channel
                  JOIN robotics.robot_components component
                    ON component.project_id = channel.project_id
                   AND component.region_code = channel.region_code
                   AND component.component_id = channel.component_id
                  JOIN registry.organization_projects membership
                    ON membership.project_id = channel.project_id
                  JOIN data_schemas.stream_schema_versions version
                    ON version.organization_id = membership.organization_id
                   AND version.schema_id = channel.schema_id
                   AND version.schema_version = channel.schema_version
                 WHERE channel.project_id = %s
                   AND channel.region_code = %s
                   AND channel.component_id = %s
                   AND channel.schema_id = %s
                   AND channel.schema_version = %s::bigint
            """,
                (project_id, region_code, component_id, schema_id, schema_version),
            )
            return tuple(
                SchemaRouteFact(
                    organization_id=str(row["organization_id"]),
                    project_id=str(row["project_id"]),
                    region_code=str(row["region_code"]),
                    component_id=str(row["component_id"]),
                    schema_id=str(row["schema_id"]),
                    schema_version=str(row["schema_version"]),
                    relation_revision=str(row["relation_revision"]),
                )
                for raw in cursor.fetchall()
                for row in (_row(cursor, raw),)
            )
        finally:
            cursor.close()
            connection.close()

    def append_audit(self, event: DataSchemaAuditEvent) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO core.audit_events (
                    audit_id, project_id, region_code, actor_id, action, resource_type,
                    resource_id, request_id, before_hash, after_hash, details, occurred_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'DATA_SCHEMA_VERSION', %s, %s, NULL, NULL,
                    %s::jsonb, %s
                )
                """,
                (
                    str(uuid4()),
                    event.project_id,
                    event.region_code,
                    event.actor_id,
                    event.action,
                    event.resource_id,
                    event.request_id,
                    json.dumps({"outcome": event.outcome}),
                    event.occurred_at,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _receipt(
        cursor: DbApiCursor,
        *,
        organization_id: str,
        project_id: str,
        resource_id: str,
        operation: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> dict[str, object] | None:
        cursor.execute(
            """
            SELECT request_fingerprint, response
              FROM data_schemas.schema_command_receipts
             WHERE organization_id = %s AND project_id = %s AND resource_id = %s
               AND operation = %s AND idempotency_key = %s
             FOR UPDATE
            """,
            (organization_id, project_id, resource_id, operation, idempotency_key),
        )
        raw = cursor.fetchone()
        if raw is None:
            return None
        row = _row(cursor, raw)
        if str(row["request_fingerprint"]) != fingerprint:
            raise ValueError("idempotency key already has a different request")
        payload = row["response"]
        decoded = json.loads(payload) if isinstance(payload, str) else payload
        if not isinstance(decoded, dict):
            raise RuntimeError("schema command receipt response is malformed")
        return cast(dict[str, object], decoded)

    @staticmethod
    def _store_receipt(
        cursor: DbApiCursor,
        *,
        organization_id: str,
        project_id: str,
        resource_id: str,
        operation: str,
        idempotency_key: str,
        fingerprint: str,
        response: Mapping[str, object],
        occurred_at: datetime,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO data_schemas.schema_command_receipts (
                organization_id, project_id, resource_id, operation, idempotency_key,
                request_fingerprint, response, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                organization_id,
                project_id,
                resource_id,
                operation,
                idempotency_key,
                fingerprint,
                json.dumps(response, ensure_ascii=False),
                occurred_at,
            ),
        )

    @staticmethod
    def _insert_mutation_audit(
        cursor: DbApiCursor,
        *,
        project_id: str,
        region_code: str | None = None,
        actor_id: str,
        action: str,
        resource_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO core.audit_events (
                audit_id, project_id, region_code, actor_id, action, resource_type,
                resource_id, request_id, before_hash, after_hash, details, occurred_at
            ) VALUES (
                %s, %s, %s, %s, %s, 'DATA_SCHEMA_VERSION', %s, %s, NULL, NULL,
                %s::jsonb, %s
            )
            """,
            (
                str(uuid4()),
                project_id,
                region_code,
                actor_id,
                action,
                resource_id,
                request_id,
                json.dumps({"outcome": "SUCCEEDED"}),
                occurred_at,
            ),
        )

    @staticmethod
    def _locked_version(
        cursor: DbApiCursor,
        *,
        organization_id: str,
        schema_id: str,
        schema_version: str,
    ) -> tuple[DataSchemaVersionRecord, int] | None:
        cursor.execute(
            """
            SELECT schema_id, family_id, schema_version, display_name, logical_type, status,
                   compatibility_mode, compatibility_result, hash_algorithm,
                   canonicalization_version, hash_value, schema_definition, etag,
                   allowed_actions, blocked_reasons, revision
              FROM data_schemas.stream_schema_versions
             WHERE organization_id = %s AND schema_id = %s AND schema_version = %s::bigint
             FOR UPDATE
            """,
            (organization_id, schema_id, schema_version),
        )
        raw = cursor.fetchone()
        if raw is None:
            return None
        row = _row(cursor, raw)
        return _version(row), int(cast(str, row["revision"]))

    @staticmethod
    def _commit_or_rollback(connection: DbApiConnection, callback: Callable[[], Any]) -> Any:
        try:
            result = callback()
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise

    def create_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        command: CreateStreamSchemaRequest,
        source: str,
        idempotency_key: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> DataSchemaVersionRecord:
        operation = "IMPORT" if source == "IMPORT" else "CREATE"
        fingerprint = _fingerprint({"command": command.model_dump(mode="json"), "source": source})
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:

            def run() -> DataSchemaVersionRecord:
                replay = self._receipt(
                    cursor,
                    organization_id=organization_id,
                    project_id=project_id,
                    resource_id=command.schema_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                )
                if replay is not None:
                    return DataSchemaVersionRecord.model_validate(replay)
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"data-schema:{organization_id}:{command.schema_id}",),
                )
                cursor.execute(
                    """
                    SELECT family_id, schema_version
                      FROM data_schemas.stream_schema_versions
                     WHERE organization_id = %s AND schema_id = %s
                     ORDER BY schema_version DESC
                     FOR UPDATE
                    """,
                    (organization_id, command.schema_id),
                )
                existing = [_row(cursor, raw) for raw in cursor.fetchall()]
                if existing and any(str(row["family_id"]) != command.family_id for row in existing):
                    raise ValueError("schema_id is already bound to another family")
                schema_version = str(
                    max((int(cast(str, row["schema_version"])) for row in existing), default=0) + 1
                )
                definition = _canonical_definition(command.schema_definition)
                content_hash = _content_hash(command.schema_definition)
                etag = _version_etag(
                    schema_id=command.schema_id,
                    schema_version=schema_version,
                    revision=1,
                    content_hash=content_hash,
                    status="DRAFT",
                )
                cursor.execute(
                    """
                    INSERT INTO data_schemas.stream_schema_versions (
                        organization_id, schema_id, schema_version, family_id, display_name,
                        logical_type, status, compatibility_mode, compatibility_result,
                        hash_algorithm, canonicalization_version, hash_value, schema_definition,
                        etag, allowed_actions, blocked_reasons, source, revision, created_by,
                        created_at, updated_at
                    ) VALUES (
                        %s, %s, %s::bigint, %s, %s, %s, 'DRAFT', %s, NULL,
                        'SHA-256', 'schema-c14n-v1', %s, %s::jsonb, %s,
                        '["VIEW", "EDIT", "VALIDATE", "PUBLISH"]'::jsonb, '[]'::jsonb,
                        %s, 1, %s, %s, %s
                    )
                    """,
                    (
                        organization_id,
                        command.schema_id,
                        schema_version,
                        command.family_id,
                        command.display_name,
                        command.logical_type,
                        command.compatibility_mode,
                        content_hash,
                        json.dumps(definition, ensure_ascii=False),
                        etag,
                        source,
                        actor_id,
                        occurred_at,
                        occurred_at,
                    ),
                )
                record = DataSchemaVersionRecord(
                    schema_id=command.schema_id,
                    family_id=command.family_id,
                    schema_version=schema_version,
                    display_name=command.display_name,
                    logical_type=command.logical_type,
                    status="DRAFT",
                    compatibility_mode=command.compatibility_mode,
                    compatibility_result=None,
                    schema_hash=SchemaHash(
                        algorithm="SHA-256",
                        canonicalization_version="schema-c14n-v1",
                        value=content_hash,
                    ),
                    schema_definition=definition,
                    etag=etag,
                    allowed_actions=("VIEW", "EDIT", "VALIDATE", "PUBLISH"),
                )
                self._store_receipt(
                    cursor,
                    organization_id=organization_id,
                    project_id=project_id,
                    resource_id=command.schema_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                    response=record.model_dump(mode="json"),
                    occurred_at=occurred_at,
                )
                self._insert_mutation_audit(
                    cursor,
                    project_id=project_id,
                    actor_id=actor_id,
                    action="data_schema.import.committed"
                    if source == "IMPORT"
                    else "data_schema.draft.created",
                    resource_id=f"{command.schema_id}:{schema_version}",
                    request_id=request_id,
                    occurred_at=occurred_at,
                )
                return record

            return cast(DataSchemaVersionRecord, self._commit_or_rollback(connection, run))
        finally:
            cursor.close()
            connection.close()

"""P16 calibration persistence ports and PostgreSQL implementation."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from datetime import datetime
from threading import RLock
from typing import Any, Protocol, cast
from uuid import uuid4

from .models import (
    CalibrationBlockedReason,
    CalibrationDatasetAssociation,
    CalibrationDocument,
    CalibrationSetRecord,
    CalibrationValidation,
    CalibrationValidationReport,
    CalibrationVersionDocument,
    CalibrationVersionSummary,
)


@dataclass(frozen=True, slots=True)
class CalibrationAuditEvent:
    project_id: str
    region_code: str
    actor_id: str
    action: str
    resource_id: str
    request_id: str
    outcome: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class CalibrationPublishPreflightRecord:
    project_id: str
    region_code: str
    preflight_id: str
    set_id: str
    version: str
    idempotency_key: str
    request_fingerprint: str
    expected_etag: str
    expected_hash: str
    validation_context_hash: str
    validation_report_id: str
    allowed: bool
    blockers: tuple[CalibrationBlockedReason, ...]
    status: str
    token_hash: str
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None


@dataclass(frozen=True, slots=True)
class CalibrationCommandReceipt:
    request_fingerprint: str
    version: str
    report_id: str | None


class CalibrationRepository(Protocol):
    def list_sets(
        self,
        *,
        project_id: str,
        region_code: str,
        query: str | None,
        robot_id: str | None,
        component_id: str | None,
    ) -> tuple[CalibrationSetRecord, ...]: ...

    def get_set(
        self, *, project_id: str, region_code: str, set_id: str
    ) -> CalibrationSetRecord | None: ...

    def find_publish_preflight_by_idempotency(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        idempotency_key: str,
    ) -> CalibrationPublishPreflightRecord | None: ...

    def create_publish_preflight(
        self, *, record: CalibrationPublishPreflightRecord, audit: CalibrationAuditEvent
    ) -> None: ...

    def publish_set(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        preflight_id: str,
        token_hash: str,
        idempotency_key: str,
        expected_etag: str,
        occurred_at: datetime,
        audit: CalibrationAuditEvent,
    ) -> CalibrationSetRecord: ...

    def create_set(
        self,
        *,
        project_id: str,
        region_code: str,
        item: CalibrationSetRecord,
        document: CalibrationVersionDocument,
        idempotency_key: str,
        request_fingerprint: str,
        audit: CalibrationAuditEvent,
    ) -> CalibrationSetRecord: ...

    def get_version_document(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
    ) -> CalibrationVersionDocument | None: ...

    def list_version_summaries(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
    ) -> tuple[CalibrationVersionSummary, ...]: ...

    def recalibrate_set(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        expected_etag: str,
        document: CalibrationDocument,
        content_hash: str,
        actor_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        audit: CalibrationAuditEvent,
    ) -> CalibrationSetRecord: ...

    def list_dataset_associations(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
    ) -> tuple[CalibrationDatasetAssociation, ...]: ...

    def associate_dataset(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        expected_etag: str,
        dataset_id: str,
        dataset_version_id: str,
        actor_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        audit: CalibrationAuditEvent,
    ) -> CalibrationDatasetAssociation: ...

    def validate_set(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        expected_etag: str,
        report: CalibrationValidationReport,
        idempotency_key: str,
        request_fingerprint: str,
        audit: CalibrationAuditEvent,
    ) -> tuple[CalibrationSetRecord, CalibrationValidationReport]: ...

    def get_validation_report(
        self,
        *,
        project_id: str,
        region_code: str,
        report_id: str,
    ) -> CalibrationValidationReport | None: ...

    def append_audit(self, event: CalibrationAuditEvent) -> None: ...


class InMemoryCalibrationRepository:
    def __init__(
        self,
        *,
        sets: tuple[tuple[str, str, CalibrationSetRecord], ...] = (),
        dataset_versions: tuple[tuple[str, str, str, str], ...] = (),
    ) -> None:
        self._sets = {(project, region, value.id): value for project, region, value in sets}
        self._publish_preflights: dict[tuple[str, str, str], CalibrationPublishPreflightRecord] = {}
        self._documents: dict[tuple[str, str, str, str], CalibrationVersionDocument] = {}
        self._reports: dict[tuple[str, str, str], CalibrationValidationReport] = {}
        self._command_receipts: dict[tuple[str, str, str, str, str], CalibrationCommandReceipt] = {}
        self._dataset_versions = frozenset(dataset_versions)
        self._dataset_associations: dict[
            tuple[str, str, str, str, str, str], CalibrationDatasetAssociation
        ] = {}
        self.audit_events: list[CalibrationAuditEvent] = []
        self._lock = RLock()

    def list_sets(
        self,
        *,
        project_id: str,
        region_code: str,
        query: str | None,
        robot_id: str | None,
        component_id: str | None,
    ) -> tuple[CalibrationSetRecord, ...]:
        needle = query.casefold().strip() if query else ""
        with self._lock:
            values = [
                item
                for (project, region, _), item in self._sets.items()
                if project == project_id
                and region == region_code
                and (robot_id is None or item.robot_instance_id == robot_id)
                and (component_id is None or item.component_id == component_id)
                and (
                    not needle
                    or needle in item.id.casefold()
                    or needle in item.robot_instance_id.casefold()
                )
            ]
        return tuple(sorted(values, key=lambda item: (item.robot_instance_id, item.id)))

    def get_set(
        self, *, project_id: str, region_code: str, set_id: str
    ) -> CalibrationSetRecord | None:
        with self._lock:
            return self._sets.get((project_id, region_code, set_id))

    def find_publish_preflight_by_idempotency(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        idempotency_key: str,
    ) -> CalibrationPublishPreflightRecord | None:
        with self._lock:
            return next(
                (
                    item
                    for item in self._publish_preflights.values()
                    if (
                        item.project_id,
                        item.region_code,
                        item.set_id,
                        item.version,
                        item.idempotency_key,
                    )
                    == (project_id, region_code, set_id, version, idempotency_key)
                ),
                None,
            )

    def create_publish_preflight(
        self, *, record: CalibrationPublishPreflightRecord, audit: CalibrationAuditEvent
    ) -> None:
        with self._lock:
            existing = self.find_publish_preflight_by_idempotency(
                project_id=record.project_id,
                region_code=record.region_code,
                set_id=record.set_id,
                version=record.version,
                idempotency_key=record.idempotency_key,
            )
            if existing is not None:
                raise ValueError("publish preflight idempotency key already exists")
            self._publish_preflights[
                (record.project_id, record.region_code, record.preflight_id)
            ] = record
            self.audit_events.append(audit)

    def publish_set(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        preflight_id: str,
        token_hash: str,
        idempotency_key: str,
        expected_etag: str,
        occurred_at: datetime,
        audit: CalibrationAuditEvent,
    ) -> CalibrationSetRecord:
        with self._lock:
            key = (project_id, region_code, preflight_id)
            preflight = self._publish_preflights.get(key)
            if preflight is None:
                raise KeyError("preflight")
            if preflight.set_id != set_id or preflight.version != version:
                raise PermissionError("publish preflight does not match calibration version")
            if preflight.token_hash != token_hash:
                raise PermissionError("publish preflight token does not match")
            if preflight.idempotency_key != idempotency_key:
                raise ValueError("publish idempotency key does not match preflight")
            if preflight.expected_etag != expected_etag:
                raise ValueError("calibration set etag does not match preflight")
            item = self._sets.get((project_id, region_code, set_id))
            if item is None:
                raise KeyError("calibration set")
            if preflight.status == "CONSUMED":
                return item
            if preflight.status != "ISSUED" or preflight.expires_at <= occurred_at:
                raise TimeoutError("publish preflight has expired")
            if (
                preflight.expected_etag != expected_etag
                or item.etag != expected_etag
                or not preflight.allowed
                or item.snapshot_status != "DRAFT"
                or item.content_hash != preflight.expected_hash
                or item.validation_context_hash != preflight.validation_context_hash
                or item.validation is None
                or item.validation.status != "PASSED"
                or item.validation.report_id != preflight.validation_report_id
                or item.validation.content_hash != preflight.expected_hash
            ):
                raise ValueError("publish preflight is stale")
            revision = _etag_revision(item.etag) + 1
            updated = item.model_copy(
                update={
                    "snapshot_status": "READY",
                    "availability": "ACTIVE",
                    "etag": f'"calibration:{set_id}:{revision}"',
                }
            )
            self._sets[(project_id, region_code, set_id)] = updated
            self._publish_preflights[key] = dataclass_replace(
                preflight, status="CONSUMED", consumed_at=occurred_at
            )
            self.audit_events.append(audit)
            return updated

    def append_audit(self, event: CalibrationAuditEvent) -> None:
        with self._lock:
            self.audit_events.append(event)

    def create_set(
        self,
        *,
        project_id: str,
        region_code: str,
        item: CalibrationSetRecord,
        document: CalibrationVersionDocument,
        idempotency_key: str,
        request_fingerprint: str,
        audit: CalibrationAuditEvent,
    ) -> CalibrationSetRecord:
        receipt_key = (project_id, region_code, item.id, "CREATE", idempotency_key)
        with self._lock:
            receipt = self._command_receipts.get(receipt_key)
            if receipt is not None:
                if receipt.request_fingerprint != request_fingerprint:
                    raise ValueError("idempotency key was reused")
                stored = self._sets.get((project_id, region_code, item.id))
                if stored is None:
                    raise RuntimeError("calibration create receipt has no set")
                return stored
            if (project_id, region_code, item.id) in self._sets:
                raise ValueError("calibration set already exists")
            self._sets[(project_id, region_code, item.id)] = item
            self._documents[(project_id, region_code, item.id, item.version)] = document
            self._command_receipts[receipt_key] = CalibrationCommandReceipt(
                request_fingerprint=request_fingerprint, version=item.version, report_id=None
            )
            self.audit_events.append(audit)
            return item

    def get_version_document(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
    ) -> CalibrationVersionDocument | None:
        with self._lock:
            return self._documents.get((project_id, region_code, set_id, version))

    def list_version_summaries(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
    ) -> tuple[CalibrationVersionSummary, ...]:
        with self._lock:
            values = [
                CalibrationVersionSummary(
                    version=document.version,
                    source=document.source,
                    content_hash=document.content_hash,
                    created_by=document.created_by,
                    created_at=document.created_at,
                )
                for (project, region, current_set_id, _), document in self._documents.items()
                if (project, region, current_set_id) == (project_id, region_code, set_id)
            ]
        return tuple(sorted(values, key=lambda value: int(value.version), reverse=True))

    def recalibrate_set(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        expected_etag: str,
        document: CalibrationDocument,
        content_hash: str,
        actor_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        audit: CalibrationAuditEvent,
    ) -> CalibrationSetRecord:
        receipt_key = (project_id, region_code, set_id, "RECALIBRATE", idempotency_key)
        with self._lock:
            receipt = self._command_receipts.get(receipt_key)
            if receipt is not None:
                if receipt.request_fingerprint != request_fingerprint:
                    raise ValueError("idempotency key was reused")
                replay = self._sets.get((project_id, region_code, set_id))
                if replay is None:
                    raise RuntimeError("calibration recalibration receipt has no set")
                return replay
            item = self._sets.get((project_id, region_code, set_id))
            if item is None:
                raise KeyError("calibration set")
            if item.etag != expected_etag:
                raise ValueError("calibration set etag does not match")
            if item.content_hash == content_hash:
                raise ValueError("recalibration has no content change")
            next_version = str(int(item.version) + 1)
            next_document = CalibrationVersionDocument(
                set_id=set_id,
                version=next_version,
                source="RECALIBRATION",
                content_hash=content_hash,
                document=document,
                created_by=actor_id,
                created_at=audit.occurred_at,
            )
            revision = _etag_revision(item.etag) + 1
            updated = item.model_copy(
                update={
                    "version": next_version,
                    "snapshot_status": "DRAFT",
                    "availability": None,
                    "content_hash": content_hash,
                    "validation_context_hash": None,
                    "validation": None,
                    "etag": f'"calibration:{set_id}:{revision}"',
                }
            )
            self._sets[(project_id, region_code, set_id)] = updated
            self._documents[(project_id, region_code, set_id, next_version)] = next_document
            self._command_receipts[receipt_key] = CalibrationCommandReceipt(
                request_fingerprint=request_fingerprint, version=next_version, report_id=None
            )
            self.audit_events.append(audit)
            return updated

    def list_dataset_associations(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
    ) -> tuple[CalibrationDatasetAssociation, ...]:
        with self._lock:
            values = [
                association
                for (
                    project,
                    region,
                    association_set_id,
                    association_version,
                    _,
                    _,
                ), association in self._dataset_associations.items()
                if (project, region, association_set_id, association_version)
                == (project_id, region_code, set_id, version)
            ]
        return tuple(
            sorted(
                values,
                key=lambda association: (
                    association.associated_at,
                    association.dataset_id,
                    association.dataset_version_id,
                ),
                reverse=True,
            )
        )

    def associate_dataset(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        expected_etag: str,
        dataset_id: str,
        dataset_version_id: str,
        actor_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        audit: CalibrationAuditEvent,
    ) -> CalibrationDatasetAssociation:
        resource_id = f"{set_id}:{version}:{dataset_id}:{dataset_version_id}"
        receipt_key = (
            project_id,
            region_code,
            resource_id,
            "ASSOCIATE_DATASET",
            idempotency_key,
        )
        association_key = (
            project_id,
            region_code,
            set_id,
            version,
            dataset_id,
            dataset_version_id,
        )
        with self._lock:
            receipt = self._command_receipts.get(receipt_key)
            if receipt is not None:
                if receipt.request_fingerprint != request_fingerprint:
                    raise ValueError("idempotency key was reused")
                replay = self._dataset_associations.get(association_key)
                if replay is None:
                    raise RuntimeError("calibration dataset association receipt is incomplete")
                return replay
            item = self._sets.get((project_id, region_code, set_id))
            if item is None:
                raise KeyError("calibration set")
            if item.etag != expected_etag:
                raise ValueError("calibration set etag does not match")
            if (
                item.version != version
                or item.snapshot_status != "READY"
                or item.validation is None
                or item.validation.status != "PASSED"
            ):
                raise ValueError("calibration version is not ready")
            if (
                project_id,
                region_code,
                dataset_id,
                dataset_version_id,
            ) not in self._dataset_versions:
                raise KeyError("dataset association")
            existing = self._dataset_associations.get(association_key)
            if existing is not None:
                self._command_receipts[receipt_key] = CalibrationCommandReceipt(
                    request_fingerprint=request_fingerprint,
                    version=version,
                    report_id=None,
                )
                return existing
            association = CalibrationDatasetAssociation(
                set_id=set_id,
                calibration_version=version,
                dataset_id=dataset_id,
                dataset_version_id=dataset_version_id,
                associated_by=actor_id,
                associated_at=audit.occurred_at,
            )
            self._dataset_associations[association_key] = association
            self._command_receipts[receipt_key] = CalibrationCommandReceipt(
                request_fingerprint=request_fingerprint,
                version=version,
                report_id=None,
            )
            self.audit_events.append(audit)
            return association

    def validate_set(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        expected_etag: str,
        report: CalibrationValidationReport,
        idempotency_key: str,
        request_fingerprint: str,
        audit: CalibrationAuditEvent,
    ) -> tuple[CalibrationSetRecord, CalibrationValidationReport]:
        receipt_key = (project_id, region_code, set_id, "VALIDATE", idempotency_key)
        with self._lock:
            receipt = self._command_receipts.get(receipt_key)
            if receipt is not None:
                if receipt.request_fingerprint != request_fingerprint:
                    raise ValueError("idempotency key was reused")
                item = self._sets.get((project_id, region_code, set_id))
                existing_report = self._reports.get(
                    (project_id, region_code, receipt.report_id or "")
                )
                if item is None or existing_report is None:
                    raise RuntimeError("calibration validation receipt is incomplete")
                return item, existing_report
            item = self._sets.get((project_id, region_code, set_id))
            document = self._documents.get((project_id, region_code, set_id, version))
            if item is None or document is None:
                raise KeyError("calibration set")
            if item.etag != expected_etag or item.version != version:
                raise ValueError("calibration set etag does not match")
            if item.snapshot_status != "DRAFT" or document.content_hash != report.content_hash:
                raise ValueError("calibration version is not a valid draft")
            duplicate = next(
                (
                    current
                    for current in self._reports.values()
                    if current.set_id == set_id
                    and current.version == version
                    and current.content_hash == report.content_hash
                    and current.validation_context_hash == report.validation_context_hash
                ),
                None,
            )
            effective_report = duplicate or report
            revision = _etag_revision(item.etag) + 1
            updated = item.model_copy(
                update={
                    "validation_context_hash": effective_report.validation_context_hash,
                    "validation": CalibrationValidation(
                        status=effective_report.status,
                        content_hash=effective_report.content_hash,
                        validation_context_hash=effective_report.validation_context_hash,
                        report_id=effective_report.id,
                    ),
                    "etag": f'"calibration:{set_id}:{revision}"',
                }
            )
            self._sets[(project_id, region_code, set_id)] = updated
            self._reports[(project_id, region_code, effective_report.id)] = effective_report
            self._command_receipts[receipt_key] = CalibrationCommandReceipt(
                request_fingerprint=request_fingerprint,
                version=version,
                report_id=effective_report.id,
            )
            self.audit_events.append(audit)
            return updated, effective_report

    def get_validation_report(
        self,
        *,
        project_id: str,
        region_code: str,
        report_id: str,
    ) -> CalibrationValidationReport | None:
        with self._lock:
            return self._reports.get((project_id, region_code, report_id))


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


def _json_list(value: object) -> list[object]:
    decoded = json.loads(value) if isinstance(value, str) else value
    return decoded if isinstance(decoded, list) else []


def _etag_revision(value: str) -> int:
    try:
        return int(value.rsplit(":", 1)[1].rstrip('"'))
    except (IndexError, ValueError):
        return 1


def _set(row: Mapping[str, object]) -> CalibrationSetRecord:
    validation = (
        None
        if row["validation_status"] is None
        else CalibrationValidation(
            status=str(row["validation_status"]),
            content_hash=str(row["validation_content_hash"]),
            validation_context_hash=str(row["validation_context_hash"]),
            report_id=str(row["validation_report_id"]),
        )
    )
    return CalibrationSetRecord(
        id=str(row["set_id"]),
        robot_instance_id=str(row["robot_instance_id"]),
        component_id=None if row["component_id"] is None else str(row["component_id"]),
        version=str(row["version"]),
        snapshot_status=str(row["snapshot_status"]),
        availability=None if row["availability"] is None else str(row["availability"]),
        content_hash=None if row["content_hash"] is None else str(row["content_hash"]),
        validation_context_hash=None
        if row["validation_context_hash"] is None
        else str(row["validation_context_hash"]),
        validation=validation,
        etag=str(row["etag"]),
        allowed_actions=tuple(str(item) for item in _json_list(row["allowed_actions"])),
        blocked_reasons=tuple(
            CalibrationBlockedReason.model_validate(item)
            for item in _json_list(row["blocked_reasons"])
        ),
    )


def _publish_preflight(row: Mapping[str, object]) -> CalibrationPublishPreflightRecord:
    return CalibrationPublishPreflightRecord(
        project_id=str(row["project_id"]),
        region_code=str(row["region_code"]),
        preflight_id=str(row["preflight_id"]),
        set_id=str(row["set_id"]),
        version=str(row["version"]),
        idempotency_key=str(row["idempotency_key"]),
        request_fingerprint=str(row["request_fingerprint"]),
        expected_etag=str(row["expected_etag"]),
        expected_hash=str(row["expected_hash"]),
        validation_context_hash=str(row["validation_context_hash"]),
        validation_report_id=str(row["validation_report_id"]),
        allowed=bool(row["allowed"]),
        blockers=tuple(
            CalibrationBlockedReason.model_validate(item) for item in _json_list(row["blockers"])
        ),
        status=str(row["status"]),
        token_hash=str(row["token_hash"]),
        expires_at=cast(datetime, row["expires_at"]),
        created_at=cast(datetime, row["created_at"]),
        consumed_at=cast(datetime | None, row["consumed_at"]),
    )


def _document(row: Mapping[str, object]) -> CalibrationVersionDocument:
    raw_document = row["document"]
    decoded = json.loads(raw_document) if isinstance(raw_document, str) else raw_document
    return CalibrationVersionDocument(
        set_id=str(row["set_id"]),
        version=str(row["version"]),
        source=str(row["source"]),
        content_hash=str(row["content_hash"]),
        document=decoded,
        created_by=str(row["created_by"]),
        created_at=cast(datetime, row["created_at"]),
    )


def _dataset_association(row: Mapping[str, object]) -> CalibrationDatasetAssociation:
    return CalibrationDatasetAssociation(
        set_id=str(row["set_id"]),
        calibration_version=str(row["calibration_version"]),
        dataset_id=str(row["dataset_id"]),
        dataset_version_id=str(row["dataset_version_id"]),
        associated_by=str(row["associated_by"]),
        associated_at=cast(datetime, row["associated_at"]),
    )


def _report(row: Mapping[str, object]) -> CalibrationValidationReport:
    raw_findings = row["findings"]
    findings = json.loads(raw_findings) if isinstance(raw_findings, str) else raw_findings
    return CalibrationValidationReport(
        id=str(row["report_id"]),
        set_id=str(row["set_id"]),
        version=str(row["version"]),
        content_hash=str(row["content_hash"]),
        validation_context_hash=str(row["validation_context_hash"]),
        status=str(row["status"]),
        findings=findings,
        checked_by=str(row["checked_by"]),
        checked_at=cast(datetime, row["checked_at"]),
    )


def _command_receipt(row: Mapping[str, object]) -> CalibrationCommandReceipt:
    return CalibrationCommandReceipt(
        request_fingerprint=str(row["request_fingerprint"]),
        version=str(row["version"]),
        report_id=None if row["report_id"] is None else str(row["report_id"]),
    )


def _append_audit_cursor(cursor: DbApiCursor, event: CalibrationAuditEvent) -> None:
    cursor.execute(
        """
        INSERT INTO core.audit_events (
            audit_id, project_id, region_code, actor_id, action, resource_type,
            resource_id, request_id, before_hash, after_hash, details, occurred_at
        ) VALUES (%s, %s, %s, %s, %s, 'CALIBRATION_SET', %s, %s, NULL, NULL, %s::jsonb, %s)
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


class PostgresCalibrationRepository:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def list_sets(
        self,
        *,
        project_id: str,
        region_code: str,
        query: str | None,
        robot_id: str | None,
        component_id: str | None,
    ) -> tuple[CalibrationSetRecord, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT set_id, robot_instance_id, component_id, version, snapshot_status,
                       availability, content_hash, validation_context_hash, validation_status,
                       validation_content_hash,
                       validation_report_id, etag, allowed_actions, blocked_reasons
                  FROM calibrations.calibration_sets
                 WHERE project_id = %s AND region_code = %s
                   AND (%s::text IS NULL OR robot_instance_id = %s)
                   AND (%s::text IS NULL OR component_id = %s)
                   AND (%s::text IS NULL OR set_id ILIKE '%%' || %s || '%%')
                 ORDER BY robot_instance_id, set_id
            """,
                (
                    project_id,
                    region_code,
                    robot_id,
                    robot_id,
                    component_id,
                    component_id,
                    query,
                    query,
                ),
            )
            return tuple(_set(_row(cursor, raw)) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def get_set(
        self, *, project_id: str, region_code: str, set_id: str
    ) -> CalibrationSetRecord | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT set_id, robot_instance_id, component_id, version, snapshot_status,
                       availability, content_hash, validation_context_hash, validation_status,
                       validation_content_hash,
                       validation_report_id, etag, allowed_actions, blocked_reasons
                  FROM calibrations.calibration_sets
                 WHERE project_id = %s AND region_code = %s AND set_id = %s
            """,
                (project_id, region_code, set_id),
            )
            raw = cursor.fetchone()
            return None if raw is None else _set(_row(cursor, raw))
        finally:
            cursor.close()
            connection.close()

    def find_publish_preflight_by_idempotency(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        idempotency_key: str,
    ) -> CalibrationPublishPreflightRecord | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT project_id, region_code, preflight_id, set_id, version, idempotency_key,
                       request_fingerprint, expected_etag, expected_hash,
                       validation_context_hash, validation_report_id, allowed, blockers, status,
                       token_hash, expires_at, created_at, consumed_at
                  FROM calibrations.calibration_publish_preflights
                 WHERE project_id = %s AND region_code = %s AND set_id = %s
                   AND version = %s::bigint AND idempotency_key = %s
                """,
                (project_id, region_code, set_id, version, idempotency_key),
            )
            raw = cursor.fetchone()
            return None if raw is None else _publish_preflight(_row(cursor, raw))
        finally:
            cursor.close()
            connection.close()

    def create_publish_preflight(
        self, *, record: CalibrationPublishPreflightRecord, audit: CalibrationAuditEvent
    ) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO calibrations.calibration_publish_preflights (
                    project_id, region_code, preflight_id, set_id, version, idempotency_key,
                    request_fingerprint, expected_etag, expected_hash,
                    validation_context_hash, validation_report_id, allowed, blockers, status,
                    token_hash, expires_at, created_at, consumed_at
                ) VALUES (
                    %s, %s, %s::uuid, %s, %s::bigint, %s, %s, %s, %s, %s, %s,
                    %s, %s::jsonb, %s, %s, %s, %s, %s
                )
                """,
                (
                    record.project_id,
                    record.region_code,
                    record.preflight_id,
                    record.set_id,
                    record.version,
                    record.idempotency_key,
                    record.request_fingerprint,
                    record.expected_etag,
                    record.expected_hash,
                    record.validation_context_hash,
                    record.validation_report_id,
                    record.allowed,
                    json.dumps([blocker.model_dump(mode="json") for blocker in record.blockers]),
                    record.status,
                    record.token_hash,
                    record.expires_at,
                    record.created_at,
                    record.consumed_at,
                ),
            )
            _append_audit_cursor(cursor, audit)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def publish_set(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        preflight_id: str,
        token_hash: str,
        idempotency_key: str,
        expected_etag: str,
        occurred_at: datetime,
        audit: CalibrationAuditEvent,
    ) -> CalibrationSetRecord:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT project_id, region_code, preflight_id, set_id, version, idempotency_key,
                       request_fingerprint, expected_etag, expected_hash,
                       validation_context_hash, validation_report_id, allowed, blockers, status,
                       token_hash, expires_at, created_at, consumed_at
                  FROM calibrations.calibration_publish_preflights
                 WHERE project_id = %s AND region_code = %s AND preflight_id = %s::uuid
                 FOR UPDATE
                """,
                (project_id, region_code, preflight_id),
            )
            raw_preflight = cursor.fetchone()
            if raw_preflight is None:
                raise KeyError("preflight")
            preflight = _publish_preflight(_row(cursor, raw_preflight))
            if preflight.set_id != set_id or preflight.version != version:
                raise PermissionError("publish preflight does not match calibration version")
            if preflight.token_hash != token_hash:
                raise PermissionError("publish preflight token does not match")
            if preflight.idempotency_key != idempotency_key:
                raise ValueError("publish idempotency key does not match preflight")
            if preflight.expected_etag != expected_etag:
                raise ValueError("calibration set etag does not match preflight")
            cursor.execute(
                """
                SELECT set_id, robot_instance_id, component_id, version, snapshot_status,
                       availability, content_hash, validation_context_hash, validation_status,
                       validation_content_hash, validation_report_id, etag, allowed_actions,
                       blocked_reasons
                  FROM calibrations.calibration_sets
                 WHERE project_id = %s AND region_code = %s AND set_id = %s
                 FOR UPDATE
                """,
                (project_id, region_code, set_id),
            )
            raw_set = cursor.fetchone()
            if raw_set is None:
                raise KeyError("calibration set")
            item = _set(_row(cursor, raw_set))
            if preflight.status == "CONSUMED":
                connection.commit()
                return item
            if preflight.status != "ISSUED" or preflight.expires_at <= occurred_at:
                raise TimeoutError("publish preflight has expired")
            if (
                preflight.expected_etag != expected_etag
                or item.etag != expected_etag
                or not preflight.allowed
                or item.version != version
                or item.snapshot_status != "DRAFT"
                or item.content_hash != preflight.expected_hash
                or item.validation_context_hash != preflight.validation_context_hash
                or item.validation is None
                or item.validation.status != "PASSED"
                or item.validation.report_id != preflight.validation_report_id
                or item.validation.content_hash != preflight.expected_hash
                or item.validation.validation_context_hash != preflight.validation_context_hash
            ):
                raise ValueError("publish preflight is stale")
            cursor.execute(
                """
                UPDATE calibrations.calibration_sets
                   SET snapshot_status = 'READY', availability = 'ACTIVE',
                       revision = revision + 1,
                       etag = '"calibration:' || set_id || ':' || (revision + 1)::text || '"',
                       updated_at = %s
                 WHERE project_id = %s AND region_code = %s AND set_id = %s
                """,
                (occurred_at, project_id, region_code, set_id),
            )
            cursor.execute(
                """
                UPDATE calibrations.calibration_publish_preflights
                   SET status = 'CONSUMED', consumed_at = %s
                 WHERE project_id = %s AND region_code = %s AND preflight_id = %s::uuid
                """,
                (occurred_at, project_id, region_code, preflight_id),
            )
            _append_audit_cursor(cursor, audit)
            cursor.execute(
                """
                SELECT set_id, robot_instance_id, component_id, version, snapshot_status,
                       availability, content_hash, validation_context_hash, validation_status,
                       validation_content_hash, validation_report_id, etag, allowed_actions,
                       blocked_reasons
                  FROM calibrations.calibration_sets
                 WHERE project_id = %s AND region_code = %s AND set_id = %s
                """,
                (project_id, region_code, set_id),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise KeyError("calibration set")
            connection.commit()
            return _set(_row(cursor, updated))
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def create_set(
        self,
        *,
        project_id: str,
        region_code: str,
        item: CalibrationSetRecord,
        document: CalibrationVersionDocument,
        idempotency_key: str,
        request_fingerprint: str,
        audit: CalibrationAuditEvent,
    ) -> CalibrationSetRecord:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            # A receipt that does not exist cannot be row-locked.  This scoped
            # transaction advisory lock closes the concurrent-create gap while
            # retaining regular row locks for all existing resources.
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"calibration-create:{project_id}:{region_code}:{item.id}",),
            )
            cursor.execute(
                """
                SELECT request_fingerprint, version, report_id
                  FROM calibrations.calibration_command_receipts
                 WHERE project_id = %s AND region_code = %s AND resource_id = %s
                   AND operation = 'CREATE' AND idempotency_key = %s
                 FOR UPDATE
                """,
                (project_id, region_code, item.id, idempotency_key),
            )
            raw_receipt = cursor.fetchone()
            if raw_receipt is not None:
                receipt = _command_receipt(_row(cursor, raw_receipt))
                if receipt.request_fingerprint != request_fingerprint:
                    raise ValueError("idempotency key was reused")
                cursor.execute(
                    """
                    SELECT set_id, robot_instance_id, component_id, version, snapshot_status,
                           availability, content_hash, validation_context_hash, validation_status,
                           validation_content_hash, validation_report_id, etag, allowed_actions,
                           blocked_reasons
                      FROM calibrations.calibration_sets
                     WHERE project_id = %s AND region_code = %s AND set_id = %s
                    """,
                    (project_id, region_code, item.id),
                )
                replay = cursor.fetchone()
                if replay is None:
                    raise RuntimeError("calibration create receipt has no set")
                connection.commit()
                return _set(_row(cursor, replay))
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM robotics.robot_instances
                     WHERE project_id = %s AND region_code = %s AND robot_id = %s
                ) AS present
                """,
                (project_id, region_code, item.robot_instance_id),
            )
            robot = cursor.fetchone()
            if robot is None or not bool(_row(cursor, robot)["present"]):
                raise KeyError("robot association")
            if item.component_id is not None:
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM robotics.robot_components
                         WHERE project_id = %s AND region_code = %s AND component_id = %s
                           AND robot_id = %s
                    ) AS present
                    """,
                    (project_id, region_code, item.component_id, item.robot_instance_id),
                )
                component = cursor.fetchone()
                if component is None or not bool(_row(cursor, component)["present"]):
                    raise KeyError("component association")
            cursor.execute(
                """
                INSERT INTO calibrations.calibration_sets (
                    project_id, region_code, set_id, robot_instance_id, component_id, version,
                    snapshot_status, availability, content_hash, validation_context_hash,
                    validation_status, validation_content_hash, validation_report_id, etag,
                    allowed_actions, blocked_reasons, revision, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s::bigint, 'DRAFT', NULL, %s, NULL,
                    NULL, NULL, NULL, %s, %s::jsonb, '[]'::jsonb, 1, %s, %s
                )
                """,
                (
                    project_id,
                    region_code,
                    item.id,
                    item.robot_instance_id,
                    item.component_id,
                    item.version,
                    item.content_hash,
                    item.etag,
                    json.dumps(item.allowed_actions),
                    audit.occurred_at,
                    audit.occurred_at,
                ),
            )
            cursor.execute(
                """
                INSERT INTO calibrations.calibration_version_documents (
                    project_id, region_code, set_id, version, source, document, content_hash,
                    created_by, created_at
                ) VALUES (%s, %s, %s, %s::bigint, %s, %s::jsonb, %s, %s, %s)
                """,
                (
                    project_id,
                    region_code,
                    item.id,
                    document.version,
                    document.source,
                    json.dumps(document.document.model_dump(mode="json")),
                    document.content_hash,
                    document.created_by,
                    document.created_at,
                ),
            )
            cursor.execute(
                """
                INSERT INTO calibrations.calibration_command_receipts (
                    project_id, region_code, resource_id, operation, idempotency_key,
                    request_fingerprint, version, report_id, created_at
                ) VALUES (%s, %s, %s, 'CREATE', %s, %s, %s::bigint, NULL, %s)
                """,
                (
                    project_id,
                    region_code,
                    item.id,
                    idempotency_key,
                    request_fingerprint,
                    item.version,
                    audit.occurred_at,
                ),
            )
            _append_audit_cursor(cursor, audit)
            connection.commit()
            return item
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def get_version_document(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
    ) -> CalibrationVersionDocument | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT set_id, version, source, document, content_hash, created_by, created_at
                  FROM calibrations.calibration_version_documents
                 WHERE project_id = %s AND region_code = %s AND set_id = %s
                   AND version = %s::bigint
                """,
                (project_id, region_code, set_id, version),
            )
            raw = cursor.fetchone()
            return None if raw is None else _document(_row(cursor, raw))
        finally:
            cursor.close()
            connection.close()

    def list_version_summaries(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
    ) -> tuple[CalibrationVersionSummary, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT version, source, content_hash, created_by, created_at
                  FROM calibrations.calibration_version_documents
                 WHERE project_id = %s AND region_code = %s AND set_id = %s
                 ORDER BY version DESC
                """,
                (project_id, region_code, set_id),
            )
            return tuple(
                CalibrationVersionSummary(
                    version=str(row["version"]),
                    source=str(row["source"]),
                    content_hash=str(row["content_hash"]),
                    created_by=str(row["created_by"]),
                    created_at=cast(datetime, row["created_at"]),
                )
                for raw in cursor.fetchall()
                for row in (_row(cursor, raw),)
            )
        finally:
            cursor.close()
            connection.close()

    def recalibrate_set(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        expected_etag: str,
        document: CalibrationDocument,
        content_hash: str,
        actor_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        audit: CalibrationAuditEvent,
    ) -> CalibrationSetRecord:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT request_fingerprint, version, report_id
                  FROM calibrations.calibration_command_receipts
                 WHERE project_id = %s AND region_code = %s AND resource_id = %s
                   AND operation = 'RECALIBRATE' AND idempotency_key = %s
                 FOR UPDATE
                """,
                (project_id, region_code, set_id, idempotency_key),
            )
            raw_receipt = cursor.fetchone()
            if raw_receipt is not None:
                receipt = _command_receipt(_row(cursor, raw_receipt))
                if receipt.request_fingerprint != request_fingerprint:
                    raise ValueError("idempotency key was reused")
                cursor.execute(
                    """
                    SELECT set_id, robot_instance_id, component_id, version, snapshot_status,
                           availability, content_hash, validation_context_hash, validation_status,
                           validation_content_hash, validation_report_id, etag, allowed_actions,
                           blocked_reasons
                      FROM calibrations.calibration_sets
                     WHERE project_id = %s AND region_code = %s AND set_id = %s
                    """,
                    (project_id, region_code, set_id),
                )
                replay = cursor.fetchone()
                if replay is None:
                    raise RuntimeError("calibration recalibration receipt has no set")
                connection.commit()
                return _set(_row(cursor, replay))
            cursor.execute(
                """
                SELECT set_id, robot_instance_id, component_id, version, snapshot_status,
                       availability, content_hash, validation_context_hash, validation_status,
                       validation_content_hash, validation_report_id, etag, allowed_actions,
                       blocked_reasons
                  FROM calibrations.calibration_sets
                 WHERE project_id = %s AND region_code = %s AND set_id = %s
                 FOR UPDATE
                """,
                (project_id, region_code, set_id),
            )
            raw_set = cursor.fetchone()
            if raw_set is None:
                raise KeyError("calibration set")
            item = _set(_row(cursor, raw_set))
            if item.etag != expected_etag:
                raise ValueError("calibration set etag does not match")
            if item.content_hash == content_hash:
                raise ValueError("recalibration has no content change")
            next_version = str(int(item.version) + 1)
            cursor.execute(
                """
                INSERT INTO calibrations.calibration_version_documents (
                    project_id, region_code, set_id, version, source, document, content_hash,
                    created_by, created_at
                ) VALUES (%s, %s, %s, %s::bigint, 'RECALIBRATION', %s::jsonb, %s, %s, %s)
                """,
                (
                    project_id,
                    region_code,
                    set_id,
                    next_version,
                    json.dumps(document.model_dump(mode="json")),
                    content_hash,
                    actor_id,
                    audit.occurred_at,
                ),
            )
            cursor.execute(
                """
                UPDATE calibrations.calibration_sets
                   SET version = %s::bigint, snapshot_status = 'DRAFT', availability = NULL,
                       content_hash = %s, validation_context_hash = NULL,
                       validation_status = NULL, validation_content_hash = NULL,
                       validation_report_id = NULL, revision = revision + 1,
                       etag = '"calibration:' || set_id || ':' || (revision + 1)::text || '"',
                       updated_at = %s
                 WHERE project_id = %s AND region_code = %s AND set_id = %s
                """,
                (
                    next_version,
                    content_hash,
                    audit.occurred_at,
                    project_id,
                    region_code,
                    set_id,
                ),
            )
            cursor.execute(
                """
                INSERT INTO calibrations.calibration_command_receipts (
                    project_id, region_code, resource_id, operation, idempotency_key,
                    request_fingerprint, version, report_id, created_at
                ) VALUES (%s, %s, %s, 'RECALIBRATE', %s, %s, %s::bigint, NULL, %s)
                """,
                (
                    project_id,
                    region_code,
                    set_id,
                    idempotency_key,
                    request_fingerprint,
                    next_version,
                    audit.occurred_at,
                ),
            )
            _append_audit_cursor(cursor, audit)
            cursor.execute(
                """
                SELECT set_id, robot_instance_id, component_id, version, snapshot_status,
                       availability, content_hash, validation_context_hash, validation_status,
                       validation_content_hash, validation_report_id, etag, allowed_actions,
                       blocked_reasons
                  FROM calibrations.calibration_sets
                 WHERE project_id = %s AND region_code = %s AND set_id = %s
                """,
                (project_id, region_code, set_id),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise KeyError("calibration set")
            connection.commit()
            return _set(_row(cursor, updated))
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def list_dataset_associations(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
    ) -> tuple[CalibrationDatasetAssociation, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT set_id, calibration_version, dataset_id, dataset_version_id,
                       associated_by, associated_at
                  FROM calibrations.calibration_dataset_version_associations
                 WHERE project_id = %s AND region_code = %s AND set_id = %s
                   AND calibration_version = %s::bigint
                 ORDER BY associated_at DESC, dataset_id DESC, dataset_version_id DESC
                """,
                (project_id, region_code, set_id, version),
            )
            return tuple(_dataset_association(_row(cursor, raw)) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def associate_dataset(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        expected_etag: str,
        dataset_id: str,
        dataset_version_id: str,
        actor_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        audit: CalibrationAuditEvent,
    ) -> CalibrationDatasetAssociation:
        resource_id = f"{set_id}:{version}:{dataset_id}:{dataset_version_id}"
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"calibration-dataset-association:{project_id}:{region_code}:{resource_id}",),
            )
            cursor.execute(
                """
                SELECT request_fingerprint
                  FROM calibrations.calibration_command_receipts
                 WHERE project_id = %s AND region_code = %s AND resource_id = %s
                   AND operation = 'ASSOCIATE_DATASET' AND idempotency_key = %s
                 FOR UPDATE
                """,
                (project_id, region_code, resource_id, idempotency_key),
            )
            raw_receipt = cursor.fetchone()
            if raw_receipt is not None:
                if str(_row(cursor, raw_receipt)["request_fingerprint"]) != request_fingerprint:
                    raise ValueError("idempotency key was reused")
                cursor.execute(
                    """
                    SELECT set_id, calibration_version, dataset_id, dataset_version_id,
                           associated_by, associated_at
                      FROM calibrations.calibration_dataset_version_associations
                     WHERE project_id = %s AND region_code = %s AND set_id = %s
                       AND calibration_version = %s::bigint AND dataset_id = %s
                       AND dataset_version_id = %s
                    """,
                    (project_id, region_code, set_id, version, dataset_id, dataset_version_id),
                )
                raw_association = cursor.fetchone()
                if raw_association is None:
                    raise RuntimeError("calibration dataset association receipt is incomplete")
                connection.commit()
                return _dataset_association(_row(cursor, raw_association))

            cursor.execute(
                """
                SELECT set_id, robot_instance_id, component_id, version, snapshot_status,
                       availability, content_hash, validation_context_hash, validation_status,
                       validation_content_hash, validation_report_id, etag, allowed_actions,
                       blocked_reasons
                  FROM calibrations.calibration_sets
                 WHERE project_id = %s AND region_code = %s AND set_id = %s
                 FOR UPDATE
                """,
                (project_id, region_code, set_id),
            )
            raw_set = cursor.fetchone()
            if raw_set is None:
                raise KeyError("calibration set")
            item = _set(_row(cursor, raw_set))
            if item.etag != expected_etag:
                raise ValueError("calibration set etag does not match")
            if (
                item.version != version
                or item.snapshot_status != "READY"
                or item.validation is None
                or item.validation.status != "PASSED"
            ):
                raise ValueError("calibration version is not ready")

            cursor.execute(
                """
                SELECT organization_id
                  FROM dataset_registry.dataset_versions
                 WHERE project_id = %s AND region_code = %s AND dataset_id = %s
                   AND version_id = %s AND version_status = 'READY'
                 ORDER BY organization_id
                 LIMIT 2
                """,
                (project_id, region_code, dataset_id, dataset_version_id),
            )
            dataset_rows = [_row(cursor, raw) for raw in cursor.fetchall()]
            if len(dataset_rows) != 1:
                raise KeyError("dataset association")
            organization_id = str(dataset_rows[0]["organization_id"])

            cursor.execute(
                """
                SELECT set_id, calibration_version, dataset_id, dataset_version_id,
                       associated_by, associated_at
                  FROM calibrations.calibration_dataset_version_associations
                 WHERE project_id = %s AND region_code = %s AND set_id = %s
                   AND calibration_version = %s::bigint AND dataset_id = %s
                   AND dataset_version_id = %s
                """,
                (project_id, region_code, set_id, version, dataset_id, dataset_version_id),
            )
            raw_existing = cursor.fetchone()
            if raw_existing is not None:
                cursor.execute(
                    """
                    INSERT INTO calibrations.calibration_command_receipts (
                        project_id, region_code, resource_id, operation, idempotency_key,
                        request_fingerprint, version, report_id, created_at
                    ) VALUES (%s, %s, %s, 'ASSOCIATE_DATASET', %s, %s, %s::bigint, NULL, %s)
                    """,
                    (
                        project_id,
                        region_code,
                        resource_id,
                        idempotency_key,
                        request_fingerprint,
                        version,
                        audit.occurred_at,
                    ),
                )
                connection.commit()
                return _dataset_association(_row(cursor, raw_existing))

            cursor.execute(
                """
                INSERT INTO calibrations.calibration_dataset_version_associations (
                    project_id, region_code, set_id, calibration_version, organization_id,
                    dataset_id, dataset_version_id, associated_by, associated_at
                ) VALUES (%s, %s, %s, %s::bigint, %s, %s, %s, %s, %s)
                """,
                (
                    project_id,
                    region_code,
                    set_id,
                    version,
                    organization_id,
                    dataset_id,
                    dataset_version_id,
                    actor_id,
                    audit.occurred_at,
                ),
            )
            cursor.execute(
                """
                INSERT INTO calibrations.calibration_command_receipts (
                    project_id, region_code, resource_id, operation, idempotency_key,
                    request_fingerprint, version, report_id, created_at
                ) VALUES (%s, %s, %s, 'ASSOCIATE_DATASET', %s, %s, %s::bigint, NULL, %s)
                """,
                (
                    project_id,
                    region_code,
                    resource_id,
                    idempotency_key,
                    request_fingerprint,
                    version,
                    audit.occurred_at,
                ),
            )
            _append_audit_cursor(cursor, audit)
            connection.commit()
            return CalibrationDatasetAssociation(
                set_id=set_id,
                calibration_version=version,
                dataset_id=dataset_id,
                dataset_version_id=dataset_version_id,
                associated_by=actor_id,
                associated_at=audit.occurred_at,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def validate_set(
        self,
        *,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        expected_etag: str,
        report: CalibrationValidationReport,
        idempotency_key: str,
        request_fingerprint: str,
        audit: CalibrationAuditEvent,
    ) -> tuple[CalibrationSetRecord, CalibrationValidationReport]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT request_fingerprint, version, report_id
                  FROM calibrations.calibration_command_receipts
                 WHERE project_id = %s AND region_code = %s AND resource_id = %s
                   AND operation = 'VALIDATE' AND idempotency_key = %s
                 FOR UPDATE
                """,
                (project_id, region_code, set_id, idempotency_key),
            )
            raw_receipt = cursor.fetchone()
            if raw_receipt is not None:
                receipt = _command_receipt(_row(cursor, raw_receipt))
                if receipt.request_fingerprint != request_fingerprint:
                    raise ValueError("idempotency key was reused")
                cursor.execute(
                    """
                    SELECT set_id, robot_instance_id, component_id, version, snapshot_status,
                           availability, content_hash, validation_context_hash, validation_status,
                           validation_content_hash, validation_report_id, etag, allowed_actions,
                           blocked_reasons
                      FROM calibrations.calibration_sets
                     WHERE project_id = %s AND region_code = %s AND set_id = %s
                    """,
                    (project_id, region_code, set_id),
                )
                raw_set = cursor.fetchone()
                replayed_set = None if raw_set is None else _set(_row(cursor, raw_set))
                cursor.execute(
                    """
                    SELECT report_id, set_id, version, content_hash, validation_context_hash,
                           status, findings, checked_by, checked_at
                      FROM calibrations.calibration_validation_reports
                     WHERE project_id = %s AND region_code = %s AND report_id = %s
                    """,
                    (project_id, region_code, receipt.report_id),
                )
                raw_report = cursor.fetchone()
                if replayed_set is None or raw_report is None:
                    raise RuntimeError("calibration validation receipt is incomplete")
                replayed_report = _report(_row(cursor, raw_report))
                connection.commit()
                return replayed_set, replayed_report
            cursor.execute(
                """
                SELECT set_id, robot_instance_id, component_id, version, snapshot_status,
                       availability, content_hash, validation_context_hash, validation_status,
                       validation_content_hash, validation_report_id, etag, allowed_actions,
                       blocked_reasons
                  FROM calibrations.calibration_sets
                 WHERE project_id = %s AND region_code = %s AND set_id = %s
                 FOR UPDATE
                """,
                (project_id, region_code, set_id),
            )
            raw_set = cursor.fetchone()
            if raw_set is None:
                raise KeyError("calibration set")
            item = _set(_row(cursor, raw_set))
            cursor.execute(
                """
                SELECT set_id, version, source, document, content_hash, created_by, created_at
                  FROM calibrations.calibration_version_documents
                 WHERE project_id = %s AND region_code = %s AND set_id = %s
                   AND version = %s::bigint
                 FOR KEY SHARE
                """,
                (project_id, region_code, set_id, version),
            )
            raw_document = cursor.fetchone()
            if raw_document is None:
                raise KeyError("calibration document")
            document = _document(_row(cursor, raw_document))
            if (
                item.etag != expected_etag
                or item.version != version
                or item.snapshot_status != "DRAFT"
                or document.content_hash != report.content_hash
            ):
                raise ValueError("calibration set etag does not match")
            cursor.execute(
                """
                SELECT report_id, set_id, version, content_hash, validation_context_hash,
                       status, findings, checked_by, checked_at
                  FROM calibrations.calibration_validation_reports
                 WHERE project_id = %s AND region_code = %s AND set_id = %s
                   AND version = %s::bigint AND content_hash = %s
                   AND validation_context_hash = %s
                """,
                (
                    project_id,
                    region_code,
                    set_id,
                    version,
                    report.content_hash,
                    report.validation_context_hash,
                ),
            )
            raw_existing_report = cursor.fetchone()
            effective_report = report
            if raw_existing_report is None:
                cursor.execute(
                    """
                    INSERT INTO calibrations.calibration_validation_reports (
                        project_id, region_code, report_id, set_id, version, content_hash,
                        validation_context_hash, status, findings, checked_by, checked_at
                    ) VALUES (%s, %s, %s, %s, %s::bigint, %s, %s, %s, %s::jsonb, %s, %s)
                    """,
                    (
                        project_id,
                        region_code,
                        report.id,
                        set_id,
                        version,
                        report.content_hash,
                        report.validation_context_hash,
                        report.status,
                        json.dumps(
                            [finding.model_dump(mode="json") for finding in report.findings]
                        ),
                        report.checked_by,
                        report.checked_at,
                    ),
                )
            else:
                effective_report = _report(_row(cursor, raw_existing_report))
            if item.validation is None or item.validation.report_id != effective_report.id:
                cursor.execute(
                    """
                    UPDATE calibrations.calibration_sets
                       SET validation_context_hash = %s, validation_status = %s,
                           validation_content_hash = %s, validation_report_id = %s,
                           revision = revision + 1,
                           etag = '"calibration:' || set_id || ':' || (revision + 1)::text || '"',
                           updated_at = %s
                     WHERE project_id = %s AND region_code = %s AND set_id = %s
                    """,
                    (
                        effective_report.validation_context_hash,
                        effective_report.status,
                        effective_report.content_hash,
                        effective_report.id,
                        audit.occurred_at,
                        project_id,
                        region_code,
                        set_id,
                    ),
                )
            cursor.execute(
                """
                INSERT INTO calibrations.calibration_command_receipts (
                    project_id, region_code, resource_id, operation, idempotency_key,
                    request_fingerprint, version, report_id, created_at
                ) VALUES (%s, %s, %s, 'VALIDATE', %s, %s, %s::bigint, %s, %s)
                """,
                (
                    project_id,
                    region_code,
                    set_id,
                    idempotency_key,
                    request_fingerprint,
                    version,
                    effective_report.id,
                    audit.occurred_at,
                ),
            )
            _append_audit_cursor(cursor, audit)
            cursor.execute(
                """
                SELECT set_id, robot_instance_id, component_id, version, snapshot_status,
                       availability, content_hash, validation_context_hash, validation_status,
                       validation_content_hash, validation_report_id, etag, allowed_actions,
                       blocked_reasons
                  FROM calibrations.calibration_sets
                 WHERE project_id = %s AND region_code = %s AND set_id = %s
                """,
                (project_id, region_code, set_id),
            )
            updated = cursor.fetchone()
            if updated is None:
                raise KeyError("calibration set")
            connection.commit()
            return _set(_row(cursor, updated)), effective_report
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def get_validation_report(
        self,
        *,
        project_id: str,
        region_code: str,
        report_id: str,
    ) -> CalibrationValidationReport | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT report_id, set_id, version, content_hash, validation_context_hash,
                       status, findings, checked_by, checked_at
                  FROM calibrations.calibration_validation_reports
                 WHERE project_id = %s AND region_code = %s AND report_id = %s
                """,
                (project_id, region_code, report_id),
            )
            raw = cursor.fetchone()
            return None if raw is None else _report(_row(cursor, raw))
        finally:
            cursor.close()
            connection.close()

    def append_audit(self, event: CalibrationAuditEvent) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            _append_audit_cursor(cursor, event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

"""P16 calibration application service."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import replace as dataclass_replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from hc_data_platform.core.errors import ProblemException, problem
from hc_data_platform.core.pagination import CursorCodec, PageInfo
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.idempotency import request_fingerprint
from hc_data_platform.security.scope import ScopeGuard

from .models import (
    CalibrationBlockedReason,
    CalibrationDatasetAssociationEnvelope,
    CalibrationDatasetAssociationPage,
    CalibrationDatasetAssociationRequest,
    CalibrationDocument,
    CalibrationPublishPreflight,
    CalibrationPublishPreflightEnvelope,
    CalibrationPublishPreflightRequest,
    CalibrationPublishRequest,
    CalibrationScope,
    CalibrationSetEnvelope,
    CalibrationSetPage,
    CalibrationSetRecord,
    CalibrationValidationFinding,
    CalibrationValidationReport,
    CalibrationValidationReportEnvelope,
    CalibrationVersionDocument,
    CalibrationVersionDocumentEnvelope,
    CalibrationVersionPage,
    CreateCalibrationSetRequest,
    RecalibrateCalibrationSetRequest,
)
from .repository import (
    CalibrationAuditEvent,
    CalibrationPublishPreflightRecord,
    CalibrationRepository,
    InMemoryCalibrationRepository,
)

_PUBLISH_PREFLIGHT_TTL = timedelta(minutes=5)
_IN_MEMORY_PREFLIGHT_SECRET = "calibration-in-memory-preflight-secret"


class CalibrationService:
    def __init__(
        self,
        repository: CalibrationRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        cursor_secret: str = _IN_MEMORY_PREFLIGHT_SECRET,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._preflight_codec = CursorCodec(cursor_secret)

    @classmethod
    def in_memory(cls) -> CalibrationService:
        return cls(InMemoryCalibrationRepository())

    def _scope(self, *, auth: AuthContext, project_id: str, region_code: str) -> CalibrationScope:
        ScopeGuard.require(auth, project_id, region_code)
        auth.require_capability("calibration.read", project_id)
        return CalibrationScope(project_id=project_id, region_code=region_code)

    def _publish_scope(
        self, *, auth: AuthContext, project_id: str, region_code: str
    ) -> CalibrationScope:
        ScopeGuard.require(auth, project_id, region_code)
        auth.require_capability("calibration.publish", project_id)
        return CalibrationScope(project_id=project_id, region_code=region_code)

    def _dataset_scope(
        self, *, auth: AuthContext, project_id: str, region_code: str
    ) -> CalibrationScope:
        scope = self._scope(auth=auth, project_id=project_id, region_code=region_code)
        auth.require_capability("dataset.read", project_id)
        return scope

    def _dataset_publish_scope(
        self, *, auth: AuthContext, project_id: str, region_code: str
    ) -> CalibrationScope:
        scope = self._publish_scope(auth=auth, project_id=project_id, region_code=region_code)
        auth.require_capability("dataset.read", project_id)
        return scope

    def _audit(
        self,
        *,
        auth: AuthContext,
        scope: CalibrationScope,
        action: str,
        resource_id: str,
        request_id: str,
        outcome: str = "SUCCEEDED",
    ) -> None:
        self._repository.append_audit(
            CalibrationAuditEvent(
                project_id=scope.project_id,
                region_code=scope.region_code,
                actor_id=auth.subject_id,
                action=action,
                resource_id=resource_id,
                request_id=request_id,
                outcome=outcome,
                occurred_at=self._clock(),
            )
        )

    def _write_audit(
        self,
        *,
        auth: AuthContext,
        scope: CalibrationScope,
        action: str,
        resource_id: str,
        request_id: str,
        outcome: str = "SUCCEEDED",
    ) -> CalibrationAuditEvent:
        return CalibrationAuditEvent(
            project_id=scope.project_id,
            region_code=scope.region_code,
            actor_id=auth.subject_id,
            action=action,
            resource_id=resource_id,
            request_id=request_id,
            outcome=outcome,
            occurred_at=self._clock(),
        )

    def list_sets(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        query: str | None,
        robot_id: str | None,
        component_id: str | None,
        request_id: str,
    ) -> CalibrationSetPage:
        scope = self._scope(auth=auth, project_id=project_id, region_code=region_code)
        items = self._repository.list_sets(
            project_id=project_id,
            region_code=region_code,
            query=query,
            robot_id=robot_id,
            component_id=component_id,
        )
        self._audit(
            auth=auth,
            scope=scope,
            action="calibration_set.listed",
            resource_id=project_id,
            request_id=request_id,
        )
        return CalibrationSetPage(
            items=items,
            page_info=PageInfo(
                has_next_page=False, has_previous_page=False, start_cursor=None, end_cursor=None
            ),
            snapshot_at=self._clock(),
            scope=scope,
            request_id=request_id,
        )

    def get_set(
        self, *, auth: AuthContext, project_id: str, region_code: str, set_id: str, request_id: str
    ) -> CalibrationSetEnvelope:
        scope = self._scope(auth=auth, project_id=project_id, region_code=region_code)
        data = self._repository.get_set(
            project_id=project_id, region_code=region_code, set_id=set_id
        )
        if data is None:
            raise problem(
                status=404,
                code="CALIBRATION_SET_NOT_FOUND",
                title="Calibration set not found",
                detail="The requested calibration set does not exist in this scope.",
            )
        self._audit(
            auth=auth,
            scope=scope,
            action="calibration_set.read",
            resource_id=set_id,
            request_id=request_id,
        )
        return CalibrationSetEnvelope(data=data, scope=scope, request_id=request_id)

    def create_set(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        command: CreateCalibrationSetRequest,
        idempotency_key: str,
        request_id: str,
    ) -> CalibrationSetEnvelope:
        scope = self._publish_scope(auth=auth, project_id=project_id, region_code=region_code)
        now = self._clock()
        content_hash = self._content_hash(command.document)
        item = CalibrationSetRecord(
            id=command.set_id,
            robot_instance_id=command.robot_instance_id,
            component_id=command.component_id,
            version="1",
            snapshot_status="DRAFT",
            availability=None,
            content_hash=content_hash,
            validation_context_hash=None,
            validation=None,
            etag=f'"calibration:{command.set_id}:1"',
            allowed_actions=("VIEW", "EDIT", "VALIDATE", "PUBLISH"),
        )
        document = CalibrationVersionDocument(
            set_id=command.set_id,
            version="1",
            source=command.source,
            content_hash=content_hash,
            document=command.document,
            created_by=auth.subject_id,
            created_at=now,
        )
        fingerprint = request_fingerprint(
            {
                "operation": "calibration_set.create",
                "command": command.model_dump(mode="json"),
            }
        )
        try:
            data = self._repository.create_set(
                project_id=project_id,
                region_code=region_code,
                item=item,
                document=document,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                audit=self._write_audit(
                    auth=auth,
                    scope=scope,
                    action="calibration_set.created",
                    resource_id=command.set_id,
                    request_id=request_id,
                ),
            )
        except KeyError as exc:
            relation = "component" if "component" in str(exc) else "robot"
            raise problem(
                status=422,
                code="CALIBRATION_ASSOCIATION_INVALID",
                title="Calibration association is invalid",
                detail=f"The selected {relation} does not exist in this project and region.",
            ) from exc
        except ValueError as exc:
            if "idempotency" in str(exc):
                raise self._idempotency_conflict() from exc
            raise problem(
                status=409,
                code="CALIBRATION_SET_ALREADY_EXISTS",
                title="Calibration set already exists",
                detail="Choose a different immutable calibration set identifier.",
            ) from exc
        return CalibrationSetEnvelope(data=data, scope=scope, request_id=request_id)

    def get_version_document(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        request_id: str,
    ) -> CalibrationVersionDocumentEnvelope:
        scope = self._scope(auth=auth, project_id=project_id, region_code=region_code)
        data = self._repository.get_version_document(
            project_id=project_id, region_code=region_code, set_id=set_id, version=version
        )
        if data is None:
            raise problem(
                status=404,
                code="CALIBRATION_VERSION_DOCUMENT_NOT_FOUND",
                title="Calibration version document not found",
                detail="This scoped calibration version has no durable document.",
            )
        self._audit(
            auth=auth,
            scope=scope,
            action="calibration_version.document_read",
            resource_id=f"{set_id}:{version}",
            request_id=request_id,
        )
        return CalibrationVersionDocumentEnvelope(data=data, scope=scope, request_id=request_id)

    def list_versions(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        set_id: str,
        request_id: str,
    ) -> CalibrationVersionPage:
        scope = self._scope(auth=auth, project_id=project_id, region_code=region_code)
        if (
            self._repository.get_set(project_id=project_id, region_code=region_code, set_id=set_id)
            is None
        ):
            raise problem(
                status=404,
                code="CALIBRATION_SET_NOT_FOUND",
                title="Calibration set not found",
                detail="The requested calibration set does not exist in this scope.",
            )
        items = self._repository.list_version_summaries(
            project_id=project_id, region_code=region_code, set_id=set_id
        )
        self._audit(
            auth=auth,
            scope=scope,
            action="calibration_version.listed",
            resource_id=set_id,
            request_id=request_id,
        )
        return CalibrationVersionPage(
            items=items,
            page_info=PageInfo(
                has_next_page=False, has_previous_page=False, start_cursor=None, end_cursor=None
            ),
            scope=scope,
            request_id=request_id,
        )

    def recalibrate_set(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        set_id: str,
        expected_etag: str,
        command: RecalibrateCalibrationSetRequest,
        idempotency_key: str,
        request_id: str,
    ) -> CalibrationSetEnvelope:
        scope = self._publish_scope(auth=auth, project_id=project_id, region_code=region_code)
        content_hash = self._content_hash(command.document)
        fingerprint = request_fingerprint(
            {
                "operation": "calibration_set.recalibrate",
                "set_id": set_id,
                "expected_etag": expected_etag,
                "command": command.model_dump(mode="json"),
            }
        )
        try:
            data = self._repository.recalibrate_set(
                project_id=project_id,
                region_code=region_code,
                set_id=set_id,
                expected_etag=expected_etag,
                document=command.document,
                content_hash=content_hash,
                actor_id=auth.subject_id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                audit=self._write_audit(
                    auth=auth,
                    scope=scope,
                    action="calibration_set.recalibrated",
                    resource_id=set_id,
                    request_id=request_id,
                ),
            )
        except KeyError as exc:
            raise problem(
                status=404,
                code="CALIBRATION_SET_NOT_FOUND",
                title="Calibration set not found",
                detail="The requested calibration set does not exist in this scope.",
            ) from exc
        except ValueError as exc:
            message = str(exc)
            if "idempotency" in message:
                raise self._idempotency_conflict() from exc
            if "no content" in message:
                raise problem(
                    status=422,
                    code="CALIBRATION_RECALIBRATION_NO_CHANGE",
                    title="Recalibration has no content change",
                    detail="Submit a new measured transform or camera fact to create a successor.",
                ) from exc
            raise self._etag_mismatch() from exc
        return CalibrationSetEnvelope(data=data, scope=scope, request_id=request_id)

    def list_dataset_associations(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        request_id: str,
    ) -> CalibrationDatasetAssociationPage:
        scope = self._dataset_scope(auth=auth, project_id=project_id, region_code=region_code)
        if (
            self._repository.get_version_document(
                project_id=project_id,
                region_code=region_code,
                set_id=set_id,
                version=version,
            )
            is None
        ):
            raise problem(
                status=404,
                code="CALIBRATION_VERSION_DOCUMENT_NOT_FOUND",
                title="Calibration version document not found",
                detail="This scoped calibration version has no durable document.",
            )
        items = self._repository.list_dataset_associations(
            project_id=project_id,
            region_code=region_code,
            set_id=set_id,
            version=version,
        )
        self._audit(
            auth=auth,
            scope=scope,
            action="calibration_dataset_association.listed",
            resource_id=f"{set_id}:{version}",
            request_id=request_id,
        )
        return CalibrationDatasetAssociationPage(
            items=items,
            page_info=PageInfo(
                has_next_page=False,
                has_previous_page=False,
                start_cursor=None,
                end_cursor=None,
            ),
            scope=scope,
            request_id=request_id,
        )

    def associate_dataset(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        expected_etag: str,
        command: CalibrationDatasetAssociationRequest,
        idempotency_key: str,
        request_id: str,
    ) -> CalibrationDatasetAssociationEnvelope:
        scope = self._dataset_publish_scope(
            auth=auth, project_id=project_id, region_code=region_code
        )
        fingerprint = request_fingerprint(
            {
                "operation": "calibration_dataset_association.create",
                "set_id": set_id,
                "version": version,
                "expected_etag": expected_etag,
                "command": command.model_dump(mode="json"),
            }
        )
        try:
            data = self._repository.associate_dataset(
                project_id=project_id,
                region_code=region_code,
                set_id=set_id,
                version=version,
                expected_etag=expected_etag,
                dataset_id=command.dataset_id,
                dataset_version_id=command.dataset_version_id,
                actor_id=auth.subject_id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                audit=self._write_audit(
                    auth=auth,
                    scope=scope,
                    action="calibration_dataset_association.created",
                    resource_id=(
                        f"{set_id}:{version}:{command.dataset_id}:{command.dataset_version_id}"
                    ),
                    request_id=request_id,
                ),
            )
        except KeyError as exc:
            if "dataset" in str(exc):
                raise problem(
                    status=422,
                    code="DATASET_VERSION_ASSOCIATION_INVALID",
                    title="Dataset version association is invalid",
                    detail=(
                        "The requested ready dataset version is not uniquely available in "
                        "this project and region."
                    ),
                ) from exc
            raise problem(
                status=404,
                code="CALIBRATION_SET_NOT_FOUND",
                title="Calibration set not found",
                detail="The requested calibration set does not exist in this scope.",
            ) from exc
        except ValueError as exc:
            message = str(exc)
            if "idempotency" in message:
                raise self._idempotency_conflict() from exc
            if "not ready" in message:
                raise problem(
                    status=409,
                    code="CALIBRATION_DATASET_ASSOCIATION_REQUIRES_READY_VERSION",
                    title="A ready calibration version is required",
                    detail=(
                        "Validate and publish the current calibration version before "
                        "pinning it to a dataset version."
                    ),
                ) from exc
            raise self._etag_mismatch() from exc
        return CalibrationDatasetAssociationEnvelope(data=data, scope=scope, request_id=request_id)

    def validate_version(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        expected_etag: str,
        idempotency_key: str,
        request_id: str,
    ) -> CalibrationValidationReportEnvelope:
        scope = self._publish_scope(auth=auth, project_id=project_id, region_code=region_code)
        item = self._repository.get_set(
            project_id=project_id, region_code=region_code, set_id=set_id
        )
        if item is None:
            raise problem(
                status=404,
                code="CALIBRATION_SET_NOT_FOUND",
                title="Calibration set not found",
                detail="The requested calibration set does not exist in this scope.",
            )
        document = self._repository.get_version_document(
            project_id=project_id, region_code=region_code, set_id=set_id, version=version
        )
        if document is None:
            raise problem(
                status=404,
                code="CALIBRATION_VERSION_DOCUMENT_NOT_FOUND",
                title="Calibration version document not found",
                detail="This scoped calibration version has no durable document to validate.",
            )
        context_hash = self._validation_context_hash(
            robot_instance_id=item.robot_instance_id,
            component_id=item.component_id,
            document=document.document,
        )
        report = CalibrationValidationReport(
            id=f"calibration-report-{uuid4()}",
            set_id=set_id,
            version=version,
            content_hash=document.content_hash,
            validation_context_hash=context_hash,
            status="PASSED",
            findings=self._validate_document(document.document),
            checked_by=auth.subject_id,
            checked_at=self._clock(),
        )
        if any(finding.severity == "ERROR" for finding in report.findings):
            report = report.model_copy(update={"status": "FAILED"})
        fingerprint = request_fingerprint(
            {
                "operation": "calibration_set.validate",
                "set_id": set_id,
                "version": version,
                "expected_etag": expected_etag,
                "content_hash": document.content_hash,
                "validation_context_hash": context_hash,
            }
        )
        try:
            _updated, result = self._repository.validate_set(
                project_id=project_id,
                region_code=region_code,
                set_id=set_id,
                version=version,
                expected_etag=expected_etag,
                report=report,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                audit=self._write_audit(
                    auth=auth,
                    scope=scope,
                    action="calibration_version.validated",
                    resource_id=f"{set_id}:{version}",
                    request_id=request_id,
                    outcome=report.status,
                ),
            )
        except KeyError as exc:
            raise problem(
                status=404,
                code="CALIBRATION_VERSION_DOCUMENT_NOT_FOUND",
                title="Calibration version document not found",
                detail="This scoped calibration version has no durable document to validate.",
            ) from exc
        except ValueError as exc:
            if "idempotency" in str(exc):
                raise self._idempotency_conflict() from exc
            raise self._etag_mismatch() from exc
        return CalibrationValidationReportEnvelope(data=result, scope=scope, request_id=request_id)

    def get_validation_report(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        report_id: str,
        request_id: str,
    ) -> CalibrationValidationReportEnvelope:
        scope = self._scope(auth=auth, project_id=project_id, region_code=region_code)
        data = self._repository.get_validation_report(
            project_id=project_id, region_code=region_code, report_id=report_id
        )
        if data is None:
            raise problem(
                status=404,
                code="CALIBRATION_VALIDATION_REPORT_NOT_FOUND",
                title="Calibration validation report not found",
                detail="The requested validation report does not exist in this scope.",
            )
        self._audit(
            auth=auth,
            scope=scope,
            action="calibration_validation_report.read",
            resource_id=report_id,
            request_id=request_id,
        )
        return CalibrationValidationReportEnvelope(data=data, scope=scope, request_id=request_id)

    def preflight_publish(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        expected_etag: str,
        idempotency_key: str,
        request_id: str,
        command: CalibrationPublishPreflightRequest,
    ) -> CalibrationPublishPreflightEnvelope:
        scope = self._publish_scope(auth=auth, project_id=project_id, region_code=region_code)
        if command.expected_etag != expected_etag:
            raise problem(
                status=422,
                code="CALIBRATION_PREFLIGHT_ETAG_CONFLICT",
                title="Calibration preflight ETag is inconsistent",
                detail="Use the same current ETag in the request header and body.",
            )
        fingerprint = request_fingerprint(
            {
                "operation": "calibration_set.preflight_publish",
                "etag": expected_etag,
                "command": command.model_dump(mode="json"),
            }
        )
        existing = self._repository.find_publish_preflight_by_idempotency(
            project_id=project_id,
            region_code=region_code,
            set_id=set_id,
            version=version,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise self._idempotency_conflict()
            return self._preflight_envelope(existing, scope=scope, request_id=request_id)
        item = self.get_set(
            auth=auth,
            project_id=project_id,
            region_code=region_code,
            set_id=set_id,
            request_id=request_id,
        ).data
        if item.etag != expected_etag:
            raise self._etag_mismatch()
        blockers = self._publish_blockers(item=item, version=version, command=command)
        now = self._clock()
        record_without_token = CalibrationPublishPreflightRecord(
            project_id=project_id,
            region_code=region_code,
            preflight_id=str(uuid4()),
            set_id=set_id,
            version=version,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            expected_etag=expected_etag,
            expected_hash=command.expected_hash,
            validation_context_hash=command.validation_context_hash,
            validation_report_id=command.validation_report_id,
            allowed=not blockers,
            blockers=blockers,
            status="ISSUED",
            token_hash="",
            expires_at=now + _PUBLISH_PREFLIGHT_TTL,
            created_at=now,
            consumed_at=None,
        )
        token = self._preflight_token(record_without_token)
        record = dataclass_replace(
            record_without_token,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
        )
        audit = self._write_audit(
            auth=auth,
            scope=scope,
            action="calibration_set.preflight_publish",
            resource_id=set_id,
            request_id=request_id,
            outcome="SUCCEEDED" if record.allowed else "BLOCKED",
        )
        try:
            self._repository.create_publish_preflight(record=record, audit=audit)
        except Exception:
            concurrent = self._repository.find_publish_preflight_by_idempotency(
                project_id=project_id,
                region_code=region_code,
                set_id=set_id,
                version=version,
                idempotency_key=idempotency_key,
            )
            if concurrent is None or concurrent.request_fingerprint != fingerprint:
                raise
            record = concurrent
        return self._preflight_envelope(record, scope=scope, request_id=request_id)

    def publish(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
        expected_etag: str,
        idempotency_key: str,
        request_id: str,
        command: CalibrationPublishRequest,
    ) -> CalibrationSetEnvelope:
        scope = self._publish_scope(auth=auth, project_id=project_id, region_code=region_code)
        preflight_id = self._decode_preflight_token(
            token=command.preflight_token,
            project_id=project_id,
            region_code=region_code,
            set_id=set_id,
            version=version,
        )
        audit = self._write_audit(
            auth=auth,
            scope=scope,
            action="calibration_set.published",
            resource_id=set_id,
            request_id=request_id,
        )
        try:
            data = self._repository.publish_set(
                project_id=project_id,
                region_code=region_code,
                set_id=set_id,
                version=version,
                preflight_id=preflight_id,
                token_hash=hashlib.sha256(command.preflight_token.encode()).hexdigest(),
                idempotency_key=idempotency_key,
                expected_etag=expected_etag,
                occurred_at=audit.occurred_at,
                audit=audit,
            )
        except KeyError as exc:
            raise problem(
                status=404,
                code="CALIBRATION_PUBLISH_PREFLIGHT_NOT_FOUND",
                title="Calibration publish preflight not found",
                detail="Start a new publish preflight for this calibration version.",
            ) from exc
        except PermissionError as exc:
            raise problem(
                status=422,
                code="CALIBRATION_PUBLISH_PREFLIGHT_TOKEN_INVALID",
                title="Calibration publish preflight token is invalid",
                detail="Start a new publish preflight for this calibration version.",
            ) from exc
        except TimeoutError as exc:
            raise problem(
                status=409,
                code="CALIBRATION_PUBLISH_PREFLIGHT_EXPIRED",
                title="Calibration publish preflight expired",
                detail="Run the publish preflight again before publishing the draft.",
            ) from exc
        except ValueError as exc:
            if "idempotency" in str(exc):
                raise self._idempotency_conflict() from exc
            if "etag" in str(exc):
                raise self._etag_mismatch() from exc
            raise problem(
                status=409,
                code="CALIBRATION_PUBLISH_PREFLIGHT_STALE",
                title="Calibration publish preflight is stale",
                detail="The draft, content hash, or validation report changed.",
            ) from exc
        return CalibrationSetEnvelope(data=data, scope=scope, request_id=request_id)

    @staticmethod
    def _idempotency_conflict() -> ProblemException:
        return problem(
            status=409,
            code="IDEMPOTENCY_KEY_REUSED",
            title="Idempotency key reused",
            detail="The same command key was reused with a different request body.",
        )

    @staticmethod
    def _canonical_hash(value: object) -> str:
        return hashlib.sha256(
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def _content_hash(cls, document: CalibrationDocument) -> str:
        return cls._canonical_hash(document.model_dump(mode="json"))

    @classmethod
    def _validation_context_hash(
        cls,
        *,
        robot_instance_id: str,
        component_id: str | None,
        document: CalibrationDocument,
    ) -> str:
        return cls._canonical_hash(
            {
                "robot_instance_id": robot_instance_id,
                "component_id": component_id,
                "frames": sorted(
                    {edge.parent_frame for edge in document.frame_transforms}
                    | {edge.child_frame for edge in document.frame_transforms}
                ),
                "camera_frames": sorted(camera.frame_id for camera in document.camera_intrinsics),
            }
        )

    @staticmethod
    def _validate_document(
        document: CalibrationDocument,
    ) -> tuple[CalibrationValidationFinding, ...]:
        """Return deterministic findings for the stored document, never browser facts."""

        findings: list[CalibrationValidationFinding] = []
        parents: dict[str, str] = {}
        graph: dict[str, list[str]] = {}
        frames: set[str] = set()
        for index, transform in enumerate(document.frame_transforms):
            path = f"/frame_transforms/{index}"
            frames.update((transform.parent_frame, transform.child_frame))
            graph.setdefault(transform.parent_frame, []).append(transform.child_frame)
            prior_parent = parents.get(transform.child_frame)
            if prior_parent is not None and prior_parent != transform.parent_frame:
                findings.append(
                    CalibrationValidationFinding(
                        code="CALIBRATION_FRAME_MULTIPLE_PARENTS",
                        severity="ERROR",
                        message="A frame must have one physical parent in one calibration version.",
                        path=f"{path}/child_frame",
                    )
                )
            parents[transform.child_frame] = transform.parent_frame
            norm = math.sqrt(sum(value * value for value in transform.quaternion_xyzw))
            if abs(norm - 1.0) > 0.02:
                findings.append(
                    CalibrationValidationFinding(
                        code="CALIBRATION_QUATERNION_NOT_NORMALIZED",
                        severity="ERROR",
                        message="Quaternion norm must be within 0.02 of one.",
                        path=f"{path}/quaternion_xyzw",
                    )
                )
            covariance = transform.covariance
            if covariance is not None:
                for row in range(6):
                    diagonal = covariance[row * 6 + row]
                    if diagonal < 0:
                        findings.append(
                            CalibrationValidationFinding(
                                code="CALIBRATION_COVARIANCE_NEGATIVE_DIAGONAL",
                                severity="ERROR",
                                message="Covariance diagonal entries must be non-negative.",
                                path=f"{path}/covariance/{row * 6 + row}",
                            )
                        )
                    for column in range(row + 1, 6):
                        if abs(covariance[row * 6 + column] - covariance[column * 6 + row]) > 1e-9:
                            findings.append(
                                CalibrationValidationFinding(
                                    code="CALIBRATION_COVARIANCE_NOT_SYMMETRIC",
                                    severity="ERROR",
                                    message="Covariance must be symmetric.",
                                    path=f"{path}/covariance",
                                )
                            )
                            break

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(frame: str) -> bool:
            if frame in visiting:
                return True
            if frame in visited:
                return False
            visiting.add(frame)
            cyclic = any(visit(child) for child in graph.get(frame, ()))
            visiting.remove(frame)
            visited.add(frame)
            return cyclic

        if any(visit(frame) for frame in sorted(frames)):
            findings.append(
                CalibrationValidationFinding(
                    code="CALIBRATION_FRAME_CYCLE",
                    severity="ERROR",
                    message="Frame transforms must form an acyclic directed graph.",
                    path="/frame_transforms",
                )
            )
        for index, camera in enumerate(document.camera_intrinsics):
            if camera.frame_id not in frames:
                findings.append(
                    CalibrationValidationFinding(
                        code="CALIBRATION_CAMERA_FRAME_UNKNOWN",
                        severity="ERROR",
                        message=(
                            "Camera intrinsics must refer to a frame in this calibration document."
                        ),
                        path=f"/camera_intrinsics/{index}/frame_id",
                    )
                )
            if camera.cx_px > camera.width_px or camera.cy_px > camera.height_px:
                findings.append(
                    CalibrationValidationFinding(
                        code="CALIBRATION_CAMERA_PRINCIPAL_POINT_OUT_OF_BOUNDS",
                        severity="WARNING",
                        message=(
                            "The camera principal point lies outside the declared image bounds."
                        ),
                        path=f"/camera_intrinsics/{index}",
                    )
                )
        return tuple(findings)

    @staticmethod
    def _etag_mismatch() -> ProblemException:
        return problem(
            status=412,
            code="CALIBRATION_SET_ETAG_MISMATCH",
            title="Calibration set changed",
            detail="Reload the calibration set before continuing publication.",
        )

    @staticmethod
    def _publish_blockers(
        *, item: CalibrationSetRecord, version: str, command: CalibrationPublishPreflightRequest
    ) -> tuple[CalibrationBlockedReason, ...]:
        # The public values are compared only with the currently stored, server
        # produced validation result; the browser cannot assert a passing state.
        calibration = item
        checks = (
            (
                calibration.version == version,
                "CALIBRATION_VERSION_MISMATCH",
                "The requested version is no longer the current calibration snapshot.",
            ),
            (
                calibration.snapshot_status == "DRAFT",
                "CALIBRATION_NOT_DRAFT",
                "Only a draft calibration snapshot can be published.",
            ),
            (
                calibration.content_hash == command.expected_hash,
                "CALIBRATION_CONTENT_HASH_MISMATCH",
                "The calibration content hash changed; reload and validate it again.",
            ),
            (
                calibration.validation_context_hash == command.validation_context_hash,
                "CALIBRATION_CONTEXT_HASH_MISMATCH",
                "The validation context changed; run validation again.",
            ),
            (
                calibration.validation is not None
                and calibration.validation.status == "PASSED"
                and calibration.validation.report_id == command.validation_report_id
                and calibration.validation.content_hash == command.expected_hash
                and calibration.validation.validation_context_hash
                == command.validation_context_hash,
                "CALIBRATION_VALIDATION_REQUIRED",
                "A passing validation report for this exact draft is required.",
            ),
        )
        return tuple(
            CalibrationBlockedReason(code=code, message=message)
            for passed, code, message in checks
            if not passed
        )

    def _preflight_token(self, record: CalibrationPublishPreflightRecord) -> str:
        return self._preflight_codec.encode(
            {
                "kind": "calibration.publish_preflight.v1",
                "project_id": record.project_id,
                "region_code": record.region_code,
                "set_id": record.set_id,
                "version": record.version,
                "preflight_id": record.preflight_id,
                "expected_etag": record.expected_etag,
                "expires_at": record.expires_at.isoformat(),
            }
        )

    def _decode_preflight_token(
        self,
        *,
        token: str,
        project_id: str,
        region_code: str,
        set_id: str,
        version: str,
    ) -> str:
        try:
            payload = self._preflight_codec.decode(token)
        except (ProblemException, ValueError) as exc:
            raise problem(
                status=422,
                code="CALIBRATION_PUBLISH_PREFLIGHT_TOKEN_INVALID",
                title="Calibration publish preflight token is invalid",
                detail="Start a new publish preflight for this calibration version.",
            ) from exc
        if not isinstance(payload, dict) or (
            payload.get("kind"),
            payload.get("project_id"),
            payload.get("region_code"),
            payload.get("set_id"),
            payload.get("version"),
        ) != (
            "calibration.publish_preflight.v1",
            project_id,
            region_code,
            set_id,
            version,
        ):
            raise problem(
                status=422,
                code="CALIBRATION_PUBLISH_PREFLIGHT_TOKEN_INVALID",
                title="Calibration publish preflight token is invalid",
                detail="Start a new publish preflight for this calibration version.",
            )
        preflight_id = payload.get("preflight_id")
        if not isinstance(preflight_id, str):
            raise problem(
                status=422,
                code="CALIBRATION_PUBLISH_PREFLIGHT_TOKEN_INVALID",
                title="Calibration publish preflight token is invalid",
                detail="Start a new publish preflight for this calibration version.",
            )
        return preflight_id

    def _preflight_envelope(
        self,
        record: CalibrationPublishPreflightRecord,
        *,
        scope: CalibrationScope,
        request_id: str,
    ) -> CalibrationPublishPreflightEnvelope:
        expired = record.status != "ISSUED" or record.expires_at <= self._clock()
        blockers = record.blockers
        if expired:
            blockers = (
                *blockers,
                CalibrationBlockedReason(
                    code="CALIBRATION_PUBLISH_PREFLIGHT_EXPIRED",
                    message="Run the publish preflight again to obtain a current proof.",
                ),
            )
        return CalibrationPublishPreflightEnvelope(
            data=CalibrationPublishPreflight(
                allowed=record.allowed and not expired,
                preflight_token=(
                    self._preflight_token(record) if record.allowed and not expired else None
                ),
                expires_at=record.expires_at,
                resource_revision=record.expected_etag,
                impacts=(
                    CalibrationBlockedReason(
                        code="CALIBRATION_READY_FOR_PUBLISH",
                        message=(
                            "The immutable draft and validation report are bound to this proof."
                        ),
                    ),
                )
                if record.allowed and not expired
                else (),
                blockers=blockers,
            ),
            scope=scope,
            request_id=request_id,
        )

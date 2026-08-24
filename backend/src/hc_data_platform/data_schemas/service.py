from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4

from hc_data_platform.core.errors import problem
from hc_data_platform.core.pagination import CursorCodec, PageInfo
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.scope import ScopeGuard

from .models import (
    CanonicalRouteQuery,
    CreateStreamSchemaRequest,
    DataSchemaDatasetReferenceEnvelope,
    DataSchemaDatasetReferencePage,
    DataSchemaDatasetReferenceRequest,
    DataSchemaDatasetReferenceScope,
    DataSchemaEnvelope,
    DataSchemaPage,
    DataSchemaPublishPreflightEnvelope,
    DataSchemaPublishPreflightRequest,
    DataSchemaPublishRequest,
    DataSchemaRouteEnvelope,
    DataSchemaRouteResolution,
    DataSchemaRouteScope,
    DataSchemaScope,
    DataSchemaValidationFinding,
    DataSchemaValidationReport,
    DataSchemaValidationReportEnvelope,
    DataSchemaVersionRecord,
    StreamSchemaDefinition,
    UpdateStreamSchemaDraftRequest,
)
from .repository import (
    DataSchemaAuditEvent,
    DataSchemaRepository,
    InMemoryDataSchemaRepository,
)


class DataSchemaService:
    def __init__(
        self,
        repository: DataSchemaRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        cursor_secret: str = "data-schema-cursor-secret",
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._cursor = CursorCodec(cursor_secret)

    @classmethod
    def in_memory(cls) -> DataSchemaService:
        return cls(InMemoryDataSchemaRepository())

    def _organization_scope(
        self, *, auth: AuthContext, organization_id: str, project_id: str
    ) -> DataSchemaScope:
        ScopeGuard.require(auth, project_id)
        auth.require_capability("data_schema.read", project_id)
        if not self._repository.has_organization_project(
            organization_id=organization_id, project_id=project_id
        ):
            raise problem(
                status=403,
                code="ORGANIZATION_SCOPE_DENIED",
                title="Organization access denied",
                detail="The current project is not a member of the requested organization.",
            )
        return DataSchemaScope(organization_id=organization_id)

    def _route_scope(self, *, auth: AuthContext, project_id: str, region_code: str) -> None:
        ScopeGuard.require(auth, project_id, region_code)
        auth.require_capability("data_schema.read", project_id)

    def _dataset_reference_scope(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        write: bool,
    ) -> DataSchemaDatasetReferenceScope:
        ScopeGuard.require(auth, project_id, region_code)
        auth.require_capability("data_schema.read", project_id)
        auth.require_capability("datasets.read", project_id)
        if write:
            auth.require_capability("data_schema.publish", project_id)
        if not self._repository.has_organization_project(
            organization_id=organization_id, project_id=project_id
        ):
            raise problem(
                status=403,
                code="ORGANIZATION_SCOPE_DENIED",
                title="Organization access denied",
                detail="The current project is not a member of the requested organization.",
            )
        return DataSchemaDatasetReferenceScope(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )

    def _mutation_scope(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        capability: str,
    ) -> DataSchemaScope:
        ScopeGuard.require(auth, project_id)
        auth.require_capability(capability, project_id)
        if not self._repository.has_organization_project(
            organization_id=organization_id, project_id=project_id
        ):
            raise problem(
                status=403,
                code="ORGANIZATION_SCOPE_DENIED",
                title="Organization access denied",
                detail="The current project is not a member of the requested organization.",
            )
        return DataSchemaScope(organization_id=organization_id)

    def _audit(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str | None,
        action: str,
        resource_id: str,
        request_id: str,
        outcome: str = "SUCCEEDED",
    ) -> None:
        self._repository.append_audit(
            DataSchemaAuditEvent(
                project_id=project_id,
                region_code=region_code,
                actor_id=auth.subject_id,
                action=action,
                resource_id=resource_id,
                request_id=request_id,
                outcome=outcome,
                occurred_at=self._clock(),
            )
        )

    def list_versions(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        query: str | None,
        status: str | None,
        logical_type: str | None,
        after: str | None,
        before: str | None,
        limit: int,
        request_id: str,
    ) -> DataSchemaPage:
        if after is not None and before is not None:
            raise problem(
                status=400,
                code="PAGINATION_BOUNDARY_CONFLICT",
                title="Pagination boundary conflict",
                detail="Use either after or before, not both.",
                request_id=request_id,
            )
        scope = self._organization_scope(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        boundary = self._decode_cursor(
            cursor=after or before,
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            query=query,
            status=status,
            logical_type=logical_type,
            request_id=request_id,
        )
        window = self._repository.list_versions(
            organization_id=organization_id,
            project_id=project_id,
            query=query,
            status=status,
            logical_type=logical_type,
            after=boundary if after is not None else None,
            before=boundary if before is not None else None,
            limit=limit,
        )
        self._audit(
            auth=auth,
            project_id=project_id,
            region_code=None,
            action="data_schema.listed",
            resource_id=organization_id,
            request_id=request_id,
        )
        return DataSchemaPage(
            items=window.items,
            page_info=PageInfo(
                has_next_page=(window.has_more if before is None else bool(before)),
                has_previous_page=(bool(after) if before is None else window.has_more),
                start_cursor=(
                    self._encode_cursor(
                        item=window.items[0],
                        auth=auth,
                        organization_id=organization_id,
                        project_id=project_id,
                        query=query,
                        status=status,
                        logical_type=logical_type,
                    )
                    if window.items
                    else None
                ),
                end_cursor=(
                    self._encode_cursor(
                        item=window.items[-1],
                        auth=auth,
                        organization_id=organization_id,
                        project_id=project_id,
                        query=query,
                        status=status,
                        logical_type=logical_type,
                    )
                    if window.items
                    else None
                ),
            ),
            snapshot_at=self._clock(),
            scope=scope,
            request_id=request_id,
        )

    def _encode_cursor(
        self,
        *,
        item: DataSchemaVersionRecord,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        query: str | None,
        status: str | None,
        logical_type: str | None,
    ) -> str:
        return self._cursor.encode(
            {
                "v": 1,
                "actor": auth.subject_id,
                "organization_id": organization_id,
                "project_id": project_id,
                "query": query,
                "status": status,
                "logical_type": logical_type,
                "position": [
                    item.display_name.casefold(),
                    item.schema_id,
                    int(item.schema_version),
                ],
            }
        )

    def _decode_cursor(
        self,
        *,
        cursor: str | None,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        query: str | None,
        status: str | None,
        logical_type: str | None,
        request_id: str,
    ) -> tuple[str, str, int] | None:
        if cursor is None:
            return None
        payload = self._cursor.decode(cursor)
        expected = {
            "v": 1,
            "actor": auth.subject_id,
            "organization_id": organization_id,
            "project_id": project_id,
            "query": query,
            "status": status,
            "logical_type": logical_type,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid pagination cursor",
                detail="The cursor does not match the current scope or filters.",
                request_id=request_id,
            )
        position = payload.get("position")
        if (
            not isinstance(position, list)
            or len(position) != 3
            or not isinstance(position[0], str)
            or not isinstance(position[1], str)
            or not isinstance(position[2], int)
            or position[2] < 1
        ):
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid pagination cursor",
                detail="The cursor position is malformed.",
                request_id=request_id,
            )
        return position[0], position[1], position[2]

    def get_version(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
        request_id: str,
    ) -> DataSchemaEnvelope:
        scope = self._organization_scope(
            auth=auth, organization_id=organization_id, project_id=project_id
        )
        data = self._repository.get_version(
            organization_id=organization_id,
            project_id=project_id,
            schema_id=schema_id,
            schema_version=schema_version,
        )
        if data is None:
            raise problem(
                status=404,
                code="DATA_SCHEMA_VERSION_NOT_FOUND",
                title="Data schema version not found",
                detail="The requested schema version does not exist in this organization.",
            )
        self._audit(
            auth=auth,
            project_id=project_id,
            region_code=None,
            action="data_schema.version.read",
            resource_id=f"{schema_id}:{schema_version}",
            request_id=request_id,
        )
        return DataSchemaEnvelope(data=data, scope=scope, request_id=request_id)

    def list_dataset_references(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        schema_id: str,
        schema_version: str,
        request_id: str,
    ) -> DataSchemaDatasetReferencePage:
        scope = self._dataset_reference_scope(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            write=False,
        )
        if (
            self._repository.get_version(
                organization_id=organization_id,
                project_id=project_id,
                schema_id=schema_id,
                schema_version=schema_version,
            )
            is None
        ):
            raise problem(
                status=404,
                code="DATA_SCHEMA_VERSION_NOT_FOUND",
                title="Data schema version not found",
                detail="The requested schema version does not exist in this organization.",
                request_id=request_id,
            )
        items = self._repository.list_dataset_references(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            schema_id=schema_id,
            schema_version=schema_version,
        )
        self._audit(
            auth=auth,
            project_id=project_id,
            region_code=region_code,
            action="data_schema.dataset_reference.listed",
            resource_id=f"{schema_id}:{schema_version}",
            request_id=request_id,
        )
        return DataSchemaDatasetReferencePage(
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

    def associate_dataset_reference(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        command: DataSchemaDatasetReferenceRequest,
        idempotency_key: str,
        request_id: str,
    ) -> DataSchemaDatasetReferenceEnvelope:
        scope = self._dataset_reference_scope(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            write=True,
        )
        try:
            data = self._repository.associate_dataset_reference(
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
                schema_id=schema_id,
                schema_version=schema_version,
                expected_etag=expected_etag,
                dataset_id=command.dataset_id,
                dataset_version_id=command.dataset_version_id,
                actor_id=auth.subject_id,
                idempotency_key=idempotency_key,
                request_id=request_id,
                occurred_at=self._clock(),
            )
        except KeyError as exc:
            if "dataset" in str(exc):
                raise problem(
                    status=422,
                    code="DATASET_VERSION_REFERENCE_INVALID",
                    title="Dataset version reference is invalid",
                    detail=(
                        "The requested dataset version must be READY and available in the "
                        "same organization, project, and region."
                    ),
                    request_id=request_id,
                ) from exc
            self._mutation_problem(exc, request_id=request_id)
            raise AssertionError("unreachable") from exc
        except PermissionError as exc:
            raise problem(
                status=409,
                code="DATA_SCHEMA_DATASET_REFERENCE_REQUIRES_PUBLISHED_VERSION",
                title="A published schema version is required",
                detail=(
                    "Publish this immutable schema version before recording dataset provenance."
                ),
                request_id=request_id,
            ) from exc
        except Exception as exc:
            self._mutation_problem(exc, request_id=request_id)
            raise AssertionError("unreachable") from exc
        return DataSchemaDatasetReferenceEnvelope(data=data, scope=scope, request_id=request_id)

    def resolve_route(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        schema_id: str,
        schema_version: str,
        component_id: str,
        detail_tab: str,
        request_id: str,
    ) -> DataSchemaRouteEnvelope:
        self._route_scope(auth=auth, project_id=project_id, region_code=region_code)
        facts = self._repository.resolve_route(
            project_id=project_id,
            region_code=region_code,
            component_id=component_id,
            schema_id=schema_id,
            schema_version=schema_version,
        )
        if len(facts) != 1:
            raise problem(
                status=404,
                code="COMPONENT_SCHEMA_RELATION_NOT_FOUND",
                title="Component schema relation not found",
                detail="The component does not reference one authorized fixed schema version.",
            )
        fact = facts[0]
        scope = DataSchemaRouteScope(
            organization_id=fact.organization_id, project_id=project_id, region_code=region_code
        )
        result = DataSchemaRouteResolution(
            canonical_query=CanonicalRouteQuery(
                schema_id=schema_id,
                schema_version=schema_version,
                component_id=component_id,
                detail_tab=detail_tab,
            ),
            scope=scope,
            relation_revision=fact.relation_revision,
        )
        self._audit(
            auth=auth,
            project_id=project_id,
            region_code=region_code,
            action="data_schema.route_resolved",
            resource_id=f"{component_id}:{schema_id}:{schema_version}",
            request_id=request_id,
        )
        return DataSchemaRouteEnvelope(data=result, scope=scope, request_id=request_id)

    @staticmethod
    def _mutation_problem(exc: Exception, *, request_id: str) -> None:
        if isinstance(exc, KeyError):
            raise problem(
                status=404,
                code="DATA_SCHEMA_VERSION_NOT_FOUND",
                title="Data schema version not found",
                detail="The requested schema version or its validation evidence does not exist.",
                request_id=request_id,
            ) from exc
        if isinstance(exc, RuntimeError):
            raise problem(
                status=412,
                code="ETAG_MISMATCH",
                title="Schema version changed",
                detail="Refresh the schema version before retrying this operation.",
                request_id=request_id,
            ) from exc
        if isinstance(exc, PermissionError):
            raise problem(
                status=409,
                code="DATA_SCHEMA_MUTATION_NOT_ALLOWED",
                title="Schema mutation is not allowed",
                detail="Only a current draft with passing validation can be changed or published.",
                request_id=request_id,
            ) from exc
        if isinstance(exc, ValueError):
            raise problem(
                status=409,
                code="IDEMPOTENCY_KEY_REUSED",
                title="Idempotency key reuse conflict",
                detail="Use a new idempotency key for a different schema command.",
                request_id=request_id,
            ) from exc
        raise exc

    def create_version(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        command: CreateStreamSchemaRequest,
        source: str,
        idempotency_key: str,
        request_id: str,
    ) -> DataSchemaEnvelope:
        capability = "data_schema.import" if source == "IMPORT" else "data_schema.create"
        scope = self._mutation_scope(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            capability=capability,
        )
        try:
            data = self._repository.create_version(
                organization_id=organization_id,
                project_id=project_id,
                command=command,
                source=source,
                idempotency_key=idempotency_key,
                actor_id=auth.subject_id,
                request_id=request_id,
                occurred_at=self._clock(),
            )
        except Exception as exc:
            self._mutation_problem(exc, request_id=request_id)
            raise AssertionError("unreachable") from exc
        return DataSchemaEnvelope(data=data, scope=scope, request_id=request_id)

    def update_draft(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        command: UpdateStreamSchemaDraftRequest,
        idempotency_key: str,
        request_id: str,
    ) -> DataSchemaEnvelope:
        scope = self._mutation_scope(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            capability="data_schema.create",
        )
        try:
            data = self._repository.update_draft(
                organization_id=organization_id,
                project_id=project_id,
                schema_id=schema_id,
                schema_version=schema_version,
                expected_etag=expected_etag,
                command=command,
                idempotency_key=idempotency_key,
                actor_id=auth.subject_id,
                request_id=request_id,
                occurred_at=self._clock(),
            )
        except Exception as exc:
            self._mutation_problem(exc, request_id=request_id)
            raise AssertionError("unreachable") from exc
        return DataSchemaEnvelope(data=data, scope=scope, request_id=request_id)

    @staticmethod
    def _compatibility_report(
        current: DataSchemaVersionRecord, prior: DataSchemaVersionRecord | None
    ) -> tuple[str, tuple[DataSchemaValidationFinding, ...]]:
        if prior is None:
            return "PASSED", ()
        previous = StreamSchemaDefinition.model_validate(prior.schema_definition)
        candidate = StreamSchemaDefinition.model_validate(current.schema_definition)
        before = {field.name: field for field in previous.fields}
        after = {field.name: field for field in candidate.fields}
        findings: list[DataSchemaValidationFinding] = []
        mode = current.compatibility_mode
        if mode in {"BACKWARD", "FULL", "STRICT"}:
            for name, field in before.items():
                candidate_field = after.get(name)
                if candidate_field is None or candidate_field.type != field.type:
                    findings.append(
                        DataSchemaValidationFinding(
                            code="BACKWARD_FIELD_CHANGED",
                            severity="ERROR",
                            message="A prior field was removed or changed type.",
                            path=f"/fields/{name}",
                        )
                    )
        if mode in {"FORWARD", "FULL", "STRICT"}:
            for name, field in after.items():
                previous_field = before.get(name)
                if previous_field is None or previous_field.type != field.type:
                    findings.append(
                        DataSchemaValidationFinding(
                            code="FORWARD_FIELD_CHANGED",
                            severity="ERROR",
                            message="A new field is not consumable by the prior version.",
                            path=f"/fields/{name}",
                        )
                    )
        if mode == "STRICT" and current.schema_definition != prior.schema_definition:
            findings.append(
                DataSchemaValidationFinding(
                    code="STRICT_SCHEMA_CHANGED",
                    severity="ERROR",
                    message="STRICT compatibility requires byte-for-byte canonical equivalence.",
                    path="/",
                )
            )
        return ("FAILED" if findings else "PASSED", tuple(findings))

    def validate_version(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        idempotency_key: str,
        request_id: str,
    ) -> DataSchemaValidationReportEnvelope:
        scope = self._mutation_scope(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            capability="data_schema.validate",
        )
        current = self._repository.get_version(
            organization_id=organization_id,
            project_id=project_id,
            schema_id=schema_id,
            schema_version=schema_version,
        )
        if current is None:
            raise problem(
                status=404,
                code="DATA_SCHEMA_VERSION_NOT_FOUND",
                title="Data schema version not found",
                detail="The requested schema version does not exist in this organization.",
                request_id=request_id,
            )
        prior = self._repository.get_previous_version(
            organization_id=organization_id,
            project_id=project_id,
            schema_id=schema_id,
            schema_version=schema_version,
        )
        try:
            result, findings = self._compatibility_report(current, prior)
        except Exception as exc:
            raise problem(
                status=422,
                code="DATA_SCHEMA_DEFINITION_INVALID",
                title="Schema definition invalid",
                detail="The stored definition does not satisfy the supported field contract.",
                request_id=request_id,
            ) from exc
        now = self._clock()
        report = DataSchemaValidationReport(
            id=str(uuid4()),
            schema_id=schema_id,
            schema_version=schema_version,
            content_hash=current.schema_hash.value if current.schema_hash else "0" * 64,
            compatibility_check_id=str(uuid4()),
            compatibility_result=result,
            status=result,
            findings=findings,
            checked_by=auth.subject_id,
            checked_at=now,
        )
        try:
            data = self._repository.validate_version(
                organization_id=organization_id,
                project_id=project_id,
                schema_id=schema_id,
                schema_version=schema_version,
                expected_etag=expected_etag,
                report=report,
                idempotency_key=idempotency_key,
                actor_id=auth.subject_id,
                request_id=request_id,
                occurred_at=now,
            )
        except Exception as exc:
            self._mutation_problem(exc, request_id=request_id)
            raise AssertionError("unreachable") from exc
        return DataSchemaValidationReportEnvelope(data=data, scope=scope, request_id=request_id)

    def preflight_publish(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        command: DataSchemaPublishPreflightRequest,
        idempotency_key: str,
        request_id: str,
    ) -> DataSchemaPublishPreflightEnvelope:
        scope = self._mutation_scope(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            capability="data_schema.publish",
        )
        if command.expected_etag != expected_etag:
            raise problem(
                status=422,
                code="PREFLIGHT_ETAG_MISMATCH",
                title="Preflight ETag mismatch",
                detail=(
                    "The request body and If-Match header must identify the same schema revision."
                ),
                request_id=request_id,
            )
        try:
            data = self._repository.preflight_publish(
                organization_id=organization_id,
                project_id=project_id,
                schema_id=schema_id,
                schema_version=schema_version,
                expected_etag=expected_etag,
                expected_hash=command.expected_hash,
                validation_report_id=command.validation_report_id,
                compatibility_check_id=command.compatibility_check_id,
                idempotency_key=idempotency_key,
                actor_id=auth.subject_id,
                request_id=request_id,
                occurred_at=self._clock(),
            )
        except Exception as exc:
            self._mutation_problem(exc, request_id=request_id)
            raise AssertionError("unreachable") from exc
        return DataSchemaPublishPreflightEnvelope(data=data, scope=scope, request_id=request_id)

    def publish_version(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        schema_id: str,
        schema_version: str,
        expected_etag: str,
        command: DataSchemaPublishRequest,
        idempotency_key: str,
        request_id: str,
    ) -> DataSchemaEnvelope:
        scope = self._mutation_scope(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            capability="data_schema.publish",
        )
        try:
            data = self._repository.publish_version(
                organization_id=organization_id,
                project_id=project_id,
                schema_id=schema_id,
                schema_version=schema_version,
                expected_etag=expected_etag,
                preflight_token=command.preflight_token,
                idempotency_key=idempotency_key,
                actor_id=auth.subject_id,
                request_id=request_id,
                occurred_at=self._clock(),
            )
        except Exception as exc:
            self._mutation_problem(exc, request_id=request_id)
            raise AssertionError("unreachable") from exc
        return DataSchemaEnvelope(data=data, scope=scope, request_id=request_id)

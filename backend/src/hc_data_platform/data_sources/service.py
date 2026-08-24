"""P02 data-source application service with scoped CAS and safe credential handling."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.core.errors import problem
from hc_data_platform.core.pagination import CursorCodec, PageInfo
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.idempotency import IdempotencyStore, InMemoryIdempotencyStore
from hc_data_platform.security.scope import ScopeGuard
from hc_data_platform.security.versioning import ResourceVersion, etag_mismatch

from .models import (
    BasicCredentialInput,
    ConnectionTestJobEnvelope,
    CreateDataSourceCommand,
    CredentialInput,
    CredentialSummary,
    DataSourceAsyncJob,
    DataSourceBlockedReason,
    DataSourceConnectionTestJob,
    DataSourceDetail,
    DataSourceEdgeAgentBinding,
    DataSourceEdgeAgentConfiguration,
    DataSourceEnvelope,
    DataSourceFacet,
    DataSourceFacets,
    DataSourceJobError,
    DataSourceJobProgress,
    DataSourceMetrics,
    DataSourceMutationRecord,
    DataSourceObservedVersions,
    DataSourceOssImportBinding,
    DataSourceOssImportConfiguration,
    DataSourcePage,
    DataSourceResourceReference,
    DataSourceRobotBinding,
    DataSourceRobotConfiguration,
    DataSourceRobotFacet,
    DataSourceSafeError,
    DataSourceScope,
    DataSourceSummary,
    DeviceCertificateCredentialInput,
    RotateCredentialCommand,
    SourceStateCommand,
    TestConnectionCommand,
    TokenCredentialInput,
    UpdateDataSourceCommand,
    UploadPolicySummary,
    WritableConnectorConfiguration,
    WritableDataSourceBinding,
    WritableEdgeAgentBinding,
    WritableEdgeAgentConfiguration,
    WritableOssImportBinding,
    WritableOssImportConfiguration,
    WritableRobotBinding,
    WritableRobotConfiguration,
)
from .repository import (
    CredentialMaterial,
    CredentialVaultUnavailable,
    DataSourceAuditEvent,
    DataSourceDuplicateError,
    DataSourceRepository,
    DataSourceVersionConflict,
    InMemoryDataSourceRepository,
)

Clock = Callable[[], datetime]
SourceSort = Literal[
    "updated_at:desc,id:desc",
    "name:asc,id:asc",
    "last_test_at:desc,id:desc",
]


@dataclass(frozen=True, slots=True)
class DataSourceMutationOutcome:
    record: DataSourceMutationRecord
    replayed: bool


class DataSourceService:
    def __init__(
        self,
        repository: DataSourceRepository,
        *,
        cursor_secret: str = "data-source-local-cursor-secret",
        credential_key: str = "data-source-local-credential-key",
        idempotency: IdempotencyStore | None = None,
        clock: Clock = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._cursor = CursorCodec(cursor_secret)
        self._credential_key = credential_key
        self._idempotency = idempotency or InMemoryIdempotencyStore()
        self._clock = clock

    @classmethod
    def in_memory(cls) -> DataSourceService:
        return cls(InMemoryDataSourceRepository())

    def list_sources(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        query: str | None,
        source_types: tuple[str, ...],
        administrative_states: tuple[str, ...],
        connectivity_states: tuple[str, ...],
        credential_states: tuple[str, ...],
        sort: SourceSort,
        after: str | None,
        before: str | None,
        limit: int,
        request_id: str,
    ) -> DataSourcePage:
        scope = self._authorize_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        if after is not None and before is not None:
            raise problem(
                status=422,
                code="CURSOR_DIRECTION_CONFLICT",
                title="Invalid pagination request",
                detail="Use either after or before, not both.",
            )
        normalized_query = query.casefold().strip() if query else None
        normalized_source_types = tuple(sorted(set(source_types)))
        normalized_administrative_states = tuple(sorted(set(administrative_states)))
        normalized_connectivity_states = tuple(sorted(set(connectivity_states)))
        normalized_credential_states = tuple(sorted(set(credential_states)))
        all_items = self._sort_sources(
            self._repository.list_sources(
                scope=scope,
                query=normalized_query,
                source_types=normalized_source_types,
                administrative_states=normalized_administrative_states,
                connectivity_states=normalized_connectivity_states,
                credential_states=normalized_credential_states,
            ),
            sort,
        )
        visible, page_info = self._page_sources(
            all_items,
            scope=scope,
            query=normalized_query,
            source_types=normalized_source_types,
            administrative_states=normalized_administrative_states,
            connectivity_states=normalized_connectivity_states,
            credential_states=normalized_credential_states,
            sort=sort,
            after=after,
            before=before,
            limit=limit,
        )
        now = self._clock()
        self._append_audit(
            auth=auth,
            scope=scope,
            action="data_source.listed",
            resource_id=project_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return DataSourcePage(
            summary=self._metrics(all_items, now),
            facets=_facets(all_items),
            items=tuple(self._summary(self._decorate(source, auth)) for source in visible),
            page_info=page_info,
            snapshot_at=now,
            allowed_actions=(
                ("CREATE",) if auth.has_capability("ingest_source.manage", project_id) else ()
            ),
            component_errors=(),
            scope=scope,
            request_id=request_id,
        )

    def get_source(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        source_id: str,
        request_id: str,
    ) -> DataSourceEnvelope:
        scope = self._authorize_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        source = self._require_source(scope=scope, source_id=source_id)
        self._append_audit(
            auth=auth,
            scope=scope,
            action="data_source.read",
            resource_id=source_id,
            request_id=request_id,
            outcome="SUCCEEDED",
        )
        return DataSourceEnvelope(
            data=self._decorate(source, auth),
            scope=scope,
            request_id=request_id,
        )

    def create_source(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        command: CreateDataSourceCommand,
        idempotency_key: str,
        request_id: str,
    ) -> DataSourceMutationOutcome:
        scope = self._authorize_manage(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        payload = _safe_create_payload(command)

        def action() -> DataSourceMutationRecord:
            now = self._clock()
            source_id = _create_source_id(scope=scope, idempotency_key=idempotency_key)
            material = _credential_material(command.credential_input)
            source = self._new_source(
                scope=scope,
                source_id=source_id,
                command=command,
                credential=material,
                now=now,
            )
            audit = self._audit_event(
                auth=auth,
                scope=scope,
                action="data_source.created",
                resource_id=source_id,
                request_id=request_id,
                before=None,
                after=source,
                details={"credential_supplied": material is not None},
            )
            try:
                saved = self._repository.create_source(
                    source=source,
                    credential=material,
                    credential_key=self._credential_key,
                    audit_event=audit,
                )
            except DataSourceDuplicateError as exc:
                raise _duplicate_source() from exc
            return DataSourceMutationRecord(source=saved)

        result = self._idempotency.execute(
            scope=project_id,
            key=f"data-source:create:{idempotency_key}",
            payload=payload,
            action=action,
        )
        return DataSourceMutationOutcome(record=result.value, replayed=result.replayed)

    def update_source(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        source_id: str,
        command: UpdateDataSourceCommand,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> DataSourceMutationOutcome:
        scope = self._authorize_manage(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        current = self._require_source(scope=scope, source_id=source_id)
        expected_version = _require_etag(current, if_match)
        if current.source_type != command.binding.kind:
            raise problem(
                status=422,
                code="SOURCE_TYPE_IMMUTABLE",
                title="Data source type is immutable",
                detail="Changing a connector type requires a new data source.",
            )
        payload = {
            "command": command.model_dump(mode="json"),
            "if_match": if_match,
        }

        def action() -> DataSourceMutationRecord:
            now = self._clock()
            candidate = self._updated_source(
                current=current,
                binding=command.binding,
                configuration=command.configuration,
                name=command.name,
                source_format=command.source_format,
                source_format_version=command.source_format_version,
                upload_policy_code=command.upload_policy_code,
                now=now,
            )
            audit = self._audit_event(
                auth=auth,
                scope=scope,
                action="data_source.updated",
                resource_id=source_id,
                request_id=request_id,
                before=current,
                after=candidate,
                details={"reason_present": True},
            )
            try:
                saved = self._repository.save_source(
                    source=candidate,
                    expected_version=expected_version,
                    audit_event=audit,
                )
            except DataSourceDuplicateError as exc:
                raise _duplicate_source() from exc
            except DataSourceVersionConflict as exc:
                raise etag_mismatch(current.etag) from exc
            return DataSourceMutationRecord(source=saved)

        result = self._idempotency.execute(
            scope=project_id,
            key=f"data-source:update:{source_id}:{idempotency_key}",
            payload=payload,
            action=action,
        )
        return DataSourceMutationOutcome(record=result.value, replayed=result.replayed)

    def rotate_credential(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        source_id: str,
        command: RotateCredentialCommand,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> DataSourceMutationOutcome:
        scope = self._authorize_manage(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        current = self._require_source(scope=scope, source_id=source_id)
        expected_version = _require_etag(current, if_match)
        material = _credential_material(command.credential_input)
        assert material is not None
        payload = {
            "credential": _safe_credential_payload(command.credential_input),
            "if_match": if_match,
            "reason_present": True,
        }

        def action() -> DataSourceMutationRecord:
            now = self._clock()
            credential_version = str(int(current.credential_version) + 1)
            candidate = current.model_copy(
                update={
                    "credential": _credential_summary(
                        source_id=source_id,
                        version=credential_version,
                        material=material,
                        now=now,
                    ),
                    "credential_version": credential_version,
                    "etag": ResourceVersion(expected_version + 1).etag,
                    "updated_at": now,
                }
            )
            audit = self._audit_event(
                auth=auth,
                scope=scope,
                action="data_source.credential_rotated",
                resource_id=source_id,
                request_id=request_id,
                before=current,
                after=candidate,
                details={"credential_kind": material.kind, "reason_present": True},
            )
            try:
                saved = self._repository.rotate_credential(
                    source=candidate,
                    expected_version=expected_version,
                    credential=material,
                    credential_key=self._credential_key,
                    audit_event=audit,
                )
            except DataSourceVersionConflict as exc:
                raise etag_mismatch(current.etag) from exc
            return DataSourceMutationRecord(source=saved)

        result = self._idempotency.execute(
            scope=project_id,
            key=f"data-source:rotate-credential:{source_id}:{idempotency_key}",
            payload=payload,
            action=action,
        )
        return DataSourceMutationOutcome(record=result.value, replayed=result.replayed)

    def test_connection(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        source_id: str,
        command: TestConnectionCommand,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> DataSourceMutationOutcome:
        scope = self._authorize_manage(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        current = self._require_source(scope=scope, source_id=source_id)
        expected_version = _require_etag(current, if_match)
        if (
            command.observed_config_version != current.config_version
            or command.observed_credential_version != current.credential_version
        ):
            raise problem(
                status=409,
                code="OBSERVED_VERSION_MISMATCH",
                title="Observed configuration is stale",
                detail="Reload the data source before testing its connection.",
            )
        payload = {
            "observed_config_version": command.observed_config_version,
            "observed_credential_version": command.observed_credential_version,
            "if_match": if_match,
        }

        def action() -> DataSourceMutationRecord:
            now = self._clock()
            usable, error = self._credential_probe(
                scope=scope,
                source_id=source_id,
                credential_version=current.credential_version,
                request_id=request_id,
            )
            connection_test, async_job = _connection_test_jobs(
                source=current,
                request_id=request_id,
                idempotency_key=idempotency_key,
                usable=usable,
                error=error,
                now=now,
            )
            connectivity = current.connectivity.model_copy(
                update={
                    "state": "ONLINE" if usable else "AUTH_FAILED",
                    "last_check_state": "SUCCEEDED" if usable else "FAILED",
                    "observed_config_version": current.config_version,
                    "observed_credential_version": current.credential_version,
                    "checked_at": now,
                    "safe_error": None
                    if error is None
                    else DataSourceSafeError(code=error.code, message=error.message),
                }
            )
            candidate = current.model_copy(
                update={
                    "connectivity": connectivity,
                    "etag": ResourceVersion(expected_version + 1).etag,
                    "updated_at": now,
                }
            )
            audit = self._audit_event(
                auth=auth,
                scope=scope,
                action="data_source.connection_tested",
                resource_id=source_id,
                request_id=request_id,
                before=current,
                after=candidate,
                outcome="SUCCEEDED" if usable else "FAILED",
                details={"job_id": async_job.job_id, "status": async_job.status},
            )
            try:
                saved = self._repository.save_connection_test(
                    source=candidate,
                    expected_version=expected_version,
                    connection_test=connection_test,
                    async_job=async_job,
                    audit_event=audit,
                )
            except DataSourceVersionConflict as exc:
                raise etag_mismatch(current.etag) from exc
            return DataSourceMutationRecord(
                source=saved,
                connection_test=connection_test,
                async_job=async_job,
            )

        result = self._idempotency.execute(
            scope=project_id,
            key=f"data-source:test-connection:{source_id}:{idempotency_key}",
            payload=payload,
            action=action,
        )
        return DataSourceMutationOutcome(record=result.value, replayed=result.replayed)

    def set_administrative_state(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        source_id: str,
        desired_state: Literal["ENABLED", "DISABLED"],
        command: SourceStateCommand,
        if_match: str,
        idempotency_key: str,
        request_id: str,
    ) -> DataSourceMutationOutcome:
        scope = self._authorize_manage(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        current = self._require_source(scope=scope, source_id=source_id)
        expected_version = _require_etag(current, if_match)
        if command.expected_administrative_state != current.administrative_state:
            raise problem(
                status=409,
                code="ADMINISTRATIVE_STATE_MISMATCH",
                title="Data source state is stale",
                detail="Reload the data source before changing its state.",
            )
        if current.administrative_state == desired_state:
            raise problem(
                status=409,
                code="ADMINISTRATIVE_STATE_UNCHANGED",
                title="Data source is already in that state",
                detail="The requested state transition has already been applied.",
            )
        payload = {
            "desired_state": desired_state,
            "expected_administrative_state": command.expected_administrative_state,
            "if_match": if_match,
            "reason_present": True,
        }

        def action() -> DataSourceMutationRecord:
            now = self._clock()
            candidate = current.model_copy(
                update={
                    "administrative_state": desired_state,
                    "etag": ResourceVersion(expected_version + 1).etag,
                    "updated_at": now,
                }
            )
            audit = self._audit_event(
                auth=auth,
                scope=scope,
                action=(
                    "data_source.enabled" if desired_state == "ENABLED" else "data_source.disabled"
                ),
                resource_id=source_id,
                request_id=request_id,
                before=current,
                after=candidate,
                details={"reason_present": True},
            )
            try:
                saved = self._repository.save_source(
                    source=candidate,
                    expected_version=expected_version,
                    audit_event=audit,
                )
            except DataSourceVersionConflict as exc:
                raise etag_mismatch(current.etag) from exc
            return DataSourceMutationRecord(source=saved)

        result = self._idempotency.execute(
            scope=project_id,
            key=f"data-source:{desired_state.casefold()}:{source_id}:{idempotency_key}",
            payload=payload,
            action=action,
        )
        return DataSourceMutationOutcome(record=result.value, replayed=result.replayed)

    def get_connection_test_async_job(
        self,
        *,
        auth: AuthContext,
        organization_id: str | None,
        project_id: str | None,
        region_code: str | None,
        job_id: str,
    ) -> DataSourceAsyncJob | None:
        """Return a P02 job if it exists; leave other platform job families untouched."""

        if not organization_id or not project_id or not region_code:
            return None
        ScopeGuard.require(auth, project_id, region_code)
        scope = DataSourceScope(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        found = self._repository.get_connection_test_job(scope=scope, job_id=job_id)
        if found is None:
            return None
        self._authorize_read(
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )
        return found[1]

    def envelope(
        self,
        *,
        outcome: DataSourceMutationOutcome,
        auth: AuthContext,
        scope: DataSourceScope,
        request_id: str,
    ) -> DataSourceEnvelope:
        source = outcome.record.source
        if source is None:
            raise RuntimeError("data-source mutation did not produce a source")
        return DataSourceEnvelope(
            data=self._decorate(source, auth),
            scope=scope,
            request_id=request_id,
        )

    def connection_test_envelope(
        self,
        *,
        outcome: DataSourceMutationOutcome,
        scope: DataSourceScope,
        request_id: str,
    ) -> ConnectionTestJobEnvelope:
        if outcome.record.connection_test is None or outcome.record.async_job is None:
            raise RuntimeError("connection test mutation did not produce a job")
        return ConnectionTestJobEnvelope(
            data=outcome.record.connection_test,
            job=outcome.record.async_job,
            scope=scope,
            request_id=request_id,
        )

    def scope_for(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
        manage: bool = False,
    ) -> DataSourceScope:
        return (
            self._authorize_manage(
                auth=auth,
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
            )
            if manage
            else self._authorize_read(
                auth=auth,
                organization_id=organization_id,
                project_id=project_id,
                region_code=region_code,
            )
        )

    def _authorize_read(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
    ) -> DataSourceScope:
        ScopeGuard.require(auth, project_id, region_code, organization_id)
        auth.require_capability("ingest_source.read", project_id, organization_id)
        return self._scope(organization_id, project_id, region_code)

    def _authorize_manage(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        project_id: str,
        region_code: str,
    ) -> DataSourceScope:
        ScopeGuard.require(auth, project_id, region_code, organization_id)
        auth.require_capability("ingest_source.manage", project_id, organization_id)
        return self._scope(organization_id, project_id, region_code)

    def _scope(self, organization_id: str, project_id: str, region_code: str) -> DataSourceScope:
        if not self._repository.has_organization_project(
            organization_id=organization_id,
            project_id=project_id,
        ):
            raise problem(
                status=403,
                code="ORGANIZATION_SCOPE_DENIED",
                title="Organization access denied",
                detail="The selected project is not a member of this organization.",
            )
        return DataSourceScope(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        )

    def _require_source(self, *, scope: DataSourceScope, source_id: str) -> DataSourceDetail:
        source = self._repository.get_source(scope=scope, source_id=source_id)
        if source is None:
            raise problem(
                status=404,
                code="DATA_SOURCE_NOT_FOUND",
                title="Data source not found",
                detail="The requested data source does not exist in this scope.",
            )
        return source

    def _new_source(
        self,
        *,
        scope: DataSourceScope,
        source_id: str,
        command: CreateDataSourceCommand,
        credential: CredentialMaterial | None,
        now: datetime,
    ) -> DataSourceDetail:
        binding, configuration = self._resolve_connector(
            scope=scope,
            binding=command.binding,
            configuration=command.configuration,
        )
        credential_version = "1" if credential is not None else "0"
        credential_summary = (
            _credential_summary(
                source_id=source_id,
                version=credential_version,
                material=credential,
                now=now,
            )
            if credential is not None
            else CredentialSummary(
                kind="UNCONFIGURED",
                state="MISSING",
                credential_ref=None,
                masked_hint=None,
                version="0",
                updated_at=None,
                expires_at=None,
                rotation_due_at=None,
            )
        )
        return DataSourceDetail(
            id=source_id,
            scope=scope,
            name=command.name,
            source_type=command.source_type,
            source_format=command.source_format,
            source_format_version=command.source_format_version,
            adapter_version="p02-connector/v1",
            binding=binding,
            configuration=configuration,
            administrative_state="ENABLED",
            credential=credential_summary,
            connectivity={
                "state": "UNKNOWN",
                "last_check_state": "NOT_RUN",
                "observed_config_version": None,
                "observed_credential_version": None,
                "checked_at": None,
                "safe_error": None,
            },
            heartbeat=None,
            upload_policy=_upload_policy(command.upload_policy_code),
            last_upload=None,
            config_version="1",
            credential_version=credential_version,
            etag=ResourceVersion(1).etag,
            allowed_actions=(),
            blocked_reasons=(),
            created_at=now,
            updated_at=now,
        )

    def _updated_source(
        self,
        *,
        current: DataSourceDetail,
        binding: WritableDataSourceBinding,
        configuration: WritableConnectorConfiguration,
        name: str,
        source_format: str,
        source_format_version: str | None,
        upload_policy_code: str,
        now: datetime,
    ) -> DataSourceDetail:
        resolved_binding, resolved_configuration = self._resolve_connector(
            scope=current.scope,
            binding=binding,
            configuration=configuration,
        )
        expected_version = _version(current)
        return current.model_copy(
            update={
                "name": name,
                "source_format": source_format,
                "source_format_version": source_format_version,
                "binding": resolved_binding,
                "configuration": resolved_configuration,
                "upload_policy": _upload_policy(upload_policy_code),
                "config_version": str(int(current.config_version) + 1),
                "etag": ResourceVersion(expected_version + 1).etag,
                "updated_at": now,
            }
        )

    def _resolve_connector(
        self,
        *,
        scope: DataSourceScope,
        binding: WritableDataSourceBinding,
        configuration: WritableConnectorConfiguration,
    ) -> tuple[
        DataSourceRobotBinding | DataSourceEdgeAgentBinding | DataSourceOssImportBinding,
        DataSourceRobotConfiguration
        | DataSourceEdgeAgentConfiguration
        | DataSourceOssImportConfiguration,
    ]:
        if isinstance(binding, WritableRobotBinding) and isinstance(
            configuration, WritableRobotConfiguration
        ):
            display_name = self._repository.robot_display_name(
                organization_id=scope.organization_id,
                project_id=scope.project_id,
                region_code=scope.region_code,
                robot_id=binding.robot_id,
            )
            if display_name is None:
                raise problem(
                    status=422,
                    code="ROBOT_BINDING_NOT_FOUND",
                    title="Robot binding not found",
                    detail="The selected robot does not exist in this project and region.",
                )
            return (
                DataSourceRobotBinding(
                    robot_id=binding.robot_id,
                    display_name=display_name,
                ),
                DataSourceRobotConfiguration(
                    transport=configuration.transport,
                    endpoint_ref=f"robot-connector:{binding.robot_id}",
                    safe_endpoint_hint=f"已绑定机器人：{display_name}",
                    tls_profile_id=None,
                ),
            )
        if isinstance(binding, WritableEdgeAgentBinding) and isinstance(
            configuration, WritableEdgeAgentConfiguration
        ):
            if binding.agent_id != configuration.agent_id:
                raise problem(
                    status=422,
                    code="AGENT_BINDING_MISMATCH",
                    title="Agent binding does not match configuration",
                    detail="The edge-agent binding and configuration must name the same agent.",
                )
            return (
                DataSourceEdgeAgentBinding(agent_id=binding.agent_id, display_name=None),
                DataSourceEdgeAgentConfiguration(
                    agent_id=configuration.agent_id,
                    transport=configuration.transport,
                    heartbeat_policy_id=configuration.heartbeat_policy_id,
                ),
            )
        if isinstance(binding, WritableOssImportBinding) and isinstance(
            configuration, WritableOssImportConfiguration
        ):
            return (
                DataSourceOssImportBinding(
                    source_alias=binding.source_alias,
                    display_name=None,
                ),
                DataSourceOssImportConfiguration(
                    oss_account_alias=configuration.oss_account_alias,
                    bucket_alias=configuration.bucket_alias,
                    prefix_hint=configuration.prefix_hint,
                    role_ref=configuration.role_ref,
                    source_region_code=configuration.source_region_code,
                ),
            )
        raise problem(
            status=422,
            code="CONNECTOR_BINDING_MISMATCH",
            title="Connector binding does not match configuration",
            detail="Binding and configuration must use the same supported connector kind.",
        )

    def _credential_probe(
        self,
        *,
        scope: DataSourceScope,
        source_id: str,
        credential_version: str,
        request_id: str,
    ) -> tuple[bool, DataSourceJobError | None]:
        if credential_version == "0":
            return (
                False,
                DataSourceJobError(
                    code="CREDENTIAL_MISSING",
                    message="A credential must be configured before testing this connector.",
                    retryable=False,
                    request_id=request_id,
                ),
            )
        try:
            usable = self._repository.credential_is_usable(
                scope=scope,
                source_id=source_id,
                credential_version=credential_version,
                credential_key=self._credential_key,
            )
        except CredentialVaultUnavailable:
            return (
                False,
                DataSourceJobError(
                    code="CREDENTIAL_VAULT_UNAVAILABLE",
                    message="The credential vault could not complete the connection check.",
                    retryable=True,
                    request_id=request_id,
                ),
            )
        if usable:
            return True, None
        return (
            False,
            DataSourceJobError(
                code="CREDENTIAL_MISSING",
                message="A credential must be configured before testing this connector.",
                retryable=False,
                request_id=request_id,
            ),
        )

    def _decorate(self, source: DataSourceDetail, auth: AuthContext) -> DataSourceDetail:
        actions: list[str] = ["VIEW", "OPEN_UPLOADS"]
        if auth.has_capability(
            "ingest_source.manage", source.scope.project_id
        ) and source.source_type in {"ROBOT", "EDGE_AGENT", "OSS_IMPORT"}:
            actions.extend(["EDIT_CONFIGURATION", "ROTATE_CREDENTIAL", "TEST_CONNECTION"])
            actions.append("DISABLE" if source.administrative_state == "ENABLED" else "ENABLE")
        blocked = list(source.blocked_reasons)
        if source.credential.state == "MISSING" and not any(
            item.code == "CREDENTIAL_MISSING" for item in blocked
        ):
            blocked.append(
                DataSourceBlockedReason(
                    code="CREDENTIAL_MISSING",
                    message="Configure a credential before a successful connection test.",
                )
            )
        return source.model_copy(
            update={"allowed_actions": tuple(actions), "blocked_reasons": tuple(blocked)}
        )

    @staticmethod
    def _summary(source: DataSourceDetail) -> DataSourceSummary:
        return DataSourceSummary.model_validate(source.model_dump(exclude={"configuration"}))

    def _sort_sources(
        self, items: tuple[DataSourceDetail, ...], sort: SourceSort
    ) -> tuple[DataSourceDetail, ...]:
        if sort == "name:asc,id:asc":
            return tuple(sorted(items, key=lambda item: (item.name.casefold(), item.id)))
        if sort == "last_test_at:desc,id:desc":
            return tuple(
                sorted(
                    items,
                    key=lambda item: (
                        item.connectivity.checked_at or datetime.min.replace(tzinfo=timezone.utc),
                        item.id,
                    ),
                    reverse=True,
                )
            )
        return tuple(sorted(items, key=lambda item: (item.updated_at, item.id), reverse=True))

    def _page_sources(
        self,
        items: tuple[DataSourceDetail, ...],
        *,
        scope: DataSourceScope,
        query: str | None,
        source_types: tuple[str, ...],
        administrative_states: tuple[str, ...],
        connectivity_states: tuple[str, ...],
        credential_states: tuple[str, ...],
        sort: SourceSort,
        after: str | None,
        before: str | None,
        limit: int,
    ) -> tuple[tuple[DataSourceDetail, ...], PageInfo]:
        if limit not in {10, 20, 50}:
            raise problem(
                status=422,
                code="PAGE_LIMIT_INVALID",
                title="Invalid page size",
                detail="Page size must be one of 10, 20, or 50.",
            )
        cursor = after or before
        index = self._cursor_index(
            items,
            scope=scope,
            query=query,
            source_types=source_types,
            administrative_states=administrative_states,
            connectivity_states=connectivity_states,
            credential_states=credential_states,
            sort=sort,
            cursor=cursor,
        )
        if not items:
            return (), PageInfo(
                has_next_page=False,
                has_previous_page=False,
                start_cursor=None,
                end_cursor=None,
            )
        if after is not None:
            start = index + 1
            visible = items[start : start + limit]
            has_previous = start > 0
            has_next = start + len(visible) < len(items)
        elif before is not None:
            end = index
            start = max(0, end - limit)
            visible = items[start:end]
            has_previous = start > 0
            has_next = end < len(items)
        else:
            visible = items[:limit]
            has_previous = False
            has_next = len(visible) < len(items)
        if not visible:
            return (), PageInfo(
                has_next_page=False,
                has_previous_page=bool(after),
                start_cursor=None,
                end_cursor=None,
            )
        return (
            visible,
            PageInfo(
                has_next_page=has_next,
                has_previous_page=has_previous,
                start_cursor=self._encode_cursor(
                    scope=scope,
                    query=query,
                    source_types=source_types,
                    administrative_states=administrative_states,
                    connectivity_states=connectivity_states,
                    credential_states=credential_states,
                    sort=sort,
                    source_id=visible[0].id,
                ),
                end_cursor=self._encode_cursor(
                    scope=scope,
                    query=query,
                    source_types=source_types,
                    administrative_states=administrative_states,
                    connectivity_states=connectivity_states,
                    credential_states=credential_states,
                    sort=sort,
                    source_id=visible[-1].id,
                ),
            ),
        )

    def _cursor_index(
        self,
        items: tuple[DataSourceDetail, ...],
        *,
        scope: DataSourceScope,
        query: str | None,
        source_types: tuple[str, ...],
        administrative_states: tuple[str, ...],
        connectivity_states: tuple[str, ...],
        credential_states: tuple[str, ...],
        sort: SourceSort,
        cursor: str | None,
    ) -> int:
        if cursor is None:
            return -1
        payload = self._cursor.decode(cursor)
        expected = {
            "kind": "p02-data-sources",
            "organization_id": scope.organization_id,
            "project_id": scope.project_id,
            "region_code": scope.region_code,
            "query": query,
            "source_types": list(source_types),
            "administrative_states": list(administrative_states),
            "connectivity_states": list(connectivity_states),
            "credential_states": list(credential_states),
            "sort": sort,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid pagination cursor",
                detail="The cursor belongs to a different data-source query.",
            )
        source_id = payload.get("source_id")
        if not isinstance(source_id, str):
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid pagination cursor",
                detail="The cursor does not contain a valid source boundary.",
            )
        for index, source in enumerate(items):
            if source.id == source_id:
                return index
        raise problem(
            status=400,
            code="INVALID_CURSOR",
            title="Invalid pagination cursor",
            detail="The cursor boundary is no longer available.",
        )

    def _encode_cursor(
        self,
        *,
        scope: DataSourceScope,
        query: str | None,
        source_types: tuple[str, ...],
        administrative_states: tuple[str, ...],
        connectivity_states: tuple[str, ...],
        credential_states: tuple[str, ...],
        sort: SourceSort,
        source_id: str,
    ) -> str:
        return self._cursor.encode(
            {
                "kind": "p02-data-sources",
                "organization_id": scope.organization_id,
                "project_id": scope.project_id,
                "region_code": scope.region_code,
                "query": query,
                "source_types": list(source_types),
                "administrative_states": list(administrative_states),
                "connectivity_states": list(connectivity_states),
                "credential_states": list(credential_states),
                "sort": sort,
                "source_id": source_id,
            }
        )

    def _metrics(self, items: tuple[DataSourceDetail, ...], now: datetime) -> DataSourceMetrics:
        return DataSourceMetrics(
            total_count=str(len(items)),
            online_count=str(sum(item.connectivity.state == "ONLINE" for item in items)),
            verified_bytes_today="0",
            abnormal_count=str(
                sum(item.connectivity.state not in {"ONLINE", "UNKNOWN"} for item in items)
            ),
            as_of=now,
        )

    def _append_audit(
        self,
        *,
        auth: AuthContext,
        scope: DataSourceScope,
        action: str,
        resource_id: str,
        request_id: str,
        outcome: str,
    ) -> None:
        self._repository.append_audit(
            DataSourceAuditEvent(
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

    def _audit_event(
        self,
        *,
        auth: AuthContext,
        scope: DataSourceScope,
        action: str,
        resource_id: str,
        request_id: str,
        before: DataSourceDetail | None,
        after: DataSourceDetail,
        outcome: str = "SUCCEEDED",
        details: dict[str, object] | None = None,
    ) -> DataSourceAuditEvent:
        return DataSourceAuditEvent(
            project_id=scope.project_id,
            region_code=scope.region_code,
            actor_id=auth.subject_id,
            action=action,
            resource_id=resource_id,
            request_id=request_id,
            outcome=outcome,
            occurred_at=self._clock(),
            before_hash=None if before is None else _safe_hash(before),
            after_hash=_safe_hash(after),
            details=details,
        )


def _facets(items: tuple[DataSourceDetail, ...]) -> DataSourceFacets:
    def values(selector: Callable[[DataSourceDetail], str]) -> tuple[DataSourceFacet, ...]:
        counts = Counter(selector(item) for item in items)
        return tuple(
            DataSourceFacet(value=value, label=value, count=str(count))
            for value, count in sorted(counts.items())
        )

    robots = Counter(
        (item.binding.robot_id, item.binding.display_name)
        for item in items
        if isinstance(item.binding, DataSourceRobotBinding)
    )
    heartbeat = Counter(item.heartbeat.state for item in items if item.heartbeat is not None)
    return DataSourceFacets(
        source_types=values(lambda item: item.source_type),
        source_formats=values(lambda item: item.source_format),
        robots=tuple(
            DataSourceRobotFacet(
                id=robot_id,
                name=display_name or robot_id,
                count=str(count),
            )
            for (robot_id, display_name), count in sorted(robots.items())
        ),
        upload_policies=values(lambda item: item.upload_policy.code),
        administrative_states=values(lambda item: item.administrative_state),
        connectivity_states=values(lambda item: item.connectivity.state),
        credential_states=values(lambda item: item.credential.state),
        heartbeat_states=tuple(
            DataSourceFacet(value=value, label=value, count=str(count))
            for value, count in sorted(heartbeat.items())
        ),
    )


def _safe_hash(source: DataSourceDetail) -> str:
    return canonical_hash(source.model_dump(mode="json"))


def _version(source: DataSourceDetail) -> int:
    return ResourceVersion.from_etag(source.etag).value


def _require_etag(source: DataSourceDetail, if_match: str) -> int:
    version = ResourceVersion.from_etag(source.etag)
    version.require(if_match)
    return version.value


def _duplicate_source() -> Exception:
    return problem(
        status=409,
        code="DATA_SOURCE_CONFLICT",
        title="Data source already exists",
        detail="A data source with this name already exists in the selected scope.",
    )


def _create_source_id(*, scope: DataSourceScope, idempotency_key: str) -> str:
    seed = "/".join(
        (
            "p02-data-source",
            scope.organization_id,
            scope.project_id,
            scope.region_code,
            idempotency_key,
        )
    )
    return f"source-{uuid5(NAMESPACE_URL, seed)}"


def _credential_material(value: CredentialInput | None) -> CredentialMaterial | None:
    if value is None:
        return None
    if isinstance(value, TokenCredentialInput):
        secret = value.token.get_secret_value()
        return CredentialMaterial(
            kind="TOKEN",
            secret=secret,
            masked_hint=f"token …{_secret_suffix(secret)}",
        )
    if isinstance(value, BasicCredentialInput):
        secret = f"{value.username}\n{value.password.get_secret_value()}"
        return CredentialMaterial(
            kind="BASIC",
            secret=secret,
            masked_hint=f"{value.username} ••••",
        )
    if isinstance(value, DeviceCertificateCredentialInput):
        secret = (
            f"{value.certificate_pem.get_secret_value()}\n"
            f"{value.private_key_pem.get_secret_value()}"
        )
        return CredentialMaterial(
            kind="DEVICE_CERTIFICATE",
            secret=secret,
            masked_hint=f"certificate …{_secret_suffix(secret)}",
        )
    raise TypeError("unsupported credential input")


def _credential_summary(
    *, source_id: str, version: str, material: CredentialMaterial, now: datetime
) -> CredentialSummary:
    return CredentialSummary(
        kind=material.kind,
        state="CONFIGURED",
        credential_ref=f"credential-ref:{source_id}:v{version}",
        masked_hint=material.masked_hint,
        version=version,
        updated_at=now,
        expires_at=None,
        rotation_due_at=None,
    )


def _secret_suffix(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()[-4:].upper()


def _safe_credential_payload(value: CredentialInput) -> dict[str, str]:
    material = _credential_material(value)
    assert material is not None
    return {
        "kind": material.kind,
        "secret_sha256": hashlib.sha256(material.secret.encode()).hexdigest(),
    }


def _safe_create_payload(command: CreateDataSourceCommand) -> dict[str, object]:
    return {
        "name": command.name,
        "source_type": command.source_type,
        "source_format": command.source_format,
        "source_format_version": command.source_format_version,
        "binding": command.binding.model_dump(mode="json"),
        "configuration": command.configuration.model_dump(mode="json"),
        "upload_policy_code": command.upload_policy_code,
        "credential": (
            None
            if command.credential_input is None
            else _safe_credential_payload(command.credential_input)
        ),
    }


def _upload_policy(code: str) -> UploadPolicySummary:
    return UploadPolicySummary(
        code=code,
        label="标准上传" if code == "STANDARD" else code,
        max_object_size_bytes="53687091200",
    )


def _connection_test_jobs(
    *,
    source: DataSourceDetail,
    request_id: str,
    idempotency_key: str,
    usable: bool,
    error: DataSourceJobError | None,
    now: datetime,
) -> tuple[DataSourceConnectionTestJob, DataSourceAsyncJob]:
    seed = f"p02-connection-test/{source.scope.project_id}/{source.id}/{idempotency_key}"
    job_id = f"job-{uuid5(NAMESPACE_URL, seed)}"
    status = "SUCCEEDED" if usable else "FAILED"
    result_ref = {"connection_test": "credential-vault-verified"} if usable else None
    data = DataSourceConnectionTestJob(
        id=job_id,
        status=status,
        stage="CREDENTIAL_VAULT_CHECK",
        progress=DataSourceJobProgress(completed="1", total="1", unit="checks"),
        resource_ref=DataSourceResourceReference(resource_id=source.id),
        observed_versions=DataSourceObservedVersions(
            config_version=source.config_version,
            credential_version=source.credential_version,
        ),
        result_ref=result_ref,
        safe_error=error,
        etag=ResourceVersion(1).etag,
        allowed_actions=(),
        created_at=now,
        updated_at=now,
    )
    job = DataSourceAsyncJob(
        job_id=job_id,
        status=status,
        resource_id=source.id,
        progress={"completed": "1", "total": "1", "unit": "checks"},
        result_ref=result_ref,
        error=error,
        created_at=now,
        updated_at=now,
        resource_version="1",
    )
    return data, job

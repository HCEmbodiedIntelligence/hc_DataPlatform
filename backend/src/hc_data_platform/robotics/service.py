"""P15 robot-directory application service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID, uuid5

from hc_data_platform.core.errors import problem
from hc_data_platform.core.pagination import CursorCodec, PageInfo
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.idempotency import request_fingerprint
from hc_data_platform.security.scope import ScopeGuard

from .models import (
    COMPONENT_LIFECYCLE_TRANSITIONS,
    ChannelPage,
    ComponentLifecycleTransitionRequest,
    ComponentPage,
    CreateMaintenanceRecordRequest,
    CreateRobotComponentRequest,
    CreateRobotRequest,
    FramePage,
    GlobalRobotSearchPage,
    GlobalSearchRobotResult,
    MaintenanceRecordEnvelope,
    MaintenanceRecordPage,
    RobotBootstrapEnvelope,
    RobotComponentMutationEnvelope,
    RobotLifecycleTransitionRequest,
    RobotPage,
    RobotRecord,
    RobotScope,
    UpdateRobotComponentRequest,
    UpdateRobotRequest,
)
from .repository import InMemoryRoboticsRepository, RoboticsAuditEvent, RoboticsRepository

_ROBOT_ID_NAMESPACE = UUID("998e7a75-5bf5-4c79-ae53-3cac6828c21f")
_MAINTENANCE_ID_NAMESPACE = UUID("537c5c6a-b646-4c3c-80a2-060e9846eabc")
_COMPONENT_ID_NAMESPACE = UUID("fe9d06fa-23e5-4389-8b22-ecbd7fc4394a")
_LIFECYCLE_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"ACTIVE", "RETIRED"}),
    "ACTIVE": frozenset({"MAINTENANCE", "DISABLED", "RETIRED"}),
    "MAINTENANCE": frozenset({"ACTIVE", "DISABLED", "RETIRED"}),
    "DISABLED": frozenset({"ACTIVE", "RETIRED"}),
    "RETIRED": frozenset(),
}


def _stable_command_id(namespace: UUID, prefix: str, *parts: str) -> str:
    return f"{prefix}-{uuid5(namespace, chr(0).join(parts))}"


class RoboticsService:
    def __init__(
        self,
        repository: RoboticsRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        cursor_secret: str = "robotics-search-cursor-development-secret",
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._cursor = CursorCodec(cursor_secret)

    @classmethod
    def in_memory(cls) -> RoboticsService:
        return cls(InMemoryRoboticsRepository())

    def _scope(self, *, auth: AuthContext, project_id: str, region_code: str) -> RobotScope:
        ScopeGuard.require(auth, project_id, region_code)
        auth.require_capability("robot.read", project_id)
        return RobotScope(project_id=project_id, region_code=region_code)

    def _manage_scope(self, *, auth: AuthContext, project_id: str, region_code: str) -> RobotScope:
        ScopeGuard.require(auth, project_id, region_code)
        auth.require_capability("robot.manage", project_id)
        return RobotScope(project_id=project_id, region_code=region_code)

    def _page_info(self) -> PageInfo:
        return PageInfo(
            has_next_page=False, has_previous_page=False, start_cursor=None, end_cursor=None
        )

    def _audit(
        self,
        *,
        auth: AuthContext,
        scope: RobotScope,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
        details: dict[str, str | int | bool] | None = None,
    ) -> None:
        self._repository.append_audit(
            RoboticsAuditEvent(
                project_id=scope.project_id,
                region_code=scope.region_code,
                actor_id=auth.subject_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=request_id,
                occurred_at=self._clock(),
                details={} if details is None else details,
            )
        )

    def _write_audit(
        self,
        *,
        auth: AuthContext,
        scope: RobotScope,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
    ) -> RoboticsAuditEvent:
        return RoboticsAuditEvent(
            project_id=scope.project_id,
            region_code=scope.region_code,
            actor_id=auth.subject_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            occurred_at=self._clock(),
        )

    @staticmethod
    def _write_problem(exc: Exception) -> None:
        if isinstance(exc, KeyError):
            if "component" in str(exc).lower():
                raise problem(
                    status=404,
                    code="ROBOT_COMPONENT_NOT_FOUND",
                    title="Robot component not found",
                    detail="The requested component does not exist in this robot topology.",
                ) from exc
            raise problem(
                status=404,
                code="ROBOT_NOT_FOUND",
                title="Robot not found",
                detail="The requested robot does not exist in this scope.",
            ) from exc
        if isinstance(exc, ValueError):
            message = str(exc)
            if "idempotency" in message:
                raise problem(
                    status=409,
                    code="IDEMPOTENCY_KEY_REUSED",
                    title="Idempotency key reused",
                    detail="The same command key was reused with a different request body.",
                ) from exc
            if "etag" in message:
                raise problem(
                    status=412,
                    code="ROBOT_ETAG_MISMATCH",
                    title="Robot changed",
                    detail="Reload the robot before submitting another change.",
                ) from exc
            if "already" in message:
                raise problem(
                    status=409,
                    code="ROBOT_STATE_CONFLICT",
                    title="Robot state conflict",
                    detail=(
                        "The requested robot state is already present or conflicts with an "
                        "existing record."
                    ),
                ) from exc
            if "component" in message:
                raise problem(
                    status=409,
                    code="ROBOT_COMPONENT_TOPOLOGY_CONFLICT",
                    title="Robot component topology conflict",
                    detail="Reload the robot topology before changing this component.",
                ) from exc
        if getattr(exc, "sqlstate", None) == "23505":
            raise problem(
                status=409,
                code="ROBOT_SERIAL_CONFLICT",
                title="Robot serial number already exists",
                detail="A robot in this scope already uses the supplied serial number.",
            ) from exc
        raise exc

    def list_robots(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        query: str | None,
        lifecycle_status: str | None,
        connectivity_state: str | None,
        request_id: str,
    ) -> RobotPage:
        scope = self._scope(auth=auth, project_id=project_id, region_code=region_code)
        items = self._repository.list_robots(
            project_id=project_id,
            region_code=region_code,
            query=query,
            lifecycle_status=lifecycle_status,
            connectivity_state=connectivity_state,
        )
        self._audit(
            auth=auth,
            scope=scope,
            action="robot.listed",
            resource_type="ROBOT",
            resource_id=project_id,
            request_id=request_id,
        )
        return RobotPage(
            items=items,
            page_info=self._page_info(),
            snapshot_at=self._clock(),
            scope=scope,
            request_id=request_id,
        )

    def search_robots(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        query: str,
        cursor: str | None,
        limit: int,
        request_id: str,
    ) -> GlobalRobotSearchPage:
        """Return a bounded shell-search page without an N+1 directory fan-out."""

        scope = self._scope(auth=auth, project_id=project_id, region_code=region_code)
        normalized_query = query.strip()
        if not normalized_query:
            raise problem(
                status=422,
                code="SEARCH_QUERY_INVALID",
                title="Search query is invalid",
                detail="Enter at least one non-whitespace search character.",
            )
        if not 1 <= limit <= 20:
            raise problem(
                status=422,
                code="SEARCH_LIMIT_INVALID",
                title="Search page limit is invalid",
                detail="Request between one and twenty results per page.",
            )
        after_display_name, after_robot_id = self._decode_search_cursor(
            cursor=cursor,
            auth=auth,
            project_id=project_id,
            region_code=region_code,
            query=normalized_query,
        )
        raw_items = self._repository.search_robots(
            project_id=project_id,
            region_code=region_code,
            query=normalized_query,
            after_display_name=after_display_name,
            after_robot_id=after_robot_id,
            limit=limit + 1,
        )
        visible = raw_items[:limit]
        has_next = len(raw_items) > limit
        end_cursor = (
            self._encode_search_cursor(
                auth=auth,
                project_id=project_id,
                region_code=region_code,
                query=normalized_query,
                robot=visible[-1],
            )
            if has_next and visible
            else None
        )
        self._audit(
            auth=auth,
            scope=scope,
            action="robot.search.executed",
            resource_type="ROBOT_SEARCH",
            resource_id=project_id,
            request_id=request_id,
            details={
                "entity_type": "ROBOT",
                "query_length": len(normalized_query),
                "result_count": len(visible),
                "has_next_page": has_next,
            },
        )
        return GlobalRobotSearchPage(
            query=normalized_query,
            items=tuple(GlobalSearchRobotResult(robot=item) for item in visible),
            page_info=PageInfo(
                has_next_page=has_next,
                has_previous_page=cursor is not None,
                start_cursor=None,
                end_cursor=end_cursor,
            ),
            snapshot_at=self._clock(),
            scope=scope,
            request_id=request_id,
        )

    def _decode_search_cursor(
        self,
        *,
        cursor: str | None,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        query: str,
    ) -> tuple[str | None, str | None]:
        if cursor is None:
            return None, None
        payload = self._cursor.decode(cursor)
        expected = {
            "kind": "robot-search.v1",
            "subject_id": auth.subject_id,
            "capability_revision": auth.capability_revision,
            "project_id": project_id,
            "region_code": region_code,
            "query": query,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid pagination cursor",
                detail="The cursor does not match this search scope or query.",
            )
        display_name = payload.get("after_display_name")
        robot_id = payload.get("after_robot_id")
        if (
            not isinstance(display_name, str)
            or not display_name
            or not isinstance(robot_id, str)
            or not robot_id
        ):
            raise problem(
                status=400,
                code="INVALID_CURSOR",
                title="Invalid pagination cursor",
                detail="The cursor does not contain a valid search position.",
            )
        return display_name, robot_id

    def _encode_search_cursor(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        query: str,
        robot: RobotRecord,
    ) -> str:
        return self._cursor.encode(
            {
                "kind": "robot-search.v1",
                "subject_id": auth.subject_id,
                "capability_revision": auth.capability_revision,
                "project_id": project_id,
                "region_code": region_code,
                "query": query,
                "after_display_name": robot.display_name,
                "after_robot_id": robot.id,
            }
        )

    def robot_bootstrap(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        robot_id: str,
        request_id: str,
    ) -> RobotBootstrapEnvelope:
        scope = self._scope(auth=auth, project_id=project_id, region_code=region_code)
        data = self._repository.get_robot(
            project_id=project_id, region_code=region_code, robot_id=robot_id
        )
        if data is None:
            raise problem(
                status=404,
                code="ROBOT_NOT_FOUND",
                title="Robot not found",
                detail="The requested robot does not exist in this scope.",
            )
        # Actions are an authorization projection for the current identity, not
        # mutable robot metadata.  The write routes still enforce robot.manage
        # so a stale browser snapshot cannot authorize a command.
        data = data.model_copy(
            update={
                "allowed_actions": (
                    ("VIEW", "EDIT", "TRANSITION")
                    if auth.has_capability("robot.manage", project_id)
                    else ("VIEW",)
                )
            }
        )
        self._audit(
            auth=auth,
            scope=scope,
            action="robot.bootstrap.read",
            resource_type="ROBOT",
            resource_id=robot_id,
            request_id=request_id,
        )
        return RobotBootstrapEnvelope(data=data, scope=scope, request_id=request_id)

    def create_robot(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        command: CreateRobotRequest,
        idempotency_key: str,
        request_id: str,
    ) -> RobotBootstrapEnvelope:
        scope = self._manage_scope(auth=auth, project_id=project_id, region_code=region_code)
        robot_id = _stable_command_id(
            _ROBOT_ID_NAMESPACE,
            "robot",
            project_id,
            region_code,
            idempotency_key,
        )
        audit = self._write_audit(
            auth=auth,
            scope=scope,
            action="robot.created",
            resource_type="ROBOT",
            resource_id=robot_id,
            request_id=request_id,
        )
        try:
            data = self._repository.create_robot(
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                command=command,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint(command.model_dump(mode="json")),
                audit=audit,
            )
        except Exception as exc:
            self._write_problem(exc)
            raise AssertionError("unreachable") from exc
        return RobotBootstrapEnvelope(data=data, scope=scope, request_id=request_id)

    def update_robot(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        robot_id: str,
        expected_etag: str,
        command: UpdateRobotRequest,
        idempotency_key: str,
        request_id: str,
    ) -> RobotBootstrapEnvelope:
        scope = self._manage_scope(auth=auth, project_id=project_id, region_code=region_code)
        audit = self._write_audit(
            auth=auth,
            scope=scope,
            action="robot.updated",
            resource_type="ROBOT",
            resource_id=robot_id,
            request_id=request_id,
        )
        try:
            data = self._repository.update_robot(
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                expected_etag=expected_etag,
                command=command,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint(command.model_dump(mode="json")),
                audit=audit,
            )
        except Exception as exc:
            self._write_problem(exc)
            raise AssertionError("unreachable") from exc
        return RobotBootstrapEnvelope(data=data, scope=scope, request_id=request_id)

    def transition_robot(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        robot_id: str,
        expected_etag: str,
        command: RobotLifecycleTransitionRequest,
        idempotency_key: str,
        request_id: str,
    ) -> RobotBootstrapEnvelope:
        scope = self._manage_scope(auth=auth, project_id=project_id, region_code=region_code)
        current = self._repository.get_robot(
            project_id=project_id, region_code=region_code, robot_id=robot_id
        )
        if current is None:
            raise problem(
                status=404,
                code="ROBOT_NOT_FOUND",
                title="Robot not found",
                detail="The requested robot does not exist in this scope.",
            )
        if command.lifecycle_status not in _LIFECYCLE_TRANSITIONS.get(
            current.robot.lifecycle_status, frozenset()
        ):
            raise problem(
                status=409,
                code="ROBOT_LIFECYCLE_TRANSITION_INVALID",
                title="Robot lifecycle transition is invalid",
                detail="The requested lifecycle transition is not allowed from the current state.",
            )
        audit = self._write_audit(
            auth=auth,
            scope=scope,
            action="robot.lifecycle.transitioned",
            resource_type="ROBOT",
            resource_id=robot_id,
            request_id=request_id,
        )
        try:
            data = self._repository.transition_robot(
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                expected_etag=expected_etag,
                command=command,
                maintenance_record_id=_stable_command_id(
                    _MAINTENANCE_ID_NAMESPACE,
                    "maintenance",
                    robot_id,
                    "transition",
                    idempotency_key,
                ),
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint(command.model_dump(mode="json")),
                audit=audit,
            )
        except Exception as exc:
            self._write_problem(exc)
            raise AssertionError("unreachable") from exc
        return RobotBootstrapEnvelope(data=data, scope=scope, request_id=request_id)

    def create_maintenance_record(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        robot_id: str,
        command: CreateMaintenanceRecordRequest,
        idempotency_key: str,
        request_id: str,
    ) -> MaintenanceRecordEnvelope:
        scope = self._manage_scope(auth=auth, project_id=project_id, region_code=region_code)
        audit = self._write_audit(
            auth=auth,
            scope=scope,
            action="robot.maintenance.recorded",
            resource_type="ROBOT",
            resource_id=robot_id,
            request_id=request_id,
        )
        try:
            data = self._repository.create_maintenance_record(
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                record_id=_stable_command_id(
                    _MAINTENANCE_ID_NAMESPACE,
                    "maintenance",
                    robot_id,
                    "maintenance",
                    idempotency_key,
                ),
                command=command,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint(command.model_dump(mode="json")),
                audit=audit,
            )
        except Exception as exc:
            self._write_problem(exc)
            raise AssertionError("unreachable") from exc
        return MaintenanceRecordEnvelope(data=data, scope=scope, request_id=request_id)

    def maintenance_records(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        robot_id: str,
        request_id: str,
    ) -> MaintenanceRecordPage:
        scope = self._scope(auth=auth, project_id=project_id, region_code=region_code)
        if (
            self._repository.get_robot(
                project_id=project_id, region_code=region_code, robot_id=robot_id
            )
            is None
        ):
            raise problem(
                status=404,
                code="ROBOT_NOT_FOUND",
                title="Robot not found",
                detail="The requested robot does not exist in this scope.",
            )
        items = self._repository.list_maintenance_records(
            project_id=project_id, region_code=region_code, robot_id=robot_id
        )
        self._audit(
            auth=auth,
            scope=scope,
            action="robot.maintenance.listed",
            resource_type="ROBOT",
            resource_id=robot_id,
            request_id=request_id,
        )
        return MaintenanceRecordPage(
            items=items,
            page_info=self._page_info(),
            snapshot_at=self._clock(),
            scope=scope,
            request_id=request_id,
        )

    def create_component(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        robot_id: str,
        expected_robot_etag: str,
        command: CreateRobotComponentRequest,
        idempotency_key: str,
        request_id: str,
    ) -> RobotComponentMutationEnvelope:
        scope = self._manage_scope(auth=auth, project_id=project_id, region_code=region_code)
        component_id = _stable_command_id(
            _COMPONENT_ID_NAMESPACE,
            "component",
            project_id,
            region_code,
            robot_id,
            idempotency_key,
        )
        audit = self._write_audit(
            auth=auth,
            scope=scope,
            action="robot.component.created",
            resource_type="ROBOT_COMPONENT",
            resource_id=component_id,
            request_id=request_id,
        )
        try:
            data = self._repository.create_component(
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                component_id=component_id,
                expected_robot_etag=expected_robot_etag,
                command=command,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint(command.model_dump(mode="json")),
                audit=audit,
            )
        except Exception as exc:
            self._write_problem(exc)
            raise AssertionError("unreachable") from exc
        return RobotComponentMutationEnvelope(data=data, scope=scope, request_id=request_id)

    def update_component(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        component_id: str,
        expected_robot_etag: str,
        command: UpdateRobotComponentRequest,
        idempotency_key: str,
        request_id: str,
    ) -> RobotComponentMutationEnvelope:
        scope = self._manage_scope(auth=auth, project_id=project_id, region_code=region_code)
        audit = self._write_audit(
            auth=auth,
            scope=scope,
            action="robot.component.updated",
            resource_type="ROBOT_COMPONENT",
            resource_id=component_id,
            request_id=request_id,
        )
        try:
            data = self._repository.update_component(
                project_id=project_id,
                region_code=region_code,
                component_id=component_id,
                expected_robot_etag=expected_robot_etag,
                command=command,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint(command.model_dump(mode="json")),
                audit=audit,
            )
        except Exception as exc:
            self._write_problem(exc)
            raise AssertionError("unreachable") from exc
        return RobotComponentMutationEnvelope(data=data, scope=scope, request_id=request_id)

    def transition_component(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        component_id: str,
        expected_robot_etag: str,
        command: ComponentLifecycleTransitionRequest,
        idempotency_key: str,
        request_id: str,
    ) -> RobotComponentMutationEnvelope:
        scope = self._manage_scope(auth=auth, project_id=project_id, region_code=region_code)
        current = self._repository.get_component(
            project_id=project_id, region_code=region_code, component_id=component_id
        )
        if current is None:
            raise problem(
                status=404,
                code="ROBOT_COMPONENT_NOT_FOUND",
                title="Robot component not found",
                detail="The requested component does not exist in this scope.",
            )
        if command.lifecycle_status not in COMPONENT_LIFECYCLE_TRANSITIONS.get(
            current.lifecycle_status, frozenset()
        ):
            raise problem(
                status=409,
                code="ROBOT_COMPONENT_LIFECYCLE_TRANSITION_INVALID",
                title="Robot component lifecycle transition is invalid",
                detail="The requested lifecycle transition is not allowed from the current state.",
            )
        audit = self._write_audit(
            auth=auth,
            scope=scope,
            action="robot.component.lifecycle.transitioned",
            resource_type="ROBOT_COMPONENT",
            resource_id=component_id,
            request_id=request_id,
        )
        try:
            data = self._repository.transition_component(
                project_id=project_id,
                region_code=region_code,
                component_id=component_id,
                expected_robot_etag=expected_robot_etag,
                command=command,
                maintenance_record_id=_stable_command_id(
                    _MAINTENANCE_ID_NAMESPACE,
                    "maintenance",
                    component_id,
                    "component-transition",
                    idempotency_key,
                ),
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint(command.model_dump(mode="json")),
                audit=audit,
            )
        except Exception as exc:
            self._write_problem(exc)
            raise AssertionError("unreachable") from exc
        return RobotComponentMutationEnvelope(data=data, scope=scope, request_id=request_id)

    def components(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        robot_id: str,
        request_id: str,
    ) -> ComponentPage:
        scope = self._scope(auth=auth, project_id=project_id, region_code=region_code)
        robot = self._repository.get_robot(
            project_id=project_id, region_code=region_code, robot_id=robot_id
        )
        if robot is None:
            raise problem(
                status=404,
                code="ROBOT_NOT_FOUND",
                title="Robot not found",
                detail="The requested robot does not exist in this scope.",
            )
        items = self._repository.list_components(
            project_id=project_id, region_code=region_code, robot_id=robot_id
        )
        self._audit(
            auth=auth,
            scope=scope,
            action="robot.components.listed",
            resource_type="ROBOT",
            resource_id=robot_id,
            request_id=request_id,
        )
        return ComponentPage(
            items=items,
            page_info=self._page_info(),
            snapshot_at=self._clock(),
            topology_revision=robot.topology_revision,
            scope=scope,
            request_id=request_id,
        )

    def frames(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        component_id: str,
        request_id: str,
    ) -> FramePage:
        scope = self._scope(auth=auth, project_id=project_id, region_code=region_code)
        if (
            self._repository.get_component(
                project_id=project_id, region_code=region_code, component_id=component_id
            )
            is None
        ):
            raise problem(
                status=404,
                code="ROBOT_COMPONENT_NOT_FOUND",
                title="Robot component not found",
                detail="The requested component does not exist in this scope.",
            )
        items = self._repository.list_frames(
            project_id=project_id, region_code=region_code, component_id=component_id
        )
        self._audit(
            auth=auth,
            scope=scope,
            action="robot.component_frames.listed",
            resource_type="ROBOT_COMPONENT",
            resource_id=component_id,
            request_id=request_id,
        )
        return FramePage(
            items=items,
            page_info=self._page_info(),
            snapshot_at=self._clock(),
            scope=scope,
            request_id=request_id,
        )

    def channels(
        self,
        *,
        auth: AuthContext,
        project_id: str,
        region_code: str,
        component_id: str,
        request_id: str,
    ) -> ChannelPage:
        scope = self._scope(auth=auth, project_id=project_id, region_code=region_code)
        if (
            self._repository.get_component(
                project_id=project_id, region_code=region_code, component_id=component_id
            )
            is None
        ):
            raise problem(
                status=404,
                code="ROBOT_COMPONENT_NOT_FOUND",
                title="Robot component not found",
                detail="The requested component does not exist in this scope.",
            )
        items = self._repository.list_channels(
            project_id=project_id, region_code=region_code, component_id=component_id
        )
        self._audit(
            auth=auth,
            scope=scope,
            action="robot.component_channels.listed",
            resource_type="ROBOT_COMPONENT",
            resource_id=component_id,
            request_id=request_id,
        )
        return ChannelPage(
            items=items,
            page_info=self._page_info(),
            snapshot_at=self._clock(),
            scope=scope,
            request_id=request_id,
        )

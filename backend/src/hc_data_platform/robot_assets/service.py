from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from hc_data_platform.core.errors import problem
from hc_data_platform.core.pagination import PageInfo
from hc_data_platform.robotics.models import (
    CreateRobotRequest,
    RobotLifecycleTransitionRequest,
    UpdateRobotRequest,
)
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.idempotency import idempotency_conflict, request_fingerprint

from .models import (
    BindOrganizationRobotModelRequest,
    OrganizationRobotBootstrapEnvelope,
    OrganizationRobotModelBinding,
    OrganizationRobotModelBindingPage,
    OrganizationRobotPage,
    OrganizationRobotScope,
)
from .repository import (
    InMemoryOrganizationRobotAssetRepository,
    OrganizationRobotAssetRepository,
)


class OrganizationRobotAssetService:
    def __init__(
        self,
        repository: OrganizationRobotAssetRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._clock = clock

    @classmethod
    def in_memory(cls) -> OrganizationRobotAssetService:
        return cls(InMemoryOrganizationRobotAssetRepository())

    def _authorize(self, auth: AuthContext, organization_id: str, capability: str) -> None:
        if not organization_id:
            raise problem(
                status=422,
                code="ORGANIZATION_SCOPE_REQUIRED",
                title="Organization scope is required",
                detail="An organization is required for robot asset operations.",
            )
        if not auth.is_platform_admin and organization_id not in auth.organization_ids:
            raise problem(
                status=403,
                code="ORGANIZATION_SCOPE_DENIED",
                title="Organization access denied",
                detail="The current identity does not belong to this organization.",
            )
        auth.require_organization_capability(organization_id, capability)
        if auth.is_platform_admin and not self._repository.organization_exists(organization_id):
            raise problem(
                status=404,
                code="ORGANIZATION_NOT_FOUND",
                title="Organization not found",
                detail="The requested organization does not exist.",
            )

    def list_robots(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        query: str | None,
        lifecycle_status: str | None,
        connectivity_state: str | None,
        configured_only: bool,
        request_id: str,
    ) -> OrganizationRobotPage:
        self._authorize(auth, organization_id, "robot.read")
        items = self._repository.list_robots(
            organization_id=organization_id,
            query=query,
            lifecycle_status=lifecycle_status,
            connectivity_state=connectivity_state,
            configured_only=configured_only,
        )
        return OrganizationRobotPage(
            items=items,
            page_info=PageInfo(
                has_next_page=False,
                has_previous_page=False,
                start_cursor=None,
                end_cursor=None,
            ),
            snapshot_at=self._clock(),
            scope=OrganizationRobotScope(organization_id=organization_id),
            request_id=request_id,
        )

    def get_robot(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        robot_id: str,
        request_id: str,
    ) -> OrganizationRobotBootstrapEnvelope:
        self._authorize(auth, organization_id, "robot.read")
        item = self._repository.get_robot(organization_id=organization_id, robot_id=robot_id)
        if item is None:
            raise problem(
                status=404,
                code="ROBOT_NOT_FOUND",
                title="Robot not found",
                detail="The requested robot does not exist in this organization.",
            )
        return OrganizationRobotBootstrapEnvelope(
            data=item,
            scope=OrganizationRobotScope(organization_id=organization_id),
            request_id=request_id,
        )

    def list_model_bindings(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        version_id: str,
        request_id: str,
    ) -> OrganizationRobotModelBindingPage:
        self._authorize(auth, organization_id, "robot.read")
        items = self._repository.list_model_bindings(
            organization_id=organization_id, version_id=version_id
        )
        return OrganizationRobotModelBindingPage(
            items=items,
            scope=OrganizationRobotScope(organization_id=organization_id),
            request_id=request_id,
        )

    def create_robot(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        command: CreateRobotRequest,
        idempotency_key: str,
        request_id: str,
    ) -> OrganizationRobotBootstrapEnvelope:
        self._authorize(auth, organization_id, "robot.manage")
        robot_id = f"robot_{uuid5(NAMESPACE_URL, f'{organization_id}:{idempotency_key}').hex}"
        fingerprint = request_fingerprint(command.model_dump(mode="json"))
        try:
            item = self._repository.create_robot(
                organization_id=organization_id,
                robot_id=robot_id,
                command=command,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                actor_id=auth.subject_id,
                request_id=request_id,
                occurred_at=self._clock(),
            )
        except ValueError as exc:
            if "idempotency" in str(exc):
                raise idempotency_conflict() from exc
            raise problem(
                status=409,
                code="ROBOT_SERIAL_CONFLICT",
                title="Robot serial number already exists",
                detail="A robot in this organization already uses the supplied serial number.",
            ) from exc
        return OrganizationRobotBootstrapEnvelope(
            data=item,
            scope=OrganizationRobotScope(organization_id=organization_id),
            request_id=request_id,
        )

    def delete_robot(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        robot_id: str,
        request_id: str,
    ) -> None:
        self._authorize(auth, organization_id, "robot.manage")
        try:
            self._repository.delete_robot(
                organization_id=organization_id,
                robot_id=robot_id,
                actor_id=auth.subject_id,
                request_id=request_id,
                occurred_at=self._clock(),
            )
        except ValueError as exc:
            raise problem(
                status=409,
                code="ROBOT_IN_USE",
                title="Robot instance is in use",
                detail=(
                    "Remove the robot's data source, upload identity, project assignment, "
                    "topology, calibration, and raw-data references before deleting it."
                ),
            ) from exc

    def update_robot(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        robot_id: str,
        command: UpdateRobotRequest,
        expected_etag: str,
        request_id: str,
    ) -> OrganizationRobotBootstrapEnvelope:
        self._authorize(auth, organization_id, "robot.manage")
        try:
            item = self._repository.update_robot(
                organization_id=organization_id,
                robot_id=robot_id,
                command=command,
                expected_etag=expected_etag,
                actor_id=auth.subject_id,
                request_id=request_id,
                occurred_at=self._clock(),
            )
        except KeyError as exc:
            raise problem(
                status=404,
                code="ROBOT_NOT_FOUND",
                title="Robot not found",
                detail="The requested robot does not exist in this organization.",
            ) from exc
        except ValueError as exc:
            raise problem(
                status=412,
                code="ROBOT_ETAG_CONFLICT",
                title="Robot version changed",
                detail="Reload the robot and retry the update.",
            ) from exc
        return OrganizationRobotBootstrapEnvelope(
            data=item,
            scope=OrganizationRobotScope(organization_id=organization_id),
            request_id=request_id,
        )

    def transition_robot(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        robot_id: str,
        command: RobotLifecycleTransitionRequest,
        expected_etag: str,
        request_id: str,
    ) -> OrganizationRobotBootstrapEnvelope:
        self._authorize(auth, organization_id, "robot.manage")
        try:
            item = self._repository.transition_robot(
                organization_id=organization_id,
                robot_id=robot_id,
                command=command,
                expected_etag=expected_etag,
                actor_id=auth.subject_id,
                request_id=request_id,
                occurred_at=self._clock(),
            )
        except KeyError as exc:
            raise problem(
                status=404,
                code="ROBOT_NOT_FOUND",
                title="Robot not found",
                detail="The requested robot does not exist in this organization.",
            ) from exc
        except ValueError as exc:
            detail = str(exc)
            if "etag" in detail:
                raise problem(
                    status=412,
                    code="ROBOT_ETAG_CONFLICT",
                    title="Robot version changed",
                    detail="Reload the robot and retry the lifecycle change.",
                ) from exc
            raise problem(
                status=409,
                code="ROBOT_LIFECYCLE_CONFLICT",
                title="Robot lifecycle transition is not allowed",
                detail="The requested lifecycle state cannot follow the current state.",
            ) from exc
        return OrganizationRobotBootstrapEnvelope(
            data=item,
            scope=OrganizationRobotScope(organization_id=organization_id),
            request_id=request_id,
        )

    def bind_model(
        self,
        *,
        auth: AuthContext,
        organization_id: str,
        robot_id: str,
        command: BindOrganizationRobotModelRequest,
        idempotency_key: str,
        request_id: str,
    ) -> OrganizationRobotModelBinding:
        self._authorize(auth, organization_id, "robot.manage")
        auth.require_organization_capability(organization_id, "robot_model.manage")
        try:
            return self._repository.bind_model(
                organization_id=organization_id,
                robot_id=robot_id,
                version_id=command.version_id,
                expected_robot_etag=command.robot_etag,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint(command.model_dump(mode="json")),
                actor_id=auth.subject_id,
                request_id=request_id,
                occurred_at=self._clock(),
            )
        except KeyError as exc:
            raise problem(
                status=404,
                code="ROBOT_OR_MODEL_NOT_FOUND",
                title="Robot or model not found",
                detail="The robot and published model version must exist in this organization.",
            ) from exc
        except ValueError as exc:
            message = str(exc)
            if "idempotency" in message:
                raise idempotency_conflict() from exc
            if "etag" in message:
                raise problem(
                    status=412,
                    code="ROBOT_ETAG_MISMATCH",
                    title="Robot changed",
                    detail="Reload the robot before binding its model.",
                ) from exc
            raise problem(
                status=409,
                code="ROBOT_MODEL_VERSION_NOT_PUBLISHED",
                title="Robot model version is not published",
                detail="Only a published model version can be bound to a robot.",
            ) from exc

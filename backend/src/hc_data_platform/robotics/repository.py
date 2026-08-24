"""P15 robot-directory persistence ports and PostgreSQL implementation."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Any, Protocol, cast
from uuid import uuid4

from .models import (
    COMPONENT_LIFECYCLE_TRANSITIONS,
    ChannelReference,
    ComponentLifecycleTransitionRequest,
    Connectivity,
    CreateMaintenanceRecordRequest,
    CreateRobotComponentRequest,
    CreateRobotRequest,
    EffectiveModelBinding,
    FrameReference,
    MaintenanceRecord,
    RobotBootstrap,
    RobotComponent,
    RobotComponentMutation,
    RobotLifecycleTransitionRequest,
    RobotRecord,
    UpdateRobotComponentRequest,
    UpdateRobotRequest,
)


@dataclass(frozen=True, slots=True)
class RoboticsAuditEvent:
    project_id: str
    region_code: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    request_id: str
    occurred_at: datetime
    details: Mapping[str, str | int | bool] = field(default_factory=dict)


class RoboticsRepository(Protocol):
    def list_robots(
        self,
        *,
        project_id: str,
        region_code: str,
        query: str | None,
        lifecycle_status: str | None,
        connectivity_state: str | None,
    ) -> tuple[RobotRecord, ...]: ...

    def search_robots(
        self,
        *,
        project_id: str,
        region_code: str,
        query: str,
        after_display_name: str | None,
        after_robot_id: str | None,
        limit: int,
    ) -> tuple[RobotRecord, ...]: ...

    def get_robot(
        self, *, project_id: str, region_code: str, robot_id: str
    ) -> RobotBootstrap | None: ...

    def create_robot(
        self,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        command: CreateRobotRequest,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotBootstrap: ...

    def update_robot(
        self,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        expected_etag: str,
        command: UpdateRobotRequest,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotBootstrap: ...

    def transition_robot(
        self,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        expected_etag: str,
        command: RobotLifecycleTransitionRequest,
        maintenance_record_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotBootstrap: ...

    def create_maintenance_record(
        self,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        record_id: str,
        command: CreateMaintenanceRecordRequest,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> MaintenanceRecord: ...

    def list_maintenance_records(
        self, *, project_id: str, region_code: str, robot_id: str
    ) -> tuple[MaintenanceRecord, ...]: ...

    def create_component(
        self,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        component_id: str,
        expected_robot_etag: str,
        command: CreateRobotComponentRequest,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotComponentMutation: ...

    def update_component(
        self,
        *,
        project_id: str,
        region_code: str,
        component_id: str,
        expected_robot_etag: str,
        command: UpdateRobotComponentRequest,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotComponentMutation: ...

    def transition_component(
        self,
        *,
        project_id: str,
        region_code: str,
        component_id: str,
        expected_robot_etag: str,
        command: ComponentLifecycleTransitionRequest,
        maintenance_record_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotComponentMutation: ...

    def list_components(
        self, *, project_id: str, region_code: str, robot_id: str
    ) -> tuple[RobotComponent, ...]: ...

    def get_component(
        self, *, project_id: str, region_code: str, component_id: str
    ) -> RobotComponent | None: ...

    def list_frames(
        self, *, project_id: str, region_code: str, component_id: str
    ) -> tuple[FrameReference, ...]: ...

    def list_channels(
        self, *, project_id: str, region_code: str, component_id: str
    ) -> tuple[ChannelReference, ...]: ...

    def append_audit(self, event: RoboticsAuditEvent) -> None: ...


class InMemoryRoboticsRepository:
    """Thread-safe reference repository; production composition uses PostgreSQL."""

    def __init__(
        self,
        *,
        robots: tuple[tuple[str, str, RobotBootstrap], ...] = (),
        components: tuple[tuple[str, str, RobotComponent], ...] = (),
        frames: tuple[tuple[str, str, str, FrameReference], ...] = (),
        channels: tuple[tuple[str, str, str, ChannelReference], ...] = (),
    ) -> None:
        self._robots = {
            (project, region, value.robot.id): value for project, region, value in robots
        }
        self._components = {
            (project, region, value.id): value for project, region, value in components
        }
        self._frames = tuple(frames)
        self._channels = tuple(channels)
        self._maintenance_records: dict[tuple[str, str, str], MaintenanceRecord] = {}
        self._command_receipts: dict[
            tuple[str, str, str, str, str],
            tuple[str, RobotBootstrap | MaintenanceRecord | RobotComponentMutation],
        ] = {}
        self.audit_events: list[RoboticsAuditEvent] = []
        self._lock = RLock()

    def list_robots(
        self,
        *,
        project_id: str,
        region_code: str,
        query: str | None,
        lifecycle_status: str | None,
        connectivity_state: str | None,
    ) -> tuple[RobotRecord, ...]:
        needle = query.casefold().strip() if query else ""
        with self._lock:
            values = [
                item.robot
                for (project, region, _), item in self._robots.items()
                if project == project_id
                and region == region_code
                and (lifecycle_status is None or item.robot.lifecycle_status == lifecycle_status)
                and (
                    connectivity_state is None
                    or item.robot.connectivity.state == connectivity_state
                )
                and (
                    not needle
                    or needle in item.robot.display_name.casefold()
                    or needle in item.robot.serial_no.casefold()
                )
            ]
        return tuple(sorted(values, key=lambda item: (item.display_name.casefold(), item.id)))

    def search_robots(
        self,
        *,
        project_id: str,
        region_code: str,
        query: str,
        after_display_name: str | None,
        after_robot_id: str | None,
        limit: int,
    ) -> tuple[RobotRecord, ...]:
        """Apply the same scoped read as the directory, but never materialize a page.

        The production adapter performs this keyset predicate in SQL.  Keeping the
        in-memory reference implementation semantically identical makes cursor and
        authorization tests representative without treating it as a production index.
        """

        needle = query.casefold()
        anchor = (
            None
            if after_display_name is None or after_robot_id is None
            else (after_display_name.casefold(), after_robot_id)
        )
        with self._lock:
            values = [
                item.robot
                for (project, region, _), item in self._robots.items()
                if project == project_id
                and region == region_code
                and (
                    needle in item.robot.display_name.casefold()
                    or needle in item.robot.serial_no.casefold()
                )
            ]
        ordered = sorted(values, key=lambda item: (item.display_name.casefold(), item.id))
        if anchor is not None:
            ordered = [item for item in ordered if (item.display_name.casefold(), item.id) > anchor]
        return tuple(ordered[:limit])

    def get_robot(
        self, *, project_id: str, region_code: str, robot_id: str
    ) -> RobotBootstrap | None:
        with self._lock:
            return self._robots.get((project_id, region_code, robot_id))

    @staticmethod
    def _memory_robot_etag(robot_id: str) -> str:
        return f'"robotics:{robot_id}:{uuid4().hex}"'

    def _replay_or_conflict(
        self,
        *,
        key: tuple[str, str, str, str, str],
        request_fingerprint: str,
    ) -> RobotBootstrap | MaintenanceRecord | RobotComponentMutation | None:
        existing = self._command_receipts.get(key)
        if existing is None:
            return None
        fingerprint, response = existing
        if fingerprint != request_fingerprint:
            raise ValueError("idempotency key was reused")
        return response

    def _store_receipt(
        self,
        *,
        key: tuple[str, str, str, str, str],
        request_fingerprint: str,
        response: RobotBootstrap | MaintenanceRecord | RobotComponentMutation,
        audit: RoboticsAuditEvent,
    ) -> None:
        self._command_receipts[key] = (request_fingerprint, response)
        self.audit_events.append(audit)

    @staticmethod
    def _memory_topology_revision(robot_id: str) -> str:
        return f"topology:{robot_id}:{uuid4().hex}"

    def _bump_memory_topology(
        self, *, project_id: str, region_code: str, robot_id: str
    ) -> RobotBootstrap:
        current = self._robots[(project_id, region_code, robot_id)]
        result = current.model_copy(
            update={
                "etag": self._memory_robot_etag(robot_id),
                "topology_revision": self._memory_topology_revision(robot_id),
            }
        )
        self._robots[(project_id, region_code, robot_id)] = result
        return result

    def _validate_memory_parent(
        self,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        component_id: str,
        parent_component_id: str | None,
    ) -> None:
        if parent_component_id is None:
            return
        if parent_component_id == component_id:
            raise ValueError("component cannot be its own parent")
        parent = self._components.get((project_id, region_code, parent_component_id))
        if parent is None or parent.robot_id != robot_id:
            raise KeyError("component parent")
        if parent.lifecycle_status == "RETIRED":
            raise ValueError("component parent is retired")

    @staticmethod
    def _component_mutation(
        component: RobotComponent, robot: RobotBootstrap
    ) -> RobotComponentMutation:
        return RobotComponentMutation(
            component=component,
            robot_etag=robot.etag,
            topology_revision=robot.topology_revision,
        )

    def create_robot(
        self,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        command: CreateRobotRequest,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotBootstrap:
        receipt_key = (project_id, region_code, "ROBOT", robot_id, f"CREATE:{idempotency_key}")
        with self._lock:
            replay = self._replay_or_conflict(
                key=receipt_key, request_fingerprint=request_fingerprint
            )
            if replay is not None:
                assert isinstance(replay, RobotBootstrap)
                return replay
            if (project_id, region_code, robot_id) in self._robots:
                raise ValueError("robot already exists")
            if any(
                project == project_id
                and region == region_code
                and item.robot.serial_no == command.serial_no
                for (project, region, _), item in self._robots.items()
            ):
                raise ValueError("robot serial number already exists")
            result = RobotBootstrap(
                robot=RobotRecord(
                    id=robot_id,
                    display_name=command.display_name,
                    serial_no=command.serial_no,
                    lifecycle_status=command.lifecycle_status,
                    connectivity=Connectivity(
                        state=command.connectivity_state,
                        observed_at=audit.occurred_at,
                        source=command.connectivity_source,
                        reason_code=command.connectivity_reason_code,
                    ),
                ),
                etag=self._memory_robot_etag(robot_id),
                topology_revision=f"topology:{robot_id}:1",
                effective_model_binding=None,
                allowed_actions=("VIEW", "EDIT", "TRANSITION"),
            )
            self._robots[(project_id, region_code, robot_id)] = result
            self._store_receipt(
                key=receipt_key,
                request_fingerprint=request_fingerprint,
                response=result,
                audit=audit,
            )
            return result

    def update_robot(
        self,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        expected_etag: str,
        command: UpdateRobotRequest,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotBootstrap:
        receipt_key = (project_id, region_code, "ROBOT", robot_id, f"UPDATE:{idempotency_key}")
        with self._lock:
            current = self._robots.get((project_id, region_code, robot_id))
            if current is None:
                raise KeyError(robot_id)
            replay = self._replay_or_conflict(
                key=receipt_key, request_fingerprint=request_fingerprint
            )
            if replay is not None:
                assert isinstance(replay, RobotBootstrap)
                return replay
            if current.etag != expected_etag:
                raise ValueError("robot etag does not match")
            robot = current.robot.model_copy(
                update={
                    "display_name": command.display_name or current.robot.display_name,
                    "connectivity": current.robot.connectivity.model_copy(
                        update={
                            "state": command.connectivity_state or current.robot.connectivity.state,
                            "observed_at": (
                                audit.occurred_at
                                if command.connectivity_state is not None
                                else current.robot.connectivity.observed_at
                            ),
                            "source": command.connectivity_source
                            if command.connectivity_source is not None
                            else current.robot.connectivity.source,
                            "reason_code": command.connectivity_reason_code
                            if command.connectivity_reason_code is not None
                            else current.robot.connectivity.reason_code,
                        }
                    ),
                }
            )
            result = current.model_copy(
                update={"robot": robot, "etag": self._memory_robot_etag(robot_id)}
            )
            self._robots[(project_id, region_code, robot_id)] = result
            self._store_receipt(
                key=receipt_key,
                request_fingerprint=request_fingerprint,
                response=result,
                audit=audit,
            )
            return result

    def transition_robot(
        self,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        expected_etag: str,
        command: RobotLifecycleTransitionRequest,
        maintenance_record_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotBootstrap:
        receipt_key = (
            project_id,
            region_code,
            "ROBOT",
            robot_id,
            f"TRANSITION:{idempotency_key}",
        )
        with self._lock:
            current = self._robots.get((project_id, region_code, robot_id))
            if current is None:
                raise KeyError(robot_id)
            replay = self._replay_or_conflict(
                key=receipt_key, request_fingerprint=request_fingerprint
            )
            if replay is not None:
                assert isinstance(replay, RobotBootstrap)
                return replay
            if current.etag != expected_etag:
                raise ValueError("robot etag does not match")
            if current.robot.lifecycle_status == command.lifecycle_status:
                raise ValueError("robot is already in the requested lifecycle")
            record = MaintenanceRecord(
                id=maintenance_record_id,
                robot_id=robot_id,
                component_id=None,
                event_type="LIFECYCLE_TRANSITION",
                previous_lifecycle_status=current.robot.lifecycle_status,
                lifecycle_status=command.lifecycle_status,
                summary=command.reason,
                details=None,
                actor_id=audit.actor_id,
                occurred_at=audit.occurred_at,
            )
            result = current.model_copy(
                update={
                    "robot": current.robot.model_copy(
                        update={"lifecycle_status": command.lifecycle_status}
                    ),
                    "etag": self._memory_robot_etag(robot_id),
                }
            )
            self._robots[(project_id, region_code, robot_id)] = result
            self._maintenance_records[(project_id, region_code, maintenance_record_id)] = record
            self._store_receipt(
                key=receipt_key,
                request_fingerprint=request_fingerprint,
                response=result,
                audit=audit,
            )
            return result

    def create_maintenance_record(
        self,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        record_id: str,
        command: CreateMaintenanceRecordRequest,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> MaintenanceRecord:
        receipt_key = (
            project_id,
            region_code,
            "ROBOT",
            robot_id,
            f"MAINTENANCE:{idempotency_key}",
        )
        with self._lock:
            if (project_id, region_code, robot_id) not in self._robots:
                raise KeyError(robot_id)
            replay = self._replay_or_conflict(
                key=receipt_key, request_fingerprint=request_fingerprint
            )
            if replay is not None:
                assert isinstance(replay, MaintenanceRecord)
                return replay
            record = MaintenanceRecord(
                id=record_id,
                robot_id=robot_id,
                component_id=None,
                event_type="MAINTENANCE",
                previous_lifecycle_status=None,
                lifecycle_status=None,
                summary=command.summary,
                details=command.details,
                actor_id=audit.actor_id,
                occurred_at=audit.occurred_at,
            )
            self._maintenance_records[(project_id, region_code, record_id)] = record
            self._store_receipt(
                key=receipt_key,
                request_fingerprint=request_fingerprint,
                response=record,
                audit=audit,
            )
            return record

    def list_maintenance_records(
        self, *, project_id: str, region_code: str, robot_id: str
    ) -> tuple[MaintenanceRecord, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        item
                        for (project, region, _), item in self._maintenance_records.items()
                        if project == project_id
                        and region == region_code
                        and item.robot_id == robot_id
                    ),
                    key=lambda item: (item.occurred_at, item.id),
                    reverse=True,
                )
            )

    def create_component(
        self,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        component_id: str,
        expected_robot_etag: str,
        command: CreateRobotComponentRequest,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotComponentMutation:
        receipt_key = (
            project_id,
            region_code,
            "COMPONENT",
            component_id,
            f"CREATE:{idempotency_key}",
        )
        with self._lock:
            current_robot = self._robots.get((project_id, region_code, robot_id))
            if current_robot is None:
                raise KeyError(robot_id)
            replay = self._replay_or_conflict(
                key=receipt_key, request_fingerprint=request_fingerprint
            )
            if replay is not None:
                assert isinstance(replay, RobotComponentMutation)
                return replay
            if current_robot.etag != expected_robot_etag:
                raise ValueError("robot etag does not match")
            if (project_id, region_code, component_id) in self._components:
                raise ValueError("component already exists")
            self._validate_memory_parent(
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                component_id=component_id,
                parent_component_id=command.parent_component_id,
            )
            component = RobotComponent(
                id=component_id,
                robot_id=robot_id,
                parent_component_id=command.parent_component_id,
                component_model_id=command.component_model_id,
                component_type=command.component_type,
                display_name=command.display_name,
                serial_no=command.serial_no,
                lifecycle_status=command.lifecycle_status,
                sort_order=str(command.sort_order),
            )
            self._components[(project_id, region_code, component_id)] = component
            robot = self._bump_memory_topology(
                project_id=project_id, region_code=region_code, robot_id=robot_id
            )
            result = self._component_mutation(component, robot)
            self._store_receipt(
                key=receipt_key,
                request_fingerprint=request_fingerprint,
                response=result,
                audit=audit,
            )
            return result

    def update_component(
        self,
        *,
        project_id: str,
        region_code: str,
        component_id: str,
        expected_robot_etag: str,
        command: UpdateRobotComponentRequest,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotComponentMutation:
        receipt_key = (
            project_id,
            region_code,
            "COMPONENT",
            component_id,
            f"UPDATE:{idempotency_key}",
        )
        with self._lock:
            component = self._components.get((project_id, region_code, component_id))
            if component is None:
                raise KeyError("component")
            current_robot = self._robots.get((project_id, region_code, component.robot_id))
            if current_robot is None:
                raise KeyError(component.robot_id)
            replay = self._replay_or_conflict(
                key=receipt_key, request_fingerprint=request_fingerprint
            )
            if replay is not None:
                assert isinstance(replay, RobotComponentMutation)
                return replay
            if current_robot.etag != expected_robot_etag:
                raise ValueError("robot etag does not match")
            parent_component_id = (
                command.parent_component_id
                if "parent_component_id" in command.model_fields_set
                else component.parent_component_id
            )
            self._validate_memory_parent(
                project_id=project_id,
                region_code=region_code,
                robot_id=component.robot_id,
                component_id=component_id,
                parent_component_id=parent_component_id,
            )
            updated = component.model_copy(
                update={
                    "parent_component_id": parent_component_id,
                    "component_model_id": command.component_model_id
                    if command.component_model_id is not None
                    else component.component_model_id,
                    "component_type": command.component_type
                    if command.component_type is not None
                    else component.component_type,
                    "display_name": command.display_name
                    if command.display_name is not None
                    else component.display_name,
                    "serial_no": command.serial_no
                    if command.serial_no is not None
                    else component.serial_no,
                    "sort_order": str(command.sort_order)
                    if command.sort_order is not None
                    else component.sort_order,
                }
            )
            self._components[(project_id, region_code, component_id)] = updated
            robot = self._bump_memory_topology(
                project_id=project_id, region_code=region_code, robot_id=component.robot_id
            )
            result = self._component_mutation(updated, robot)
            self._store_receipt(
                key=receipt_key,
                request_fingerprint=request_fingerprint,
                response=result,
                audit=audit,
            )
            return result

    def transition_component(
        self,
        *,
        project_id: str,
        region_code: str,
        component_id: str,
        expected_robot_etag: str,
        command: ComponentLifecycleTransitionRequest,
        maintenance_record_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotComponentMutation:
        receipt_key = (
            project_id,
            region_code,
            "COMPONENT",
            component_id,
            f"TRANSITION:{idempotency_key}",
        )
        with self._lock:
            component = self._components.get((project_id, region_code, component_id))
            if component is None:
                raise KeyError("component")
            current_robot = self._robots.get((project_id, region_code, component.robot_id))
            if current_robot is None:
                raise KeyError(component.robot_id)
            replay = self._replay_or_conflict(
                key=receipt_key, request_fingerprint=request_fingerprint
            )
            if replay is not None:
                assert isinstance(replay, RobotComponentMutation)
                return replay
            if current_robot.etag != expected_robot_etag:
                raise ValueError("robot etag does not match")
            if component.lifecycle_status == command.lifecycle_status:
                raise ValueError("component is already in the requested lifecycle")
            if command.lifecycle_status not in COMPONENT_LIFECYCLE_TRANSITIONS.get(
                component.lifecycle_status, frozenset()
            ):
                raise ValueError("component lifecycle transition is invalid")
            if command.lifecycle_status == "RETIRED" and any(
                candidate.robot_id == component.robot_id
                and candidate.parent_component_id == component_id
                and candidate.lifecycle_status != "RETIRED"
                for (project, region, _), candidate in self._components.items()
                if project == project_id and region == region_code
            ):
                raise ValueError("component has active children")
            updated = component.model_copy(update={"lifecycle_status": command.lifecycle_status})
            self._components[(project_id, region_code, component_id)] = updated
            self._maintenance_records[(project_id, region_code, maintenance_record_id)] = (
                MaintenanceRecord(
                    id=maintenance_record_id,
                    robot_id=component.robot_id,
                    component_id=component_id,
                    event_type="COMPONENT_LIFECYCLE_TRANSITION",
                    previous_lifecycle_status=component.lifecycle_status,
                    lifecycle_status=command.lifecycle_status,
                    summary=command.reason,
                    details=None,
                    actor_id=audit.actor_id,
                    occurred_at=audit.occurred_at,
                )
            )
            robot = self._bump_memory_topology(
                project_id=project_id, region_code=region_code, robot_id=component.robot_id
            )
            result = self._component_mutation(updated, robot)
            self._store_receipt(
                key=receipt_key,
                request_fingerprint=request_fingerprint,
                response=result,
                audit=audit,
            )
            return result

    def list_components(
        self, *, project_id: str, region_code: str, robot_id: str
    ) -> tuple[RobotComponent, ...]:
        with self._lock:
            values = [
                item
                for (project, region, _), item in self._components.items()
                if project == project_id and region == region_code and item.robot_id == robot_id
            ]
        return tuple(sorted(values, key=lambda item: (int(item.sort_order), item.id)))

    def get_component(
        self, *, project_id: str, region_code: str, component_id: str
    ) -> RobotComponent | None:
        with self._lock:
            return self._components.get((project_id, region_code, component_id))

    def list_frames(
        self, *, project_id: str, region_code: str, component_id: str
    ) -> tuple[FrameReference, ...]:
        return tuple(
            item
            for project, region, component, item in self._frames
            if project == project_id and region == region_code and component == component_id
        )

    def list_channels(
        self, *, project_id: str, region_code: str, component_id: str
    ) -> tuple[ChannelReference, ...]:
        return tuple(
            item
            for project, region, component, item in self._channels
            if project == project_id and region == region_code and component == component_id
        )

    def append_audit(self, event: RoboticsAuditEvent) -> None:
        with self._lock:
            self.audit_events.append(event)


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


def _actions(value: object) -> tuple[str, ...]:
    decoded = json.loads(value) if isinstance(value, str) else value
    return tuple(str(item) for item in decoded) if isinstance(decoded, list | tuple) else ()


def _robot(row: Mapping[str, object]) -> RobotRecord:
    return RobotRecord(
        id=str(row["robot_id"]),
        display_name=str(row["display_name"]),
        serial_no=str(row["serial_no"]),
        lifecycle_status=str(row["lifecycle_status"]),
        connectivity=Connectivity(
            state=str(row["connectivity_state"]),
            observed_at=cast(datetime | None, row.get("connectivity_observed_at")),
            source=None
            if row.get("connectivity_source") is None
            else str(row["connectivity_source"]),
            reason_code=None
            if row.get("connectivity_reason_code") is None
            else str(row["connectivity_reason_code"]),
        ),
    )


def _component(row: Mapping[str, object]) -> RobotComponent:
    return RobotComponent(
        id=str(row["component_id"]),
        robot_id=str(row["robot_id"]),
        parent_component_id=None
        if row.get("parent_component_id") is None
        else str(row["parent_component_id"]),
        component_model_id=str(row["component_model_id"]),
        component_type=str(row["component_type"]),
        display_name=str(row["display_name"]),
        serial_no=str(row["serial_no"]),
        lifecycle_status=str(row["lifecycle_status"]),
        sort_order=str(row["sort_order"]),
    )


def _bootstrap(row: Mapping[str, object]) -> RobotBootstrap:
    binding = (
        None
        if row["binding_id"] is None
        else EffectiveModelBinding(
            id=str(row["binding_id"]),
            scope_type=str(row["binding_scope_type"]),
            scope_id=str(row["binding_scope_id"]),
            robot_model_version_id=str(row["robot_model_version_id"]),
            valid_from=cast(datetime, row["binding_valid_from"]),
            valid_to=cast(datetime | None, row["binding_valid_to"]),
            etag=str(row["binding_etag"]),
        )
    )
    return RobotBootstrap(
        robot=_robot(row),
        etag=str(row["etag"]),
        topology_revision=str(row["topology_revision"]),
        effective_model_binding=binding,
        allowed_actions=_actions(row["allowed_actions"]),
    )


def _maintenance_record(row: Mapping[str, object]) -> MaintenanceRecord:
    return MaintenanceRecord(
        id=str(row["record_id"]),
        robot_id=str(row["robot_id"]),
        component_id=None if row["component_id"] is None else str(row["component_id"]),
        event_type=str(row["event_type"]),
        previous_lifecycle_status=(
            None
            if row["previous_lifecycle_status"] is None
            else str(row["previous_lifecycle_status"])
        ),
        lifecycle_status=(
            None if row["lifecycle_status"] is None else str(row["lifecycle_status"])
        ),
        summary=str(row["summary"]),
        details=None if row["details"] is None else str(row["details"]),
        actor_id=str(row["actor_id"]),
        occurred_at=cast(datetime, row["occurred_at"]),
    )


class PostgresRoboticsRepository:
    """RLS-scoped PostgreSQL reads for the P15 robot directory."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    @staticmethod
    def _append_audit_cursor(cursor: DbApiCursor, event: RoboticsAuditEvent) -> None:
        details = {"outcome": "SUCCEEDED", **dict(event.details)}
        cursor.execute(
            """
            INSERT INTO core.audit_events (
                audit_id, project_id, region_code, actor_id, action, resource_type,
                resource_id, request_id, before_hash, after_hash, details, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL, %s::jsonb, %s)
            """,
            (
                str(uuid4()),
                event.project_id,
                event.region_code,
                event.actor_id,
                event.action,
                event.resource_type,
                event.resource_id,
                event.request_id,
                json.dumps(details, sort_keys=True),
                event.occurred_at,
            ),
        )

    @staticmethod
    def _locked_robot(
        cursor: DbApiCursor, *, project_id: str, region_code: str, robot_id: str
    ) -> RobotBootstrap:
        cursor.execute(
            """
            SELECT robot_id, display_name, serial_no, lifecycle_status, connectivity_state,
                   connectivity_observed_at, connectivity_source, connectivity_reason_code,
                   etag, topology_revision, binding_id, binding_scope_type, binding_scope_id,
                   robot_model_version_id, binding_valid_from, binding_valid_to, binding_etag,
                   allowed_actions
              FROM robotics.robot_instances
             WHERE project_id = %s AND region_code = %s AND robot_id = %s
             FOR UPDATE
            """,
            (project_id, region_code, robot_id),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise KeyError(robot_id)
        return _bootstrap(_row(cursor, raw))

    @staticmethod
    def _locked_component(
        cursor: DbApiCursor, *, project_id: str, region_code: str, component_id: str
    ) -> RobotComponent:
        cursor.execute(
            """
            SELECT component_id, robot_id, parent_component_id, component_model_id,
                   component_type, display_name, serial_no, lifecycle_status, sort_order
              FROM robotics.robot_components
             WHERE project_id = %s AND region_code = %s AND component_id = %s
             FOR UPDATE
            """,
            (project_id, region_code, component_id),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise KeyError("component")
        return _component(_row(cursor, raw))

    @staticmethod
    def _bump_topology_cursor(
        cursor: DbApiCursor,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        occurred_at: datetime,
    ) -> RobotBootstrap:
        cursor.execute(
            """
            UPDATE robotics.robot_instances
               SET revision = revision + 1,
                   etag = '"robotics:' || robot_id || ':' || (revision + 1)::text || '"',
                   topology_revision = 'topology:' || robot_id || ':' || (revision + 1)::text,
                   updated_at = %s
             WHERE project_id = %s AND region_code = %s AND robot_id = %s
            """,
            (occurred_at, project_id, region_code, robot_id),
        )
        return PostgresRoboticsRepository._locked_robot(
            cursor, project_id=project_id, region_code=region_code, robot_id=robot_id
        )

    @staticmethod
    def _validate_component_parent_cursor(
        cursor: DbApiCursor,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        component_id: str,
        parent_component_id: str | None,
    ) -> None:
        if parent_component_id is None:
            return
        if parent_component_id == component_id:
            raise ValueError("component cannot be its own parent")
        cursor.execute(
            """
            SELECT component_id, robot_id, lifecycle_status
              FROM robotics.robot_components
             WHERE project_id = %s AND region_code = %s AND component_id = %s
             FOR UPDATE
            """,
            (project_id, region_code, parent_component_id),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise KeyError("component parent")
        parent = _row(cursor, raw)
        if str(parent["robot_id"]) != robot_id:
            raise ValueError("component parent belongs to another robot")
        if str(parent["lifecycle_status"]) == "RETIRED":
            raise ValueError("component parent is retired")
        cursor.execute(
            """
            WITH RECURSIVE ancestors AS (
                SELECT component_id, parent_component_id
                  FROM robotics.robot_components
                 WHERE project_id = %s AND region_code = %s AND component_id = %s
                UNION ALL
                SELECT parent.component_id, parent.parent_component_id
                  FROM robotics.robot_components parent
                  JOIN ancestors child
                    ON child.parent_component_id = parent.component_id
                 WHERE parent.project_id = %s AND parent.region_code = %s
            )
            SELECT EXISTS(
                SELECT 1 FROM ancestors WHERE component_id = %s
            ) AS creates_cycle
            """,
            (
                project_id,
                region_code,
                parent_component_id,
                project_id,
                region_code,
                component_id,
            ),
        )
        cycle_row = cursor.fetchone()
        if cycle_row is None:
            raise RuntimeError("component ancestry validation did not return a result")
        if bool(_row(cursor, cycle_row)["creates_cycle"]):
            raise ValueError("component parent creates a cycle")

    @staticmethod
    def _read_receipt(
        cursor: DbApiCursor,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
        model: type[RobotBootstrap] | type[MaintenanceRecord] | type[RobotComponentMutation],
    ) -> RobotBootstrap | MaintenanceRecord | RobotComponentMutation | None:
        cursor.execute(
            """
            SELECT request_fingerprint, response
              FROM robotics.robot_command_receipts
             WHERE project_id = %s AND region_code = %s AND resource_type = 'ROBOT'
               AND resource_id = %s AND operation = %s AND idempotency_key = %s
             FOR UPDATE
            """,
            (project_id, region_code, robot_id, operation, idempotency_key),
        )
        raw = cursor.fetchone()
        if raw is None:
            return None
        row = _row(cursor, raw)
        if str(row["request_fingerprint"]) != request_fingerprint:
            raise ValueError("idempotency key was reused")
        decoded = row["response"]
        if isinstance(decoded, str):
            decoded = json.loads(decoded)
        return model.model_validate(decoded)

    @staticmethod
    def _store_receipt(
        cursor: DbApiCursor,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
        response: RobotBootstrap | MaintenanceRecord | RobotComponentMutation,
        occurred_at: datetime,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO robotics.robot_command_receipts (
                project_id, region_code, resource_type, resource_id, operation,
                idempotency_key, request_fingerprint, response, created_at
            ) VALUES (%s, %s, 'ROBOT', %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                project_id,
                region_code,
                robot_id,
                operation,
                idempotency_key,
                request_fingerprint,
                json.dumps(response.model_dump(mode="json")),
                occurred_at,
            ),
        )

    def list_robots(
        self,
        *,
        project_id: str,
        region_code: str,
        query: str | None,
        lifecycle_status: str | None,
        connectivity_state: str | None,
    ) -> tuple[RobotRecord, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT robot_id, display_name, serial_no, lifecycle_status, connectivity_state,
                       connectivity_observed_at, connectivity_source, connectivity_reason_code
                 FROM robotics.robot_instances
                 WHERE project_id = %s AND region_code = %s
                   AND (%s::text IS NULL OR lifecycle_status = %s)
                   AND (%s::text IS NULL OR connectivity_state = %s)
                   AND (
                       %s::text IS NULL
                       OR display_name ILIKE '%%' || %s || '%%'
                       OR serial_no ILIKE '%%' || %s || '%%'
                   )
                 ORDER BY lower(display_name), robot_id
            """,
                (
                    project_id,
                    region_code,
                    lifecycle_status,
                    lifecycle_status,
                    connectivity_state,
                    connectivity_state,
                    query,
                    query,
                    query,
                ),
            )
            return tuple(_robot(_row(cursor, raw)) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def search_robots(
        self,
        *,
        project_id: str,
        region_code: str,
        query: str,
        after_display_name: str | None,
        after_robot_id: str | None,
        limit: int,
    ) -> tuple[RobotRecord, ...]:
        """Keyset-search rows under the RLS scope selected by the router.

        ``limit`` is deliberately required by the port: shell search must not turn a
        convenient header affordance into an unbounded directory read.
        """

        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT robot_id, display_name, serial_no, lifecycle_status, connectivity_state,
                       connectivity_observed_at, connectivity_source, connectivity_reason_code
                  FROM robotics.robot_instances
                 WHERE project_id = %s AND region_code = %s
                   AND (
                       display_name ILIKE '%%' || %s || '%%'
                       OR serial_no ILIKE '%%' || %s || '%%'
                   )
                   AND (
                       %s::text IS NULL
                       OR lower(display_name) > lower(%s)
                       OR (
                           lower(display_name) = lower(%s)
                           AND robot_id > %s
                       )
                   )
                 ORDER BY lower(display_name), robot_id
                 LIMIT %s
                """,
                (
                    project_id,
                    region_code,
                    query,
                    query,
                    after_display_name,
                    after_display_name,
                    after_display_name,
                    after_robot_id,
                    limit,
                ),
            )
            return tuple(_robot(_row(cursor, raw)) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def create_robot(
        self,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        command: CreateRobotRequest,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotBootstrap:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            # A deterministic creation id is protected by an advisory transaction
            # lock before either the row or its idempotency receipt exists.
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"robot:create:{project_id}:{region_code}:{robot_id}",),
            )
            replay = self._read_receipt(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                operation="CREATE",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                model=RobotBootstrap,
            )
            if replay is not None:
                assert isinstance(replay, RobotBootstrap)
                connection.commit()
                return replay
            cursor.execute(
                """
                INSERT INTO robotics.robot_instances (
                    project_id, region_code, robot_id, display_name, serial_no,
                    lifecycle_status, connectivity_state, connectivity_observed_at,
                    connectivity_source, connectivity_reason_code, etag, topology_revision,
                    allowed_actions, revision, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    '"robotics:' || %s || ':1"', 'topology:' || %s || ':1',
                    '["VIEW", "EDIT", "TRANSITION"]'::jsonb, 1, %s, %s
                )
                """,
                (
                    project_id,
                    region_code,
                    robot_id,
                    command.display_name,
                    command.serial_no,
                    command.lifecycle_status,
                    command.connectivity_state,
                    audit.occurred_at,
                    command.connectivity_source,
                    command.connectivity_reason_code,
                    robot_id,
                    robot_id,
                    audit.occurred_at,
                    audit.occurred_at,
                ),
            )
            result = self._locked_robot(
                cursor, project_id=project_id, region_code=region_code, robot_id=robot_id
            )
            self._store_receipt(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                operation="CREATE",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                response=result,
                occurred_at=audit.occurred_at,
            )
            self._append_audit_cursor(cursor, audit)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def update_robot(
        self,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        expected_etag: str,
        command: UpdateRobotRequest,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotBootstrap:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            current = self._locked_robot(
                cursor, project_id=project_id, region_code=region_code, robot_id=robot_id
            )
            replay = self._read_receipt(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                operation="UPDATE",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                model=RobotBootstrap,
            )
            if replay is not None:
                assert isinstance(replay, RobotBootstrap)
                connection.commit()
                return replay
            if current.etag != expected_etag:
                raise ValueError("robot etag does not match")
            cursor.execute(
                """
                UPDATE robotics.robot_instances
                   SET display_name = COALESCE(%s, display_name),
                       connectivity_state = COALESCE(%s, connectivity_state),
                       connectivity_observed_at = CASE
                           WHEN %s::text IS NULL THEN connectivity_observed_at ELSE %s END,
                       connectivity_source = COALESCE(%s, connectivity_source),
                       connectivity_reason_code = COALESCE(%s, connectivity_reason_code),
                       revision = revision + 1,
                       etag = '"robotics:' || robot_id || ':' || (revision + 1)::text || '"',
                       updated_at = %s
                 WHERE project_id = %s AND region_code = %s AND robot_id = %s
                """,
                (
                    command.display_name,
                    command.connectivity_state,
                    command.connectivity_state,
                    audit.occurred_at,
                    command.connectivity_source,
                    command.connectivity_reason_code,
                    audit.occurred_at,
                    project_id,
                    region_code,
                    robot_id,
                ),
            )
            result = self._locked_robot(
                cursor, project_id=project_id, region_code=region_code, robot_id=robot_id
            )
            self._store_receipt(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                operation="UPDATE",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                response=result,
                occurred_at=audit.occurred_at,
            )
            self._append_audit_cursor(cursor, audit)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def transition_robot(
        self,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        expected_etag: str,
        command: RobotLifecycleTransitionRequest,
        maintenance_record_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotBootstrap:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            current = self._locked_robot(
                cursor, project_id=project_id, region_code=region_code, robot_id=robot_id
            )
            replay = self._read_receipt(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                operation="TRANSITION",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                model=RobotBootstrap,
            )
            if replay is not None:
                assert isinstance(replay, RobotBootstrap)
                connection.commit()
                return replay
            if current.etag != expected_etag:
                raise ValueError("robot etag does not match")
            if current.robot.lifecycle_status == command.lifecycle_status:
                raise ValueError("robot is already in the requested lifecycle")
            cursor.execute(
                """
                UPDATE robotics.robot_instances
                   SET lifecycle_status = %s,
                       revision = revision + 1,
                       etag = '"robotics:' || robot_id || ':' || (revision + 1)::text || '"',
                       updated_at = %s
                 WHERE project_id = %s AND region_code = %s AND robot_id = %s
                """,
                (
                    command.lifecycle_status,
                    audit.occurred_at,
                    project_id,
                    region_code,
                    robot_id,
                ),
            )
            cursor.execute(
                """
                INSERT INTO robotics.robot_maintenance_records (
                    project_id, region_code, record_id, robot_id, component_id, event_type,
                    previous_lifecycle_status, lifecycle_status, summary, details, actor_id,
                    occurred_at
                ) VALUES (%s, %s, %s, %s, NULL, 'LIFECYCLE_TRANSITION', %s, %s, %s, NULL, %s, %s)
                """,
                (
                    project_id,
                    region_code,
                    maintenance_record_id,
                    robot_id,
                    current.robot.lifecycle_status,
                    command.lifecycle_status,
                    command.reason,
                    audit.actor_id,
                    audit.occurred_at,
                ),
            )
            result = self._locked_robot(
                cursor, project_id=project_id, region_code=region_code, robot_id=robot_id
            )
            self._store_receipt(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                operation="TRANSITION",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                response=result,
                occurred_at=audit.occurred_at,
            )
            self._append_audit_cursor(cursor, audit)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def create_maintenance_record(
        self,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        record_id: str,
        command: CreateMaintenanceRecordRequest,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> MaintenanceRecord:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            self._locked_robot(
                cursor, project_id=project_id, region_code=region_code, robot_id=robot_id
            )
            replay = self._read_receipt(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                operation="MAINTENANCE",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                model=MaintenanceRecord,
            )
            if replay is not None:
                assert isinstance(replay, MaintenanceRecord)
                connection.commit()
                return replay
            cursor.execute(
                """
                INSERT INTO robotics.robot_maintenance_records (
                    project_id, region_code, record_id, robot_id, component_id, event_type,
                    previous_lifecycle_status, lifecycle_status, summary, details, actor_id,
                    occurred_at
                ) VALUES (%s, %s, %s, %s, NULL, 'MAINTENANCE', NULL, NULL, %s, %s, %s, %s)
                RETURNING record_id, robot_id, component_id, event_type, previous_lifecycle_status,
                          lifecycle_status, summary, details, actor_id, occurred_at
                """,
                (
                    project_id,
                    region_code,
                    record_id,
                    robot_id,
                    command.summary,
                    command.details,
                    audit.actor_id,
                    audit.occurred_at,
                ),
            )
            raw = cursor.fetchone()
            if raw is None:
                raise RuntimeError("maintenance record insert did not return its row")
            result = _maintenance_record(_row(cursor, raw))
            self._store_receipt(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                operation="MAINTENANCE",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                response=result,
                occurred_at=audit.occurred_at,
            )
            self._append_audit_cursor(cursor, audit)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def list_maintenance_records(
        self, *, project_id: str, region_code: str, robot_id: str
    ) -> tuple[MaintenanceRecord, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT record_id, robot_id, component_id, event_type, previous_lifecycle_status,
                       lifecycle_status, summary, details, actor_id, occurred_at
                  FROM robotics.robot_maintenance_records
                 WHERE project_id = %s AND region_code = %s AND robot_id = %s
                 ORDER BY occurred_at DESC, record_id DESC
                """,
                (project_id, region_code, robot_id),
            )
            return tuple(_maintenance_record(_row(cursor, raw)) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def create_component(
        self,
        *,
        project_id: str,
        region_code: str,
        robot_id: str,
        component_id: str,
        expected_robot_etag: str,
        command: CreateRobotComponentRequest,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotComponentMutation:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            robot = self._locked_robot(
                cursor, project_id=project_id, region_code=region_code, robot_id=robot_id
            )
            replay = self._read_receipt(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=component_id,
                operation="CREATE",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                model=RobotComponentMutation,
            )
            if replay is not None:
                assert isinstance(replay, RobotComponentMutation)
                connection.commit()
                return replay
            if robot.etag != expected_robot_etag:
                raise ValueError("robot etag does not match")
            self._validate_component_parent_cursor(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                component_id=component_id,
                parent_component_id=command.parent_component_id,
            )
            cursor.execute(
                """
                INSERT INTO robotics.robot_components (
                    project_id, region_code, component_id, robot_id, parent_component_id,
                    component_model_id, component_type, display_name, serial_no,
                    lifecycle_status, sort_order, revision, etag, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    1, '"robotics-component:' || %s || ':1"', %s, %s
                )
                RETURNING component_id, robot_id, parent_component_id, component_model_id,
                          component_type, display_name, serial_no, lifecycle_status, sort_order
                """,
                (
                    project_id,
                    region_code,
                    component_id,
                    robot_id,
                    command.parent_component_id,
                    command.component_model_id,
                    command.component_type,
                    command.display_name,
                    command.serial_no,
                    command.lifecycle_status,
                    command.sort_order,
                    component_id,
                    audit.occurred_at,
                    audit.occurred_at,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("component insert did not return its row")
            component = _component(_row(cursor, row))
            current = self._bump_topology_cursor(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=robot_id,
                occurred_at=audit.occurred_at,
            )
            result = RobotComponentMutation(
                component=component,
                robot_etag=current.etag,
                topology_revision=current.topology_revision,
            )
            self._store_receipt(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=component_id,
                operation="CREATE",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                response=result,
                occurred_at=audit.occurred_at,
            )
            self._append_audit_cursor(cursor, audit)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def update_component(
        self,
        *,
        project_id: str,
        region_code: str,
        component_id: str,
        expected_robot_etag: str,
        command: UpdateRobotComponentRequest,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotComponentMutation:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            component = self._locked_component(
                cursor,
                project_id=project_id,
                region_code=region_code,
                component_id=component_id,
            )
            robot = self._locked_robot(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=component.robot_id,
            )
            replay = self._read_receipt(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=component_id,
                operation="UPDATE",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                model=RobotComponentMutation,
            )
            if replay is not None:
                assert isinstance(replay, RobotComponentMutation)
                connection.commit()
                return replay
            if robot.etag != expected_robot_etag:
                raise ValueError("robot etag does not match")
            parent_component_id = (
                command.parent_component_id
                if "parent_component_id" in command.model_fields_set
                else component.parent_component_id
            )
            self._validate_component_parent_cursor(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=component.robot_id,
                component_id=component_id,
                parent_component_id=parent_component_id,
            )
            cursor.execute(
                """
                UPDATE robotics.robot_components
                   SET parent_component_id = %s,
                       component_model_id = COALESCE(%s, component_model_id),
                       component_type = COALESCE(%s, component_type),
                       display_name = COALESCE(%s, display_name),
                       serial_no = COALESCE(%s, serial_no),
                       sort_order = COALESCE(%s, sort_order),
                       revision = revision + 1,
                       etag = '"robotics-component:' || component_id || ':'
                              || (revision + 1)::text || '"',
                       updated_at = %s
                 WHERE project_id = %s AND region_code = %s AND component_id = %s
                RETURNING component_id, robot_id, parent_component_id, component_model_id,
                          component_type, display_name, serial_no, lifecycle_status, sort_order
                """,
                (
                    parent_component_id,
                    command.component_model_id,
                    command.component_type,
                    command.display_name,
                    command.serial_no,
                    command.sort_order,
                    audit.occurred_at,
                    project_id,
                    region_code,
                    component_id,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError("component")
            updated = _component(_row(cursor, row))
            current = self._bump_topology_cursor(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=component.robot_id,
                occurred_at=audit.occurred_at,
            )
            result = RobotComponentMutation(
                component=updated,
                robot_etag=current.etag,
                topology_revision=current.topology_revision,
            )
            self._store_receipt(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=component_id,
                operation="UPDATE",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                response=result,
                occurred_at=audit.occurred_at,
            )
            self._append_audit_cursor(cursor, audit)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def transition_component(
        self,
        *,
        project_id: str,
        region_code: str,
        component_id: str,
        expected_robot_etag: str,
        command: ComponentLifecycleTransitionRequest,
        maintenance_record_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        audit: RoboticsAuditEvent,
    ) -> RobotComponentMutation:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            component = self._locked_component(
                cursor,
                project_id=project_id,
                region_code=region_code,
                component_id=component_id,
            )
            robot = self._locked_robot(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=component.robot_id,
            )
            replay = self._read_receipt(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=component_id,
                operation="TRANSITION",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                model=RobotComponentMutation,
            )
            if replay is not None:
                assert isinstance(replay, RobotComponentMutation)
                connection.commit()
                return replay
            if robot.etag != expected_robot_etag:
                raise ValueError("robot etag does not match")
            if component.lifecycle_status == command.lifecycle_status:
                raise ValueError("component is already in the requested lifecycle")
            if command.lifecycle_status not in COMPONENT_LIFECYCLE_TRANSITIONS.get(
                component.lifecycle_status, frozenset()
            ):
                raise ValueError("component lifecycle transition is invalid")
            if command.lifecycle_status == "RETIRED":
                cursor.execute(
                    """
                    SELECT EXISTS(
                        SELECT 1
                          FROM robotics.robot_components
                         WHERE project_id = %s AND region_code = %s AND robot_id = %s
                           AND parent_component_id = %s AND lifecycle_status <> 'RETIRED'
                    ) AS has_active_children
                    """,
                    (project_id, region_code, component.robot_id, component_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError("component child check did not return a result")
                if bool(_row(cursor, row)["has_active_children"]):
                    raise ValueError("component has active children")
            cursor.execute(
                """
                UPDATE robotics.robot_components
                   SET lifecycle_status = %s,
                       revision = revision + 1,
                       etag = '"robotics-component:' || component_id || ':'
                              || (revision + 1)::text || '"',
                       updated_at = %s
                 WHERE project_id = %s AND region_code = %s AND component_id = %s
                RETURNING component_id, robot_id, parent_component_id, component_model_id,
                          component_type, display_name, serial_no, lifecycle_status, sort_order
                """,
                (
                    command.lifecycle_status,
                    audit.occurred_at,
                    project_id,
                    region_code,
                    component_id,
                ),
            )
            updated_row = cursor.fetchone()
            if updated_row is None:
                raise KeyError("component")
            updated = _component(_row(cursor, updated_row))
            cursor.execute(
                """
                INSERT INTO robotics.robot_maintenance_records (
                    project_id, region_code, record_id, robot_id, component_id, event_type,
                    previous_lifecycle_status, lifecycle_status, summary, details, actor_id,
                    occurred_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'COMPONENT_LIFECYCLE_TRANSITION',
                    %s, %s, %s, NULL, %s, %s
                )
                """,
                (
                    project_id,
                    region_code,
                    maintenance_record_id,
                    component.robot_id,
                    component_id,
                    component.lifecycle_status,
                    command.lifecycle_status,
                    command.reason,
                    audit.actor_id,
                    audit.occurred_at,
                ),
            )
            current = self._bump_topology_cursor(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=component.robot_id,
                occurred_at=audit.occurred_at,
            )
            result = RobotComponentMutation(
                component=updated,
                robot_etag=current.etag,
                topology_revision=current.topology_revision,
            )
            self._store_receipt(
                cursor,
                project_id=project_id,
                region_code=region_code,
                robot_id=component_id,
                operation="TRANSITION",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                response=result,
                occurred_at=audit.occurred_at,
            )
            self._append_audit_cursor(cursor, audit)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def get_robot(
        self, *, project_id: str, region_code: str, robot_id: str
    ) -> RobotBootstrap | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT robot_id, display_name, serial_no, lifecycle_status, connectivity_state,
                       connectivity_observed_at, connectivity_source, connectivity_reason_code,
                       etag,
                       topology_revision, binding_id, binding_scope_type, binding_scope_id,
                       robot_model_version_id, binding_valid_from, binding_valid_to, binding_etag,
                       allowed_actions
                  FROM robotics.robot_instances
                 WHERE project_id = %s AND region_code = %s AND robot_id = %s
            """,
                (project_id, region_code, robot_id),
            )
            raw = cursor.fetchone()
            if raw is None:
                return None
            row = _row(cursor, raw)
            binding = (
                None
                if row["binding_id"] is None
                else EffectiveModelBinding(
                    id=str(row["binding_id"]),
                    scope_type=str(row["binding_scope_type"]),
                    scope_id=str(row["binding_scope_id"]),
                    robot_model_version_id=str(row["robot_model_version_id"]),
                    valid_from=cast(datetime, row["binding_valid_from"]),
                    valid_to=cast(datetime | None, row["binding_valid_to"]),
                    etag=str(row["binding_etag"]),
                )
            )
            return RobotBootstrap(
                robot=_robot(row),
                etag=str(row["etag"]),
                topology_revision=str(row["topology_revision"]),
                effective_model_binding=binding,
                allowed_actions=_actions(row["allowed_actions"]),
            )
        finally:
            cursor.close()
            connection.close()

    def list_components(
        self, *, project_id: str, region_code: str, robot_id: str
    ) -> tuple[RobotComponent, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT component_id, robot_id, parent_component_id, component_model_id,
                       component_type,
                       display_name, serial_no, lifecycle_status, sort_order
                  FROM robotics.robot_components
                 WHERE project_id = %s AND region_code = %s AND robot_id = %s
                 ORDER BY sort_order, component_id
            """,
                (project_id, region_code, robot_id),
            )
            return tuple(_component(_row(cursor, raw)) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def get_component(
        self, *, project_id: str, region_code: str, component_id: str
    ) -> RobotComponent | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT component_id, robot_id, parent_component_id, component_model_id,
                       component_type,
                       display_name, serial_no, lifecycle_status, sort_order
                  FROM robotics.robot_components
                 WHERE project_id = %s AND region_code = %s AND component_id = %s
            """,
                (project_id, region_code, component_id),
            )
            raw = cursor.fetchone()
            return None if raw is None else _component(_row(cursor, raw))
        finally:
            cursor.close()
            connection.close()

    def list_frames(
        self, *, project_id: str, region_code: str, component_id: str
    ) -> tuple[FrameReference, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT frame_id, name, parent_frame, source, calibration_set_id, status, valid_from,
                       valid_to
                  FROM robotics.component_frames
                 WHERE project_id = %s AND region_code = %s AND component_id = %s
                 ORDER BY valid_from DESC, frame_id
            """,
                (project_id, region_code, component_id),
            )
            return tuple(
                FrameReference(
                    id=str(row["frame_id"]),
                    name=str(row["name"]),
                    parent_frame=None if row["parent_frame"] is None else str(row["parent_frame"]),
                    source=str(row["source"]),
                    calibration_set_id=str(row["calibration_set_id"]),
                    status=str(row["status"]),
                    valid_from=cast(datetime, row["valid_from"]),
                    valid_to=cast(datetime | None, row["valid_to"]),
                )
                for raw in cursor.fetchall()
                for row in (_row(cursor, raw),)
            )
        finally:
            cursor.close()
            connection.close()

    def list_channels(
        self, *, project_id: str, region_code: str, component_id: str
    ) -> tuple[ChannelReference, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT channel_id, canonical_path, display_name, modality, schema_id,
                       schema_version,
                       role, unit, frequency_hz, frame_id, clock_id, status
                  FROM robotics.component_channels
                 WHERE project_id = %s AND region_code = %s AND component_id = %s
                 ORDER BY canonical_path, channel_id
            """,
                (project_id, region_code, component_id),
            )
            return tuple(
                ChannelReference(
                    id=str(row["channel_id"]),
                    canonical_path=str(row["canonical_path"]),
                    display_name=str(row["display_name"]),
                    modality=str(row["modality"]),
                    schema_id=str(row["schema_id"]),
                    schema_version=str(row["schema_version"]),
                    role=str(row["role"]),
                    unit=None if row["unit"] is None else str(row["unit"]),
                    frequency_hz=None if row["frequency_hz"] is None else str(row["frequency_hz"]),
                    frame_id=None if row["frame_id"] is None else str(row["frame_id"]),
                    clock_id=None if row["clock_id"] is None else str(row["clock_id"]),
                    status=str(row["status"]),
                )
                for raw in cursor.fetchall()
                for row in (_row(cursor, raw),)
            )
        finally:
            cursor.close()
            connection.close()

    def append_audit(self, event: RoboticsAuditEvent) -> None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO core.audit_events (
                    audit_id, project_id, region_code, actor_id, action, resource_type,
                    resource_id, request_id, before_hash, after_hash, details, occurred_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL, %s::jsonb, %s)
            """,
                (
                    str(uuid4()),
                    event.project_id,
                    event.region_code,
                    event.actor_id,
                    event.action,
                    event.resource_type,
                    event.resource_id,
                    event.request_id,
                    json.dumps({"outcome": "SUCCEEDED", **dict(event.details)}, sort_keys=True),
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

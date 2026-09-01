from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from threading import RLock
from typing import Any, Protocol, cast
from uuid import uuid4

from hc_data_platform.robotics.models import (
    Connectivity,
    CreateRobotRequest,
    EffectiveModelBinding,
    RobotBootstrap,
    RobotRecord,
)

from .models import OrganizationRobotModelBinding


class OrganizationRobotAssetRepository(Protocol):
    def organization_exists(self, organization_id: str) -> bool: ...

    def list_robots(
        self,
        *,
        organization_id: str,
        query: str | None,
        lifecycle_status: str | None,
        connectivity_state: str | None,
        configured_only: bool,
    ) -> tuple[RobotRecord, ...]: ...

    def get_robot(self, *, organization_id: str, robot_id: str) -> RobotBootstrap | None: ...

    def create_robot(
        self,
        *,
        organization_id: str,
        robot_id: str,
        command: CreateRobotRequest,
        idempotency_key: str,
        request_fingerprint: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> RobotBootstrap: ...

    def delete_provisional_robot(
        self,
        *,
        organization_id: str,
        robot_id: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> bool: ...

    def bind_model(
        self,
        *,
        organization_id: str,
        robot_id: str,
        version_id: str,
        expected_robot_etag: str,
        idempotency_key: str,
        request_fingerprint: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> OrganizationRobotModelBinding: ...

    def list_model_bindings(
        self, *, organization_id: str, version_id: str
    ) -> tuple[OrganizationRobotModelBinding, ...]: ...


def _robot_from_mapping(row: Mapping[str, Any]) -> RobotRecord:
    return RobotRecord(
        id=str(row["robot_id"]),
        display_name=str(row["display_name"]),
        serial_no=str(row["serial_no"]),
        lifecycle_status=str(row["lifecycle_status"]),
        connectivity=Connectivity(
            state=str(row["connectivity_state"]),
            observed_at=cast(datetime | None, row.get("connectivity_observed_at")),
            source=cast(str | None, row.get("connectivity_source")),
            reason_code=cast(str | None, row.get("connectivity_reason_code")),
        ),
    )


def _bootstrap_from_mapping(row: Mapping[str, Any]) -> RobotBootstrap:
    binding_id = row.get("binding_id")
    return RobotBootstrap(
        robot=_robot_from_mapping(row),
        etag=str(row["etag"]),
        topology_revision=str(row["topology_revision"]),
        effective_model_binding=(
            None
            if binding_id is None
            else EffectiveModelBinding(
                id=str(binding_id),
                scope_type="ROBOT_INSTANCE",
                scope_id=str(row["robot_id"]),
                robot_model_version_id=str(row["robot_model_version_id"]),
                valid_from=cast(datetime, row["binding_valid_from"]),
                valid_to=cast(datetime | None, row.get("binding_valid_to")),
                etag=str(row["binding_etag"]),
            )
        ),
        allowed_actions=tuple(cast(list[str], row.get("allowed_actions") or [])),
    )


class InMemoryOrganizationRobotAssetRepository:
    def __init__(self, organizations: tuple[str, ...] = ()) -> None:
        self._organizations = set(organizations)
        self._robots: dict[tuple[str, str], RobotBootstrap] = {}
        self._receipts: dict[tuple[str, str, str], tuple[str, Any]] = {}
        self._bindings: dict[tuple[str, str], OrganizationRobotModelBinding] = {}
        self._published_versions: set[tuple[str, str]] = set()
        self._lock = RLock()

    def add_published_version(self, organization_id: str, version_id: str) -> None:
        self._organizations.add(organization_id)
        self._published_versions.add((organization_id, version_id))

    def organization_exists(self, organization_id: str) -> bool:
        return organization_id in self._organizations

    def list_robots(
        self,
        *,
        organization_id: str,
        query: str | None,
        lifecycle_status: str | None,
        connectivity_state: str | None,
        configured_only: bool,
    ) -> tuple[RobotRecord, ...]:
        needle = query.casefold().strip() if query else ""
        with self._lock:
            items = [
                bootstrap.robot
                for (organization, _), bootstrap in self._robots.items()
                if organization == organization_id
                and (not configured_only or bootstrap.effective_model_binding is not None)
                and (not lifecycle_status or bootstrap.robot.lifecycle_status == lifecycle_status)
                and (
                    not connectivity_state
                    or bootstrap.robot.connectivity.state == connectivity_state
                )
                and (
                    not needle
                    or needle in bootstrap.robot.display_name.casefold()
                    or needle in bootstrap.robot.serial_no.casefold()
                )
            ]
        return tuple(sorted(items, key=lambda item: (item.display_name.casefold(), item.id)))

    def get_robot(self, *, organization_id: str, robot_id: str) -> RobotBootstrap | None:
        with self._lock:
            return self._robots.get((organization_id, robot_id))

    def create_robot(
        self,
        *,
        organization_id: str,
        robot_id: str,
        command: CreateRobotRequest,
        idempotency_key: str,
        request_fingerprint: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> RobotBootstrap:
        del actor_id, request_id
        receipt_key = (organization_id, "CREATE", idempotency_key)
        with self._lock:
            receipt = self._receipts.get(receipt_key)
            if receipt is not None:
                if receipt[0] != request_fingerprint:
                    raise ValueError("idempotency key was reused")
                return cast(RobotBootstrap, receipt[1])
            provisional = next(
                (
                    item
                    for (organization, _), item in self._robots.items()
                    if organization == organization_id
                    and item.robot.serial_no == command.serial_no
                    and item.robot.lifecycle_status == "DRAFT"
                    and item.effective_model_binding is None
                ),
                None,
            )
            if provisional is not None:
                self._receipts[receipt_key] = (request_fingerprint, provisional)
                return provisional
            if any(
                item.robot.serial_no == command.serial_no
                for (organization, _), item in self._robots.items()
                if organization == organization_id
            ):
                raise ValueError("serial number already exists")
            result = RobotBootstrap(
                robot=RobotRecord(
                    id=robot_id,
                    display_name=command.display_name,
                    serial_no=command.serial_no,
                    lifecycle_status=command.lifecycle_status,
                    connectivity=Connectivity(
                        state=command.connectivity_state,
                        observed_at=occurred_at,
                        source=command.connectivity_source,
                        reason_code=command.connectivity_reason_code,
                    ),
                ),
                etag=f'"robot-asset:{robot_id}:1"',
                topology_revision=f"robot-asset:{robot_id}:1",
                allowed_actions=("VIEW", "EDIT", "TRANSITION", "BIND_MODEL"),
            )
            self._organizations.add(organization_id)
            self._robots[(organization_id, robot_id)] = result
            self._receipts[receipt_key] = (request_fingerprint, result)
            return result

    def delete_provisional_robot(
        self,
        *,
        organization_id: str,
        robot_id: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> bool:
        del actor_id, request_id, occurred_at
        with self._lock:
            robot = self._robots.get((organization_id, robot_id))
            if robot is None:
                return False
            if (
                robot.robot.lifecycle_status != "DRAFT"
                or robot.effective_model_binding is not None
                or any(
                    binding.robot_id == robot_id
                    for (candidate_organization, _), binding in self._bindings.items()
                    if candidate_organization == organization_id
                )
            ):
                raise ValueError("robot is not provisional")
            del self._robots[(organization_id, robot_id)]
            for key, (_fingerprint, response) in tuple(self._receipts.items()):
                response_robot_id = (
                    response.robot.id if isinstance(response, RobotBootstrap) else None
                )
                if key[0] == organization_id and (
                    key[1] == robot_id or response_robot_id == robot_id
                ):
                    del self._receipts[key]
            return True

    def bind_model(
        self,
        *,
        organization_id: str,
        robot_id: str,
        version_id: str,
        expected_robot_etag: str,
        idempotency_key: str,
        request_fingerprint: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> OrganizationRobotModelBinding:
        del actor_id, request_id
        receipt_key = (organization_id, "BIND_MODEL", idempotency_key)
        with self._lock:
            receipt = self._receipts.get(receipt_key)
            if receipt is not None:
                if receipt[0] != request_fingerprint:
                    raise ValueError("idempotency key was reused")
                return cast(OrganizationRobotModelBinding, receipt[1])
            robot = self._robots.get((organization_id, robot_id))
            if robot is None:
                raise KeyError(robot_id)
            if (organization_id, version_id) not in self._published_versions:
                raise KeyError(version_id)
            if robot.etag != expected_robot_etag:
                raise ValueError("robot etag does not match")
            previous_entry = next(
                (
                    (key, binding)
                    for key, binding in self._bindings.items()
                    if key[0] == organization_id
                    and binding.robot_id == robot_id
                    and binding.status == "ACTIVE"
                ),
                None,
            )
            if previous_entry is not None:
                previous_key, previous = previous_entry
                self._bindings[previous_key] = previous.model_copy(
                    update={"status": "SUPERSEDED", "unbound_at": occurred_at}
                )
            binding_id = str(uuid4())
            binding = OrganizationRobotModelBinding(
                binding_id=binding_id,
                robot_id=robot_id,
                version_id=version_id,
                status="ACTIVE",
                bound_at=occurred_at,
            )
            updated = robot.model_copy(
                update={
                    "etag": f'"robot-asset:{robot_id}:binding:{binding_id}"',
                    "topology_revision": f"robot-asset:{robot_id}:binding:{binding_id}",
                    "effective_model_binding": EffectiveModelBinding(
                        id=binding_id,
                        scope_type="ROBOT_INSTANCE",
                        scope_id=robot_id,
                        robot_model_version_id=version_id,
                        valid_from=occurred_at,
                        etag=f'"robot-asset-binding:{binding_id}"',
                    ),
                }
            )
            self._robots[(organization_id, robot_id)] = updated
            self._bindings[(organization_id, binding_id)] = binding
            self._receipts[receipt_key] = (request_fingerprint, binding)
            return binding

    def list_model_bindings(
        self, *, organization_id: str, version_id: str
    ) -> tuple[OrganizationRobotModelBinding, ...]:
        with self._lock:
            items = [
                binding
                for (organization, _), binding in self._bindings.items()
                if organization == organization_id and binding.version_id == version_id
            ]
        return tuple(sorted(items, key=lambda item: (item.bound_at, item.binding_id), reverse=True))


class PostgresOrganizationRobotAssetRepository:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    @staticmethod
    def _row(cursor: Any, raw: Any) -> Mapping[str, Any]:
        return dict(zip((item.name for item in cursor.description), raw, strict=True))

    def organization_exists(self, organization_id: str) -> bool:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT EXISTS(
                    SELECT 1 FROM registry.organization_projects
                    WHERE organization_id = %s
                    UNION ALL
                    SELECT 1 FROM access_control.organization_join_codes
                    WHERE organization_id = %s
                ) AS present
                """,
                (organization_id, organization_id),
            )
            raw = cursor.fetchone()
            return raw is not None and bool(self._row(cursor, raw)["present"])
        finally:
            cursor.close()
            connection.close()

    def list_robots(
        self,
        *,
        organization_id: str,
        query: str | None,
        lifecycle_status: str | None,
        connectivity_state: str | None,
        configured_only: bool,
    ) -> tuple[RobotRecord, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT robot_id, display_name, serial_no, lifecycle_status,
                       connectivity_state, connectivity_observed_at,
                       connectivity_source, connectivity_reason_code
                  FROM robotics.robot_assets
                 WHERE organization_id = %s
                   AND (
                       NOT %s
                       OR EXISTS (
                           SELECT 1
                             FROM registry.organization_robot_model_bindings AS binding
                            WHERE binding.organization_id = robotics.robot_assets.organization_id
                              AND binding.robot_id = robotics.robot_assets.robot_id
                              AND binding.status = 'ACTIVE'
                       )
                   )
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
                    organization_id,
                    configured_only,
                    lifecycle_status,
                    lifecycle_status,
                    connectivity_state,
                    connectivity_state,
                    query,
                    query,
                    query,
                ),
            )
            return tuple(_robot_from_mapping(self._row(cursor, raw)) for raw in cursor.fetchall())
        finally:
            cursor.close()
            connection.close()

    def list_model_bindings(
        self, *, organization_id: str, version_id: str
    ) -> tuple[OrganizationRobotModelBinding, ...]:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT binding_id, robot_id, version_id, status, bound_at, unbound_at
                  FROM registry.organization_robot_model_bindings
                 WHERE organization_id = %s AND version_id = %s
                 ORDER BY bound_at DESC, binding_id DESC
                """,
                (organization_id, version_id),
            )
            return tuple(
                OrganizationRobotModelBinding.model_validate(self._row(cursor, raw))
                for raw in cursor.fetchall()
            )
        finally:
            cursor.close()
            connection.close()

    def get_robot(self, *, organization_id: str, robot_id: str) -> RobotBootstrap | None:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT robot_id, display_name, serial_no, lifecycle_status,
                       connectivity_state, connectivity_observed_at,
                       connectivity_source, connectivity_reason_code, etag,
                       topology_revision, binding_id, robot_model_version_id,
                       binding_valid_from, binding_valid_to, binding_etag,
                       allowed_actions
                  FROM robotics.robot_assets
                 WHERE organization_id = %s AND robot_id = %s
                """,
                (organization_id, robot_id),
            )
            raw = cursor.fetchone()
            return None if raw is None else _bootstrap_from_mapping(self._row(cursor, raw))
        finally:
            cursor.close()
            connection.close()

    @staticmethod
    def _read_receipt(
        cursor: Any,
        *,
        organization_id: str,
        resource_id: str,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Mapping[str, Any] | None:
        cursor.execute(
            """
            SELECT request_fingerprint, response
              FROM robotics.robot_asset_command_receipts
             WHERE organization_id = %s AND resource_id = %s
               AND operation = %s AND idempotency_key = %s
             FOR UPDATE
            """,
            (organization_id, resource_id, operation, idempotency_key),
        )
        raw = cursor.fetchone()
        if raw is None:
            return None
        row = PostgresOrganizationRobotAssetRepository._row(cursor, raw)
        if str(row["request_fingerprint"]) != request_fingerprint:
            raise ValueError("idempotency key was reused")
        response = row["response"]
        return cast(
            Mapping[str, Any],
            json.loads(response) if isinstance(response, str) else response,
        )

    @staticmethod
    def _store_receipt(
        cursor: Any,
        *,
        organization_id: str,
        resource_id: str,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
        response: Mapping[str, Any],
        occurred_at: datetime,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO robotics.robot_asset_command_receipts (
                organization_id, resource_id, operation, idempotency_key,
                request_fingerprint, response, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                organization_id,
                resource_id,
                operation,
                idempotency_key,
                request_fingerprint,
                json.dumps(response),
                occurred_at,
            ),
        )

    @staticmethod
    def _append_audit(
        cursor: Any,
        *,
        organization_id: str,
        actor_id: str,
        action: str,
        resource_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO robotics.robot_asset_audit_events (
                organization_id, actor_id, action, resource_id, request_id,
                occurred_at, details
            ) VALUES (%s, %s, %s, %s, %s, %s, '{}'::jsonb)
            """,
            (organization_id, actor_id, action, resource_id, request_id, occurred_at),
        )

    def create_robot(
        self,
        *,
        organization_id: str,
        robot_id: str,
        command: CreateRobotRequest,
        idempotency_key: str,
        request_fingerprint: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> RobotBootstrap:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"organization-robot:create:{organization_id}:{idempotency_key}",),
            )
            replay = self._read_receipt(
                cursor,
                organization_id=organization_id,
                resource_id=robot_id,
                operation="CREATE",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                connection.commit()
                return RobotBootstrap.model_validate(replay)
            cursor.execute(
                """
                SELECT robot_id
                  FROM robotics.robot_assets
                 WHERE organization_id = %s
                   AND serial_no = %s
                   AND lifecycle_status = 'DRAFT'
                   AND binding_id IS NULL
                 FOR UPDATE
                """,
                (organization_id, command.serial_no),
            )
            provisional_row = cursor.fetchone()
            if provisional_row is not None:
                provisional_id = str(self._row(cursor, provisional_row)["robot_id"])
                result = self._get_locked(cursor, organization_id, provisional_id)
                self._store_receipt(
                    cursor,
                    organization_id=organization_id,
                    resource_id=robot_id,
                    operation="CREATE",
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                    response=result.model_dump(mode="json"),
                    occurred_at=occurred_at,
                )
                self._append_audit(
                    cursor,
                    organization_id=organization_id,
                    actor_id=actor_id,
                    action="robot.asset.provisional_resumed",
                    resource_id=provisional_id,
                    request_id=request_id,
                    occurred_at=occurred_at,
                )
                connection.commit()
                return result
            cursor.execute(
                """
                INSERT INTO robotics.robot_assets (
                    organization_id, robot_id, display_name, serial_no,
                    lifecycle_status, connectivity_state,
                    connectivity_observed_at, connectivity_source,
                    connectivity_reason_code, etag, topology_revision,
                    allowed_actions, revision, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    '"robot-asset:' || %s || ':1"',
                    'robot-asset:' || %s || ':1',
                    '["VIEW", "EDIT", "TRANSITION", "BIND_MODEL"]'::jsonb,
                    1, %s, %s
                )
                """,
                (
                    organization_id,
                    robot_id,
                    command.display_name,
                    command.serial_no,
                    command.lifecycle_status,
                    command.connectivity_state,
                    occurred_at,
                    command.connectivity_source,
                    command.connectivity_reason_code,
                    robot_id,
                    robot_id,
                    occurred_at,
                    occurred_at,
                ),
            )
            result = self._get_locked(cursor, organization_id, robot_id)
            self._store_receipt(
                cursor,
                organization_id=organization_id,
                resource_id=robot_id,
                operation="CREATE",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                response=result.model_dump(mode="json"),
                occurred_at=occurred_at,
            )
            self._append_audit(
                cursor,
                organization_id=organization_id,
                actor_id=actor_id,
                action="robot.asset.created",
                resource_id=robot_id,
                request_id=request_id,
                occurred_at=occurred_at,
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def _get_locked(self, cursor: Any, organization_id: str, robot_id: str) -> RobotBootstrap:
        cursor.execute(
            """
            SELECT robot_id, display_name, serial_no, lifecycle_status,
                   connectivity_state, connectivity_observed_at,
                   connectivity_source, connectivity_reason_code, etag,
                   topology_revision, binding_id, robot_model_version_id,
                   binding_valid_from, binding_valid_to, binding_etag,
                   allowed_actions
              FROM robotics.robot_assets
             WHERE organization_id = %s AND robot_id = %s
             FOR UPDATE
            """,
            (organization_id, robot_id),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise KeyError(robot_id)
        return _bootstrap_from_mapping(self._row(cursor, raw))

    def delete_provisional_robot(
        self,
        *,
        organization_id: str,
        robot_id: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> bool:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT lifecycle_status, binding_id, robot_model_version_id
                  FROM robotics.robot_assets
                 WHERE organization_id = %s AND robot_id = %s
                 FOR UPDATE
                """,
                (organization_id, robot_id),
            )
            raw = cursor.fetchone()
            if raw is None:
                connection.commit()
                return False
            row = self._row(cursor, raw)
            if (
                str(row["lifecycle_status"]) != "DRAFT"
                or row["binding_id"] is not None
                or row["robot_model_version_id"] is not None
            ):
                raise ValueError("robot is not provisional")
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                      FROM registry.organization_robot_model_bindings
                     WHERE organization_id = %s AND robot_id = %s
                    UNION ALL
                    SELECT 1
                      FROM robotics.robot_asset_components
                     WHERE organization_id = %s AND robot_id = %s
                    UNION ALL
                    SELECT 1
                      FROM robotics.project_robot_assignments
                     WHERE organization_id = %s AND robot_id = %s
                ) AS in_use
                """,
                (
                    organization_id,
                    robot_id,
                    organization_id,
                    robot_id,
                    organization_id,
                    robot_id,
                ),
            )
            usage = cursor.fetchone()
            if usage is None or bool(self._row(cursor, usage)["in_use"]):
                raise ValueError("robot is not provisional")
            cursor.execute(
                """
                DELETE FROM robotics.robot_asset_command_receipts
                 WHERE organization_id = %s
                   AND (resource_id = %s OR response #>> '{robot,id}' = %s)
                """,
                (organization_id, robot_id, robot_id),
            )
            self._append_audit(
                cursor,
                organization_id=organization_id,
                actor_id=actor_id,
                action="robot.asset.provisional_deleted",
                resource_id=robot_id,
                request_id=request_id,
                occurred_at=occurred_at,
            )
            cursor.execute(
                """
                DELETE FROM robotics.robot_assets
                 WHERE organization_id = %s AND robot_id = %s
                """,
                (organization_id, robot_id),
            )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def bind_model(
        self,
        *,
        organization_id: str,
        robot_id: str,
        version_id: str,
        expected_robot_etag: str,
        idempotency_key: str,
        request_fingerprint: str,
        actor_id: str,
        request_id: str,
        occurred_at: datetime,
    ) -> OrganizationRobotModelBinding:
        connection = self._connection_factory()
        cursor = connection.cursor()
        try:
            current = self._get_locked(cursor, organization_id, robot_id)
            replay = self._read_receipt(
                cursor,
                organization_id=organization_id,
                resource_id=robot_id,
                operation="BIND_MODEL",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                connection.commit()
                return OrganizationRobotModelBinding.model_validate(replay)
            if current.etag != expected_robot_etag:
                raise ValueError("robot etag does not match")
            cursor.execute(
                """
                SELECT lifecycle
                  FROM registry.robot_model_versions
                 WHERE organization_id = %s AND version_id = %s
                """,
                (organization_id, version_id),
            )
            raw_version = cursor.fetchone()
            if raw_version is None:
                raise KeyError(version_id)
            if str(self._row(cursor, raw_version)["lifecycle"]) != "PUBLISHED":
                raise ValueError("robot model version is not published")
            cursor.execute(
                """
                UPDATE registry.organization_robot_model_bindings
                   SET status = 'SUPERSEDED', unbound_at = %s
                 WHERE organization_id = %s AND robot_id = %s AND status = 'ACTIVE'
                """,
                (occurred_at, organization_id, robot_id),
            )
            binding_id = str(uuid4())
            binding_etag = f'"robot-asset-binding:{binding_id}"'
            cursor.execute(
                """
                INSERT INTO registry.organization_robot_model_bindings (
                    organization_id, binding_id, robot_id, version_id,
                    idempotency_key, request_fingerprint, expected_robot_etag,
                    status, bound_at, unbound_at
                ) VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, 'ACTIVE', %s, NULL)
                """,
                (
                    organization_id,
                    binding_id,
                    robot_id,
                    version_id,
                    idempotency_key,
                    request_fingerprint,
                    expected_robot_etag,
                    occurred_at,
                ),
            )
            cursor.execute(
                """
                UPDATE robotics.robot_assets
                   SET binding_id = %s::uuid,
                       robot_model_version_id = %s,
                       binding_valid_from = %s,
                       binding_valid_to = NULL,
                       binding_etag = %s,
                       revision = revision + 1,
                       etag = '"robot-asset:' || robot_id || ':' || (revision + 1)::text || '"',
                       topology_revision =
                           'robot-asset:' || robot_id || ':' || (revision + 1)::text,
                       updated_at = %s
                 WHERE organization_id = %s AND robot_id = %s
                """,
                (
                    binding_id,
                    version_id,
                    occurred_at,
                    binding_etag,
                    occurred_at,
                    organization_id,
                    robot_id,
                ),
            )
            binding = OrganizationRobotModelBinding(
                binding_id=binding_id,
                robot_id=robot_id,
                version_id=version_id,
                status="ACTIVE",
                bound_at=occurred_at,
            )
            self._store_receipt(
                cursor,
                organization_id=organization_id,
                resource_id=robot_id,
                operation="BIND_MODEL",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                response=binding.model_dump(mode="json"),
                occurred_at=occurred_at,
            )
            self._append_audit(
                cursor,
                organization_id=organization_id,
                actor_id=actor_id,
                action="robot.asset.model_bound",
                resource_id=robot_id,
                request_id=request_id,
                occurred_at=occurred_at,
            )
            connection.commit()
            return binding
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

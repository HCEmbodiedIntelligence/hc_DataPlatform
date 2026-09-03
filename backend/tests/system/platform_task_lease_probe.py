"""Real PostgreSQL/Kubernetes fault probe for platform singleton task leases.

This file is mounted into disposable acceptance Pods and deliberately imports the
production repository and commit fence. It is not an application entry point.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg

from hc_data_platform.core.context import (
    RequestContext,
    bind_platform_task_lease,
    bind_request_context,
    reset_platform_task_lease,
    reset_request_context,
)
from hc_data_platform.core.dbapi import normalize_postgres_dsn, psycopg_connection_factory
from hc_data_platform.platform_control.maintenance_contract import MaintenanceContractError
from hc_data_platform.platform_ops.task_leases import (
    TASK_LEASE_RENEWAL_INTERVAL_SECONDS,
    PostgresPlatformTaskLeaseRepository,
)
from hc_data_platform.storage.inventory_worker import _inventory_task_id, parse_inventory_scope


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


DSN = normalize_postgres_dsn(_required("HC_POSTGRES_DSN"))
ENVIRONMENT_ID = _required("HC_PLATFORM_ENVIRONMENT_ID")
INVENTORY_SCOPE = _required("HC_INVENTORY_SCOPE")
TASK_ID = _inventory_task_id(INVENTORY_SCOPE)
SCOPE = parse_inventory_scope(INVENTORY_SCOPE)
OWNER_ID = UUID(_required("HC_OWNER_INSTANCE_ID"))


def emit(event: str, **details: object) -> None:
    print(
        json.dumps(
            {
                "event": event,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "task_id": TASK_ID,
                **details,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )


def initialize_probe_table() -> None:
    with psycopg.connect(DSN) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS platform.task_lease_probe_writes (
                environment_id text NOT NULL,
                task_id text NOT NULL,
                writer_label text NOT NULL,
                lease_id uuid NOT NULL,
                fencing_token bigint NOT NULL,
                committed_at timestamptz NOT NULL DEFAULT statement_timestamp(),
                PRIMARY KEY (environment_id, task_id, writer_label)
            )
            """
        )


def fenced_write(*, label: str, lease_id: UUID, fencing_token: int) -> None:
    request_token = bind_request_context(
        RequestContext(
            organization_id=SCOPE.organization_id,
            project_id=SCOPE.project_id,
            region_code=SCOPE.region_code,
            subject_id=f"platform-task-lease-probe:{label}",
            service_identity=True,
        )
    )
    lease_token = bind_platform_task_lease(lease_id)
    try:
        with psycopg_connection_factory(DSN)() as connection:
            connection.execute(
                """
                INSERT INTO platform.task_lease_probe_writes (
                    environment_id, task_id, writer_label, lease_id, fencing_token
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (ENVIRONMENT_ID, TASK_ID, label, lease_id, fencing_token),
            )
    finally:
        reset_platform_task_lease(lease_token)
        reset_request_context(request_token)


def owner_a() -> int:
    initialize_probe_table()
    repository = PostgresPlatformTaskLeaseRepository.from_dsn(DSN)
    lease = repository.try_acquire(
        environment_id=ENVIRONMENT_ID,
        task_id=TASK_ID,
        owner_instance_id=OWNER_ID,
    )
    if lease is None:
        emit("OWNER_A_ACQUIRE_REJECTED")
        return 2
    emit(
        "OWNER_A_ACQUIRED",
        lease_id=str(lease.lease_id),
        fencing_token=lease.fencing_token,
        lease_version=lease.lease_version,
    )
    fenced_write(
        label="owner-a-before-partition",
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
    )
    emit("OWNER_A_CURRENT_COMMIT_SUCCEEDED")

    while True:
        time.sleep(TASK_LEASE_RENEWAL_INTERVAL_SECONDS)
        try:
            renewed = repository.renew(lease.lease_id)
        except psycopg.Error as exc:
            emit("OWNER_A_RENEWAL_DISCONNECTED", error_type=type(exc).__name__)
            break
        except MaintenanceContractError as exc:
            emit("OWNER_A_RENEWAL_UNEXPECTEDLY_REJECTED", error_code=exc.code)
            return 3
        emit(
            "OWNER_A_RENEWED",
            lease_id=str(renewed.lease_id),
            fencing_token=renewed.fencing_token,
            lease_version=renewed.lease_version,
        )

    while True:
        try:
            repository.renew(lease.lease_id)
        except MaintenanceContractError as exc:
            emit("OWNER_A_OLD_LEASE_RENEWAL_REJECTED", error_code=exc.code)
            if exc.code != "PLATFORM_TASK_LEASE_STALE":
                return 4
            break
        except psycopg.Error as exc:
            emit("OWNER_A_RECONNECT_PENDING", error_type=type(exc).__name__)
            time.sleep(2)
            continue
        emit("OWNER_A_OLD_LEASE_RENEWED_UNEXPECTEDLY")
        return 5

    try:
        fenced_write(
            label="owner-a-after-reconnect",
            lease_id=lease.lease_id,
            fencing_token=lease.fencing_token,
        )
    except MaintenanceContractError as exc:
        emit("OWNER_A_STALE_COMMIT_REJECTED", error_code=exc.code)
        return 0 if exc.code == "PLATFORM_TASK_LEASE_STALE" else 6
    emit("OWNER_A_STALE_COMMIT_SUCCEEDED_UNEXPECTEDLY")
    return 7


def owner_b() -> int:
    initialize_probe_table()
    repository = PostgresPlatformTaskLeaseRepository.from_dsn(DSN)
    attempts = 0
    while True:
        attempts += 1
        lease = repository.try_acquire(
            environment_id=ENVIRONMENT_ID,
            task_id=TASK_ID,
            owner_instance_id=OWNER_ID,
        )
        if lease is not None:
            break
        if attempts % 5 == 0:
            emit("OWNER_B_WAITING_FOR_DATABASE_CLOCK_EXPIRY", attempts=attempts)
        time.sleep(2)

    with psycopg.connect(DSN) as connection:
        previous_token_row = connection.execute(
            """
            SELECT max(fencing_token)
            FROM platform.platform_task_lease_events
            WHERE environment_id = %s AND task_id = %s AND lease_id <> %s
            """,
            (ENVIRONMENT_ID, TASK_ID, lease.lease_id),
        ).fetchone()
    if previous_token_row is None:
        emit("OWNER_B_PREVIOUS_TOKEN_MISSING")
        return 8
    previous_token = previous_token_row[0]
    if previous_token is None or lease.fencing_token <= int(previous_token):
        emit(
            "OWNER_B_TOKEN_NOT_MONOTONIC",
            fencing_token=lease.fencing_token,
            previous_token=previous_token,
        )
        return 8
    emit(
        "OWNER_B_TAKEN_OVER",
        attempts=attempts,
        lease_id=str(lease.lease_id),
        fencing_token=lease.fencing_token,
        previous_token=int(previous_token),
        lease_version=lease.lease_version,
    )
    fenced_write(
        label="owner-b-after-takeover",
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
    )
    emit("OWNER_B_CURRENT_COMMIT_SUCCEEDED")
    repository.release(lease.lease_id)
    emit("OWNER_B_RELEASED")
    return 0


def summary() -> int:
    with psycopg.connect(DSN) as connection:
        current = connection.execute(
            """
            SELECT lease_id, owner_instance_id, fencing_token, lease_version,
                   lease_until <= statement_timestamp() AS expired
            FROM platform.platform_task_leases
            WHERE environment_id = %s AND task_id = %s
            """,
            (ENVIRONMENT_ID, TASK_ID),
        ).fetchone()
        events = connection.execute(
            """
            SELECT event_code, lease_id, owner_instance_id, fencing_token, lease_version
            FROM platform.platform_task_lease_events
            WHERE environment_id = %s AND task_id = %s
            ORDER BY event_id
            """,
            (ENVIRONMENT_ID, TASK_ID),
        ).fetchall()
        writes = connection.execute(
            """
            SELECT writer_label, lease_id, fencing_token
            FROM platform.task_lease_probe_writes
            WHERE environment_id = %s AND task_id = %s
            ORDER BY writer_label
            """,
            (ENVIRONMENT_ID, TASK_ID),
        ).fetchall()

    event_codes = [row[0] for row in events]
    write_labels = [row[0] for row in writes]
    passed = (
        current is not None
        and current[4] is True
        and event_codes
        == [
            "PLATFORM_TASK_LEASE_ACQUIRED",
            "PLATFORM_TASK_LEASE_TAKEN_OVER",
            "PLATFORM_TASK_LEASE_RELEASED",
        ]
        and write_labels
        == [
            "owner-a-before-partition",
            "owner-b-after-takeover",
        ]
        and events[1][3] > events[0][3]
        and writes[0][1] == events[0][1]
        and writes[1][1] == events[1][1]
    )
    payload: dict[str, Any] = {
        "passed": passed,
        "current": None
        if current is None
        else {
            "lease_id": str(current[0]),
            "owner_instance_id": str(current[1]),
            "fencing_token": current[2],
            "lease_version": current[3],
            "expired": current[4],
        },
        "events": [
            {
                "event_code": row[0],
                "lease_id": str(row[1]),
                "owner_instance_id": str(row[2]),
                "fencing_token": row[3],
                "lease_version": row[4],
            }
            for row in events
        ],
        "writes": [
            {"writer_label": row[0], "lease_id": str(row[1]), "fencing_token": row[2]}
            for row in writes
        ],
    }
    emit("PLATFORM_TASK_LEASE_PROBE_SUMMARY", **payload)
    return 0 if passed else 9


def main() -> int:
    modes = {"owner-a": owner_a, "owner-b": owner_b, "summary": summary}
    if len(sys.argv) != 2 or sys.argv[1] not in modes:
        print(f"usage: {sys.argv[0]} <{'|'.join(modes)}>", file=sys.stderr)
        return 64
    return modes[sys.argv[1]]()


if __name__ == "__main__":
    raise SystemExit(main())

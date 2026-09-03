"""Global media-capacity leases shared by all media pods."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from threading import RLock
from typing import Any, cast


class UnlimitedMediaCapacityGate:
    """Explicit local/test adapter which never imposes deployment capacity."""

    def acquire(
        self,
        *,
        owner_id: str,
        attempt_token: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> int | None:
        del owner_id, attempt_token, lease_expires_at, now
        return 1

    def renew(
        self,
        *,
        slot_id: int,
        owner_id: str,
        attempt_token: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> bool:
        del slot_id, owner_id, attempt_token, lease_expires_at, now
        return True

    def release(self, *, slot_id: int, owner_id: str, attempt_token: str) -> bool:
        del slot_id, owner_id, attempt_token
        return True


class InMemoryMediaCapacityGate:
    def __init__(self, limit: int) -> None:
        if limit < 1 or limit > 128:
            raise ValueError("global media capacity must be between 1 and 128")
        self._limit = limit
        self._slots: dict[int, tuple[str, str, datetime]] = {}
        self._lock = RLock()

    def acquire(
        self,
        *,
        owner_id: str,
        attempt_token: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> int | None:
        with self._lock:
            for slot_id in range(1, self._limit + 1):
                current = self._slots.get(slot_id)
                if current is None or current[2] <= now:
                    self._slots[slot_id] = (owner_id, attempt_token, lease_expires_at)
                    return slot_id
        return None

    def renew(
        self,
        *,
        slot_id: int,
        owner_id: str,
        attempt_token: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> bool:
        with self._lock:
            current = self._slots.get(slot_id)
            if current is None or current[:2] != (owner_id, attempt_token) or current[2] <= now:
                return False
            self._slots[slot_id] = (owner_id, attempt_token, lease_expires_at)
            return True

    def release(self, *, slot_id: int, owner_id: str, attempt_token: str) -> bool:
        with self._lock:
            current = self._slots.get(slot_id)
            if current is None or current[:2] != (owner_id, attempt_token):
                return False
            del self._slots[slot_id]
            return True


class PostgresMediaCapacityGate:
    """Claim one global slot with a single conditional UPDATE statement."""

    def __init__(self, connection_factory: Callable[[], Any], *, limit: int) -> None:
        if limit < 1 or limit > 128:
            raise ValueError("global media capacity must be between 1 and 128")
        self._factory = connection_factory
        self._limit = limit

    def acquire(
        self,
        *,
        owner_id: str,
        attempt_token: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> int | None:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                WITH candidate AS (
                    SELECT slot_id
                    FROM aligned_media.media_capacity_slots
                    WHERE slot_id <= %s
                      AND (lease_expires_at IS NULL OR lease_expires_at <= %s)
                    ORDER BY slot_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE aligned_media.media_capacity_slots AS slot
                SET owner_id = %s, attempt_token = %s::uuid,
                    lease_expires_at = %s, heartbeat_at = %s
                FROM candidate
                WHERE slot.slot_id = candidate.slot_id
                  AND (slot.lease_expires_at IS NULL OR slot.lease_expires_at <= %s)
                RETURNING slot.slot_id
                """,
                (
                    self._limit,
                    now,
                    owner_id,
                    attempt_token,
                    lease_expires_at,
                    now,
                    now,
                ),
            )
            row = cursor.fetchone()
            connection.commit()
            if row is None:
                return None
            value = row["slot_id"] if isinstance(row, Mapping) else cast(Sequence[object], row)[0]
            if not isinstance(value, int):
                raise TypeError("media capacity slot_id must be an integer")
            return value
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def renew(
        self,
        *,
        slot_id: int,
        owner_id: str,
        attempt_token: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> bool:
        return self._mutate(
            """
            UPDATE aligned_media.media_capacity_slots
            SET lease_expires_at = %s, heartbeat_at = %s
            WHERE slot_id = %s AND owner_id = %s
              AND attempt_token = %s::uuid AND lease_expires_at > %s
            """,
            (lease_expires_at, now, slot_id, owner_id, attempt_token, now),
        )

    def release(self, *, slot_id: int, owner_id: str, attempt_token: str) -> bool:
        return self._mutate(
            """
            UPDATE aligned_media.media_capacity_slots
            SET owner_id = NULL, attempt_token = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE slot_id = %s AND owner_id = %s AND attempt_token = %s::uuid
            """,
            (slot_id, owner_id, attempt_token),
        )

    def _mutate(self, statement: str, parameters: tuple[object, ...]) -> bool:
        connection = self._factory()
        cursor = connection.cursor()
        try:
            cursor.execute(statement, parameters)
            changed = bool(cursor.rowcount == 1)
            connection.commit()
            return changed
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import RLock
from typing import Any

from .models import Endpoint, RobotPresence

LEASE = 20


class PresenceStore:
    """One transaction per robot, including its exclusive operator lease."""

    def __init__(self, connect: Callable[[], Any] | None = None):
        self.connect = connect
        self.rows: dict[tuple[str, str], Endpoint] = {}
        self.lock = RLock()

    @contextmanager
    def edit(
        self, org: str, robot: str, *, initial: Endpoint | None = None
    ) -> Iterator[Endpoint | None]:
        if self.connect is None:
            with self.lock:
                previous = self.rows.get((org, robot), initial)
                row = previous.model_copy(deep=True) if previous else None
                yield row
                if row:
                    self.rows[org, robot] = row
            return
        with self.connect() as db, db.cursor() as cur:
            if initial:
                cur.execute(
                    """INSERT INTO ingest.teleoperation_endpoints
                    (organization_id,robot_id,document) VALUES (%s,%s,%s::jsonb)
                    ON CONFLICT DO NOTHING""",
                    (org, robot, initial.model_dump_json()),
                )
            cur.execute(
                """SELECT document FROM ingest.teleoperation_endpoints
                WHERE organization_id=%s AND robot_id=%s FOR UPDATE""",
                (org, robot),
            )
            result = cur.fetchone()
            row = Endpoint.model_validate(result[0]) if result else None
            yield row
            if row:
                cur.execute(
                    """UPDATE ingest.teleoperation_endpoints SET document=%s::jsonb
                    WHERE organization_id=%s AND robot_id=%s""",
                    (row.model_dump_json(), org, robot),
                )

    def online(self, org: str, now: float) -> list[RobotPresence]:
        if self.connect is None:
            with self.lock:
                rows = [
                    (rid, r.name, r.seen, bool(r.session and r.session.expires > now))
                    for (tenant, rid), r in self.rows.items()
                    if tenant == org and r.seen > now - LEASE
                ]
        else:
            with self.connect() as db, db.cursor() as cur:
                cur.execute(
                    """SELECT robot_id, document->>'name',
                    (document->>'seen')::double precision,
                    COALESCE((document#>>'{session,expires}')::double precision > %s, false)
                    FROM ingest.teleoperation_endpoints WHERE organization_id=%s
                    AND (document->>'seen')::double precision > %s""",
                    (now, org, now - LEASE),
                )
                rows = cur.fetchall()
        return [
            RobotPresence(robot_id=rid, name=name, last_seen=seen, busy=busy)
            for rid, name, seen, busy in sorted(rows)
        ]

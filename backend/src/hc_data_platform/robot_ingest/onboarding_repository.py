"""Bounded SQL round trips for organization facts and committed Raw volume."""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from .onboarding_models import LegacyRobotSource, OnboardingFacts


def read_facts(
    connections: Callable[[], Any],
    organization_id: str,
    legacy_scopes: tuple[tuple[str, str], ...] = (),
) -> tuple[OnboardingFacts, ...]:
    connection = connections()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """SELECT r.robot_id, r.display_name, r.serial_no, r.lifecycle_status,
                      r.robot_model_version_id, r.updated_at, i.identity_document,
                      COALESCE(c.items, '[]'::jsonb)
               FROM robotics.robot_assets r
               LEFT JOIN ingest.robot_ingest_identities i
                 ON i.organization_id=r.organization_id AND i.robot_id=r.robot_id
                AND i.deleted_at IS NULL
               LEFT JOIN LATERAL (
                 SELECT jsonb_agg(jsonb_build_object(
                   'credential_id', credential_id, 'credential_version', credential_version,
                   'state', state, 'token_prefix', token_prefix, 'issued_at', issued_at,
                   'expires_at', expires_at, 'revoked_at', revoked_at,
                   'last_authenticated_at', last_authenticated_at,
                   'reveal_available', secret_ciphertext IS NOT NULL)) AS items
                 FROM ingest.robot_ingest_credentials c
                 WHERE c.organization_id=i.organization_id
                   AND c.ingest_identity_id=i.ingest_identity_id
                   AND c.deleted_at IS NULL
               ) c ON true
               WHERE r.organization_id=%s ORDER BY r.robot_id""",
            (organization_id,),
        )
        facts = tuple(
            OnboardingFacts(
                robot_id=row[0],
                display_name=row[1],
                serial_no=row[2],
                lifecycle_status=row[3],
                model_version_id=row[4],
                updated_at=row[5],
                identity=row[6],
                credentials=row[7],
            )
            for row in cursor.fetchall()
        )
        legacy: dict[str, list[LegacyRobotSource]] = {}
        # Each scope is explicitly authorized by the route. Keep project RLS in
        # effect while reading old associations, including on non-superuser roles.
        for project_id, region_code in legacy_scopes:
            cursor.execute(
                "SELECT set_config('app.project_id', %s, true), "
                "set_config('app.region_code', %s, true)",
                (project_id, region_code),
            )
            cursor.execute(
                """SELECT source_document->'binding'->>'robot_id', source_id, administrative_state
                   FROM ingest.data_sources WHERE organization_id=%s AND project_id=%s
                     AND region_code=%s AND source_type='ROBOT' ORDER BY source_id""",
                (organization_id, project_id, region_code),
            )
            for robot_id, source_id, state in cursor.fetchall():
                legacy.setdefault(robot_id, []).append(
                    LegacyRobotSource(
                        source_id=source_id,
                        project_id=project_id,
                        region_code=region_code,
                        administrative_state=state,
                    )
                )
        return tuple(
            item.model_copy(update={"legacy_sources": tuple(legacy.get(item.robot_id, ()))})
            for item in facts
        )
    finally:
        cursor.close()
        connection.close()


def volume(
    connections: Callable[[], Any],
    *,
    organization_id: str,
    project_id: str,
    region_code: str,
    start: datetime,
    end: datetime,
    robot_id: str | None,
) -> tuple[int, int]:
    connection = connections()
    cursor = connection.cursor()
    try:
        # raw_sources has a unique scope/format/upload identity and immutable commit
        # timestamps. No join to assets/attempts: neither retries nor processing fan-out
        # can multiply original file bytes. Includes robot, browser and LeRobot commits.
        cursor.execute(
            """SELECT COALESCE(SUM(total_bytes),0), COUNT(*) FROM ingest.raw_sources
               WHERE organization_id=%s AND project_id=%s AND region_code=%s
                 AND raw_status='COMMITTED' AND committed_at >= %s AND committed_at < %s
                 AND (%s::text IS NULL OR robotics.robot_reference_owner(organization_id,
                 'raw',project_id,region_code,raw_source_id,robot_id)=%s)""",
            (organization_id, project_id, region_code, start, end, robot_id, robot_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Commit volume aggregate returned no result")
        return int(row[0]), int(row[1])
    finally:
        cursor.close()
        connection.close()


def heartbeat(
    connections: Callable[[], Any],
    organization_id: str,
    identity_id: str,
    observed_at: datetime,
    activity_expires_at: datetime | None,
) -> None:
    connection = connections()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """UPDATE ingest.robot_ingest_identities
               SET identity_document=identity_document || jsonb_build_object(
                 'last_heartbeat_at', %s::timestamptz, 'activity_expires_at', %s::timestamptz)
               WHERE organization_id=%s AND ingest_identity_id=%s
                 AND state='ENABLED' AND deleted_at IS NULL""",
            (observed_at, activity_expires_at, organization_id, identity_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

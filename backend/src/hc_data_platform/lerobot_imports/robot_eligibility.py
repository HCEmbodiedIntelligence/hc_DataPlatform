"""Operator imports use project authorization and canonical organization assets.

Robot-token permission is independent. Explicit project revocations and disabled
legacy sources remain effective; absence of a connector is no longer a denial.
"""

from typing import Any

from pydantic import BaseModel


class ImportRobotOption(BaseModel):
    robot_id: str
    display_name: str
    serial_no: str


def import_robots(
    db: Any, organization_id: str, project_id: str, region_code: str, robot_id: str | None = None
) -> list[ImportRobotOption]:
    rows = db.execute(
        """SELECT r.robot_id, r.display_name, r.serial_no FROM robotics.robot_assets r
        WHERE r.organization_id=%s AND r.lifecycle_status='ACTIVE'
          AND (%s::text IS NULL OR r.robot_id=%s)
          AND NOT EXISTS (SELECT 1 FROM robotics.project_robot_assignments a
            WHERE a.organization_id=r.organization_id AND a.robot_id=r.robot_id
              AND a.project_id=%s AND a.region_code=%s AND NOT a.active)
          AND NOT EXISTS (SELECT 1 FROM ingest.data_sources s
            WHERE s.organization_id=r.organization_id AND s.project_id=%s AND s.region_code=%s
              AND s.source_type='ROBOT' AND s.administrative_state='DISABLED'
              AND s.source_document->'binding'->>'robot_id'=r.robot_id)
        ORDER BY lower(r.display_name), r.robot_id""",
        (organization_id, robot_id, robot_id, project_id, region_code, project_id, region_code),
    ).fetchall()
    return [
        ImportRobotOption(robot_id=row[0], display_name=row[1], serial_no=row[2]) for row in rows
    ]

"""Model-owned, transaction-bound reads of published immutable assets and mappings."""

from collections.abc import Callable
from typing import Any

from .models import RobotModelAsset, RobotModelJointMapping


def published_model_inputs(
    cursor: Any, organization_id: str, version_id: str
) -> tuple[str, str, tuple[RobotModelAsset, ...], str, tuple[RobotModelJointMapping, ...]]:
    cursor.execute(
        """SELECT lifecycle,asset_manifest_hash,validation_input_hash,robot_model_id
        FROM registry.robot_model_versions WHERE organization_id=%s AND version_id=%s FOR SHARE""",
        (organization_id, version_id),
    )
    version = cursor.fetchone()
    if version is None or version[0] != "PUBLISHED":
        raise ValueError("frozen scene requires a published model version")
    cursor.execute(
        """SELECT asset_id,relative_path,role,media_type,size_bytes,sha256,created_at
        FROM registry.robot_model_assets WHERE organization_id=%s AND version_id=%s
        ORDER BY relative_path,asset_id""",
        (organization_id, version_id),
    )
    assets = tuple(
        RobotModelAsset(
            asset_id=str(row[0]),
            relative_path=row[1],
            role=row[2],
            media_type=row[3],
            size_bytes=row[4],
            sha256=row[5],
            created_at=row[6],
        )
        for row in cursor.fetchall()
    )
    cursor.execute(
        """SELECT source_joint_name,target_joint_name,direction
        FROM registry.robot_model_joint_mappings WHERE organization_id=%s AND version_id=%s
        ORDER BY source_joint_name""",
        (organization_id, version_id),
    )
    mappings = tuple(
        sorted(
            (
                RobotModelJointMapping(source_joint_name=row[0], target_joint_name=row[1], direction=row[2])
                for row in cursor.fetchall()
            ),
            key=lambda candidate: candidate.source_joint_name,
        )
    )
    return str(version[3]), version[1], assets, version[2], mappings


class PublishedModelAssetReader:
    """Organization model identity, independent of the upload's accounting project."""

    def __init__(self, connections: Callable[[], Any]) -> None:
        self.connections = connections

    def find(
        self, organization_id: str, version_id: str, asset_id: str
    ) -> tuple[str, str, RobotModelAsset, bytes | None] | None:
        with self.connections() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT a.project_id,a.object_key,a.asset_id,a.relative_path,a.role,
                a.media_type,a.size_bytes,a.sha256,a.created_at,a.content
                FROM registry.robot_model_assets a JOIN registry.robot_model_versions v
                  ON (a.organization_id,a.version_id)=(v.organization_id,v.version_id)
                WHERE a.organization_id=%s AND a.version_id=%s AND a.asset_id::text=%s
                  AND v.lifecycle='PUBLISHED'""",
                (organization_id, version_id, asset_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return (
                row[0],
                row[1],
                RobotModelAsset(
                    asset_id=str(row[2]),
                    relative_path=row[3],
                    role=row[4],
                    media_type=row[5],
                    size_bytes=row[6],
                    sha256=row[7],
                    created_at=row[8],
                ),
                None if row[9] is None else bytes(row[9]),
            )

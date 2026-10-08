"""Instance-owned binding query participating in the caller's snapshot transaction."""

from typing import Any


def model_has_binding_history(cursor: Any, organization_id: str, version_id: str) -> bool:
    """Instance-owned retention fact, including superseded historical bindings."""
    cursor.execute(
        """SELECT EXISTS (
            SELECT 1 FROM registry.organization_robot_model_bindings
            WHERE organization_id=%s AND version_id=%s
            UNION ALL
            SELECT 1 FROM robotics.robot_assets
            WHERE organization_id=%s AND robot_model_version_id=%s
        )""",
        (organization_id, version_id, organization_id, version_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("instance binding usage query returned no result")
    return bool(row[0])


def locked_model_binding(
    cursor: Any, organization_id: str, robot_id: str
) -> tuple[str, str] | None:
    # Binding commands lock the same robot row. A snapshot observes either complete binding.
    cursor.execute(
        """SELECT binding_id,robot_model_version_id FROM robotics.robot_assets
        WHERE organization_id=%s AND robot_id=%s FOR SHARE""",
        (organization_id, robot_id),
    )
    row = cursor.fetchone()
    return (str(row[0]), str(row[1])) if row and row[0] and row[1] else None

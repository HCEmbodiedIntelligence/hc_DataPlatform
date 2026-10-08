"""Instance-owned binding query participating in the caller's snapshot transaction."""

from typing import Any


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

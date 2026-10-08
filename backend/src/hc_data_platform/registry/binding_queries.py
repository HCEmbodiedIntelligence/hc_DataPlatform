"""Model-owned validation on the caller's existing binding transaction."""

from typing import Any

from .binding_contract import PublishedModelIdentity


def published_model_for_binding(
    cursor: Any, organization_id: str, version_id: str
) -> PublishedModelIdentity:
    # Hold this version through binding+history+receipt commit. A concurrent
    # discard cannot delete a version after validation and before binding.
    cursor.execute(
        """SELECT robot_model_id,lifecycle FROM registry.robot_model_versions
        WHERE organization_id=%s AND version_id=%s FOR SHARE""",
        (organization_id, version_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise KeyError(version_id)
    if row[1] != "PUBLISHED":
        raise ValueError("robot model version is not published")
    return PublishedModelIdentity(organization_id, str(row[0]), version_id)

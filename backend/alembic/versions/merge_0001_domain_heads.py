"""Merge all completed domain migration branches.

Revision ID: merge_0001
Revises: access_0001, annotation_0002, cleaning_0002, datasets_0002,
    ingest_0002, robotics_0001, storage_0001
Create Date: 2026-08-11
"""

from collections.abc import Sequence

revision: str = "merge_0001"
down_revision: tuple[str, ...] = (
    "access_0001",
    "annotation_0002",
    "cleaning_0002",
    "datasets_0002",
    "ingest_0002",
    "robotics_0001",
    "storage_0001",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join the completed domain branches without changing schema state."""


def downgrade() -> None:
    """Split the revision graph back into its domain heads."""

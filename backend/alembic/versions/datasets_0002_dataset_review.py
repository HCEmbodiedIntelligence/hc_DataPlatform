"""dataset, version, manifest, review and deletion-preflight facts

Revision ID: datasets_0002
Revises: foundation_0001
Create Date: 2026-08-11
"""

from alembic import op
from app.domains.datasets.models import (
    Dataset,
    DatasetVersion,
    DatasetVersionManifest,
    DatasetVersionReferenceSnapshot,
    DatasetVersionRevision,
    DeletionPreflight,
    Episode,
    EpisodeRevision,
    ManifestEntry,
    ReviewDecision,
    ReviewFinding,
    ReviewReturnLineage,
)

revision = "datasets_0002"
down_revision = "foundation_0001"
branch_labels = ("datasets",)
depends_on = None

TABLES = (
    Dataset.__table__,
    DatasetVersion.__table__,
    Episode.__table__,
    EpisodeRevision.__table__,
    DatasetVersionRevision.__table__,
    DatasetVersionReferenceSnapshot.__table__,
    DatasetVersionManifest.__table__,
    ManifestEntry.__table__,
    ReviewDecision.__table__,
    ReviewFinding.__table__,
    ReviewReturnLineage.__table__,
    DeletionPreflight.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        table.create(bind=bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind=bind, checkfirst=False)

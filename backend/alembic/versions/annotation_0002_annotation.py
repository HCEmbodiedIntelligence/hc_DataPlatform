"""annotation resolver, tasks, drafts, submissions, sets and reviews

Revision ID: annotation_0002
Revises: foundation_0001
Create Date: 2026-08-11
"""

from alembic import op
from app.domains.annotation import models

revision = "annotation_0002"
down_revision = "foundation_0001"
branch_labels = ("annotation",)
depends_on = None

TABLES = (
    models.AnnotationTask.__table__,
    models.AnnotationDraft.__table__,
    models.AnnotationDraftRevision.__table__,
    models.AnnotationEntryResolution.__table__,
    models.AnnotationSubmission.__table__,
    models.AnnotationSubmitPreflight.__table__,
    models.AnnotationSet.__table__,
    models.AnnotationReview.__table__,
    models.AnnotationIdempotency.__table__,
    models.AnnotationOutbox.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE SCHEMA IF NOT EXISTS annotation")
    for table in TABLES:
        table.create(bind=bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind=bind, checkfirst=False)
    op.execute("DROP SCHEMA IF EXISTS annotation")

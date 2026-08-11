"""manual issue, cleaning draft, EDL, preview and commit facts

Revision ID: cleaning_0002
Revises: foundation_0001
Create Date: 2026-08-11
"""

from alembic import op
from app.domains.cleaning import models

revision = "cleaning_0002"
down_revision = "foundation_0001"
branch_labels = ("cleaning",)
depends_on = None

TABLES = (
    models.ManualIssue.__table__,
    models.ManualIssueHistory.__table__,
    models.CleaningDraft.__table__,
    models.ManualIssueDraftLink.__table__,
    models.IssueDerivedDraftContext.__table__,
    models.ReviewReturnDraftLineage.__table__,
    models.ReviewSuccessorComposition.__table__,
    models.ReviewSuccessorCompositionMember.__table__,
    models.CleaningDraftLineageClosure.__table__,
    models.CleaningEdlRevision.__table__,
    models.CleaningOperation.__table__,
    models.CleaningPreview.__table__,
    models.CleaningCommit.__table__,
    models.CleaningCommitOutputRevision.__table__,
    models.CleaningEditSession.__table__,
    models.ManualCleaningIdempotency.__table__,
    models.ManualCleaningOutbox.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE SCHEMA IF NOT EXISTS manual_cleaning")
    for table in TABLES:
        table.create(bind=bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind=bind, checkfirst=False)
    op.execute("DROP SCHEMA IF EXISTS manual_cleaning")

"""ingest normalized aggregates and append-only history

Revision ID: ingest_0002
Revises: foundation_0001
Create Date: 2026-08-11
"""

from alembic import op
from app.domains.ingest.models import (
    ConnectionTest,
    CredentialRef,
    DatasetRegistrationReceipt,
    DataSource,
    ManifestNode,
    Quarantine,
    SourceManifest,
    UploadEvent,
    UploadJob,
    UploadObject,
    UploadPartAttempt,
    UploadSession,
    VerificationFinding,
    VerificationRun,
    VerificationStage,
)

revision = "ingest_0002"
down_revision = "foundation_0001"
branch_labels = None
depends_on = None


TABLES = (
    DataSource.__table__,
    CredentialRef.__table__,
    ConnectionTest.__table__,
    UploadSession.__table__,
    UploadObject.__table__,
    UploadPartAttempt.__table__,
    SourceManifest.__table__,
    ManifestNode.__table__,
    VerificationRun.__table__,
    VerificationStage.__table__,
    VerificationFinding.__table__,
    Quarantine.__table__,
    UploadJob.__table__,
    DatasetRegistrationReceipt.__table__,
    UploadEvent.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        table.create(bind=bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind=bind, checkfirst=False)

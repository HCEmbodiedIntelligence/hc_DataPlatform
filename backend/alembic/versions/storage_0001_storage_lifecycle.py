"""Storage inventory and lifecycle governance facts.

Revision ID: storage_0001
Revises: None
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op
from app.domains.storage import models

revision: str = "storage_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("storage",)
depends_on: str | Sequence[str] | None = None


TABLES = (
    models.StorageAreaModel.__table__,
    models.StorageObjectModel.__table__,
    models.StorageReferenceModel.__table__,
    models.ObjectProtectionModel.__table__,
    models.InventorySnapshotModel.__table__,
    models.CostSnapshotModel.__table__,
    models.LifecyclePolicySetModel.__table__,
    models.LifecyclePolicyVersionModel.__table__,
    models.StorageJobModel.__table__,
    models.LifecycleSimulationModel.__table__,
    models.LifecycleExecutionModel.__table__,
    models.RestorePreflightModel.__table__,
    models.RestoreTaskModel.__table__,
    models.MultipartUploadProjectionModel.__table__,
    models.MultipartAbortPlanModel.__table__,
    models.MultipartAbortModel.__table__,
    models.StorageCommandIntentModel.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        table.create(bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind, checkfirst=True)

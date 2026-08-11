"""Robotics model, calibration, and stream-schema projections.

Revision ID: robotics_0001
Revises: None
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op
from app.domains.robotics import models

revision: str = "robotics_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("robotics",)
depends_on: str | Sequence[str] | None = None


TABLES = (
    models.RobotModel.__table__,
    models.RobotModelVersion.__table__,
    models.RobotAssetUploadSession.__table__,
    models.RobotValidationReport.__table__,
    models.RobotModelBinding.__table__,
    models.Robot.__table__,
    models.Component.__table__,
    models.RoboticsPreflight.__table__,
    models.RoboticsJob.__table__,
    models.CalibrationSet.__table__,
    models.CalibrationVersion.__table__,
    models.CalibrationReport.__table__,
    models.DataSchema.__table__,
    models.DataSchemaVersion.__table__,
    models.SchemaCompatibilityCheck.__table__,
    models.SchemaImportValidation.__table__,
    models.DatasetSchemaSnapshot.__table__,
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        table.create(bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind, checkfirst=True)

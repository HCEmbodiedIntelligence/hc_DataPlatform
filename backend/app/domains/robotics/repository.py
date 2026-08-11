"""Scope-safe persistence queries for P14-P17."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models


class RoboticsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, value: object) -> None:
        self.session.add(value)

    async def robot_model(self, organization_id: str, model_id: str):
        return await self.session.scalar(
            select(models.RobotModel).where(
                models.RobotModel.organization_id == organization_id,
                models.RobotModel.model_id == model_id,
            )
        )

    async def robot_models(self, organization_id: str, limit: int = 101):
        return list(
            (
                await self.session.scalars(
                    select(models.RobotModel)
                    .where(models.RobotModel.organization_id == organization_id)
                    .order_by(
                        models.RobotModel.created_at.desc(), models.RobotModel.model_id.desc()
                    )
                    .limit(limit)
                )
            ).all()
        )

    async def model_version(self, organization_id: str, version_id: str):
        return await self.session.scalar(
            select(models.RobotModelVersion).where(
                models.RobotModelVersion.organization_id == organization_id,
                models.RobotModelVersion.version_id == version_id,
            )
        )

    async def model_versions(self, organization_id: str, model_id: str, limit: int = 101):
        return list(
            (
                await self.session.scalars(
                    select(models.RobotModelVersion)
                    .where(
                        models.RobotModelVersion.organization_id == organization_id,
                        models.RobotModelVersion.model_id == model_id,
                    )
                    .order_by(
                        models.RobotModelVersion.created_at.desc(),
                        models.RobotModelVersion.version_id.desc(),
                    )
                    .limit(limit)
                )
            ).all()
        )

    async def upload_session(self, organization_id: str, upload_session_id: str):
        return await self.session.scalar(
            select(models.RobotAssetUploadSession).where(
                models.RobotAssetUploadSession.organization_id == organization_id,
                models.RobotAssetUploadSession.upload_session_id == upload_session_id,
            )
        )

    async def validation_report(self, organization_id: str, report_id: str):
        return await self.session.scalar(
            select(models.RobotValidationReport).where(
                models.RobotValidationReport.organization_id == organization_id,
                models.RobotValidationReport.report_id == report_id,
            )
        )

    async def validation_reports(self, organization_id: str, version_id: str, limit: int = 101):
        return list(
            (
                await self.session.scalars(
                    select(models.RobotValidationReport)
                    .where(
                        models.RobotValidationReport.organization_id == organization_id,
                        models.RobotValidationReport.version_id == version_id,
                    )
                    .order_by(
                        models.RobotValidationReport.created_at.desc(),
                        models.RobotValidationReport.report_id.desc(),
                    )
                    .limit(limit)
                )
            ).all()
        )

    async def bindings(self, project_id: str, limit: int = 101):
        return list(
            (
                await self.session.scalars(
                    select(models.RobotModelBinding)
                    .where(models.RobotModelBinding.project_id == project_id)
                    .order_by(
                        models.RobotModelBinding.valid_from.desc(),
                        models.RobotModelBinding.binding_id.desc(),
                    )
                    .limit(limit)
                )
            ).all()
        )

    async def robot(self, project_id: str, region_code: str, robot_id: str):
        return await self.session.scalar(
            select(models.Robot).where(
                models.Robot.project_id == project_id,
                models.Robot.region_code == region_code,
                models.Robot.robot_id == robot_id,
            )
        )

    async def robots(self, project_id: str, region_code: str, limit: int = 101):
        return list(
            (
                await self.session.scalars(
                    select(models.Robot)
                    .where(
                        models.Robot.project_id == project_id,
                        models.Robot.region_code == region_code,
                    )
                    .order_by(models.Robot.created_at.desc(), models.Robot.robot_id.desc())
                    .limit(limit)
                )
            ).all()
        )

    async def component(self, project_id: str, region_code: str, component_id: str):
        return await self.session.scalar(
            select(models.Component).where(
                models.Component.project_id == project_id,
                models.Component.region_code == region_code,
                models.Component.component_id == component_id,
            )
        )

    async def components(self, project_id: str, region_code: str, robot_id: str):
        return list(
            (
                await self.session.scalars(
                    select(models.Component)
                    .where(
                        models.Component.project_id == project_id,
                        models.Component.region_code == region_code,
                        models.Component.robot_id == robot_id,
                    )
                    .order_by(models.Component.sort_key, models.Component.component_id)
                )
            ).all()
        )

    async def preflight(self, preflight_id: str):
        return await self.session.get(models.RoboticsPreflight, preflight_id)

    async def calibration_set(self, project_id: str, region_code: str, set_id: str):
        return await self.session.scalar(
            select(models.CalibrationSet).where(
                models.CalibrationSet.project_id == project_id,
                models.CalibrationSet.region_code == region_code,
                models.CalibrationSet.set_id == set_id,
            )
        )

    async def calibration_sets(self, project_id: str, region_code: str, limit: int = 101):
        return list(
            (
                await self.session.scalars(
                    select(models.CalibrationSet)
                    .where(
                        models.CalibrationSet.project_id == project_id,
                        models.CalibrationSet.region_code == region_code,
                    )
                    .order_by(
                        models.CalibrationSet.created_at.desc(), models.CalibrationSet.set_id.desc()
                    )
                    .limit(limit)
                )
            ).all()
        )

    async def calibration_version(
        self, project_id: str, region_code: str, set_id: str, version: str
    ):
        return await self.session.scalar(
            select(models.CalibrationVersion).where(
                models.CalibrationVersion.project_id == project_id,
                models.CalibrationVersion.region_code == region_code,
                models.CalibrationVersion.set_id == set_id,
                models.CalibrationVersion.version == version,
            )
        )

    async def calibration_versions(self, project_id: str, region_code: str, set_id: str):
        return list(
            (
                await self.session.scalars(
                    select(models.CalibrationVersion)
                    .where(
                        models.CalibrationVersion.project_id == project_id,
                        models.CalibrationVersion.region_code == region_code,
                        models.CalibrationVersion.set_id == set_id,
                    )
                    .order_by(models.CalibrationVersion.created_at.desc())
                )
            ).all()
        )

    async def calibration_report(self, project_id: str, region_code: str, report_id: str):
        return await self.session.scalar(
            select(models.CalibrationReport).where(
                models.CalibrationReport.project_id == project_id,
                models.CalibrationReport.region_code == region_code,
                models.CalibrationReport.report_id == report_id,
            )
        )

    async def calibration_reports(self, version_row_id: str):
        return list(
            (
                await self.session.scalars(
                    select(models.CalibrationReport)
                    .where(models.CalibrationReport.version_row_id == version_row_id)
                    .order_by(models.CalibrationReport.created_at.desc())
                )
            ).all()
        )

    async def jobs(self, scope_key: str):
        return list(
            (
                await self.session.scalars(
                    select(models.RoboticsJob)
                    .where(models.RoboticsJob.scope_key == scope_key)
                    .order_by(models.RoboticsJob.created_at.desc())
                )
            ).all()
        )

    async def job(self, scope_key: str, job_id: str):
        return await self.session.scalar(
            select(models.RoboticsJob).where(
                models.RoboticsJob.scope_key == scope_key,
                models.RoboticsJob.job_id == job_id,
            )
        )

    async def data_schema(self, organization_id: str, schema_id: str):
        return await self.session.scalar(
            select(models.DataSchema).where(
                models.DataSchema.organization_id == organization_id,
                models.DataSchema.schema_id == schema_id,
            )
        )

    async def data_schemas(self, organization_id: str, limit: int = 101):
        return list(
            (
                await self.session.scalars(
                    select(models.DataSchema)
                    .where(models.DataSchema.organization_id == organization_id)
                    .order_by(
                        models.DataSchema.created_at.desc(), models.DataSchema.schema_id.desc()
                    )
                    .limit(limit)
                )
            ).all()
        )

    async def family_schema_versions(self, organization_id: str, family_id: str):
        return list(
            (
                await self.session.scalars(
                    select(models.DataSchemaVersion)
                    .join(
                        models.DataSchema,
                        models.DataSchema.schema_id == models.DataSchemaVersion.schema_id,
                    )
                    .where(
                        models.DataSchemaVersion.organization_id == organization_id,
                        models.DataSchema.family_id == family_id,
                    )
                    .order_by(models.DataSchemaVersion.created_at.desc())
                )
            ).all()
        )

    async def schema_version(self, organization_id: str, schema_id: str, version: str):
        return await self.session.scalar(
            select(models.DataSchemaVersion).where(
                models.DataSchemaVersion.organization_id == organization_id,
                models.DataSchemaVersion.schema_id == schema_id,
                models.DataSchemaVersion.version == version,
            )
        )

    async def schema_version_by_row(self, organization_id: str, row_id: str):
        return await self.session.scalar(
            select(models.DataSchemaVersion).where(
                models.DataSchemaVersion.organization_id == organization_id,
                models.DataSchemaVersion.row_id == row_id,
            )
        )

    async def schema_versions(self, organization_id: str, schema_id: str):
        return list(
            (
                await self.session.scalars(
                    select(models.DataSchemaVersion)
                    .where(
                        models.DataSchemaVersion.organization_id == organization_id,
                        models.DataSchemaVersion.schema_id == schema_id,
                    )
                    .order_by(models.DataSchemaVersion.created_at.desc())
                )
            ).all()
        )

    async def compatibility_check(self, organization_id: str, check_id: str):
        return await self.session.scalar(
            select(models.SchemaCompatibilityCheck).where(
                models.SchemaCompatibilityCheck.organization_id == organization_id,
                models.SchemaCompatibilityCheck.check_id == check_id,
            )
        )

    async def import_validation(self, organization_id: str, validation_id: str):
        return await self.session.scalar(
            select(models.SchemaImportValidation).where(
                models.SchemaImportValidation.organization_id == organization_id,
                models.SchemaImportValidation.import_validation_id == validation_id,
            )
        )

    async def schema_snapshot(self, organization_id: str, snapshot_id: str):
        return await self.session.scalar(
            select(models.DatasetSchemaSnapshot).where(
                models.DatasetSchemaSnapshot.organization_id == organization_id,
                models.DatasetSchemaSnapshot.snapshot_id == snapshot_id,
            )
        )

    async def schema_snapshots(self, organization_id: str, limit: int = 101):
        return list(
            (
                await self.session.scalars(
                    select(models.DatasetSchemaSnapshot)
                    .where(models.DatasetSchemaSnapshot.organization_id == organization_id)
                    .order_by(
                        models.DatasetSchemaSnapshot.created_at.desc(),
                        models.DatasetSchemaSnapshot.snapshot_id.desc(),
                    )
                    .limit(limit)
                )
            ).all()
        )

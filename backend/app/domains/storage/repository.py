"""Persistence queries for storage/lifecycle; repositories never commit."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    CostSnapshotModel,
    InventorySnapshotModel,
    LifecycleExecutionModel,
    LifecyclePolicySetModel,
    LifecyclePolicyVersionModel,
    LifecycleSimulationModel,
    MultipartAbortPlanModel,
    MultipartUploadProjectionModel,
    ObjectProtectionModel,
    RestorePreflightModel,
    RestoreTaskModel,
    StorageCommandIntentModel,
    StorageJobModel,
    StorageObjectModel,
    StorageReferenceModel,
)

ScopeTuple = tuple[str, str, str]


class StorageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _scope(model: type, scope: ScopeTuple) -> tuple[object, ...]:
        organization_id, project_id, region_code = scope
        return (
            model.organization_id == organization_id,
            model.project_id == project_id,
            model.region_code == region_code,
        )

    async def current_snapshot(self, scope: ScopeTuple) -> InventorySnapshotModel | None:
        return await self.session.scalar(
            select(InventorySnapshotModel).where(
                *self._scope(InventorySnapshotModel, scope),
                InventorySnapshotModel.current.is_(True),
            )
        )

    async def get_snapshot(
        self, scope: ScopeTuple, snapshot_id: str
    ) -> InventorySnapshotModel | None:
        return await self.session.scalar(
            select(InventorySnapshotModel).where(
                *self._scope(InventorySnapshotModel, scope),
                InventorySnapshotModel.snapshot_id == snapshot_id,
            )
        )

    async def list_objects(
        self,
        scope: ScopeTuple,
        *,
        limit: int,
        after: tuple[datetime, str] | None = None,
        before: tuple[datetime, str] | None = None,
        object_role: str | None = None,
        storage_class: str | None = None,
        status: str | None = None,
    ) -> list[StorageObjectModel]:
        stmt = select(StorageObjectModel).where(
            *self._scope(StorageObjectModel, scope),
            StorageObjectModel.classification_status == "CLASSIFIED",
            StorageObjectModel.object_role.is_not(None),
        )
        if object_role:
            stmt = stmt.where(StorageObjectModel.object_role == object_role)
        if storage_class:
            stmt = stmt.where(StorageObjectModel.storage_class == storage_class)
        if status:
            stmt = stmt.where(StorageObjectModel.status == status)
        if after:
            created_at, object_id = after
            stmt = stmt.where(
                or_(
                    StorageObjectModel.created_at < created_at,
                    and_(
                        StorageObjectModel.created_at == created_at,
                        StorageObjectModel.object_id < object_id,
                    ),
                )
            )
        if before:
            created_at, object_id = before
            stmt = stmt.where(
                or_(
                    StorageObjectModel.created_at > created_at,
                    and_(
                        StorageObjectModel.created_at == created_at,
                        StorageObjectModel.object_id > object_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            StorageObjectModel.created_at.desc(), StorageObjectModel.object_id.desc()
        ).limit(limit + 1)
        return list((await self.session.scalars(stmt)).all())

    async def get_object(self, scope: ScopeTuple, object_id: str) -> StorageObjectModel | None:
        return await self.session.scalar(
            select(StorageObjectModel).where(
                *self._scope(StorageObjectModel, scope), StorageObjectModel.object_id == object_id
            )
        )

    async def object_references(self, object_id: str) -> list[StorageReferenceModel]:
        return list(
            (
                await self.session.scalars(
                    select(StorageReferenceModel)
                    .where(
                        StorageReferenceModel.object_id == object_id,
                        StorageReferenceModel.active.is_(True),
                    )
                    .order_by(StorageReferenceModel.reference_id)
                )
            ).all()
        )

    async def current_protection(self, object_id: str) -> ObjectProtectionModel | None:
        return await self.session.scalar(
            select(ObjectProtectionModel).where(
                ObjectProtectionModel.object_id == object_id,
                ObjectProtectionModel.current.is_(True),
            )
        )

    async def current_cost(
        self, scope: ScopeTuple, billing_period: str | None, currency: str | None
    ) -> CostSnapshotModel | None:
        stmt = select(CostSnapshotModel).where(*self._scope(CostSnapshotModel, scope))
        if billing_period:
            stmt = stmt.where(CostSnapshotModel.billing_period == billing_period)
        if currency:
            stmt = stmt.where(CostSnapshotModel.currency == currency)
        return await self.session.scalar(
            stmt.order_by(CostSnapshotModel.billing_period.desc(), CostSnapshotModel.as_of.desc())
        )

    async def policy_set(self, scope: ScopeTuple) -> LifecyclePolicySetModel | None:
        return await self.session.scalar(
            select(LifecyclePolicySetModel).where(*self._scope(LifecyclePolicySetModel, scope))
        )

    async def list_policies(
        self,
        scope: ScopeTuple,
        *,
        limit: int,
        status: str | None = None,
        after: tuple[datetime, str] | None = None,
        before: tuple[datetime, str] | None = None,
    ) -> list[LifecyclePolicyVersionModel]:
        stmt = select(LifecyclePolicyVersionModel).where(
            *self._scope(LifecyclePolicyVersionModel, scope),
            LifecyclePolicyVersionModel.current.is_(True),
        )
        if status:
            stmt = stmt.where(LifecyclePolicyVersionModel.status == status)
        if after:
            updated_at, policy_id = after
            stmt = stmt.where(
                or_(
                    LifecyclePolicyVersionModel.updated_at < updated_at,
                    and_(
                        LifecyclePolicyVersionModel.updated_at == updated_at,
                        LifecyclePolicyVersionModel.policy_id < policy_id,
                    ),
                )
            )
        if before:
            updated_at, policy_id = before
            stmt = stmt.where(
                or_(
                    LifecyclePolicyVersionModel.updated_at > updated_at,
                    and_(
                        LifecyclePolicyVersionModel.updated_at == updated_at,
                        LifecyclePolicyVersionModel.policy_id > policy_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            LifecyclePolicyVersionModel.updated_at.desc(),
            LifecyclePolicyVersionModel.policy_id.desc(),
        ).limit(limit + 1)
        return list((await self.session.scalars(stmt)).all())

    async def current_policy(
        self, scope: ScopeTuple, policy_id: str
    ) -> LifecyclePolicyVersionModel | None:
        return await self.session.scalar(
            select(LifecyclePolicyVersionModel).where(
                *self._scope(LifecyclePolicyVersionModel, scope),
                LifecyclePolicyVersionModel.policy_id == policy_id,
                LifecyclePolicyVersionModel.current.is_(True),
            )
        )

    async def get_simulation(
        self, scope: ScopeTuple, simulation_id: str
    ) -> LifecycleSimulationModel | None:
        return await self.session.scalar(
            select(LifecycleSimulationModel).where(
                *self._scope(LifecycleSimulationModel, scope),
                LifecycleSimulationModel.simulation_id == simulation_id,
            )
        )

    async def get_job(self, job_id: str) -> StorageJobModel | None:
        return await self.session.get(StorageJobModel, job_id)

    async def list_executions(
        self,
        scope: ScopeTuple,
        *,
        limit: int,
        status: str | None = None,
        started_from: datetime | None = None,
        started_to: datetime | None = None,
        after: tuple[datetime, str] | None = None,
        before: tuple[datetime, str] | None = None,
    ) -> list[LifecycleExecutionModel]:
        stmt = select(LifecycleExecutionModel).where(*self._scope(LifecycleExecutionModel, scope))
        if status:
            stmt = stmt.where(LifecycleExecutionModel.status == status)
        if started_from:
            stmt = stmt.where(LifecycleExecutionModel.started_at >= started_from)
        if started_to:
            stmt = stmt.where(LifecycleExecutionModel.started_at <= started_to)
        if after:
            started_at, execution_id = after
            stmt = stmt.where(
                or_(
                    LifecycleExecutionModel.started_at < started_at,
                    and_(
                        LifecycleExecutionModel.started_at == started_at,
                        LifecycleExecutionModel.execution_id < execution_id,
                    ),
                )
            )
        if before:
            started_at, execution_id = before
            stmt = stmt.where(
                or_(
                    LifecycleExecutionModel.started_at > started_at,
                    and_(
                        LifecycleExecutionModel.started_at == started_at,
                        LifecycleExecutionModel.execution_id > execution_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            LifecycleExecutionModel.started_at.desc(), LifecycleExecutionModel.execution_id.desc()
        ).limit(limit + 1)
        return list((await self.session.scalars(stmt)).all())

    async def get_execution(
        self, scope: ScopeTuple, execution_id: str
    ) -> LifecycleExecutionModel | None:
        return await self.session.scalar(
            select(LifecycleExecutionModel).where(
                *self._scope(LifecycleExecutionModel, scope),
                LifecycleExecutionModel.execution_id == execution_id,
            )
        )

    async def list_restore_tasks(
        self,
        scope: ScopeTuple,
        *,
        limit: int,
        status: str | None = None,
        after: tuple[datetime, str] | None = None,
        before: tuple[datetime, str] | None = None,
    ) -> list[RestoreTaskModel]:
        stmt = select(RestoreTaskModel).where(*self._scope(RestoreTaskModel, scope))
        if status:
            stmt = stmt.where(RestoreTaskModel.status == status)
        if after:
            requested_at, task_id = after
            stmt = stmt.where(
                or_(
                    RestoreTaskModel.requested_at < requested_at,
                    and_(
                        RestoreTaskModel.requested_at == requested_at,
                        RestoreTaskModel.restore_task_id < task_id,
                    ),
                )
            )
        if before:
            requested_at, task_id = before
            stmt = stmt.where(
                or_(
                    RestoreTaskModel.requested_at > requested_at,
                    and_(
                        RestoreTaskModel.requested_at == requested_at,
                        RestoreTaskModel.restore_task_id > task_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            RestoreTaskModel.requested_at.desc(), RestoreTaskModel.restore_task_id.desc()
        ).limit(limit + 1)
        return list((await self.session.scalars(stmt)).all())

    async def get_restore_preflight(self, quote_id: str) -> RestorePreflightModel | None:
        return await self.session.get(RestorePreflightModel, quote_id)

    async def list_multipart(
        self,
        scope: ScopeTuple,
        *,
        limit: int,
        statuses: Sequence[str] = (),
        after: tuple[datetime, str] | None = None,
        before: tuple[datetime, str] | None = None,
    ) -> list[MultipartUploadProjectionModel]:
        stmt = select(MultipartUploadProjectionModel).where(
            *self._scope(MultipartUploadProjectionModel, scope)
        )
        if statuses:
            stmt = stmt.where(MultipartUploadProjectionModel.status.in_(statuses))
        if after:
            last_activity_at, upload_id = after
            stmt = stmt.where(
                or_(
                    MultipartUploadProjectionModel.last_activity_at < last_activity_at,
                    and_(
                        MultipartUploadProjectionModel.last_activity_at == last_activity_at,
                        MultipartUploadProjectionModel.upload_id < upload_id,
                    ),
                )
            )
        if before:
            last_activity_at, upload_id = before
            stmt = stmt.where(
                or_(
                    MultipartUploadProjectionModel.last_activity_at > last_activity_at,
                    and_(
                        MultipartUploadProjectionModel.last_activity_at == last_activity_at,
                        MultipartUploadProjectionModel.upload_id > upload_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            MultipartUploadProjectionModel.last_activity_at.desc(),
            MultipartUploadProjectionModel.upload_id.desc(),
        ).limit(limit + 1)
        return list((await self.session.scalars(stmt)).all())

    async def get_multipart(
        self, scope: ScopeTuple, upload_id: str
    ) -> MultipartUploadProjectionModel | None:
        return await self.session.scalar(
            select(MultipartUploadProjectionModel).where(
                *self._scope(MultipartUploadProjectionModel, scope),
                MultipartUploadProjectionModel.upload_id == upload_id,
            )
        )

    async def get_abort_plan(self, plan_id: str) -> MultipartAbortPlanModel | None:
        return await self.session.get(MultipartAbortPlanModel, plan_id)

    async def command_intent(
        self, scope_key: str, idempotency_key: str
    ) -> StorageCommandIntentModel | None:
        return await self.session.scalar(
            select(StorageCommandIntentModel).where(
                StorageCommandIntentModel.scope_key == scope_key,
                StorageCommandIntentModel.idempotency_key == idempotency_key,
            )
        )

    def add(self, value: object) -> None:
        self.session.add(value)

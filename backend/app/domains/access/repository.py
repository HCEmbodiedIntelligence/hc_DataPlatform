"""Persistence operations for the access/audit bounded context.

Repositories never commit.  Application services own the transaction so the
domain fact, idempotency record, Outbox row and audit record share one boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Text, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    AccessPreflightModel,
    AuditEventModel,
    AuditExportJobModel,
    AuditOutboxModel,
    AuditReadProjectionModel,
    AuditRetentionPolicyModel,
    InvitationModel,
    MemberModel,
    RoleAssignmentModel,
    ScopeGrantModel,
)


class AccessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_member(self, project_id: str, member_id: str) -> MemberModel | None:
        return await self.session.scalar(
            select(MemberModel).where(
                MemberModel.project_id == project_id,
                MemberModel.member_id == member_id,
            )
        )

    async def list_members(
        self,
        project_id: str,
        *,
        limit: int,
        after: tuple[datetime, str] | None = None,
        before: tuple[datetime, str] | None = None,
        role_ids: Sequence[str] = (),
        statuses: Sequence[str] = (),
    ) -> list[MemberModel]:
        stmt = select(MemberModel).where(MemberModel.project_id == project_id)
        if role_ids:
            stmt = stmt.where(MemberModel.role_id.in_(role_ids))
        if statuses:
            stmt = stmt.where(MemberModel.status.in_(statuses))
        if after:
            joined_at, member_id = after
            stmt = stmt.where(
                or_(
                    MemberModel.joined_at < joined_at,
                    and_(MemberModel.joined_at == joined_at, MemberModel.member_id < member_id),
                )
            )
        if before:
            joined_at, member_id = before
            stmt = stmt.where(
                or_(
                    MemberModel.joined_at > joined_at,
                    and_(MemberModel.joined_at == joined_at, MemberModel.member_id > member_id),
                )
            )
        stmt = stmt.order_by(MemberModel.joined_at.desc(), MemberModel.member_id.desc()).limit(
            limit + 1
        )
        return list((await self.session.scalars(stmt)).all())

    async def current_role_assignment(self, member_id: str) -> RoleAssignmentModel | None:
        return await self.session.scalar(
            select(RoleAssignmentModel).where(
                RoleAssignmentModel.member_id == member_id,
                RoleAssignmentModel.status == "ACTIVE",
            )
        )

    async def active_admin_count(self, project_id: str) -> int:
        value = await self.session.scalar(
            select(func.count())
            .select_from(MemberModel)
            .where(
                MemberModel.project_id == project_id,
                MemberModel.status == "ACTIVE",
                MemberModel.role_id == "PROJECT_ADMIN",
            )
        )
        return int(value or 0)

    async def list_role_assignments(
        self,
        project_id: str,
        *,
        limit: int,
        member_id: str | None = None,
        after: tuple[datetime, str] | None = None,
        before: tuple[datetime, str] | None = None,
    ) -> list[RoleAssignmentModel]:
        stmt = select(RoleAssignmentModel).where(
            RoleAssignmentModel.scope_key.like(f"%:{project_id}%")
        )
        if member_id:
            stmt = stmt.where(RoleAssignmentModel.member_id == member_id)
        if after:
            valid_from, assignment_id = after
            stmt = stmt.where(
                or_(
                    RoleAssignmentModel.valid_from < valid_from,
                    and_(
                        RoleAssignmentModel.valid_from == valid_from,
                        RoleAssignmentModel.role_assignment_id < assignment_id,
                    ),
                )
            )
        if before:
            valid_from, assignment_id = before
            stmt = stmt.where(
                or_(
                    RoleAssignmentModel.valid_from > valid_from,
                    and_(
                        RoleAssignmentModel.valid_from == valid_from,
                        RoleAssignmentModel.role_assignment_id > assignment_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            RoleAssignmentModel.valid_from.desc(), RoleAssignmentModel.role_assignment_id.desc()
        ).limit(limit + 1)
        return list((await self.session.scalars(stmt)).all())

    async def list_scope_grants(
        self,
        project_id: str,
        *,
        limit: int,
        member_id: str | None = None,
        after: tuple[datetime, str] | None = None,
        before: tuple[datetime, str] | None = None,
    ) -> list[ScopeGrantModel]:
        stmt = select(ScopeGrantModel).where(ScopeGrantModel.scope_key.like(f"%:{project_id}%"))
        if member_id:
            stmt = stmt.where(ScopeGrantModel.subject_key.like(f"%:{member_id}%"))
        if after:
            valid_from, grant_id = after
            stmt = stmt.where(
                or_(
                    ScopeGrantModel.valid_from < valid_from,
                    and_(
                        ScopeGrantModel.valid_from == valid_from,
                        ScopeGrantModel.scope_grant_id < grant_id,
                    ),
                )
            )
        if before:
            valid_from, grant_id = before
            stmt = stmt.where(
                or_(
                    ScopeGrantModel.valid_from > valid_from,
                    and_(
                        ScopeGrantModel.valid_from == valid_from,
                        ScopeGrantModel.scope_grant_id > grant_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            ScopeGrantModel.valid_from.desc(), ScopeGrantModel.scope_grant_id.desc()
        ).limit(limit + 1)
        return list((await self.session.scalars(stmt)).all())

    async def get_scope_grant(self, grant_id: str) -> ScopeGrantModel | None:
        return await self.session.get(ScopeGrantModel, grant_id)

    def add_role_assignment(self, assignment: RoleAssignmentModel) -> None:
        self.session.add(assignment)

    def add_scope_grant(self, grant: ScopeGrantModel) -> None:
        self.session.add(grant)

    async def get_invitation(self, project_id: str, invitation_id: str) -> InvitationModel | None:
        return await self.session.scalar(
            select(InvitationModel).where(
                InvitationModel.project_id == project_id,
                InvitationModel.invitation_id == invitation_id,
            )
        )

    async def list_invitations(
        self,
        project_id: str,
        *,
        limit: int,
        after: tuple[datetime, str] | None = None,
        before: tuple[datetime, str] | None = None,
    ) -> list[InvitationModel]:
        stmt = select(InvitationModel).where(InvitationModel.project_id == project_id)
        if after:
            created_at, invitation_id = after
            stmt = stmt.where(
                or_(
                    InvitationModel.created_at < created_at,
                    and_(
                        InvitationModel.created_at == created_at,
                        InvitationModel.invitation_id < invitation_id,
                    ),
                )
            )
        if before:
            created_at, invitation_id = before
            stmt = stmt.where(
                or_(
                    InvitationModel.created_at > created_at,
                    and_(
                        InvitationModel.created_at == created_at,
                        InvitationModel.invitation_id > invitation_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            InvitationModel.created_at.desc(), InvitationModel.invitation_id.desc()
        ).limit(limit + 1)
        return list((await self.session.scalars(stmt)).all())

    def add_invitation(self, invitation: InvitationModel) -> None:
        self.session.add(invitation)

    def add_preflight(self, preflight: AccessPreflightModel) -> None:
        self.session.add(preflight)

    async def consume_preflight(
        self, token_hash: str, now: datetime
    ) -> AccessPreflightModel | None:
        row = await self.session.scalar(
            select(AccessPreflightModel)
            .where(
                AccessPreflightModel.token_hash == token_hash,
                AccessPreflightModel.used_at.is_(None),
                AccessPreflightModel.expires_at > now,
            )
            .with_for_update()
        )
        if row is not None:
            row.used_at = now
        return row


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_event(self, scope_key: str, event_id: str) -> AuditEventModel | None:
        return await self.session.scalar(
            select(AuditEventModel).where(
                AuditEventModel.scope_key == scope_key,
                AuditEventModel.event_id == event_id,
            )
        )

    async def list_events(
        self,
        scope_key: str,
        *,
        limit: int,
        after: tuple[datetime, str] | None = None,
        before: tuple[datetime, str] | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        actor_ids: Sequence[str] = (),
        event_names: Sequence[str] = (),
        resource_types: Sequence[str] = (),
        outcomes: Sequence[str] = (),
        risk_levels: Sequence[str] = (),
        request_id: str | None = None,
        region_codes: Sequence[str] = (),
    ) -> list[AuditEventModel]:
        stmt = select(AuditEventModel).where(AuditEventModel.scope_key == scope_key)
        if occurred_from:
            stmt = stmt.where(AuditEventModel.occurred_at >= occurred_from)
        if occurred_to:
            stmt = stmt.where(AuditEventModel.occurred_at < occurred_to)
        if actor_ids:
            stmt = stmt.where(AuditEventModel.actor_principal_id.in_(actor_ids))
        if event_names:
            stmt = stmt.where(AuditEventModel.event_name.in_(event_names))
        if resource_types:
            stmt = stmt.where(AuditEventModel.resource_type.in_(resource_types))
        if outcomes:
            stmt = stmt.where(AuditEventModel.outcome_status.in_(outcomes))
        if risk_levels:
            stmt = stmt.where(AuditEventModel.risk_level.in_(risk_levels))
        if request_id:
            stmt = stmt.where(AuditEventModel.request_id == request_id)
        if region_codes:
            stmt = stmt.where(AuditEventModel.region_code.in_(region_codes))
        if after:
            occurred_at, event_id = after
            stmt = stmt.where(
                or_(
                    AuditEventModel.occurred_at < occurred_at,
                    and_(
                        AuditEventModel.occurred_at == occurred_at,
                        AuditEventModel.event_id < event_id,
                    ),
                )
            )
        if before:
            occurred_at, event_id = before
            stmt = stmt.where(
                or_(
                    AuditEventModel.occurred_at > occurred_at,
                    and_(
                        AuditEventModel.occurred_at == occurred_at,
                        AuditEventModel.event_id > event_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            AuditEventModel.occurred_at.desc(), AuditEventModel.event_id.desc()
        ).limit(limit + 1)
        return list((await self.session.scalars(stmt)).all())

    async def list_related_events(
        self,
        scope_key: str,
        event_id: str,
        *,
        limit: int,
        after: tuple[datetime, str] | None = None,
        before: tuple[datetime, str] | None = None,
    ) -> list[AuditEventModel]:
        # JSON relationship filtering is deliberately conservative and portable.
        # PostgreSQL migration adds a GIN index for production lookup.
        stmt = select(AuditEventModel).where(
            AuditEventModel.scope_key == scope_key,
            AuditEventModel.input_payload.cast(Text).contains(event_id),
        )
        if after:
            occurred_at, related_id = after
            stmt = stmt.where(
                or_(
                    AuditEventModel.occurred_at < occurred_at,
                    and_(
                        AuditEventModel.occurred_at == occurred_at,
                        AuditEventModel.event_id < related_id,
                    ),
                )
            )
        if before:
            occurred_at, related_id = before
            stmt = stmt.where(
                or_(
                    AuditEventModel.occurred_at > occurred_at,
                    and_(
                        AuditEventModel.occurred_at == occurred_at,
                        AuditEventModel.event_id > related_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            AuditEventModel.occurred_at.desc(), AuditEventModel.event_id.desc()
        ).limit(limit + 1)
        return list((await self.session.scalars(stmt)).all())

    async def next_ingest_sequence(self) -> int:
        value = await self.session.scalar(
            select(func.coalesce(func.max(AuditEventModel.ingest_sequence), 0))
        )
        return int(value or 0) + 1

    def add_event(self, event: AuditEventModel) -> None:
        self.session.add(event)

    def add_outbox(self, outbox: AuditOutboxModel) -> None:
        self.session.add(outbox)

    def add_projection(self, projection: AuditReadProjectionModel) -> None:
        self.session.add(projection)

    async def get_export(self, scope_key: str, export_id: str) -> AuditExportJobModel | None:
        return await self.session.scalar(
            select(AuditExportJobModel).where(
                AuditExportJobModel.scope_key == scope_key,
                AuditExportJobModel.export_id == export_id,
            )
        )

    def add_export(self, export: AuditExportJobModel) -> None:
        self.session.add(export)

    async def get_retention_policy(self) -> AuditRetentionPolicyModel | None:
        return await self.session.scalar(
            select(AuditRetentionPolicyModel)
            .order_by(AuditRetentionPolicyModel.effective_at.desc())
            .limit(1)
        )

    async def facet_values(self, scope_key: str) -> dict[str, list[str]]:
        rows = await self.session.execute(
            select(
                AuditEventModel.event_name,
                AuditEventModel.actor_principal_id,
                AuditEventModel.resource_type,
                AuditEventModel.outcome_status,
                AuditEventModel.risk_level,
            ).where(AuditEventModel.scope_key == scope_key)
        )
        columns: list[set[str]] = [set() for _ in range(5)]
        for row in rows:
            for idx, value in enumerate(row):
                if value is not None:
                    columns[idx].add(value)
        names = ["event_names", "actor_ids", "resource_types", "outcomes", "risk_levels"]
        return {name: sorted(values) for name, values in zip(names, columns, strict=True)}

    async def counts(self, scope_key: str, since: datetime) -> dict[str, int]:
        today = await self.session.scalar(
            select(func.count())
            .select_from(AuditEventModel)
            .where(AuditEventModel.scope_key == scope_key, AuditEventModel.occurred_at >= since)
        )
        high = await self.session.scalar(
            select(func.count())
            .select_from(AuditEventModel)
            .where(
                AuditEventModel.scope_key == scope_key,
                AuditEventModel.risk_level.in_(("HIGH", "CRITICAL")),
            )
        )
        failed = await self.session.scalar(
            select(func.count())
            .select_from(AuditEventModel)
            .where(
                AuditEventModel.scope_key == scope_key,
                AuditEventModel.outcome_status == "FAILED",
            )
        )
        actors = await self.session.scalar(
            select(func.count(func.distinct(AuditEventModel.actor_principal_id))).where(
                AuditEventModel.scope_key == scope_key
            )
        )
        return {
            "today": int(today or 0),
            "high_risk": int(high or 0),
            "failed": int(failed or 0),
            "active_actors": int(actors or 0),
        }

"""In-memory implementation of administrative scopes used by the access API."""

from .governance import (
    ORGANIZATION_ASSET_PERMISSIONS,
    GovernanceDirectory,
    InboxEntry,
    MemberEntry,
    denied,
    missing_member,
)


from .directory_memory import MemoryDirectory


class MemoryGovernance(MemoryDirectory):
    def governance_directory(self, auth):
        with self._lock:
            managed = {o for a, o in self._organization_admins if a == auth.subject_id}
            projects = [
                (o, p)
                for o, p in sorted(self._organization_projects)
                if auth.is_platform_admin
                or o in managed
                or auth.has_capability("access.manage", p, o)
            ]
            return GovernanceDirectory(
                organizations=[
                    dict(
                        organization_id=o,
                        organization_name=self._organization_names.get(o,o),
                        join_code=self._join_codes.get(o) if auth.is_platform_admin or o in managed else None,
                        can_manage=auth.is_platform_admin or o in managed,
                    )
                    for o in sorted(managed | {o for o, _ in projects} | (set(self._organization_names) if auth.is_platform_admin else set())) if (o,"") not in self._retired
                ],
                projects=[
                    dict(organization_id=o, project_id=p, project_name=self._project_names.get((o,p),p)) for o, p in projects
                ],
            )

    def _check_management(self, auth, org, project):
        if auth.is_platform_admin or (auth.subject_id, org) in self._organization_admins:
            return
        if not project or not auth.has_capability("access.manage", project, org):
            raise denied()

    def joinable_projects(self, auth, org, principal=None):
        from .access_repository import _organization_membership_required
        from .access_models import AccessRequestStatus
        from .governance import ProjectEntry

        target = principal or auth.subject_id
        with self._lock:
            if auth.service_identity:
                raise denied()
            if principal is not None:
                self._check_management(auth, org, None)
            if (target, org) not in self._organization_memberships:
                raise _organization_membership_required()
            pending = {
                r.project_id
                for r in self._membership_requests.values()
                if principal is None
                and r.organization_id == org
                and r.requester_id == target
                and r.status is AccessRequestStatus.PENDING
            }
            items = [
                ProjectEntry(
                    organization_id=o, project_id=p, project_name=self._project_names.get((o, p), p)
                )
                for o, p in self._organization_projects
                if o == org
                and (target, o, p) not in self._memberships
                and p not in pending
                and (o, p) not in self._retired
                and (o, "") not in self._retired
            ]
            return sorted(items, key=lambda item: (item.project_name, item.project_id))

    def assign_project_member(self, auth, org, project, principal, key, request_id):
        from uuid import uuid4
        from .access_models import (
            AccountNotificationKind,
            AccountNotificationResourceType,
            AccessRequestStatus,
            MembershipRequest,
        )
        from .access_repository import _now, _organization_membership_required
        from .directory_changes import unknown_scope

        operation = "membership.assign"
        payload = dict(organization_id=org, project_id=project, principal_id=principal)
        with self._lock:
            self._check_management(auth, org, None)
            if auth.service_identity:
                raise denied()
            self.assert_directory_active(org, project)
            if (org, project) not in self._organization_projects:
                raise unknown_scope()
            if (principal, org) not in self._organization_memberships:
                raise _organization_membership_required()
            if self._accounts[principal].principal.status.value != "ACTIVE":
                raise denied()
            if self._repeat(auth.subject_id, operation, key, payload):
                return
            if (principal, org, project) not in self._memberships:
                reason = "管理员直接添加至项目"
                timestamp = _now()
                pending = next(
                    (
                        r
                        for r in self._membership_requests.values()
                        if r.organization_id == org
                        and r.project_id == project
                        and r.requester_id == principal
                        and r.status is AccessRequestStatus.PENDING
                    ),
                    None,
                )
                if pending:
                    record = pending.model_copy(
                        update=dict(
                            status=AccessRequestStatus.APPROVED,
                            decided_by=auth.subject_id,
                            decision_reason=reason,
                            updated_at=timestamp,
                            revision=pending.revision + 1,
                        )
                    )
                else:
                    record = MembershipRequest(
                        request_id=str(uuid4()),
                        organization_id=org,
                        project_id=project,
                        requester_id=principal,
                        status=AccessRequestStatus.APPROVED,
                        reason=reason,
                        decided_by=auth.subject_id,
                        decision_reason=reason,
                        created_at=timestamp,
                        updated_at=timestamp,
                        revision=1,
                    )
                self._membership_requests[record.request_id] = record
                self._memberships.add((principal, org, project))
                self._bump_revision(principal)
                self._append_audit(
                    scope_kind="PROJECT",
                    organization_id=org,
                    project_id=project,
                    actor_id=auth.subject_id,
                    action=operation,
                    resource_type="account",
                    resource_id=principal,
                    request_id=request_id,
                    outcome="SUCCEEDED",
                    safe_details={"source_request_id": record.request_id},
                )
                self._create_notification(
                    recipient_id=principal,
                    organization_id=org,
                    project_id=project,
                    resource_type=AccountNotificationResourceType.ACCESS_REQUEST,
                    resource_id=record.request_id,
                    event_key=f"access-request:MEMBERSHIP_APPROVED:{record.request_id}",
                    kind=AccountNotificationKind.MEMBERSHIP_APPROVED,
                )
            self._remember(auth.subject_id, operation, key, payload, principal)

    def governance_members(self, auth, org, project, offset, *, unassigned_only=False):
        with self._lock:
            self._check_management(auth, org, project)
            members = self._memberships if project else self._organization_memberships
            admins = self._project_admins if project else self._organization_admins
            scope = (org, project) if project else (org,)
            assigned = {user for user, organization, _ in self._memberships if organization == org} if unassigned_only else set()
            rows = [
                MemberEntry(
                    principal_id=k[0],
                    username=self._accounts[k[0]].principal.username,
                    administrator=k in admins,
                )
                for k in members
                if k[1:] == scope and k[0] not in assigned
            ]
            return sorted(rows, key=lambda r: (r.username, r.principal_id))[offset : offset + 51]

    def change_administrator(self, auth, principal, cmd, request_id):
        with self._lock:
            org, project = cmd.organization_id, cmd.project_id
            self.assert_directory_active(org, project)
            manages_projects = project and (auth.is_platform_admin or (auth.subject_id, org) in self._organization_admins)
            if auth.service_identity or not (manages_projects or (not project and auth.is_platform_admin and principal != auth.subject_id)):
                raise denied()
            account = self._accounts.get(principal)
            if (
                not account
                or account.principal.status.value != "ACTIVE"
                or org not in self._organization_names or (org,"") in self._retired
            ):
                raise missing_member()
            if project:
                key = (principal, org, project)
                if (
                    principal,
                    org,
                ) not in self._organization_memberships or key not in self._memberships:
                    raise missing_member()
                admins = self._project_admins
                if principal == auth.subject_id and (key in admins) == cmd.enabled:
                    return MemberEntry(principal_id=principal, username=account.principal.username, administrator=cmd.enabled)
            else:
                key = (principal, org)
                if cmd.enabled:
                    self._organization_memberships.add(key)
                if key not in self._organization_memberships:
                    raise missing_member()
                admins = self._organization_admins
            if cmd.enabled:
                admins.add(key)
            else:
                admins.discard(key)
            self._bump_revision(principal)
            self._append_audit(
                scope_kind="PROJECT" if project else "PLATFORM",
                organization_id=org,
                project_id=project,
                actor_id=auth.subject_id,
                action="administrator.appoint" if cmd.enabled else "administrator.remove",
                resource_type="account",
                resource_id=principal,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"reason": cmd.reason},
            )
            return MemberEntry(
                principal_id=principal,
                username=account.principal.username,
                administrator=cmd.enabled,
            )

    def governance_inbox(self, auth, org, project, offset):
        with self._lock:
            self._check_management(auth, org, project)
            rows = []
            for principal, account in self._accounts.items():
                for r in self.account_access_overview(principal_id=principal).requests:
                    if (
                        r.organization_id != org
                        or r.project_id != project
                        or r.status.value != "PENDING"
                    ):
                        continue
                    rows.append(
                        (
                            r.created_at,
                            InboxEntry(
                                request_id=r.request_id,
                                requester_id=principal,
                                username=account.principal.username,
                                organization_id=org,
                                project_id=project,
                                kind=r.kind,
                                reason=r.reason,
                                capability_keys=list(r.capability_keys),
                                resource_id=r.resource_id,
                                expires_at=r.expires_at.isoformat() if r.expires_at else None,
                                replaces_request_id=r.replaces_request_id,
                            ),
                        )
                    )
            return [
                item
                for _, item in sorted(rows, key=lambda v: (v[0], v[1].request_id))[
                    offset : offset + 51
                ]
            ]

    def create_managed_project(self, auth, cmd, request_id):
        from .governance import ProjectEntry
        from hc_data_platform.core.errors import problem

        with self._lock:
            self._check_management(auth, cmd.organization_id, None)
            key = cmd.organization_id, cmd.project_id
            if key in self._organization_projects:
                raise problem(
                    status=409,
                    code="PROJECT_ALREADY_EXISTS",
                    title="Project already exists",
                    detail="项目编号已存在。",
                )
            if cmd.organization_id not in self._organization_names or (cmd.organization_id,"") in self._retired or key in self._retired:
                raise missing_member()
            self._organization_projects.add(key)
            self._project_names[key]=cmd.project_name
            return ProjectEntry(**cmd.model_dump())

    def remove_managed_member(self, auth, principal, command, request_id):
        from .access_models import AccessRequestStatus
        from .access_repository import _now

        org, project = command.organization_id, command.project_id
        with self._lock:
            self._check_management(auth, org, project)
            if principal == auth.subject_id and (
                not project or auth.service_identity or not (auth.is_platform_admin or (principal, org) in self._organization_admins)
            ):
                raise denied()
            key = (principal, org, project) if project else (principal, org)
            members = self._memberships if project else self._organization_memberships
            admins = self._project_admins if project else self._organization_admins
            if key not in members:
                return
            if not self.managed_member_detail(auth, principal, org, project).can_remove:
                raise denied()
            if key in admins and not (
                auth.is_platform_admin
                or (project and (auth.subject_id, org) in self._organization_admins)
            ):
                raise denied()
            self._direct_project_grants = {
                k: v for k, v in self._direct_project_grants.items() if k[: len(key)] != key
            }
            members.discard(key)
            admins.discard(key)
            if not project:
                self._memberships = {k for k in self._memberships if k[:2] != key}
                self._project_admins = {k for k in self._project_admins if k[:2] != key}
            for requests in (
                self._organization_membership_requests,
                self._membership_requests,
                self._capability_requests,
            ):
                for rid, item in requests.items():
                    owner = getattr(
                        item, "requester_id", self._organization_request_owners.get(rid)
                    )
                    if (
                        owner != principal
                        or item.organization_id != org
                        or (project and item.project_id != project)
                    ):
                        continue
                    if item.status is AccessRequestStatus.APPROVED:
                        requests[rid] = item.model_copy(
                            update={
                                "status": AccessRequestStatus.REVOKED,
                                "updated_at": _now(),
                                "revision": item.revision + 1,
                            }
                        )
            self._bump_revision(principal)
            self._append_audit(
                scope_kind="PROJECT" if project else "PLATFORM",
                organization_id=org,
                project_id=project,
                actor_id=auth.subject_id,
                action="membership.remove",
                resource_type="account",
                resource_id=principal,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"reason": command.reason},
            )

    def managed_member_detail(self, auth, principal, org, project):
        from .access_models import AccessRequestStatus
        from .access_repository import _now
        from .governance import MemberGrant, member_detail, can_revoke_approval

        with self._lock:
            self._check_management(auth, org, project)
            key = (principal, org, project) if project else (principal, org)
            if key not in (self._memberships if project else self._organization_memberships):
                from hc_data_platform.core.errors import problem

                raise problem(
                    status=404,
                    code="MANAGED_MEMBER_NOT_FOUND",
                    title="Member not found",
                    detail="此范围内没有该成员。",
                )
            profile = self._accounts[principal].principal.model_dump(exclude={"principal_id"})
            grants = [
                MemberGrant(
                    capability=cap,
                    expires_at=r.expires_at,
                    source_request_id=r.request_id if project else None,
                    can_revoke=bool(project) and can_revoke_approval(auth, r),
                )
                for r in self._capability_requests.values()
                if r.requester_id == principal
                and r.organization_id == org
                and (not project or r.project_id == project)
                and (principal, org, r.project_id) in self._memberships
                and r.status is AccessRequestStatus.APPROVED
                and (r.expires_at is None or r.expires_at > _now())
                for cap in r.effective_grant_keys
                if project or cap.partition("/")[0] in ORGANIZATION_ASSET_PERMISSIONS
            ]
            direct = self._direct_project_grants.get(key, set()) if project else {
                cap for membership, permissions in self._direct_project_grants.items()
                if membership[:2] == (principal, org) and membership in self._memberships
                for cap in permissions if cap.partition("/")[0] in ORGANIZATION_ASSET_PERMISSIONS
            }
            return member_detail(
                auth,
                principal,
                profile,
                (principal, org) in self._organization_admins,
                key in self._project_admins,
                "platform.admin" in self._platform_capabilities.get(principal, set()),
                direct,
                grants,
                project,
                auth.is_platform_admin or (auth.subject_id, org) in self._organization_admins,
                org,
            )

    def set_member_assignments(self, auth, principal, command, request_id):
        from .governance import assignment_change

        org, project = command.organization_id, command.project_id
        with self._lock:
            detail = self.managed_member_detail(auth, principal, org, project)
            if not detail.can_set_roles:
                raise denied()
            if (principal, org) not in self._organization_memberships:
                raise missing_member()
            kind, previous, selected = assignment_change(detail, command)
            if previous == selected:
                return detail
            key = (principal, org, project)
            self._direct_project_grants[key] = (self._direct_project_grants.get(key, set()) - previous) | selected
            self._bump_revision(principal)
            self._append_audit(
                scope_kind="PROJECT",
                organization_id=org,
                project_id=project,
                actor_id=auth.subject_id,
                action=f"membership.{kind}.set",
                resource_type="account",
                resource_id=principal,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={
                    f"previous_{kind}": sorted(previous),
                    f"assigned_{kind}": sorted(selected),
                    "reason": command.reason,
                },
            )
            return self.managed_member_detail(auth, principal, org, project)

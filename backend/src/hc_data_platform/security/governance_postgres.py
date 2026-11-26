"""Scoped administration of organizations and projects."""

from .governance import (
    ORGANIZATION_ASSET_PERMISSIONS,
    GovernanceDirectory,
    InboxEntry,
    MemberEntry,
    denied,
    missing_member,
)
from hc_data_platform.core.errors import problem


def manages_org(cursor, auth, org, *, lock=False):
    if auth.is_platform_admin:
        return True
    cursor.execute(
        """
        SELECT 1 FROM access_control.organization_memberships
        WHERE principal_id::text=%s AND organization_id=%s AND active AND is_administrator
    """
        + (" FOR SHARE" if lock else ""),
        (auth.subject_id, org),
    )
    return cursor.fetchone() is not None


def require_active_directory(cursor, org, project=None):
    cursor.execute("SELECT 1 WHERE access_control.directory_retired(%s,%s)",(org,project or ""))
    if cursor.fetchone():
        from .directory_changes import unknown_scope
        raise unknown_scope()


def lock_project_enrollment(cursor, org, project, principal):
    cursor.execute(
        "SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0))", ("directory/" + org,)
    )
    cursor.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(jsonb_build_array(%s::text,%s::text,%s::text)::text,1))",
        (org, project, principal),
    )


def require_manager(cursor, auth, org, project=None):
    require_active_directory(cursor,org,project)
    if manages_org(cursor, auth, org):
        return
    if project and auth.has_capability("access.manage", project, org):
        return
    raise denied()


from .directory_postgres import PostgresDirectory, lock_directory


class PostgresGovernance(PostgresDirectory):
    def governance_directory(self, auth):
        from .access_postgres import _row

        with self._connection_factory() as db, db.cursor() as c:
            c.execute(
                """SELECT organization_id FROM access_control.organization_memberships
                        WHERE principal_id::text=%s AND active AND is_administrator""",
                (auth.subject_id,),
            )
            managed = [_row(c, r)["organization_id"] for r in c.fetchall()]
            pairs = sorted(
                {
                    (o, p)
                    for o, p, _ in auth.organization_scope_triples
                    if auth.has_capability("access.manage", p, o)
                }
            )
            # The RLS-protected directory read is limited to verified management scopes.
            c.execute("SELECT set_config('app.platform_admin','true',true)")
            c.execute(
                """
                SELECT d.organization_id, d.project_id, d.display_name AS project_name
                FROM access_control.active_projects d
                WHERE %s OR d.organization_id=ANY(%s::text[]) OR (d.organization_id,d.project_id)
                  IN (SELECT * FROM unnest(%s::text[],%s::text[]))
                ORDER BY 1,2
            """,
                (auth.is_platform_admin, managed, [p[0] for p in pairs], [p[1] for p in pairs]),
            )
            projects = [_row(c, r) for r in c.fetchall()]
            visible = set(managed) | {p["organization_id"] for p in projects}
            c.execute(
                """
                SELECT organization_id, display_name AS organization_name,
                       CASE WHEN %s OR organization_id=ANY(%s::text[]) THEN join_code END AS join_code
                FROM access_control.organization_join_codes o WHERE (%s OR organization_id=ANY(%s::text[])) AND NOT EXISTS (SELECT 1 FROM access_control.retired_scopes r WHERE r.organization_id=o.organization_id AND r.project_id='')
            """,
                (auth.is_platform_admin, managed, auth.is_platform_admin, list(visible)),
            )
            orgs = {r["organization_id"]: r for r in (_row(c, v) for v in c.fetchall())}
            for org in visible:
                orgs.setdefault(
                    org, dict(organization_id=org, organization_name=org, join_code=None)
                )
            return GovernanceDirectory(
                organizations=[
                    dict(**orgs[o], can_manage=auth.is_platform_admin or o in managed)
                    for o in sorted(orgs)
                ],
                projects=projects,
            )

    def joinable_projects(self, auth, org, principal=None):
        from .access_postgres import _require_active_organization, _row
        from .governance import ProjectEntry

        target = principal or auth.subject_id
        if auth.service_identity:
            raise denied()
        with self._connection_factory() as db, db.cursor() as c:
            if principal is not None and not manages_org(c, auth, org, lock=True):
                raise denied()
            _require_active_organization(c, target, org)
            # This read-only catalog query returns names only within the verified organization.
            c.execute("SELECT set_config('app.platform_admin','true',true)")
            c.execute(
                """SELECT p.organization_id, p.project_id, p.display_name AS project_name
                   FROM access_control.active_projects p WHERE p.organization_id=%s
                   AND NOT EXISTS (SELECT 1 FROM access_control.memberships m
                       WHERE (m.organization_id,m.project_id)=(p.organization_id,p.project_id)
                         AND m.principal_id=%s::uuid AND m.active)
                   AND (%s OR NOT EXISTS (SELECT 1 FROM access_control.membership_requests r
                       WHERE (r.organization_id,r.project_id)=(p.organization_id,p.project_id)
                         AND r.requester_id=%s::uuid AND r.status='PENDING'))
                   ORDER BY p.display_name,p.project_id""",
                (org, target, principal is not None, target),
            )
            return [ProjectEntry(**_row(c, row)) for row in c.fetchall()]

    def assign_project_member(self, auth, org, project, principal, key, request_id):
        from uuid import uuid4
        from .access_models import AccountNotificationKind, AccountNotificationResourceType
        from .access_postgres import _require_active_organization, _row
        from .directory_changes import unknown_scope

        operation = "membership.assign"
        payload = dict(organization_id=org, project_id=project, principal_id=principal)
        with self._connection_factory() as db, db.cursor() as c:
            if auth.service_identity:
                raise denied()
            lock_project_enrollment(c, org, project, principal)
            if not manages_org(c, auth, org, lock=True):
                raise denied()
            c.execute(
                "SELECT set_config('app.organization_id',%s,true),set_config('app.project_id',%s,true)",
                (org, project),
            )
            c.execute(
                "SELECT 1 FROM access_control.active_projects WHERE organization_id=%s AND project_id=%s",
                (org, project),
            )
            if not c.fetchone():
                raise unknown_scope()
            _require_active_organization(c, principal, org)
            c.execute(
                "SELECT 1 FROM access_control.accounts WHERE principal_id=%s::uuid AND status='ACTIVE'",
                (principal,),
            )
            if not c.fetchone():
                raise denied()
            if self._begin_idempotency(c, auth.subject_id, operation, key, payload):
                return
            c.execute(
                """SELECT request_id FROM access_control.membership_requests WHERE requester_id=%s::uuid
                      AND organization_id=%s AND project_id=%s AND status='PENDING' FOR UPDATE""",
                (principal, org, project),
            )
            pending_row = c.fetchone()
            pending = str(_row(c, pending_row)["request_id"]) if pending_row else None
            c.execute(
                """SELECT 1 FROM access_control.memberships WHERE principal_id=%s::uuid
                      AND organization_id=%s AND project_id=%s AND active""",
                (principal, org, project),
            )
            if not c.fetchone():
                reason = "管理员直接添加至项目"
                if pending:
                    source = pending
                    c.execute(
                        """UPDATE access_control.membership_requests SET status='APPROVED', decided_by=%s,
                              decision_reason=%s, updated_at=now(),revision=revision+1 WHERE request_id=%s::uuid""",
                        (auth.subject_id, reason, source),
                    )
                else:
                    source = str(uuid4())
                    c.execute(
                        """INSERT INTO access_control.membership_requests
                              (request_id,organization_id,project_id,requester_id,status,reason,decided_by,decision_reason)
                              VALUES (%s::uuid,%s,%s,%s::uuid,'APPROVED',%s,%s,%s)""",
                        (source, org, project, principal, reason, auth.subject_id, reason),
                    )
                c.execute(
                    """INSERT INTO access_control.memberships
                          (principal_id,organization_id,project_id,source_request_id,activated_by)
                          VALUES (%s::uuid,%s,%s,%s::uuid,%s)
                          ON CONFLICT (principal_id,organization_id,project_id) DO UPDATE SET
                          source_request_id=EXCLUDED.source_request_id,active=true,activated_by=EXCLUDED.activated_by,
                          activated_at=now(),revoked_at=NULL,revoked_by=NULL WHERE NOT access_control.memberships.active""",
                    (principal, org, project, source, auth.subject_id),
                )
                # Recheck account status while publishing the access revision in this transaction.
                c.execute("""UPDATE access_control.accounts SET capability_revision=capability_revision+1
                             WHERE principal_id=%s::uuid AND status='ACTIVE' RETURNING 1""",(principal,))
                if not c.fetchone():
                    raise denied()
                self._audit(
                    c,
                    scope_kind="PROJECT",
                    organization_id=org,
                    project_id=project,
                    actor_id=auth.subject_id,
                    action=operation,
                    resource_type="account",
                    resource_id=principal,
                    request_id=request_id,
                    outcome="SUCCEEDED",
                    safe_details={"source_request_id": source},
                )
                self._insert_notification(
                    c,
                    recipient_id=principal,
                    organization_id=org,
                    project_id=project,
                    resource_type=AccountNotificationResourceType.ACCESS_REQUEST,
                    resource_id=source,
                    event_key=f"access-request:MEMBERSHIP_APPROVED:{source}",
                    kind=AccountNotificationKind.MEMBERSHIP_APPROVED,
                )
            self._finish_idempotency(c, auth.subject_id, operation, key, principal)

    def governance_members(self, auth, org, project, offset, *, unassigned_only=False):
        from .access_postgres import _row

        with self._connection_factory() as db, db.cursor() as c:
            require_manager(c, auth, org, project)
            table = "memberships" if project else "organization_memberships"
            cond = " AND m.project_id=%s" if project else ""
            if unassigned_only:
                cond = """ AND NOT EXISTS (
                    SELECT 1 FROM access_control.memberships AS pm
                    WHERE pm.organization_id = m.organization_id
                      AND pm.principal_id = m.principal_id AND pm.active
                )"""
            c.execute(
                f"""SELECT a.principal_id::text AS principal_id, a.display_username AS username,
                            m.is_administrator AS administrator
                         FROM access_control.{table} m JOIN access_control.accounts a USING(principal_id)
                         WHERE m.organization_id=%s AND m.active{cond}
                         ORDER BY a.display_username,a.principal_id LIMIT 51 OFFSET %s""",
                (org, *([project] if project else []), offset),
            )
            return [MemberEntry(**_row(c, r)) for r in c.fetchall()]

    def change_administrator(self, auth, principal, cmd, request_id):
        from .access_postgres import _row

        org, project = cmd.organization_id, cmd.project_id
        with self._connection_factory() as db, db.cursor() as c:
            self_change = principal == auth.subject_id
            if project:
                lock_project_enrollment(c, org, project, principal)
                if auth.service_identity or not manages_org(c, auth, org, lock=True):
                    raise denied()
            elif self_change or not auth.is_platform_admin:
                raise denied()
            require_active_directory(c,org,project)
            c.execute(
                "SELECT display_username AS username FROM access_control.accounts WHERE principal_id=%s::uuid AND status='ACTIVE' FOR UPDATE",
                (principal,),
            )
            target = c.fetchone()
            if not target:
                raise missing_member()
            username = _row(c, target)["username"]
            if project:
                c.execute(
                    """SELECT is_administrator FROM access_control.organization_memberships
                       WHERE principal_id=%s::uuid AND organization_id=%s AND active FOR SHARE""",
                    (principal, org),
                )
                membership = c.fetchone()
                if not membership:
                    raise missing_member()
                if self_change:
                    c.execute(
                        """SELECT is_administrator FROM access_control.memberships
                           WHERE principal_id=%s::uuid AND organization_id=%s AND project_id=%s AND active FOR UPDATE""",
                        (principal, org, project),
                    )
                    current = c.fetchone()
                    if not current:
                        raise missing_member()
                    if _row(c, current)["is_administrator"] == cmd.enabled:
                        return MemberEntry(principal_id=principal, username=username, administrator=cmd.enabled)
                c.execute(
                    """UPDATE access_control.memberships SET is_administrator=%s
                             WHERE principal_id=%s::uuid AND organization_id=%s AND project_id=%s AND active RETURNING 1""",
                    (cmd.enabled, principal, org, project),
                )
            else:
                c.execute("SELECT set_config('app.platform_admin','true',true)")
                c.execute(
                    """SELECT 1 FROM access_control.organization_join_codes WHERE organization_id=%s
                          UNION ALL SELECT 1 FROM registry.organization_projects WHERE organization_id=%s LIMIT 1""",
                    (org, org),
                )
                if not c.fetchone():
                    raise problem(
                        status=404,
                        code="UNKNOWN_ORGANIZATION",
                        title="Unknown organization",
                        detail="组织不存在。",
                    )
                if cmd.enabled:
                    c.execute(
                        """INSERT INTO access_control.organization_memberships AS m
                               (principal_id,organization_id,active,activated_by,is_administrator) VALUES (%s::uuid,%s,true,%s,true)
                               ON CONFLICT(principal_id,organization_id) DO UPDATE SET active=true,is_administrator=true,
                               activated_by=CASE WHEN m.active THEN m.activated_by ELSE EXCLUDED.activated_by END,
                               activated_at=CASE WHEN m.active THEN m.activated_at ELSE now() END,
                               source_request_id=CASE WHEN m.active THEN m.source_request_id END,revoked_by=NULL,revoked_at=NULL RETURNING 1""",
                        (principal, org, auth.subject_id),
                    )
                else:
                    c.execute(
                        """UPDATE access_control.organization_memberships SET is_administrator=false
                              WHERE principal_id=%s::uuid AND organization_id=%s AND active RETURNING 1""",
                        (principal, org),
                    )
            if not c.fetchone():
                raise missing_member()
            self._bump_revision(c, principal)
            self._audit(
                c,
                scope_kind="PROJECT" if project else "PLATFORM",
                organization_id=org,
                project_id=project,
                actor_id=auth.subject_id,
                action="administrator.appoint" if cmd.enabled else "administrator.remove",
                resource_type="account",
                resource_id=principal,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"reason": cmd.reason, "organization_id": org, "project_id": project},
            )
            return MemberEntry(principal_id=principal, username=username, administrator=cmd.enabled)

    def governance_inbox(self, auth, org, project, offset):
        from .access_postgres import _row

        with self._connection_factory() as db, db.cursor() as c:
            require_manager(c, auth, org, project)
            if not project:
                query = """SELECT request_id,requester_id,organization_id,NULL::text AS project_id,'ORGANIZATION' AS kind,
                    reason,ARRAY[]::text[] AS capability_keys,NULL::text AS resource_id,NULL::text AS expires_at,NULL::text AS replaces_request_id,created_at
                    FROM access_control.organization_membership_requests WHERE organization_id=%s AND status='PENDING'"""
                args = (org,)
            else:
                query = """SELECT request_id,requester_id,organization_id,project_id,'PROJECT' AS kind,reason,
                    ARRAY[]::text[] AS capability_keys,NULL::text AS resource_id,NULL::text AS expires_at,NULL::text AS replaces_request_id,created_at FROM access_control.membership_requests
                    WHERE organization_id=%s AND project_id=%s AND status='PENDING'
                    UNION ALL SELECT request_id,requester_id,organization_id,project_id,'CAPABILITY',reason,capability_keys,resource_id,expires_at::text,replaces_request_id::text,created_at
                    FROM access_control.capability_requests WHERE organization_id=%s AND project_id=%s AND status='PENDING'"""
                args = (org, project, org, project)
            c.execute(
                f"""SELECT r.request_id::text, r.requester_id::text, a.display_username AS username,r.organization_id,r.project_id,r.kind,
                         r.reason,r.capability_keys,r.resource_id,r.expires_at,r.replaces_request_id FROM ({query}) r JOIN access_control.accounts a ON a.principal_id=r.requester_id
                         ORDER BY r.created_at,r.request_id LIMIT 51 OFFSET %s""",
                (*args, offset),
            )
            return [InboxEntry(**_row(c, r)) for r in c.fetchall()]

    def create_managed_project(self, auth, cmd, request_id):
        from .governance import ProjectEntry

        with self._connection_factory() as db, db.cursor() as c:
            lock_directory(c, cmd.organization_id)
            if not manages_org(c, auth, cmd.organization_id, lock=True):
                raise denied()
            c.execute(
                """SELECT 1 FROM access_control.organization_join_codes o WHERE organization_id=%s AND NOT EXISTS
                   (SELECT 1 FROM access_control.retired_scopes r WHERE r.organization_id=o.organization_id AND r.project_id IN ('',%s))""",
                (cmd.organization_id,cmd.project_id),
            )
            if not c.fetchone():
                raise problem(
                    status=409,
                    code="ORGANIZATION_DIRECTORY_REQUIRED",
                    title="Organization directory required",
                    detail="请先登记组织。",
                )
            c.execute(
                "SELECT set_config('app.organization_id',%s,true),set_config('app.project_id',%s,true)",
                (cmd.organization_id, cmd.project_id),
            )
            c.execute(
                """INSERT INTO registry.organization_projects(organization_id,project_id,display_name)
                         VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING 1""",
                (cmd.organization_id, cmd.project_id, cmd.project_name),
            )
            if not c.fetchone():
                raise problem(
                    status=409,
                    code="PROJECT_ALREADY_EXISTS",
                    title="Project already exists",
                    detail="项目编号已存在。",
                )
            self._audit(
                c,
                scope_kind="PROJECT",
                organization_id=cmd.organization_id,
                project_id=cmd.project_id,
                actor_id=auth.subject_id,
                action="project.create",
                resource_type="project",
                resource_id=cmd.project_id,
                request_id=request_id,
                outcome="SUCCEEDED",
            )
            return ProjectEntry(**cmd.model_dump())

    def remove_managed_member(self, auth, principal, command, request_id):
        from .access_postgres import _row

        org, project = command.organization_id, command.project_id
        with self._connection_factory() as db, db.cursor() as c:
            require_manager(c, auth, org, project)
            if auth.subject_id == principal and (
                not project or auth.service_identity or not manages_org(c, auth, org, lock=True)
            ):
                raise denied()
            table = "memberships" if project else "organization_memberships"
            project_clause = " AND project_id=%s" if project else ""
            scope = (principal, org, project) if project else (principal, org)
            where = "principal_id=%s::uuid AND organization_id=%s" + project_clause
            c.execute(
                f"SELECT is_administrator FROM access_control.{table} WHERE {where} AND active FOR UPDATE",
                scope,
            )
            row = c.fetchone()
            if not row:
                return
            if not self._member_detail(c, auth, principal, org, project).can_remove:
                raise denied()
            for relation in (
                ["memberships", "capability_grants"]
                if project
                else ["organization_memberships", "memberships", "capability_grants"]
            ):
                c.execute(
                    f"""UPDATE access_control.{relation} SET active=false, revoked_at=now(), revoked_by=%s
                          WHERE {where} AND active""",
                    (auth.subject_id, *scope),
                )
            for relation in (
                ["membership_requests", "capability_requests"]
                if project
                else [
                    "organization_membership_requests",
                    "membership_requests",
                    "capability_requests",
                ]
            ):
                c.execute(
                    f"""UPDATE access_control.{relation} SET status='REVOKED', updated_at=now(),
                          revision=revision+1,decided_by=%s,decision_reason=%s
                          WHERE requester_id=%s::uuid AND organization_id=%s{project_clause} AND status='APPROVED'""",
                    (auth.subject_id, command.reason, *scope),
                )
            self._bump_revision(c, principal)
            self._audit(
                c,
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

    def _member_detail(self, c, auth, principal, org, project, *, lock=False):
        from .access_postgres import _row
        from .governance import MemberGrant, member_detail, can_revoke_approval

        require_manager(c, auth, org, project)
        table = "memberships" if project else "organization_memberships"
        project_fields = (
            "(m.assigned_roles || m.assigned_permissions) AS assigned_roles, m.is_administrator AS project_admin,"
            if project
            else "ARRAY[]::text[] AS assigned_roles, false AS project_admin,"
        )
        project_filter = " AND m.project_id=%s" if project else ""
        c.execute(
            f"""SELECT a.display_username AS username, a.display_name, a.status, a.created_at,
                    {project_fields} om.is_administrator AS org_admin,
                    EXISTS(SELECT 1 FROM access_control.platform_capability_grants g WHERE g.principal_id=a.principal_id
                       AND g.capability_key='platform.admin' AND g.active) AS platform_admin
                  FROM access_control.{table} m JOIN access_control.accounts a USING(principal_id)
                  LEFT JOIN access_control.organization_memberships om ON om.principal_id=m.principal_id
                      AND om.organization_id=m.organization_id AND om.active
                  WHERE m.principal_id=%s::uuid AND m.organization_id=%s AND m.active {project_filter}"""
            + (" FOR UPDATE OF m" if lock else ""),
            (principal, org, *([project] if project else [])),
        )
        row = c.fetchone()
        if not row:
            raise problem(
                status=404,
                code="MANAGED_MEMBER_NOT_FOUND",
                title="Member not found",
                detail="此范围内没有该成员。",
            )
        data = _row(c, row)
        org_admin = bool(data.pop("org_admin"))
        project_admin = data.pop("project_admin")
        platform_admin = data.pop("platform_admin")
        assigned = data.pop("assigned_roles")
        if project:
            c.execute(
                """SELECT capability_key AS capability,expires_at,source_request_id::text AS source_request_id FROM access_control.capability_grants
                      WHERE principal_id=%s::uuid AND organization_id=%s AND project_id=%s AND active
                      AND (expires_at IS NULL OR expires_at>now()) ORDER BY capability_key""",
                (principal, org, project),
            )
        else:
            c.execute(
                """SELECT DISTINCT key FROM access_control.memberships m
                   CROSS JOIN LATERAL unnest(m.assigned_permissions) AS direct(key)
                   WHERE m.principal_id=%s::uuid AND m.organization_id=%s AND m.active
                     AND split_part(key, '/', 1)=ANY(%s::text[])""",
                (principal, org, list(ORGANIZATION_ASSET_PERMISSIONS)),
            )
            assigned = [row[0] for row in c.fetchall()]
            c.execute(
                """SELECT DISTINCT g.capability_key AS capability,g.expires_at FROM access_control.capability_grants g
                      JOIN access_control.memberships m USING(principal_id,organization_id,project_id)
                      WHERE g.principal_id=%s::uuid AND g.organization_id=%s AND m.active AND g.active
                      AND (g.expires_at IS NULL OR g.expires_at>now()) AND split_part(g.capability_key, '/', 1)=ANY(%s::text[])
                      ORDER BY capability,expires_at""",
                (principal, org, list(ORGANIZATION_ASSET_PERMISSIONS)),
            )
        grants = [MemberGrant(**_row(c, r)) for r in c.fetchall()]
        if project:
            approvals = {g.source_request_id for g in grants if g.source_request_id}
            allowed = {
                rid
                for rid in approvals
                if can_revoke_approval(auth, self._get_capability(c, org, project, rid))
            }
            for grant in grants:
                grant.can_revoke = grant.source_request_id in allowed
        return member_detail(
            auth,
            principal,
            data,
            org_admin,
            project_admin,
            platform_admin,
            assigned,
            grants,
            project,
            manages_org(c, auth, org, lock=lock),
            org,
        )

    def managed_member_detail(self, auth, principal, org, project):
        with self._connection_factory() as db, db.cursor() as c:
            return self._member_detail(c, auth, principal, org, project)

    def set_member_assignments(self, auth, principal, command, request_id):
        from .governance import assignment_change

        org, project = command.organization_id, command.project_id
        with self._connection_factory() as db, db.cursor() as c:
            detail = self._member_detail(c, auth, principal, org, project, lock=True)
            if not detail.can_set_roles:
                raise denied()
            c.execute(
                """SELECT 1 FROM access_control.organization_memberships WHERE principal_id=%s::uuid
                      AND organization_id=%s AND active FOR SHARE""",
                (principal, org),
            )
            if not c.fetchone():
                raise missing_member()
            kind, previous, selected = assignment_change(detail, command)
            if previous == selected:
                return detail
            statement = (
                "UPDATE access_control.memberships SET assigned_permissions=%s::text[]"
                if kind == "permissions" else
                "UPDATE access_control.memberships SET assigned_roles=%s::text[]"
            )
            c.execute(statement + " WHERE principal_id=%s::uuid AND organization_id=%s AND project_id=%s AND active",
                      (sorted(selected), principal, org, project))
            self._bump_revision(c, principal)
            self._audit(
                c,
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
            return self._member_detail(c, auth, principal, org, project)

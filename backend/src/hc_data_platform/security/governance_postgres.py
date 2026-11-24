"""Scoped administration of organizations and projects."""
from .governance import GovernanceDirectory, InboxEntry, MemberEntry, denied, missing_member
from hc_data_platform.core.errors import problem


def manages_org(cursor, auth, org, *, lock=False):
    if auth.is_platform_admin:
        return True
    cursor.execute("""
        SELECT 1 FROM access_control.organization_memberships
        WHERE principal_id::text=%s AND organization_id=%s AND active AND is_administrator
    """ + (" FOR SHARE" if lock else ""), (auth.subject_id, org))
    return cursor.fetchone() is not None


def require_manager(cursor, auth, org, project=None):
    if manages_org(cursor, auth, org):
        return
    if project and auth.has_capability("access.manage", project, org):
        return
    raise denied()


class PostgresGovernance:
    def governance_directory(self, auth):
        from .access_postgres import _row
        with self._connection_factory() as db, db.cursor() as c:
            c.execute("""SELECT organization_id FROM access_control.organization_memberships
                        WHERE principal_id::text=%s AND active AND is_administrator""", (auth.subject_id,))
            managed = [_row(c,r)["organization_id"] for r in c.fetchall()]
            pairs = sorted({(o,p) for o,p,_ in auth.organization_scope_triples if auth.has_capability("access.manage",p,o)})
            # The RLS-protected directory read is limited to verified management scopes.
            c.execute("SELECT set_config('app.platform_admin','true',true)")
            c.execute("""
                SELECT d.organization_id, d.project_id, d.display_name AS project_name
                FROM registry.organization_projects d
                WHERE %s OR d.organization_id=ANY(%s::text[]) OR (d.organization_id,d.project_id)
                  IN (SELECT * FROM unnest(%s::text[],%s::text[]))
                ORDER BY 1,2
            """,(auth.is_platform_admin,managed,[p[0] for p in pairs],[p[1] for p in pairs]))
            projects = [_row(c,r) for r in c.fetchall()]
            visible = set(managed) | {p["organization_id"] for p in projects}
            c.execute("""
                SELECT organization_id, display_name AS organization_name,
                       CASE WHEN %s OR organization_id=ANY(%s::text[]) THEN join_code END AS join_code
                FROM access_control.organization_join_codes WHERE %s OR organization_id=ANY(%s::text[])
            """,(auth.is_platform_admin,managed,auth.is_platform_admin,list(visible)))
            orgs = {r["organization_id"]:r for r in (_row(c,v) for v in c.fetchall())}
            for org in visible:
                orgs.setdefault(org,dict(organization_id=org,organization_name=org,join_code=None))
            return GovernanceDirectory(organizations=[dict(**orgs[o],can_manage=auth.is_platform_admin or o in managed) for o in sorted(orgs)],projects=projects)

    def governance_members(self, auth, org, project, offset):
        from .access_postgres import _row
        with self._connection_factory() as db, db.cursor() as c:
            require_manager(c,auth,org,project)
            table = "memberships" if project else "organization_memberships"
            cond = " AND m.project_id=%s" if project else ""
            c.execute(f"""SELECT a.principal_id::text AS principal_id, a.display_username AS username,
                            m.is_administrator AS administrator
                         FROM access_control.{table} m JOIN access_control.accounts a USING(principal_id)
                         WHERE m.organization_id=%s AND m.active{cond}
                         ORDER BY a.display_username,a.principal_id LIMIT 51 OFFSET %s""",
                      (org,*([project] if project else []),offset))
            return [MemberEntry(**_row(c,r)) for r in c.fetchall()]

    def change_administrator(self, auth, principal, cmd, request_id):
        from .access_postgres import _row
        org,project=cmd.organization_id,cmd.project_id
        with self._connection_factory() as db, db.cursor() as c:
            if project:
                if not manages_org(c,auth,org,lock=True):
                    raise denied()
            elif not auth.is_platform_admin:
                raise denied()
            if principal==auth.subject_id:
                raise denied()
            c.execute("SELECT display_username AS username FROM access_control.accounts WHERE principal_id=%s::uuid AND status='ACTIVE' FOR UPDATE",(principal,))
            target=c.fetchone()
            if not target:
                raise missing_member()
            username=_row(c,target)["username"]
            if project:
                c.execute("SELECT 1 FROM access_control.organization_memberships WHERE principal_id=%s::uuid AND organization_id=%s AND active FOR SHARE",(principal,org))
                if not c.fetchone():
                    raise missing_member()
                c.execute("""UPDATE access_control.memberships SET is_administrator=%s
                             WHERE principal_id=%s::uuid AND organization_id=%s AND project_id=%s AND active RETURNING 1""",
                          (cmd.enabled,principal,org,project))
            else:
                c.execute("SELECT set_config('app.platform_admin','true',true)")
                c.execute("""SELECT 1 FROM access_control.organization_join_codes WHERE organization_id=%s
                          UNION ALL SELECT 1 FROM registry.organization_projects WHERE organization_id=%s LIMIT 1""",(org,org))
                if not c.fetchone():
                    raise problem(status=404,code="UNKNOWN_ORGANIZATION",title="Unknown organization",detail="组织不存在。")
                if cmd.enabled:
                    c.execute("""INSERT INTO access_control.organization_memberships AS m
                               (principal_id,organization_id,active,activated_by,is_administrator) VALUES (%s::uuid,%s,true,%s,true)
                               ON CONFLICT(principal_id,organization_id) DO UPDATE SET active=true,is_administrator=true,
                               activated_by=CASE WHEN m.active THEN m.activated_by ELSE EXCLUDED.activated_by END,
                               activated_at=CASE WHEN m.active THEN m.activated_at ELSE now() END,
                               source_request_id=CASE WHEN m.active THEN m.source_request_id END,revoked_by=NULL,revoked_at=NULL RETURNING 1""",
                              (principal,org,auth.subject_id))
                else:
                    c.execute("""UPDATE access_control.organization_memberships SET is_administrator=false
                              WHERE principal_id=%s::uuid AND organization_id=%s AND active RETURNING 1""",(principal,org))
            if not c.fetchone():
                raise missing_member()
            self._bump_revision(c,principal)
            self._audit(c,scope_kind="PROJECT" if project else "PLATFORM",organization_id=org,project_id=project,actor_id=auth.subject_id,
                        action="administrator.appoint" if cmd.enabled else "administrator.remove",resource_type="account",resource_id=principal,
                        request_id=request_id,outcome="SUCCEEDED",safe_details={"reason":cmd.reason,"organization_id":org,"project_id":project})
            return MemberEntry(principal_id=principal,username=username,administrator=cmd.enabled)

    def governance_inbox(self,auth,org,project,offset):
        from .access_postgres import _row
        with self._connection_factory() as db, db.cursor() as c:
            require_manager(c,auth,org,project)
            if not project:
                query="""SELECT request_id,requester_id,organization_id,NULL::text AS project_id,'ORGANIZATION' AS kind,
                    reason,ARRAY[]::text[] AS capability_keys,NULL::text AS resource_id,NULL::text AS expires_at,NULL::text AS replaces_request_id,created_at
                    FROM access_control.organization_membership_requests WHERE organization_id=%s AND status='PENDING'"""
                args=(org,)
            else:
                query="""SELECT request_id,requester_id,organization_id,project_id,'PROJECT' AS kind,reason,
                    ARRAY[]::text[] AS capability_keys,NULL::text AS resource_id,NULL::text AS expires_at,NULL::text AS replaces_request_id,created_at FROM access_control.membership_requests
                    WHERE organization_id=%s AND project_id=%s AND status='PENDING'
                    UNION ALL SELECT request_id,requester_id,organization_id,project_id,'CAPABILITY',reason,capability_keys,resource_id,expires_at::text,replaces_request_id::text,created_at
                    FROM access_control.capability_requests WHERE organization_id=%s AND project_id=%s AND status='PENDING'"""
                args=(org,project,org,project)
            c.execute(f"""SELECT r.request_id::text, r.requester_id::text, a.display_username AS username,r.organization_id,r.project_id,r.kind,
                         r.reason,r.capability_keys,r.resource_id,r.expires_at,r.replaces_request_id FROM ({query}) r JOIN access_control.accounts a ON a.principal_id=r.requester_id
                         ORDER BY r.created_at,r.request_id LIMIT 51 OFFSET %s""",(*args,offset))
            return [InboxEntry(**_row(c,r)) for r in c.fetchall()]

    def create_managed_project(self,auth,cmd,request_id):
        from .governance import ProjectEntry
        with self._connection_factory() as db, db.cursor() as c:
            if not manages_org(c,auth,cmd.organization_id,lock=True):
                raise denied()
            c.execute("SELECT 1 FROM access_control.organization_join_codes WHERE organization_id=%s",(cmd.organization_id,))
            if not c.fetchone():
                raise problem(status=409,code="ORGANIZATION_DIRECTORY_REQUIRED",title="Organization directory required",detail="请先登记组织。")
            c.execute("SELECT set_config('app.organization_id',%s,true),set_config('app.project_id',%s,true)",(cmd.organization_id,cmd.project_id))
            c.execute("""INSERT INTO registry.organization_projects(organization_id,project_id,display_name)
                         VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING 1""",(cmd.organization_id,cmd.project_id,cmd.project_name))
            if not c.fetchone():
                raise problem(status=409,code="PROJECT_ALREADY_EXISTS",title="Project already exists",detail="项目编号已存在。")
            self._audit(c,scope_kind="PROJECT",organization_id=cmd.organization_id,project_id=cmd.project_id,actor_id=auth.subject_id,
                        action="project.create",resource_type="project",resource_id=cmd.project_id,request_id=request_id,outcome="SUCCEEDED")
            return ProjectEntry(**cmd.model_dump())

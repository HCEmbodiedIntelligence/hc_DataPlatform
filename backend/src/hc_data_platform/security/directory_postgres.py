"""Transactional directory mutations. Identifiers never change when a scope is renamed."""

from secrets import token_hex
from uuid import uuid4

from .directory_changes import ArchivedScope, unknown_scope
from .governance import OrganizationEntry, denied


def lock_directory(cursor, org):
    cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("directory/" + org,))


class PostgresDirectory:
    def assert_directory_active(self, org, project):
        from .governance_postgres import require_active_directory

        with self._connection_factory() as db, db.cursor() as c:
            require_active_directory(c, org, project)

    def create_personal_organization(self, auth, command, key, request_id):
        from .access_postgres import _row

        with self._connection_factory() as db, db.cursor() as c:
            c.execute(
                "SELECT 1 FROM access_control.accounts WHERE principal_id::text=%s AND status='ACTIVE' FOR SHARE",
                (auth.subject_id,),
            )
            if auth.service_identity or not c.fetchone():
                raise denied()
            operation = "organization.create"
            org = self._begin_idempotency(c, auth.subject_id, operation, key, command.model_dump())
            if org is None:
                org = str(uuid4())
                c.execute(
                    "INSERT INTO access_control.organization_join_codes (organization_id, display_name) VALUES (%s,%s)",
                    (org, command.name),
                )
                c.execute(
                    """INSERT INTO access_control.organization_memberships
                    (principal_id,organization_id,activated_by,is_administrator) VALUES (%s,%s,%s,true)""",
                    (auth.subject_id, org, auth.subject_id),
                )
                self._bump_revision(c, auth.subject_id)
                self._finish_idempotency(c, auth.subject_id, operation, key, org)
                self._audit(
                    c,
                    scope_kind="PLATFORM",
                    organization_id=org,
                    project_id=None,
                    actor_id=auth.subject_id,
                    action=operation,
                    resource_type="organization",
                    resource_id=org,
                    request_id=request_id,
                    outcome="SUCCEEDED",
                )
            c.execute(
                """SELECT display_name,join_code FROM access_control.organization_join_codes o WHERE organization_id=%s
                AND NOT EXISTS (SELECT 1 FROM access_control.retired_scopes r WHERE r.organization_id=o.organization_id AND r.project_id='')
                AND EXISTS (SELECT 1 FROM access_control.organization_memberships m WHERE m.organization_id=o.organization_id
                   AND m.principal_id::text=%s AND m.is_administrator AND m.active)""",
                (org, auth.subject_id),
            )
            row = c.fetchone()
            if not row:
                raise unknown_scope()
            result = _row(c, row)
            return OrganizationEntry(
                organization_id=org,
                organization_name=result["display_name"],
                join_code=result["join_code"],
                can_manage=True,
            )

    def archived_directory(self, auth):
        from .access_postgres import _row

        with self._connection_factory() as db, db.cursor() as c:
            c.execute(
                "SELECT * FROM access_control.managed_directory_history(%s)", (auth.subject_id,)
            )
            return [ArchivedScope(**_row(c, row)) for row in c.fetchall()]

    def change_directory(self, auth, cmd, key, request_id):
        from .access_postgres import _row
        from .governance_postgres import manages_org

        org, project, op = cmd.organization_id, cmd.project_id, cmd.operation
        with self._connection_factory() as db, db.cursor() as c:
            lock_directory(c, org)
            if auth.service_identity or not manages_org(c, auth, org, lock=True):
                raise denied()
            if not project and op in ("archive", "delete") and not auth.is_platform_admin:
                raise denied()
            operation = "directory." + op
            replay = self._begin_idempotency(c, auth.subject_id, operation, key, cmd.model_dump())
            if replay:
                return
            c.execute("SELECT set_config('app.platform_admin','true',true)")
            c.execute(
                """SELECT project_id,status FROM access_control.retired_scopes WHERE organization_id=%s
                AND project_id IN ('',%s) FOR UPDATE""",
                (org, project or ""),
            )
            for prior in [_row(c, row) for row in c.fetchall()]:
                if not (
                    prior["project_id"] == (project or "")
                    and prior["status"] == "ARCHIVED"
                    and op == "delete"
                ):
                    raise unknown_scope()
            if project:
                c.execute(
                    "SELECT display_name FROM registry.organization_projects WHERE organization_id=%s AND project_id=%s FOR UPDATE",
                    (org, project),
                )
            else:
                c.execute(
                    """INSERT INTO access_control.organization_join_codes(organization_id,display_name)
                    SELECT %s,%s WHERE EXISTS(SELECT 1 FROM registry.organization_projects WHERE organization_id=%s)
                    ON CONFLICT DO NOTHING""",
                    (org, org, org),
                )
                c.execute(
                    "SELECT display_name FROM access_control.organization_join_codes WHERE organization_id=%s FOR UPDATE",
                    (org,),
                )
            raw = c.fetchone()
            if not raw:
                raise unknown_scope()
            name = _row(c, raw)["display_name"]
            if op == "rename":
                if project:
                    c.execute(
                        "UPDATE registry.organization_projects SET display_name=%s WHERE organization_id=%s AND project_id=%s",
                        (cmd.name, org, project),
                    )
                else:
                    c.execute(
                        "UPDATE access_control.organization_join_codes SET display_name=%s WHERE organization_id=%s",
                        (cmd.name, org),
                    )
            elif op in ("generate_code", "revoke_code"):
                code = "HC:" + token_hex(12).upper() if op == "generate_code" else None
                c.execute(
                    """UPDATE access_control.organization_join_codes SET join_code=%s,
                    allow_id_application=allow_id_application AND join_code IS DISTINCT FROM organization_id WHERE organization_id=%s""",
                    (code, org),
                )
            else:
                self._retire_directory(c, auth, cmd, name)
            self._finish_idempotency(c, auth.subject_id, operation, key, str(uuid4()))
            self._audit(
                c,
                scope_kind="PLATFORM",
                organization_id=org,
                project_id=None,
                actor_id=auth.subject_id,
                action=operation,
                resource_type="project" if project else "organization",
                resource_id=project or org,
                request_id=request_id,
                outcome="SUCCEEDED",
                safe_details={"project_id": project, "retention_days": cmd.retention_days},
            )

    def _retire_directory(self, c, auth, cmd, name):
        from .access_postgres import _row

        org, project = cmd.organization_id, cmd.project_id
        deleting = cmd.operation == "delete"
        scope = (org, project) if project else (org,)
        where = "organization_id=%s" + (" AND project_id=%s" if project else "")
        people = set()
        membership_tables = (
            ["memberships"] if project else ["memberships", "organization_memberships"]
        )
        for table in membership_tables:
            c.execute(
                f"SELECT principal_id::text FROM access_control.{table} WHERE {where} AND active FOR UPDATE",
                scope,
            )
            people.update(_row(c, row)["principal_id"] for row in c.fetchall())
        request_tables = ["capability_requests", "membership_requests"]
        if not project:
            request_tables.append("organization_membership_requests")
            c.execute(
                "UPDATE access_control.organization_join_codes SET join_code=NULL,allow_id_application=false WHERE organization_id=%s",
                (org,),
            )
        for table in ["capability_grants", *membership_tables]:
            if deleting:
                c.execute(f"DELETE FROM access_control.{table} WHERE {where}", scope)
            else:
                c.execute(
                    f"UPDATE access_control.{table} SET active=false,revoked_by=%s,revoked_at=now() WHERE {where} AND active",
                    (auth.subject_id, *scope),
                )
        for table in request_tables:
            if deleting:
                c.execute(f"DELETE FROM access_control.{table} WHERE {where}", scope)
            else:
                c.execute(
                    f"""UPDATE access_control.{table} SET status=CASE WHEN status='PENDING' THEN 'REJECTED' ELSE 'REVOKED' END,
                    decided_by=%s,decision_reason='范围已归档',revision=revision+1,updated_at=now()
                    WHERE {where} AND status IN ('PENDING','APPROVED')""",
                    (auth.subject_id, *scope),
                )
        for principal in sorted(people):
            self._bump_revision(c, principal)
        c.execute(
            """INSERT INTO access_control.retired_scopes(organization_id,project_id,display_name,status,purge_after)
            VALUES (%s,%s,%s,%s,now()+%s::integer * interval '1 day') ON CONFLICT(organization_id,project_id)
            DO UPDATE SET status=EXCLUDED.status,purge_after=EXCLUDED.purge_after,retired_at=now()""",
            (org, project or "", name, "DELETED" if deleting else "ARCHIVED", cmd.retention_days),
        )

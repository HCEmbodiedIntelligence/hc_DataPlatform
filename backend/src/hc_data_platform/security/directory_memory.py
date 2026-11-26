from datetime import datetime, timedelta, timezone
from uuid import uuid4
from secrets import token_hex
from .directory_changes import ArchivedScope, unknown_scope
from .governance import OrganizationEntry, denied
from .access_models import AccessRequestStatus


class MemoryDirectory:
    def assert_directory_active(self, org, project):
        with self._lock:
            if (org, "") in self._retired or (org, project) in self._retired:
                raise unknown_scope()

    def create_personal_organization(self, auth, cmd, key, request_id):
        with self._lock:
            if auth.service_identity or auth.subject_id not in self._accounts:
                raise denied()
            op = "organization.create"
            org = self._repeat(auth.subject_id, op, key, cmd.model_dump())
            if org is None:
                org = str(uuid4())
                self._organization_names[org] = cmd.name
                self._organization_memberships.add((auth.subject_id, org))
                self._organization_admins.add((auth.subject_id, org))
                self._bump_revision(auth.subject_id)
                self._remember(auth.subject_id, op, key, cmd.model_dump(), org)
            if (auth.subject_id, org) not in self._organization_admins:
                raise unknown_scope()
            return OrganizationEntry(
                organization_id=org,
                organization_name=self._organization_names[org],
                join_code=self._join_codes.get(org),
                can_manage=True,
            )

    def archived_directory(self, auth):
        with self._lock:
            return [
                ArchivedScope(
                    organization_id=o,
                    project_id=p,
                    display_name=name,
                    status="ARCHIVED" if status == "archive" else "DELETED",
                    purge_after=self._retention_deadlines.get((o, p)),
                )
                for (o, p), (name, status) in sorted(self._retired.items())
                if (not p or (o, "") not in self._retired)
                and (
                    auth.is_platform_admin
                    or (p and (auth.subject_id, o) in self._organization_admins)
                )
            ]

    def change_directory(self, auth, cmd, key, request_id):
        with self._lock:
            org, project, op = cmd.organization_id, cmd.project_id, cmd.operation
            self._check_management(auth, org, None)
            if auth.service_identity or (
                not project and op in ("archive", "delete") and not auth.is_platform_admin
            ):
                raise denied()
            operation = "directory." + op
            if self._repeat(auth.subject_id, operation, key, cmd.model_dump()):
                return
            scope = (org, project or "")
            prior = self._retired.get(scope)
            if (org, "") in self._retired and project:
                raise unknown_scope()
            if prior and not (prior[1] == "archive" and op == "delete"):
                raise unknown_scope()
            if not prior and (
                org not in self._organization_names
                or (project and scope not in self._organization_projects)
            ):
                raise unknown_scope()
            if op == "rename":
                if project:
                    self._project_names[scope] = cmd.name
                else:
                    self._organization_names[org] = cmd.name
            elif op in ("generate_code", "revoke_code"):
                if self._join_codes.get(org) == org:
                    self._disabled_id_applications.add(org)
                self._join_codes[org] = (
                    "HC:" + token_hex(12).upper() if op == "generate_code" else None
                )
            else:
                name = (
                    prior[0]
                    if prior
                    else self._project_names.get(scope, project)
                    if project
                    else self._organization_names[org]
                )
                self._retired[scope] = (name, op)
                if op == "delete":
                    self._retention_deadlines[scope] = datetime.now(timezone.utc) + timedelta(
                        days=cmd.retention_days
                    )

                def matches(item):
                    return item[1] == org and (
                        not project or (len(item) > 2 and item[2] == project)
                    )

                affected = {
                    k[0] for k in self._memberships | self._organization_memberships if matches(k)
                }
                self._organization_projects = {
                    p
                    for p in self._organization_projects
                    if p[0] != org or (project and p[1] != project)
                }
                self._memberships = {k for k in self._memberships if not matches(k)}
                self._project_admins = {k for k in self._project_admins if not matches(k)}
                self._direct_project_grants = {
                    k: v for k, v in self._direct_project_grants.items() if not matches(k)
                }
                if not project:
                    self._organization_admins = {
                        k for k in self._organization_admins if not matches(k)
                    }
                    self._organization_memberships = {
                        k for k in self._organization_memberships if not matches(k)
                    }
                    self._join_codes.pop(org, None)
                for requests in (
                    self._organization_membership_requests,
                    self._membership_requests,
                    self._capability_requests,
                ):
                    for rid, item in list(requests.items()):
                        if item.organization_id != org or (project and item.project_id != project):
                            continue
                        if op == "delete":
                            del requests[rid]
                        elif item.status in (
                            AccessRequestStatus.PENDING,
                            AccessRequestStatus.APPROVED,
                        ):
                            status = (
                                AccessRequestStatus.REJECTED
                                if item.status == AccessRequestStatus.PENDING
                                else AccessRequestStatus.REVOKED
                            )
                            requests[rid] = item.model_copy(
                                update={"status": status, "revision": item.revision + 1}
                            )
                for principal in affected:
                    self._bump_revision(principal)
            self._remember(auth.subject_id, operation, key, cmd.model_dump(), str(uuid4()))

"""In-memory implementation of administrative scopes used by the access API."""
from .governance import GovernanceDirectory, InboxEntry, MemberEntry, denied, missing_member


class MemoryGovernance:
    def governance_directory(self,auth):
        with self._lock:
            managed={o for a,o in self._organization_admins if a==auth.subject_id}
            projects=[(o,p) for o,p in sorted(self._organization_projects) if auth.is_platform_admin or o in managed or auth.has_capability("access.manage",p,o)]
            return GovernanceDirectory(organizations=[dict(organization_id=o,organization_name=o,can_manage=auth.is_platform_admin or o in managed)
                for o in sorted(managed|{o for o,_ in projects})],projects=[dict(organization_id=o,project_id=p,project_name=p) for o,p in projects])

    def _check_management(self,auth,org,project):
        if auth.is_platform_admin or (auth.subject_id,org) in self._organization_admins:
            return
        if not project or not auth.has_capability("access.manage",project,org):
            raise denied()

    def governance_members(self,auth,org,project,offset):
        with self._lock:
            self._check_management(auth,org,project)
            members=self._memberships if project else self._organization_memberships
            admins=self._project_admins if project else self._organization_admins
            scope=(org,project) if project else (org,)
            rows=[MemberEntry(principal_id=k[0],username=self._accounts[k[0]].principal.username,administrator=k in admins)
                  for k in members if k[1:]==scope]
            return sorted(rows,key=lambda r:(r.username,r.principal_id))[offset:offset+51]

    def change_administrator(self,auth,principal,cmd,request_id):
        with self._lock:
            org,project=cmd.organization_id,cmd.project_id
            if auth.subject_id==principal or not (auth.is_platform_admin or (project and (auth.subject_id,org) in self._organization_admins)):
                raise denied()
            account=self._accounts.get(principal)
            if not account or account.principal.status.value!="ACTIVE" or not any(o==org for o,_ in self._organization_projects):
                raise missing_member()
            if project:
                key=(principal,org,project)
                if (principal,org) not in self._organization_memberships or key not in self._memberships:
                    raise missing_member()
                admins=self._project_admins
            else:
                key=(principal,org)
                if cmd.enabled: self._organization_memberships.add(key)
                if key not in self._organization_memberships: raise missing_member()
                admins=self._organization_admins
            if cmd.enabled: admins.add(key)
            else: admins.discard(key)
            self._bump_revision(principal)
            self._append_audit(scope_kind="PROJECT" if project else "PLATFORM",organization_id=org,project_id=project,
                actor_id=auth.subject_id,action="administrator.appoint" if cmd.enabled else "administrator.remove",
                resource_type="account",resource_id=principal,request_id=request_id,outcome="SUCCEEDED",safe_details={"reason":cmd.reason})
            return MemberEntry(principal_id=principal,username=account.principal.username,administrator=cmd.enabled)

    def governance_inbox(self,auth,org,project,offset):
        with self._lock:
            self._check_management(auth,org,project)
            rows=[]
            for principal,account in self._accounts.items():
                for r in self.account_access_overview(principal_id=principal).requests:
                    if r.organization_id!=org or r.project_id!=project or r.status.value!="PENDING": continue
                    rows.append((r.created_at,InboxEntry(request_id=r.request_id,requester_id=principal,username=account.principal.username,
                        organization_id=org,project_id=project,kind=r.kind,reason=r.reason,capability_keys=list(r.capability_keys),
                        resource_id=r.resource_id,expires_at=r.expires_at.isoformat() if r.expires_at else None,replaces_request_id=r.replaces_request_id)))
            return [item for _,item in sorted(rows,key=lambda v:(v[0],v[1].request_id))[offset:offset+51]]

    def create_managed_project(self,auth,cmd,request_id):
        from .governance import ProjectEntry
        from hc_data_platform.core.errors import problem
        with self._lock:
            self._check_management(auth,cmd.organization_id,None)
            key=cmd.organization_id,cmd.project_id
            if key in self._organization_projects:
                raise problem(status=409,code="PROJECT_ALREADY_EXISTS",title="Project already exists",detail="项目编号已存在。")
            if not any(o==cmd.organization_id for o,_ in self._organization_projects):
                raise missing_member()
            self._organization_projects.add(key)
            return ProjectEntry(**cmd.model_dump())

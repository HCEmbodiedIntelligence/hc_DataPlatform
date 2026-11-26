import { useState } from "react";
import { Alert, Button, Descriptions, Divider, Drawer, Flex, Select, Space, Spin, Tag, Typography } from "antd";
import { Link } from "react-router-dom";
import { useJoinableProjects } from "../../features/account/access-api";
import { useQuery } from "@tanstack/react-query";
import { request } from "../../shared/api/http-client";
import { grantLabel, grantOptions } from "../../shared/auth/project-grants";
import { permissionLabel, formatDateTime } from "./presentation";
import { MemberPermissionEditor } from "./MemberPermissionEditor";
import type { ScopeNode, TreeMember } from "./ManagementTree";

type Detail = TreeMember & {
  display_name: string; status: string; created_at: string;
  platform_administrator: boolean; organization_administrator: boolean;
  assigned_permissions?: string[]; assignable_permissions?: string[];
  assigned_roles: string[]; approved_grants: { capability: string; expires_at: string | null; source_request_id?: string | null; can_revoke?: boolean }[];
  effective_permissions: string[]; can_set_roles: boolean; can_change_administrator: boolean; can_remove: boolean;
};
const ordinaryRoles = grantOptions.filter(o => o.value.startsWith("role.") && o.value !== "role.project_admin");
type MemberProject = { project_id: string; project_name: string };

function MemberProjects({ organization, memberId, projects, canRemove, add, openProject, remove, self }: {
  organization: string; memberId: string; projects: MemberProject[];
  canRemove: boolean; add?: () => void; openProject: (id: string) => void;
  remove: (id: string) => void; self: boolean;
}) {
  const available = useJoinableProjects(organization, memberId);
  const availableIds = new Set(available.data?.map(p => p.project_id));
  const joined = projects.filter(p => !availableIds.has(p.project_id));
  return <section aria-label="所属项目">
    <Flex align="center" justify="space-between" wrap gap="small">
      <Typography.Title level={5} style={{ margin: 0 }}>所属项目</Typography.Title>
      {add && <Button type="primary" onClick={add} disabled={!available.isSuccess || !availableIds.size}>加入项目</Button>}
      {self && !add && <Link to="/account/settings?tab=memberships">申请加入项目</Link>}
    </Flex>
    {available.isPending && <Spin size="small" aria-label="正在读取所属项目" />}
    {available.isError && <Alert type="error" showIcon message={available.error.message}
      action={<Button onClick={() => void available.refetch()}>重试</Button>} />}
    {available.isSuccess && (joined.length ? joined.map(p =>
      <Flex key={p.project_id} align="center" justify="space-between" wrap gap="small" style={{ paddingBlock: 6 }}>
        <Typography.Text style={{ overflowWrap: "anywhere" }}>{p.project_name}{p.project_name !== p.project_id && "（" + p.project_id + "）"}</Typography.Text>
        <Space><Button type="link" size="small" onClick={() => openProject(p.project_id)}>项目权限</Button>
          {canRemove && <Button type="link" danger size="small" onClick={() => remove(p.project_id)}>移出项目</Button>}
        </Space>
      </Flex>) : <Typography.Text type="secondary">尚未加入本组织的项目</Typography.Text>)}
  </section>;
}

export function MemberDetailDrawer({ scope, member, actor, revision, organizationProjects, onAssign, openProject, close, confirm, appoint, remove }: {
  scope: ScopeNode; member: TreeMember; actor?: string; revision?: string; close: () => void;
  confirm: (label: string, run: (reason: string) => Promise<unknown>) => void;
  organizationProjects?: MemberProject[]; onAssign: () => void; openProject: (id: string) => void;
  appoint: (enabled: boolean) => void; remove: (project?: string) => void;
}) {
  const [roles, setRoles] = useState<string[]>();
  const [editingVersion, setEditingVersion] = useState<string[]>();
  const query = new URLSearchParams({ organization_id: scope.org });
  if (scope.project) query.set("project_id", scope.project);
  const path = "/account/managed-members/" + encodeURIComponent(member.principal_id);
  const detail = useQuery({ queryKey: ["managed-member-detail", actor, revision, scope.org, scope.project, member.principal_id],
    queryFn: ({ signal }) => request<Detail>({ method: "GET", path: path + "?" + query, scopeMode: "session", signal, cache: "no-store" }) });
  const d = detail.data;
  function save() {
    if (!d || !roles || !scope.project) return;
    confirm("调整 " + member.username + " 在项目 " + scope.project + " 的岗位", async reason => {
      await request({ method: "PUT", path: path + "/roles", scopeMode: "session", idempotencyKey: crypto.randomUUID(),
        body: { organization_id: scope.org, project_id: scope.project, roles, expected_roles: editingVersion ?? d.assigned_roles, reason } });
      setRoles(undefined); setEditingVersion(undefined);
    });
  }
  return <Drawer open onClose={close} title={member.username + " · 成员详情"} width="min(560px, 100vw)">
    {detail.isLoading && <Spin aria-label="正在读取成员详情" />}
    {detail.error && <Alert type="error" message={detail.error.message} action={<Button onClick={() => void detail.refetch()}>重试</Button>} />}
    {d && <Space direction="vertical" style={{ display: "flex" }} size="middle">
      <Descriptions title="基本信息" column={1} size="small" items={[
        { key:"username",label:"用户名",children:d.username },{ key:"name",label:"显示名称",children:d.display_name },
        { key:"id",label:"账号 ID",children:<Typography.Text copyable>{d.principal_id}</Typography.Text> },
        { key:"status",label:"账号状态",children:({ACTIVE:"正常",DISABLED:"已禁用",DELETED:"已删除"} as Record<string,string>)[d.status] ?? d.status },
        { key:"created",label:"注册时间",children:formatDateTime(d.created_at) },
        { key:"scope",label:"当前范围",children:scope.project ? scope.org + " / " + scope.project : scope.org },
        { key:"roles",label:"管理身份",children:<Space wrap>
          {d.platform_administrator && <Tag>平台管理员</Tag>}{d.organization_administrator && <Tag>组织管理员</Tag>}
          {scope.project && (d.administrator || d.approved_grants.some(g=>g.capability==="role.project_admin")) && <Tag>项目管理员</Tag>}
          <Tag>{scope.project ? "项目成员" : "组织成员"}</Tag></Space> },
      ]}/>
      {d.organization_administrator && <Typography.Text type="secondary">组织管理员可管理本组织的全部项目和项目成员。</Typography.Text>}
      {organizationProjects && <MemberProjects organization={scope.org} memberId={member.principal_id} projects={organizationProjects}
        canRemove={actor !== member.principal_id || d.platform_administrator || d.organization_administrator} self={actor === member.principal_id} openProject={openProject} remove={remove}
        add={(actor !== member.principal_id || d.platform_administrator || d.organization_administrator) && d.status === "ACTIVE" ? onAssign : undefined} />}
      {scope.project && <div><Typography.Title level={5}>直接分配的项目岗位</Typography.Title>
        {d.can_set_roles ? <Space direction="vertical" style={{display:"flex"}}>
          <Select mode="multiple" aria-label="项目岗位" placeholder="选择项目岗位" style={{width:"100%"}} options={ordinaryRoles}
            value={roles ?? d.assigned_roles} onChange={next=>{if (!roles) setEditingVersion(d.assigned_roles);setRoles(next);}} />
          <Button disabled={!roles || [...roles].sort().join() === [...d.assigned_roles].sort().join()} onClick={save}>保存岗位</Button>
        </Space> : d.assigned_roles.map(grantLabel).join("、") || "未直接分配岗位"}</div>}
      {scope.project && d.assigned_permissions && <MemberPermissionEditor
        assigned={d.assigned_permissions} assignable={d.assignable_permissions ?? []}
        save={(permissions, expected_permissions, done) => {
          confirm("调整 " + member.username + " 在项目 " + scope.project + " 的专项权限", async reason => {
            try {
              await request({ method: "PUT", path: path + "/permissions", scopeMode: "session",
                idempotencyKey: crypto.randomUUID(), body: {
                  organization_id: scope.org, project_id: scope.project, permissions, expected_permissions, reason,
                } });
            } catch (error) {
              await detail.refetch();
              throw error;
            }
            done();
          });
        }} />}
      {!!d.approved_grants.length && <div><Typography.Title level={5}>审批授权</Typography.Title>
        {Array.from(new Set(d.approved_grants.map(g=>g.source_request_id ?? g.capability))).map(key=>{
          const grants=d.approved_grants.filter(g=>(g.source_request_id ?? g.capability)===key);
          const first=grants[0];
          if (!first) return null;
          const label=grants.map(g=>permissionLabel(g.capability)).join("、");
          return <div key={key}>{label}{first.expires_at && " · 到期 " + formatDateTime(first.expires_at)}
            {scope.project && first.can_revoke && first.source_request_id && <Button size="small" type="link" danger
              onClick={()=>confirm("撤销 "+d.username+" 的授权："+label,reason=>request({
                method:"POST",path:`/organizations/${encodeURIComponent(scope.org)}/projects/${encodeURIComponent(scope.project!)}/capability-requests/${encodeURIComponent(key)}:revoke`,
                scope:{organizationId:scope.org,projectId:scope.project},idempotencyKey:crypto.randomUUID(),body:{reason},
              }))}>撤销授权</Button>}</div>;
        })}</div>}
      <div><Typography.Title level={5}>范围内的权限</Typography.Title><Space wrap size={[4,6]}>
        {d.effective_permissions.length ? d.effective_permissions.map(key=><Tag key={key}>{permissionLabel(key)}</Tag>) : "尚无业务权限"}
      </Space></div>
      {(d.can_change_administrator || d.can_remove) && <><Divider style={{margin:0}}/><Space wrap>
        {d.can_change_administrator && <Button onClick={()=>appoint(!d.administrator)}>{d.administrator?"撤销":"任命"}{scope.project?"项目":"组织"}管理员</Button>}
        {d.can_remove && <Button danger onClick={()=>remove(scope.project)}>移出{scope.project?"项目":"组织"}</Button>}
      </Space></>}
    </Space>}
  </Drawer>;
}

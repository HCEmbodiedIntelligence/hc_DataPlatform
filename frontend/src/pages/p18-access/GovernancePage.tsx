import { AssignProjectDialog } from "./AssignProjectDialog";
import { MemberDetailDrawer } from "./MemberDetailDrawer";
import { ManagementTree, type ScopeNode } from "./ManagementTree";
import { useManagementAccess } from "../../shared/auth/use-management-access";
import ui from "./OrganizationManagement.module.css";
import { ArrowUpRight, Building2, Folder, Inbox, Network, RefreshCw, Search, Settings2, Users } from "lucide-react";
import { ObjectStoreConfigurationPanel } from "../p22-platform-operations/page";
import { useState } from "react";
import { Alert, Button, Card, Input, Modal, Space, Table, Typography } from "antd";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "../../shared/api/http-client";
import { useShellStore } from "../../shared/scope/shell-store";
import { CreateOrganizationButton, DirectoryActions } from "./DirectoryActions";

type Org = { organization_id: string; organization_name: string; join_code: string | null; can_manage: boolean };
type Project = { organization_id: string; project_id: string; project_name: string };
type Member = { principal_id: string; username: string; administrator: boolean };
type Entry = { request_id: string; requester_id: string; username: string; kind: "PROJECT" | "ORGANIZATION" | "CAPABILITY";
  organization_id: string; project_id: string | null; capability_keys: string[]; reason: string | null;
  expires_at: string | null; resource_id: string | null; replaces_request_id: string | null };
type Action = { label: string; run: (reason: string) => Promise<unknown> };
export function useManagementDirectory() {
  const identity = useShellStore(s => [s.principal?.actorId, s.authorization?.roleVersion].join("/"));
  return useQuery({ queryKey: ["management-directory", identity], queryFn: ({ signal }) =>
    request<{ organizations: Org[]; projects: Project[] }>({ method: "GET", path: "/account/management-directory", scopeMode: "session", cache: "no-store", signal }) });
}
export function ManagementLink() {
  const access = useManagementAccess();
  return access.projects ? <Link to={access.organizations ? "/settings/access?tab=organizations" : "/settings/access?tab=projects"}>组织与项目管理</Link> : null;
}
type ManagementView = "organization" | "project" | "combined";
export function OrganizationManagementPage() { return <ManagementPage view="organization" />; }
export function ProjectManagementPage() { return <ManagementPage view="project" />; }
export function Component() { return <ManagementPage view="combined" />; }

function ManagementPage({ view }: { view: ManagementView }) {
  const [params] = useSearchParams();
  const client = useQueryClient();
  const [person, setPerson] = useState<Member>();
  const [assignment, setAssignment] = useState<{ member: Member; organizationId: string }>();
  const [assignedNotice, setAssignedNotice] = useState("");
  const directory = useManagementDirectory();
  const actor = useShellStore(s => s.principal?.actorId);
  const platform = useShellStore(s => s.platformCapabilities.includes("platform.admin"));
  const activeScope = useShellStore(s => s.scope);
  const [directorySearch, setDirectorySearch] = useState("");
  const [showArchived,setShowArchived] = useState(false);
  const archived = useQuery({queryKey:["archived-directory",actor],enabled:showArchived,refetchInterval:showArchived?15000:false,
    queryFn:({signal})=>request<{organization_id:string;project_id:string;display_name:string;status:"DELETED"|"ARCHIVED";purge_after?:string;retrying:boolean}[]>({method:"GET",path:"/account/archived-directory",scopeMode:"session",cache:"no-store",signal})});
  const [orgId, setOrg] = useState<string>();
  const [projectId, setProject] = useState<string>();
  const [memberOffset, setMemberOffset] = useState(0);
  const [inboxOffset, setInboxOffset] = useState(0);
  const [action,setAction] = useState<Action>();
  const [reason,setReason] = useState("");
  const [code,setCode] = useState("");
  const [name,setName] = useState("");
  const orgs = (directory.data?.organizations ?? []).filter(o => view !== "organization" || o.can_manage);
  const org = orgs.find(o => o.organization_id === (orgId ?? params.get("organization") ?? activeScope?.organizationId)) ?? orgs[0];
  const id = org?.organization_id ?? "";
  const managesSelectedOrganization = useShellStore(s => s.sessionOrganizations.some(
    o => o.organizationId === id && o.capabilities?.includes("organization.admin")
  ));
  const projects = (directory.data?.projects ?? []).filter(p => p.organization_id === id);
  const defaultProject = projects.find(p => p.project_id === activeScope?.projectId)?.project_id ?? projects[0]?.project_id;
  const project = projects.find(p => p.project_id === projectId)?.project_id
    ?? (view === "project" || !org?.can_manage ? defaultProject : undefined);
  const hasScope = !!org && (!!project || (view !== "project" && org.can_manage));
  const scopeQuery = new URLSearchParams({ organization_id: id, ...(project ? {project_id: project} : {}) });
  const revision = useShellStore(s => s.authorization?.roleVersion);
  const key = [actor,revision,id,project] as const;
  const members = useQuery({ queryKey:["management-members",...key,memberOffset], enabled:hasScope,
    queryFn:({signal})=>request<Member[]>({ method:"GET",path:`/account/managed-members?${scopeQuery}&offset=${memberOffset}`,scopeMode:"session",signal,cache:"no-store"}) });
  const inbox = useQuery({ queryKey:["management-inbox",...key,inboxOffset],enabled:hasScope,
    queryFn:({signal})=>request<Entry[]>({ method:"GET",path:`/account/management-inbox?${scopeQuery}&offset=${inboxOffset}`,scopeMode:"session",signal,cache:"no-store"}) });
  const mutate = useMutation({mutationFn:async()=>{ if(action) await action.run(reason.trim()); },
    onSuccess:async()=>{setAction(undefined);setReason("");await Promise.all([directory.refetch(),client.invalidateQueries({queryKey:["management-members",actor]}),client.invalidateQueries({queryKey:["managed-member-detail",actor]}),client.invalidateQueries({queryKey:["management-inbox",actor]}),client.invalidateQueries({queryKey:["joinable-projects",actor]})]);} });
  function confirm(label:string,run:Action["run"]) {mutate.reset();setReason("");setAction({label,run});}
  function appoint(user:string,label:string,enabled:boolean) {
    confirm(`${enabled?"任命":"撤销"} ${label} 的${project?"项目":"组织"}管理员权限（${id}${project?"/"+project:""}）`,reason=>
      request({method:"PUT",path:`/account/managed-members/${encodeURIComponent(user)}/administrator`,scopeMode:"session",
        idempotencyKey:crypto.randomUUID(),body:{organization_id:id,project_id:project??null,enabled,reason}}));
  }
  function removeMember(member: Member, targetProject?: string) {
    const target = projects.find(p => p.project_id === targetProject);
    const scope = targetProject ? "项目 " + (target?.project_name ?? targetProject) + "（" + targetProject + "）" : "组织 " + id + " 及其下属项目";
    confirm("将 "+member.username+" 移出"+scope,async reason=>{ await request({
      method:"POST",path:`/account/managed-members/${encodeURIComponent(member.principal_id)}:remove`,
      scopeMode:"session",idempotencyKey:crypto.randomUUID(),body:{organization_id:id,project_id:targetProject??null,reason}});
      if (!targetProject || project) setPerson(undefined);
      setAssignedNotice(member.username+" 已移出"+scope);
    });
  }
  function selectNode(scope: ScopeNode, member?: Member) {
    setAssignedNotice(""); setAssignment(undefined);
    setOrg(scope.org); setProject(scope.project); setPerson(member); resetPages();
  }
  function decide(row:Entry,approve:boolean) {
    const orgRequest=row.kind==="ORGANIZATION";
    const path=orgRequest?"/organization-membership-requests":`/organizations/${encodeURIComponent(id)}/projects/${encodeURIComponent(project!)}/${row.kind==="PROJECT"?"membership":"capability"}-requests`;
    confirm(`${approve?"批准":"拒绝"} ${row.username} 的申请（${id}${project?"/"+project:""}）`,reason=>request({
      method:"POST",path:`${path}/${encodeURIComponent(row.request_id)}:${approve?"approve":"reject"}`,
      ...(orgRequest?{scopeMode:"session" as const}:{scope:{organizationId:id,projectId:project}}),
      idempotencyKey:crypto.randomUUID(),body:{reason}}));
  }
  function resetPages(){setMemberOffset(0);setInboxOffset(0);}
  const assignTarget=params.get("user");
  const error = directory.error ?? members.error ?? inbox.error;
  const searchTerm = directorySearch.trim().toLocaleLowerCase();
  const matches = (...values:string[])=>values.some(value=>value.toLocaleLowerCase().includes(searchTerm));
  const organizationMatches = new Set(orgs.filter(o=>matches(o.organization_name,o.organization_id)).map(o=>o.organization_id));
  const treeProjects = (directory.data?.projects ?? []).filter(p=>organizationMatches.has(p.organization_id)
    || (view!=="organization" && matches(p.project_name,p.project_id)));
  const visibleOrganizations = orgs.filter(o=>organizationMatches.has(o.organization_id) || treeProjects.some(p=>p.organization_id===o.organization_id));
  const selectedName = project
    ? projects.find(item => item.project_id === project)?.project_name ?? project
    : org?.organization_name;
  function refreshDirectory() {
    void directory.refetch();
    void client.invalidateQueries({ queryKey: ["management-members", actor] });
    if (hasScope) void inbox.refetch();
  }
  return <section className={ui.workspace}>
    {view === "combined" && <Typography.Title level={2}>组织与项目管理</Typography.Title>}
    {view === "combined" && platform && <Link to="/settings/access?tab=users">用户管理</Link>}
    {error && <Alert type="error" showIcon message={error.message} action={<Button onClick={refreshDirectory}>重试</Button>}/>}
    {directory.isPending && <Typography.Text>正在读取管理范围…</Typography.Text>}
    <div className={ui.layout}>
      <aside className={ui.directory} aria-label="组织目录">
        <div className={ui.directoryHead}>
          <h2 className={ui.panelTitle}><Network size={18} aria-hidden />组织目录</h2>
          {view !== "project" && <CreateOrganizationButton onCreated={newId=>{setOrg(newId);setProject(undefined);}}/>}
        </div>
        <div className={ui.search}>
          <Input allowClear prefix={<Search size={15} aria-hidden />} aria-label={view==="project"?"搜索项目":"搜索组织"} placeholder={view==="project"?"搜索项目名称、组织或编号":"搜索组织名称或编号"}
            value={directorySearch} onChange={event => setDirectorySearch(event.target.value)} />
        </div>
        <div className={ui.tree}>
          {visibleOrganizations.length > 0
            ? <ManagementTree organizations={visibleOrganizations} projects={treeProjects} current={{org:id,project}}
                actor={actor} revision={revision} onSelect={selectNode} projectOnly={view==="project"}/>
            : directory.isSuccess && <div className={ui.noMatch}>{searchTerm ? (view==="project"?"没有匹配的项目":"没有匹配的组织") : <>暂无可管理的组织 <Button type="link" onClick={refreshDirectory}>刷新</Button></>}</div>}
        </div>
        <Button type="link" onClick={()=>setShowArchived(true)}>归档与删除记录</Button>
        <Modal open={showArchived} title="归档与删除记录" footer={null} onCancel={()=>setShowArchived(false)}>
          {archived.isLoading && <p>正在读取…</p>}
          {archived.isError && <Alert type="error" message={archived.error.message}/>}
          {archived.data?.length===0 && <p>暂无归档或待清理记录</p>}
          {archived.data?.map(row=><div className={ui.settingsLine} key={JSON.stringify([row.organization_id,row.project_id])}>
            <div><strong>{row.display_name}</strong><p>{row.project_id ? row.organization_id+" / "+row.project_id : row.organization_id}</p></div>
            {row.status==="ARCHIVED" ? <DirectoryActions name={row.display_name} mode="archived" target={{organization_id:row.organization_id,project_id:row.project_id||undefined}}/> : <span>{row.retrying?"清理待重试":row.purge_after && Date.parse(row.purge_after)>Date.now()?"计划清理 · "+new Date(row.purge_after).toLocaleString():"等待完成清理"}</span>}
          </div>)}
        </Modal>
      </aside>
      <div className={ui.detail}>
        {org && <header className={ui.identity}>
          <div className={ui.identityMain}>
            <span className={ui.identityIcon}>{project ? <Folder size={25} aria-hidden/> : <Building2 size={25} aria-hidden/>}</span>
            <div className={ui.identityText}>
              <h2>{selectedName}</h2>
              <p className={ui.meta}>{project ? `${org.organization_name} · 项目编号 ${project}` : `组织编号 ${id} · ${projects.length} 个项目`}</p>
            </div>
          </div>
          <div className={ui.actions}>
            {view === "organization" && org.can_manage && !project && <Link className={ui.projectsLink} to={`/settings/access?tab=projects&organization=${encodeURIComponent(id)}`}>管理本组织项目<ArrowUpRight size={15} aria-hidden /></Link>}
            {org.can_manage && (project || view !== "project") && <DirectoryActions key={JSON.stringify([id,project])} target={{organization_id:id,project_id:project}} name={selectedName ?? id} mode="identity" canRetire={!!project || platform}/>}
            <Button icon={<RefreshCw size={15} aria-hidden />} onClick={refreshDirectory} loading={directory.isFetching}>刷新</Button>
          </div>
        </header>}
    {platform && assignTarget && org && view !== "project" && !project && <Card title={`为 ${params.get("username")??assignTarget} 设置组织权限`}><Button disabled={assignTarget===actor} onClick={()=>appoint(assignTarget,params.get("username")??assignTarget,true)}>任命为本组织管理员</Button></Card>}
    {org?.can_manage && !project && view !== "project" && <Card className={ui.panel}
      title={<h3 className={ui.panelTitle}><Settings2 size={17} aria-hidden />组织设置</h3>}>
      <div className={ui.settingsLine}>
        <div><div className={ui.rowLabel}>组织加入码</div><p className={ui.hint}>用户提交加入申请后，需要管理员审批。</p></div>
        <div className={ui.actions}>
          {org.join_code ? <Typography.Text className={ui.code} copyable>{org.join_code}</Typography.Text> : <span className={ui.role}>未设置</span>}
          <DirectoryActions key={id} mode="code" name={org.organization_name} target={{organization_id:id}} hasCode={!!org.join_code}/>
        </div>
      </div>
      <div className={ui.store}><ObjectStoreConfigurationPanel key={id} organizationId={id} embedded /></div>
    </Card>}
    {org?.can_manage && (view === "project" || (view === "combined" && !project)) && <Card title="创建项目"><Space wrap><Input aria-label="新项目编号" placeholder="项目编号" value={code} onChange={e=>setCode(e.target.value)}/>
    <Input aria-label="新项目名称" placeholder="项目名称" value={name} onChange={e=>setName(e.target.value)}/>
    <Button disabled={!code.trim()||!name.trim()} onClick={()=>confirm("在组织 "+id+" 创建项目 "+name,()=>request({method:"POST",scopeMode:"session",path:"/account/managed-projects",body:{organization_id:id,project_id:code.trim(),project_name:name.trim()},idempotencyKey:crypto.randomUUID()}))}>创建项目</Button></Space></Card>}
    {directory.isSuccess && !hasScope && <Alert type="info" showIcon message={view === "project" && org ? "本组织尚无可管理的项目" : "当前账号没有可管理的组织或项目"} />}
    {assignedNotice && <Alert type="success" showIcon message={assignedNotice} closable onClose={()=>setAssignedNotice("")} />}
    {hasScope && org && <><Card className={ui.panel} title={<h3 className={ui.panelTitle}><Users size={17} aria-hidden />{project?"项目成员":"组织成员"}</h3>}><Table<Member> locale={{emptyText:<div className={ui.empty}>暂无成员</div>}} size="small" loading={members.isLoading} rowKey="principal_id" pagination={false} dataSource={members.data?.slice(0,50)} columns={[
      {title:"用户",dataIndex:"username",render:(_,m)=><span className={ui.person}><span aria-hidden className={ui.avatar}>{Array.from(m.username)[0]?.toUpperCase()}</span><Button type="link" onClick={()=>setPerson(m)}>{m.username}</Button></span>},{title:"身份",render:(_,m)=><span className={ui.role} data-admin={m.administrator}>{m.administrator?"管理员":"成员"}</span>},
      {title:"操作",render:(_,m)=><Space wrap>{!project && org.can_manage && (m.principal_id!==actor || platform || managesSelectedOrganization) && <Button type="link" onClick={()=>setAssignment({member:m,organizationId:id})}>加入项目</Button>}<Button type="link" onClick={()=>setPerson(m)}>管理成员</Button></Space>} ]}/>
    <Pages offset={memberOffset} more={(members.data?.length??0)>50} change={setMemberOffset}/></Card>
    <Card className={ui.panel} title={<h3 className={ui.panelTitle}><Inbox size={17} aria-hidden />待审批申请</h3>}><Table<Entry> locale={{emptyText:<div className={ui.empty}>暂无待审批申请</div>}} size="small" rowKey="request_id" loading={inbox.isLoading} pagination={false} dataSource={inbox.data?.slice(0,50)} columns={[
      {title:"申请人",dataIndex:"username"},{title:"申请内容",render:(_,r)=>[r.kind==="ORGANIZATION"?"加入组织":r.kind==="PROJECT"?"加入项目":r.capability_keys.join("、"),r.resource_id,r.expires_at,r.replaces_request_id&&"替换 "+r.replaces_request_id,r.reason].filter(Boolean).join(" · ")},
      {title:"审批",render:(_,r)=><Space><Button disabled={r.requester_id===actor && !(project && (platform || managesSelectedOrganization) && r.kind!=="ORGANIZATION")} onClick={()=>decide(r,true)}>批准</Button><Button onClick={()=>decide(r,false)}>拒绝</Button></Space>} ]}/>
    <Pages offset={inboxOffset} more={(inbox.data?.length??0)>50} change={setInboxOffset}/></Card></>}
    </div></div>
    {assignment && !project && assignment.organizationId===id && <AssignProjectDialog key={JSON.stringify([id,assignment.member.principal_id])}
      organizationId={id} member={assignment.member} close={()=>setAssignment(undefined)} onAssigned={name=>{
        setAssignedNotice(assignment.member.username+" 已加入 "+name); setAssignment(undefined);
        for (const prefix of ["management-members","managed-member-detail","management-inbox","joinable-projects"])
          void client.invalidateQueries({queryKey:[prefix,actor]});
      }}/>}
    {person && hasScope && <MemberDetailDrawer key={JSON.stringify([id,project,person.principal_id])} member={person} scope={{org:id,project}} actor={actor} revision={revision}
      organizationProjects={!project && org?.can_manage ? projects : undefined} openProject={projectId=>selectNode({org:id,project:projectId},person)}
      onAssign={()=>setAssignment({member:person,organizationId:id})}
      close={()=>setPerson(undefined)} confirm={confirm} appoint={enabled=>appoint(person.principal_id,person.username,enabled)}
      remove={targetProject=>removeMember(person,targetProject)}/>}

    <Modal zIndex={1200} open={!!action} title={action?.label} onCancel={()=>{if(!mutate.isPending)setAction(undefined);}} okText="确认" cancelText="取消" confirmLoading={mutate.isPending}
      okButtonProps={{disabled:!reason.trim()}} onOk={()=>mutate.mutate()}><Input.TextArea aria-label="操作原因" placeholder="填写操作原因" value={reason} maxLength={500} onChange={e=>setReason(e.target.value)}/>
      {mutate.error && <Alert type="error" message={mutate.error.message}/>}</Modal>
  </section>;
}
function Pages({offset,more,change}:{offset:number;more:boolean;change:(n:number)=>void}) {
  return offset||more?<Space><Button disabled={!offset} onClick={()=>change(offset-50)}>上一页</Button><Button disabled={!more} onClick={()=>change(offset+50)}>下一页</Button></Space>:null;
}

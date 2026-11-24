import { useState } from "react";
import { Alert, Button, Card, Input, Modal, Select, Space, Table, Typography } from "antd";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { request } from "../../shared/api/http-client";
import { useShellStore } from "../../shared/scope/shell-store";
import { createPlatformOrganization } from "../../features/platform-operations/api";

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
  const directory = useManagementDirectory();
  const admin = useShellStore(s => s.platformCapabilities.includes("platform.admin"));
  return directory.data?.organizations.length || admin ? <Link to="/settings/access/management">组织与项目管理</Link> : null;
}
export function Component() {
  const [params] = useSearchParams();
  const directory = useManagementDirectory();
  const actor = useShellStore(s => s.principal?.actorId);
  const platform = useShellStore(s => s.platformCapabilities.includes("platform.admin"));
  const [orgId, setOrg] = useState<string>();
  const [projectId, setProject] = useState<string>();
  const [memberOffset, setMemberOffset] = useState(0);
  const [inboxOffset, setInboxOffset] = useState(0);
  const [action,setAction] = useState<Action>();
  const [reason,setReason] = useState("");
  const [code,setCode] = useState("");
  const [name,setName] = useState("");
  const orgs = directory.data?.organizations ?? [];
  const org = orgs.find(o => o.organization_id === orgId) ?? orgs[0];
  const id = org?.organization_id ?? "";
  const projects = (directory.data?.projects ?? []).filter(p => p.organization_id === id);
  const project = projects.find(p=>p.project_id===projectId)?.project_id ?? (org?.can_manage ? undefined : projects[0]?.project_id);
  const scopeQuery = new URLSearchParams({ organization_id: id, ...(project ? {project_id: project} : {}) });
  const revision = useShellStore(s => s.authorization?.roleVersion);
  const key = [actor,revision,id,project] as const;
  const members = useQuery({ queryKey:["management-members",...key,memberOffset], enabled:!!org && (!!project || org.can_manage),
    queryFn:({signal})=>request<Member[]>({ method:"GET",path:`/account/managed-members?${scopeQuery}&offset=${memberOffset}`,scopeMode:"session",signal,cache:"no-store"}) });
  const inbox = useQuery({ queryKey:["management-inbox",...key,inboxOffset],enabled:!!org && (!!project || org.can_manage),
    queryFn:({signal})=>request<Entry[]>({ method:"GET",path:`/account/management-inbox?${scopeQuery}&offset=${inboxOffset}`,scopeMode:"session",signal,cache:"no-store"}) });
  const mutate = useMutation({mutationFn:async()=>{ if(action) await action.run(reason.trim()); },
    onSuccess:async()=>{setAction(undefined);setReason("");await Promise.all([directory.refetch(),members.refetch(),inbox.refetch()]);} });
  function confirm(label:string,run:Action["run"]) {mutate.reset();setReason("");setAction({label,run});}
  function appoint(user:string,label:string,enabled:boolean) {
    confirm(`${enabled?"任命":"撤销"} ${label} 的${project?"项目":"组织"}管理员权限（${id}${project?"/"+project:""}）`,reason=>
      request({method:"PUT",path:`/account/managed-members/${encodeURIComponent(user)}/administrator`,scopeMode:"session",
        idempotencyKey:crypto.randomUUID(),body:{organization_id:id,project_id:project??null,enabled,reason}}));
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
  return <Space direction="vertical" size="middle" style={{display:"flex",padding:24}}>
    <Typography.Title level={2}>组织与项目管理</Typography.Title>
    {platform && <Space><Link to="/settings/access?tab=users">用户管理</Link><Link to="/settings/platform-operations#organization-initialization">创建组织</Link></Space>}
    {error && <Alert type="error" showIcon message={error.message}/>}
    <Space wrap><Select aria-label="管理组织" style={{minWidth:180}} value={id||undefined} placeholder="选择组织" options={orgs.map(o=>({value:o.organization_id,label:`${o.organization_name}（${o.organization_id}）`}))}
      onChange={v=>{setOrg(v);setProject(undefined);resetPages();}}/>
    <Select aria-label="管理范围" style={{minWidth:180}} value={project??""} options={[...(org?.can_manage?[{value:"",label:"组织成员与申请"}]:[]),
      ...projects.map(p=>({value:p.project_id,label:`${p.project_name}（${p.project_id}）`}))]} onChange={v=>{setProject(v||undefined);resetPages();}}/>
    <Button onClick={()=>{void directory.refetch();if(org){void members.refetch();void inbox.refetch();}}}>刷新</Button></Space>
    {org?.can_manage && !project && <Card title="组织设置"><Space wrap><span>组织加入码</span>
      {org.join_code?<Typography.Text copyable>{org.join_code}</Typography.Text>:<span>未设置</span>}
      {platform && !org.join_code && <Button onClick={()=>confirm("为组织 "+id+" 生成加入码",()=>createPlatformOrganization({organization_id:id,organization_name:org.organization_name}))}>生成加入码</Button>}
    </Space><p>用户提交加入申请后，需要管理员审批。</p>
    <Space wrap><Input aria-label="新项目编号" placeholder="项目编号" value={code} onChange={e=>setCode(e.target.value)}/>
    <Input aria-label="新项目名称" placeholder="项目名称" value={name} onChange={e=>setName(e.target.value)}/>
    <Button disabled={!code.trim()||!name.trim()} onClick={()=>confirm("在组织 "+id+" 创建项目 "+name,()=>request({method:"POST",scopeMode:"session",path:"/account/managed-projects",body:{organization_id:id,project_id:code.trim(),project_name:name.trim()},idempotencyKey:crypto.randomUUID()}))}>创建项目</Button></Space></Card>}
    {platform && assignTarget && org && !project && <Card title={`为 ${params.get("username")??assignTarget} 设置组织权限`}><Button disabled={assignTarget===actor} onClick={()=>appoint(assignTarget,params.get("username")??assignTarget,true)}>任命为本组织管理员</Button></Card>}
    {org && <><Card title={project?"项目成员":"组织成员"}><Table<Member> size="small" loading={members.isLoading} rowKey="principal_id" pagination={false} dataSource={members.data?.slice(0,50)} columns={[
      {title:"用户",dataIndex:"username"},{title:"身份",render:(_,m)=>m.administrator?"管理员":"成员"},
      {title:"管理",render:(_,m)=>(project?org.can_manage:platform)&&<Button disabled={m.principal_id===actor} onClick={()=>appoint(m.principal_id,m.username,!m.administrator)}>{m.administrator?"撤销管理员":"任命管理员"}</Button>} ]}/>
    <Pages offset={memberOffset} more={(members.data?.length??0)>50} change={setMemberOffset}/></Card>
    <Card title="待审批申请"><Table<Entry> size="small" rowKey="request_id" loading={inbox.isLoading} pagination={false} dataSource={inbox.data?.slice(0,50)} columns={[
      {title:"申请人",dataIndex:"username"},{title:"申请内容",render:(_,r)=>[r.kind==="ORGANIZATION"?"加入组织":r.kind==="PROJECT"?"加入项目":r.capability_keys.join("、"),r.resource_id,r.expires_at,r.replaces_request_id&&"替换 "+r.replaces_request_id,r.reason].filter(Boolean).join(" · ")},
      {title:"审批",render:(_,r)=><Space><Button disabled={r.requester_id===actor} onClick={()=>decide(r,true)}>批准</Button><Button onClick={()=>decide(r,false)}>拒绝</Button></Space>} ]}/>
    <Pages offset={inboxOffset} more={(inbox.data?.length??0)>50} change={setInboxOffset}/></Card></>}
    <Modal open={!!action} title={action?.label} onCancel={()=>{if(!mutate.isPending)setAction(undefined);}} okText="确认" cancelText="取消" confirmLoading={mutate.isPending}
      okButtonProps={{disabled:!reason.trim()}} onOk={()=>mutate.mutate()}><Input.TextArea aria-label="操作原因" placeholder="填写操作原因" value={reason} maxLength={500} onChange={e=>setReason(e.target.value)}/>
      {mutate.error && <Alert type="error" message={mutate.error.message}/>}</Modal>
  </Space>;
}
function Pages({offset,more,change}:{offset:number;more:boolean;change:(n:number)=>void}) {
  return offset||more?<Space><Button disabled={!offset} onClick={()=>change(offset-50)}>上一页</Button><Button disabled={!more} onClick={()=>change(offset+50)}>下一页</Button></Space>:null;
}

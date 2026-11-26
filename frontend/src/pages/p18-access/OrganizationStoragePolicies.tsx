import { useState } from "react";
import { Alert, Button, Card, Collapse, Space, Table } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useScope } from "../../app/providers/ScopeProvider";
import { loadRuntimeAuthorization } from "../../features/account/session/runtime-scope";
import { request } from "../../shared/api/http-client";
import type { components } from "../../shared/api/generated/platform";
import { useShellStore } from "../../shared/scope/shell-store";

type Project = { project_id: string; project_name: string };
type Page = components["schemas"]["LifecyclePolicyPage"];
type Policy = components["schemas"]["LifecyclePolicy"];

function ProjectPolicies({ organization, project }: { organization: string; project: Project }) {
  const projectId = project.project_id;
  const [cursor, setCursor] = useState<string>();
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const { switchScope } = useScope();
  const actor = useShellStore(s => s.principal?.actorId);
  const grant = useShellStore(s => s.sessionScopes.find(g => g.organizationId === organization && g.projectId === projectId));
  const scope = { organizationId: organization, projectId, ...(grant?.regionCodes[0] ? { regionCode: grant.regionCodes[0] } : {}) };
  const page = useQuery({
    queryKey: ["organization-storage-policies", actor, organization, projectId, cursor],
    queryFn: ({ signal }) => request<Page>({
      method: "GET", path: `/projects/${encodeURIComponent(projectId)}/storage/lifecycle-policies`,
      query: { limit: 50, ...(cursor ? { cursor } : {}) }, scope, cache: "no-store", signal,
    }),
  });
  async function open(path: string) {
    try {
      await switchScope(scope, loadRuntimeAuthorization);
      navigate(path);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "切换项目失败");
    }
  }
  const statuses = { DRAFT: "草稿", ENABLED: "启用", PAUSED: "暂停" };
  return <Space direction="vertical" style={{ display: "flex" }}>
    <Space>
      <Button onClick={() => void open("/storage/lifecycle")}>管理生命周期</Button>
      <Button onClick={() => void open("/storage/overview")}>查看存储容量</Button>
    </Space>
    {(error || page.error) && <Alert type="error" message={error || page.error?.message} />}
    <Table<Policy> loading={page.isLoading} rowKey="policy_id" size="small" pagination={false}
      dataSource={page.data?.items} columns={[
        { title: "策略名称", dataIndex: "name" },
        { title: "保留天数", dataIndex: "minimum_age_days" },
        { title: "状态", render: (_, row) => statuses[row.state] },
      ]} />
    <Space>
      {cursor && <Button onClick={() => setCursor(undefined)}>返回首页</Button>}
      {page.data?.page_info.has_next_page && <Button onClick={() => setCursor(page.data?.page_info.end_cursor ?? undefined)}>下一页</Button>}
    </Space>
  </Space>;
}

export function OrganizationStoragePolicies({ organization, projects }: { organization: string; projects: Project[] }) {
  return <Card title="组织存储治理">
    {projects.length ? <Collapse items={projects.map(project => ({
      key: project.project_id, label: project.project_name + "（" + project.project_id + "）",
      children: <ProjectPolicies organization={organization} project={project} />,
    }))} /> : "本组织尚无项目"}
  </Card>;
}

import { ManagementLink } from "../../p18-access/GovernancePage";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Checkbox,
  Empty,
  Form,
  Input,
  Select,
  Spin,
} from "antd";
import { Building2, FolderKanban, RefreshCw, Send, ShieldCheck } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  accountAccessKeys,
  requestOrganizationMembership,
  requestProjectCapabilities,
  requestProjectMembership,
  useAccountAccessOverview,
  withdrawAccountAccessRequest,
  type AccountAccessRequest,
  type AccountOrganizationMembership,
  type AccountProjectMembership,
} from "../../../features/account/access-api";
import { getSessionBootstrap } from "../../../features/account/session/api";
import { installSessionBootstrap } from "../../../features/account/session/runtime-scope";
import { isDomainError } from "../../../shared/api/domain-error";
import { useShellStore } from "../../../shared/scope/shell-store";
import styles from "../styles.module.css";

import { grantLabel, grantOptions } from "../../../shared/auth/project-grants";

const statusLabels: Readonly<Record<AccountAccessRequest["status"], string>> = {
  PENDING: "待审批",
  APPROVED: "已通过",
  REJECTED: "未通过",
  WITHDRAWN: "已撤回",
  REVOKED: "已撤销",
};

const kindLabels: Readonly<Record<AccountAccessRequest["kind"], string>> = {
  ORGANIZATION: "加入组织",
  PROJECT: "加入项目",
  CAPABILITY: "权限申请",
};

function accessError(reason: unknown): string {
  if (isDomainError(reason)) return reason.message;
  return reason instanceof Error ? reason.message : "操作未完成，请稍后重试。";
}

function fallbackOrganizations(): readonly AccountOrganizationMembership[] {
  return useShellStore.getState().sessionOrganizations.map((organization) => ({
    organization_id: organization.organizationId,
    organization_name: organization.organizationName,
    member_status: organization.memberStatus,
  }));
}

function fallbackProjects(): readonly AccountProjectMembership[] {
  const organizations = new Map(
    useShellStore
      .getState()
      .sessionOrganizations.map((item) => [item.organizationId, item.organizationName]),
  );
  return useShellStore.getState().sessionScopes.map((scope) => ({
    organization_id: scope.organizationId,
    organization_name: organizations.get(scope.organizationId) ?? scope.organizationId,
    project_id: scope.projectId,
    project_name: scope.projectId,
    member_status: "ACTIVE",
  }));
}

function useAccessRelationships() {
  const query = useAccountAccessOverview();
  return {
    query,
    organizations: query.data?.organizations ?? fallbackOrganizations(),
    projects: query.data?.projects ?? fallbackProjects(),
  } as const;
}

function SectionHeader({
  icon,
  title,
  description,
}: {
  readonly icon: React.ReactNode;
  readonly title: string;
  readonly description: string;
}) {
  return (
    <header className={styles.sectionHeader}>
      <span className={styles.sectionIcon} aria-hidden="true">{icon}</span>
      <div>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
    </header>
  );
}

function RelationshipSummary({
  organizations,
  projects,
}: {
  readonly organizations: readonly AccountOrganizationMembership[];
  readonly projects: readonly AccountProjectMembership[];
}) {
  if (organizations.length === 0) {
    return (
      <div className={styles.relationshipEmpty}>
        <Building2 aria-hidden="true" size={22} />
        <div><strong>当前状态：尚未加入组织</strong><span>组织审批通过后，才可申请该组织下的项目。</span></div>
      </div>
    );
  }
  return (
    <div className={styles.relationshipList}>
      {organizations.map((organization) => (
        <article key={organization.organization_id}>
          <Building2 aria-hidden="true" size={18} />
          <div>
            <strong>{organization.organization_name}</strong>
            <span>组织 ID：{organization.organization_id}</span>
          </div>
          <span className={styles.activeMembership}>成员状态：正常</span>
        </article>
      ))}
      {projects.map((project) => (
        <article key={`${project.organization_id}:${project.project_id}`}>
          <FolderKanban aria-hidden="true" size={18} />
          <div>
            <strong>{project.project_name}</strong>
            <span>{project.organization_name} · 项目 ID：{project.project_id}</span>
          </div>
          <span className={styles.activeMembership}>成员状态：正常</span>
        </article>
      ))}
    </div>
  );
}

export function MembershipsPanel() {
  const { query, organizations, projects } = useAccessRelationships();
  const principalId = useShellStore((state) => state.principal?.actorId ?? null);
  const queryClient = useQueryClient();
  const [organizationIdentifier, setOrganizationIdentifier] = useState("");
  const [organizationReason, setOrganizationReason] = useState("");
  const [organizationId, setOrganizationId] = useState(organizations[0]?.organization_id ?? "");
  const [projectId, setProjectId] = useState("");
  const [projectReason, setProjectReason] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const organizationKey = useRef(globalThis.crypto.randomUUID());
  const projectKey = useRef(globalThis.crypto.randomUUID());
  const refresh = useMutation({
    mutationFn: async () => {
      const bootstrap = await getSessionBootstrap();
      installSessionBootstrap(bootstrap);
      if (principalId !== null) {
        await queryClient.invalidateQueries({ queryKey: accountAccessKeys.overview(principalId) });
      }
      return bootstrap;
    },
    onSuccess: () => setFeedback("账户访问范围已刷新。审批通过的组织或项目会直接显示，无需新建账户。"),
  });
  const organizationRequest = useMutation({
    mutationFn: () => requestOrganizationMembership(
      organizationIdentifier.trim(),
      organizationReason.trim(),
      organizationKey.current,
    ),
    onSuccess: async () => {
      organizationKey.current = globalThis.crypto.randomUUID();
      setFeedback("加入组织申请已提交。审批通过后刷新账户访问范围即可。 ");
      await query.refetch();
    },
  });
  const projectRequest = useMutation({
    mutationFn: () => requestProjectMembership(
      organizationId,
      projectId.trim(),
      projectReason.trim() || null,
      projectKey.current,
    ),
    onSuccess: async () => {
      projectKey.current = globalThis.crypto.randomUUID();
      setFeedback("加入项目申请已提交。项目成员关系不会自动授予业务权限。");
      await query.refetch();
    },
  });
  const organizationPending = query.data?.requests.find(
    (item) => item.kind === "ORGANIZATION" && item.status === "PENDING",
  );

  return (
    <section className={styles.formSection} aria-label="我的组织与项目">
      <SectionHeader
        description="组织成员关系、项目成员关系和业务权限彼此独立。"
        icon={<Building2 size={20} />}
        title="我的组织与项目"
      />
      <ManagementLink/>
      {query.isError ? <Alert title="访问关系暂时无法同步，当前登录不受影响。" showIcon type="warning" /> : null}
      <RelationshipSummary organizations={organizations} projects={projects} />

      {feedback ? <Alert className={styles.feedback} title={feedback} showIcon type="success" /> : null}
      {refresh.isError ? <Alert className={styles.feedback} title={accessError(refresh.error)} showIcon type="error" /> : null}

      {organizations.length === 0 ? (
        <Form className={styles.accessForm} layout="vertical" requiredMark={false}>
          {organizationPending ? (
            <Alert
              description={`申请 ID：${organizationPending.request_id}`}
              title="已有加入组织申请正在审批"
              showIcon
              type="info"
            />
          ) : null}
          <Form.Item label="组织 ID 或加入码" required>
            <Input
              aria-label="组织 ID 或加入码"
              maxLength={256}
              placeholder="输入管理员提供的组织 ID 或加入码"
              value={organizationIdentifier}
              onChange={(event) => setOrganizationIdentifier(event.target.value)}
            />
          </Form.Item>
          <Form.Item label="申请原因" required>
            <Input.TextArea
              aria-label="申请原因"
              maxLength={2000}
              placeholder="说明加入组织的工作需要"
              rows={4}
              value={organizationReason}
              onChange={(event) => setOrganizationReason(event.target.value)}
            />
          </Form.Item>
          {organizationRequest.isError ? <Alert title={accessError(organizationRequest.error)} showIcon type="error" /> : null}
          <div className={styles.formActions}>
            <Button
              disabled={!organizationIdentifier.trim() || !organizationReason.trim() || organizationPending !== undefined}
              icon={<Send aria-hidden="true" size={16} />}
              loading={organizationRequest.isPending}
              type="primary"
              onClick={() => organizationRequest.mutate()}
            >提交加入组织申请</Button>
          </div>
        </Form>
      ) : (
        <Form className={styles.accessForm} layout="vertical" requiredMark={false}>
          <h3>申请加入项目</h3>
          <Form.Item label="所属组织" required>
            <Select
              aria-label="所属组织"
              options={organizations.map((item) => ({ label: `${item.organization_name} (${item.organization_id})`, value: item.organization_id }))}
              value={organizationId || undefined}
              onChange={setOrganizationId}
            />
          </Form.Item>
          <Form.Item label="项目 ID" required>
            <Input aria-label="项目 ID" maxLength={256} value={projectId} onChange={(event) => setProjectId(event.target.value)} />
          </Form.Item>
          <Form.Item label="申请原因">
            <Input.TextArea aria-label="项目申请原因" maxLength={2000} rows={3} value={projectReason} onChange={(event) => setProjectReason(event.target.value)} />
          </Form.Item>
          {projectRequest.isError ? <Alert title={accessError(projectRequest.error)} showIcon type="error" /> : null}
          <div className={styles.formActions}>
            <Button disabled={!organizationId || !projectId.trim()} loading={projectRequest.isPending} type="primary" onClick={() => projectRequest.mutate()}>提交加入项目申请</Button>
          </div>
        </Form>
      )}

      <div className={styles.accessFooter}>
        <Link to="/account/settings?tab=requests">查看申请记录</Link>
        <Button icon={<RefreshCw aria-hidden="true" size={16} />} loading={refresh.isPending} onClick={() => refresh.mutate()}>审批后刷新账户访问范围</Button>
      </div>
    </section>
  );
}

export function CapabilityRequestsPanel() {
  const { query, projects } = useAccessRelationships();
  const [projectKey, setProjectKey] = useState("");
  const [capabilities, setCapabilities] = useState<string[]>([]);
  const [target, setTarget] = useState("");
  const [expiry, setExpiry] = useState("");
  const [replace, setReplace] = useState("");
  const [reason, setReason] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const lastSubmission = useRef<{ payload: string; key: string } | null>(null);
  const selectedProject = useMemo(
    () => projects.find((item) => `${item.organization_id}:${item.project_id}` === projectKey),
    [projectKey, projects],
  );
  const selected = capabilities.length === 1 ? capabilities[0] : undefined;
  const targeted = ["role.collector", "teleoperation.connect", "collection_task.delete", "dataset.delete", "dataset_version.delete"];
  const mustTarget = targeted.includes(selected ?? "");
  const mayTarget = mustTarget || ["export.download", "dataset_version.download_raw", "dataset_version.download_manifest"].includes(selected ?? "");
  const roleTransfer = selected?.startsWith("role.");
  const existingRoles = (query.data?.requests ?? []).filter(item =>
    item.organization_id === selectedProject?.organization_id && item.project_id === selectedProject?.project_id &&
    item.status === "APPROVED" && item.capability_keys.some(key => key.startsWith("role.")) &&
    (!item.expires_at || Date.parse(item.expires_at) > Date.now())
  );
  const invalid = !selectedProject || capabilities.length === 0 ||
    (capabilities.length > 1 && capabilities.some(key => key.startsWith("role."))) ||
    (capabilities.some(key => targeted.includes(key)) && !mustTarget) ||
    (mustTarget && !target.trim()) || Boolean(expiry && !(Date.parse(expiry) > Date.now()));
  const mutation = useMutation({
    mutationFn: () => {
      const payload = JSON.stringify([projectKey, capabilities, reason, target, expiry, replace]);
      if (lastSubmission.current?.payload !== payload) {
        lastSubmission.current = { payload, key: globalThis.crypto.randomUUID() };
      }
      return requestProjectCapabilities(
      selectedProject!.organization_id,
      selectedProject!.project_id,
      capabilities,
      reason.trim() || null,
      lastSubmission.current.key,
      {
        ...(expiry ? { expires_at: new Date(expiry).toISOString() } : {}),
        ...(mayTarget && target.trim() ? { resource_id: target.trim() } : {}),
        ...(roleTransfer && replace ? { replaces_request_id: replace } : {}),
      },
      );
    },
    onSuccess: async () => {
      lastSubmission.current = null;
      setFeedback("权限申请已提交。审批通过后刷新账户访问范围即可生效。");
      await query.refetch();
    },
  });

  return (
    <section className={styles.formSection} aria-label="权限申请">
      <SectionHeader description="加入项目不会自动获得业务权限，请只申请工作所需的最小权限。" icon={<ShieldCheck size={20} />} title="权限申请" />
      {projects.length === 0 ? (
        <Empty description="加入项目审批通过后，才可申请项目业务权限。">
          <Button><Link to="/account/settings?tab=memberships">管理组织与项目</Link></Button>
        </Empty>
      ) : (
        <Form className={styles.accessForm} layout="vertical" requiredMark={false}>
          <Form.Item label="项目" required>
            <Select
              aria-label="权限申请项目"
              options={projects.map((item) => ({ label: `${item.organization_name} / ${item.project_name}`, value: `${item.organization_id}:${item.project_id}` }))}
              placeholder="选择已加入的项目"
              value={projectKey || undefined}
              onChange={key => { setProjectKey(key); setReplace(""); setTarget(""); }}
            />
          </Form.Item>
          <Form.Item label="申请权限" required>
            <Checkbox.Group
              aria-label="申请权限"
              className={styles.capabilityChoices}
              options={grantOptions.map((item) => ({ ...item }))}
              value={capabilities}
              onChange={(values) => { setCapabilities(values as string[]); setReplace(""); setTarget(""); }}
            />
          </Form.Item>
          {existingRoles.length > 0 && <Form.Item label="当前有效岗位">
            {existingRoles.map(item => item.capability_keys.map(grantLabel).join("、") + (item.resource_id ? " · " + item.resource_id : "")).join("；")}
          </Form.Item>}
          {roleTransfer && existingRoles.length > 0 && <Form.Item label="岗位变更">
            <Select aria-label="岗位变更" value={replace} onChange={setReplace}
              options={[{ value: "", label: "兼岗，保留已有岗位" }, ...existingRoles.map(item =>
                ({ value: item.request_id, label: "转岗并收回 " + item.capability_keys.map(grantLabel).join("、") }))]} />
          </Form.Item>}
          {mayTarget && <Form.Item label="目标资源" required={mustTarget}>
            <Input aria-label="目标资源" maxLength={256} value={target} onChange={e => setTarget(e.target.value)}
              placeholder={selected === "dataset_version.delete" ? "数据集ID/版本ID" : mustTarget ? "输入目标资源 ID" : "version:版本ID 或 dataset:数据集ID，留空为项目范围"} />
          </Form.Item>}
          <Form.Item label="授权截止时间">
            <Input type="datetime-local" aria-label="授权截止时间" value={expiry} onChange={e => setExpiry(e.target.value)} />
          </Form.Item>
          <Form.Item label="申请原因">
            <Input.TextArea aria-label="权限申请原因" maxLength={2000} rows={4} value={reason} onChange={(event) => setReason(event.target.value)} />
          </Form.Item>
          {feedback ? <Alert title={feedback} showIcon type="success" /> : null}
          {mutation.isError ? <Alert title={accessError(mutation.error)} showIcon type="error" /> : null}
          <div className={styles.formActions}>
            <Button disabled={invalid} loading={mutation.isPending} type="primary" onClick={() => mutation.mutate()}>提交权限申请</Button>
          </div>
        </Form>
      )}
    </section>
  );
}

function requestTarget(item: AccountAccessRequest): string {
  if (item.project_id) return `${item.organization_id} / ${item.project_id}`;
  return item.organization_id;
}

export function RequestHistoryPanel() {
  const query = useAccountAccessOverview();
  const mutation = useMutation({
    mutationFn: withdrawAccountAccessRequest,
    onSuccess: () => void query.refetch(),
  });
  return (
    <section className={styles.formSection} aria-label="申请记录">
      <SectionHeader description="查看组织、项目和权限申请，并可撤回仍在审批中的申请。" icon={<RefreshCw size={20} />} title="申请记录" />
      {query.isPending ? <div className={styles.centeredState}><Spin size="small" /><span>正在加载申请记录…</span></div> : null}
      {query.isError ? <Alert action={<Button size="small" onClick={() => void query.refetch()}>重试</Button>} title="申请记录暂时无法加载。" showIcon type="error" /> : null}
      {query.data?.requests.length === 0 ? <Empty description="暂无申请记录" /> : null}
      {mutation.isError ? <Alert title={accessError(mutation.error)} showIcon type="error" /> : null}
      <div className={styles.requestHistory}>
        {query.data?.requests.map((item) => (
          <article key={item.request_id}>
            <div>
              <strong>{kindLabels[item.kind]}</strong>
              <span>{requestTarget(item)}</span>
              {item.capability_keys.length > 0 ? <small>{item.capability_keys.map(grantLabel).join("、")}</small> : null}
            </div>
            <div>
              <span>{item.status === "APPROVED" && item.expires_at && Date.parse(item.expires_at) <= Date.now() ? "已到期" : statusLabels[item.status]}</span>
              {item.status === "PENDING" ? (
                <Button danger loading={mutation.isPending && mutation.variables?.request_id === item.request_id} size="small" onClick={() => mutation.mutate(item)}>撤回申请</Button>
              ) : null}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

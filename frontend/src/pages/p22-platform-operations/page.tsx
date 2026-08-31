import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Input, Select } from "antd";
import {
  ArchiveRestore,
  Building2,
  Boxes,
  Check,
  CircleAlert,
  Clock3,
  FileSearch,
  Fingerprint,
  FolderPlus,
  GitPullRequestArrow,
  RefreshCw,
  Server,
} from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import {
  approvePlatformRelease,
  createPlatformOrganization,
  createPlatformProject,
  getPlatformObjectStoreConfig,
  getPlatformReleaseHistory,
  getPlatformOperationsOverview,
  listPlatformOrganizations,
  listPlatformProjects,
  queryPlatformRuntimeLogs,
  updatePlatformObjectStoreConfig,
  type PlatformLogEvent,
  type PlatformLogFilters,
  type PlatformObjectStoreConfig,
  type PlatformOperationsBackup,
  type PlatformOperationsNode,
  type PlatformReleaseHistory,
} from "../../features/platform-operations/api";
import { isDomainError } from "../../shared/api/domain-error";
import { getSessionBootstrap } from "../auth/api";
import { installSessionBootstrap } from "../auth/runtime-scope";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  PageState,
  StandardPageScaffold,
  StatusTag,
  type PageStateKind,
} from "../../shared/ui";
import styles from "./styles.module.css";

const serviceOptions = [
  { value: "", label: "全部服务" },
  { value: "hc-data-platform-api", label: "API" },
  { value: "hc-data-platform-worker", label: "主任务 Worker" },
  { value: "hc-data-platform-media-worker", label: "媒体 Worker" },
  { value: "hc-data-platform-frontend", label: "前端" },
  { value: "hc-data-platform-gateway", label: "网关" },
] as const;

const severityOptions = [
  { value: "", label: "全部级别" },
  ...["TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"].map((value) => ({
    value,
    label: value,
  })),
] as const;

const correlationOptions = [
  { value: "requestId", label: "请求 ID" },
  { value: "operationId", label: "操作 ID" },
  { value: "workflowId", label: "工作流 ID" },
] as const;

type CorrelationKind = (typeof correlationOptions)[number]["value"];
type LogFilterForm = Readonly<{
  occurredFrom: string;
  occurredTo: string;
  service: string;
  severity: string;
  eventCode: string;
  correlationKind: CorrelationKind;
  correlationValue: string;
}>;

const preflightLabels = {
  RELEASE_IDENTITY: "版本身份",
  NODE_CONVERGENCE: "节点收敛",
  NODE_READINESS: "运行就绪",
  CONFIG_CONVERGENCE: "配置一致",
  VERIFIED_BACKUP: "可恢复备份",
  VERIFIED_RESTORE: "恢复演练",
  CENTRAL_LOG_SEARCH: "日志可检索",
} as const;

const roleLabels: Readonly<Record<PlatformOperationsNode["role"], string>> = {
  frontend: "前端",
  api: "API",
  worker: "主任务 Worker",
  "media-worker": "媒体 Worker",
  "maintenance-controller": "维护控制器",
};

const serviceLabels: Readonly<Record<PlatformLogEvent["service"], string>> = {
  "hc-data-platform-api": "API",
  "hc-data-platform-worker": "主任务 Worker",
  "hc-data-platform-media-worker": "媒体 Worker",
  "hc-data-platform-frontend": "前端",
  "hc-data-platform-gateway": "网关",
};

function localInputValue(date: Date): string {
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function defaultLogFilterForm(): LogFilterForm {
  const occurredTo = new Date();
  return {
    occurredFrom: localInputValue(new Date(occurredTo.getTime() - 60 * 60_000)),
    occurredTo: localInputValue(occurredTo),
    service: "",
    severity: "",
    eventCode: "",
    correlationKind: "requestId",
    correlationValue: "",
  };
}

function toLogFilters(form: LogFilterForm): PlatformLogFilters {
  const correlationValue = form.correlationValue.trim();
  return {
    occurredFrom: new Date(form.occurredFrom).toISOString(),
    occurredTo: new Date(form.occurredTo).toISOString(),
    ...(form.service
      ? { service: form.service as PlatformLogEvent["service"] }
      : {}),
    ...(form.severity
      ? { severity: form.severity as PlatformLogEvent["severity"] }
      : {}),
    ...(form.eventCode.trim() ? { eventCode: form.eventCode.trim() } : {}),
    ...(correlationValue ? { [form.correlationKind]: correlationValue } : {}),
    limit: 100,
  };
}

function errorState(error: unknown): PageStateKind {
  if (!isDomainError(error)) return "contract-mismatch";
  if (error.code === "FORBIDDEN" || error.code === "UNAUTHENTICATED")
    return "forbidden";
  if (error.code === "RATE_LIMITED") return "rate-limited";
  if (error.code === "NETWORK_ERROR") return "offline";
  if (error.code === "CONTRACT_MISMATCH") return "contract-mismatch";
  return "error";
}

function formatInstant(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "medium",
    hour12: false,
  }).format(new Date(value));
}

function shortDigest(value: string): string {
  if (value === "unreleased") return value;
  const digest = value.replace(/^sha256:/u, "");
  return `${digest.slice(0, 12)}…${digest.slice(-6)}`;
}

function shortReference(value: string): string {
  return `…${value.slice(-10)}`;
}

function statusTone(status: PlatformOperationsBackup["status"]) {
  if (status === "INTEGRITY_VERIFIED" || status === "RESTORE_VERIFIED")
    return "success";
  if (status === "FAILED" || status === "CORRUPT") return "danger";
  if (status === "EXPIRED") return "neutral";
  return "info";
}

function severityTone(severity: PlatformLogEvent["severity"]) {
  if (severity === "ERROR" || severity === "FATAL") return "danger";
  if (severity === "WARN") return "warning";
  if (severity === "INFO") return "info";
  return "neutral";
}

function releaseTone(state: PlatformReleaseHistory["items"][number]["state"]) {
  if (state === "COMPLETED" || state === "APPROVED") return "success";
  if (
    state === "FAILED" ||
    state === "ROLLED_BACK" ||
    state === "PREFLIGHT_BLOCKED"
  )
    return "danger";
  if (state === "AWAITING_APPROVAL" || state === "CONTRACT_PENDING")
    return "warning";
  return "info";
}

function ReleaseHistoryPanel({
  history,
  canApprove,
  approvalDrafts,
  approvalError,
  approvingReleaseId,
  onDraftChange,
  onApprove,
}: Readonly<{
  history: PlatformReleaseHistory;
  canApprove: boolean;
  approvalDrafts: Readonly<Record<string, string>>;
  approvalError: string | null;
  approvingReleaseId: string | null;
  onDraftChange: (releaseId: string, value: string) => void;
  onApprove: (releaseId: string, stateVersion: number) => void;
}>) {
  return (
    <section
      className={styles.dataPanel}
      aria-labelledby="release-history-title"
    >
      <div className={styles.sectionHeading}>
        <div>
          <span className={styles.eyebrow}>AUDITED RELEASE TRAIN</span>
          <h2 id="release-history-title">升级历史与人工批准</h2>
        </div>
        <span className={styles.countBadge}>{history.count} 条发布记录</span>
      </div>
      {history.items.length === 0 ? (
        <PageState
          state="empty"
          label="升级历史"
          description="尚未有通过签名预检的目标版本。"
        />
      ) : (
        <div className={styles.releaseHistoryList}>
          {history.items.map((release) => (
            <article
              className={styles.releaseHistoryItem}
              key={release.release_id}
            >
              <div className={styles.releaseHistoryMark} aria-hidden="true">
                <GitPullRequestArrow size={18} />
              </div>
              <div className={styles.releaseHistoryCopy}>
                <div>
                  <strong>{release.release_id}</strong>
                  <span>
                    {release.source_version} → {release.target_version} ·
                    状态修订 r{release.state_version}
                  </span>
                </div>
                <small>
                  最近更新 {formatInstant(release.updated_at)} ·{" "}
                  {release.events.length} 个审计步骤
                </small>
                {canApprove && release.state === "AWAITING_APPROVAL" ? (
                  <div className={styles.approvalRow}>
                    <Input
                      aria-label={`${release.release_id} 批准理由`}
                      maxLength={500}
                      placeholder="填写批准理由（至少 8 个字符）"
                      value={approvalDrafts[release.release_id] ?? ""}
                      onChange={(event) =>
                        onDraftChange(release.release_id, event.target.value)
                      }
                    />
                    <Button
                      loading={approvingReleaseId === release.release_id}
                      onClick={() =>
                        onApprove(release.release_id, release.state_version)
                      }
                      type="primary"
                    >
                      人工批准
                    </Button>
                  </div>
                ) : null}
              </div>
              <StatusTag
                known
                label={release.state.replaceAll("_", " ")}
                status={release.state}
                tone={releaseTone(release.state)}
              />
            </article>
          ))}
        </div>
      )}
      {approvalError ? (
        <p className={styles.filterError} role="alert">
          {approvalError}
        </p>
      ) : null}
    </section>
  );
}

function ReleaseGateRail({
  preflight,
}: Readonly<{
  preflight: Awaited<
    ReturnType<typeof getPlatformOperationsOverview>
  >["upgrade_preflight"];
}>) {
  return (
    <section className={styles.gatePanel} aria-labelledby="upgrade-gate-title">
      <div className={styles.sectionHeading}>
        <div>
          <span className={styles.eyebrow}>RELEASE GATE</span>
          <h2 id="upgrade-gate-title">升级准入链</h2>
        </div>
        <StatusTag
          known
          label={
            preflight.status === "READY" ? "允许进入升级窗口" : "升级被阻断"
          }
          status={preflight.status}
          tone={preflight.status === "READY" ? "success" : "danger"}
        />
      </div>
      <ol className={styles.gateRail}>
        {preflight.checks.map((check, index) => (
          <li
            className={styles.gateStep}
            data-status={check.status}
            key={check.code}
          >
            <span className={styles.stepIndex}>
              {String(index + 1).padStart(2, "0")}
            </span>
            <span className={styles.stepIcon} aria-hidden="true">
              {check.status === "PASS" ? (
                <Check size={16} />
              ) : (
                <CircleAlert size={16} />
              )}
            </span>
            <span className={styles.stepCopy}>
              <strong>{preflightLabels[check.code]}</strong>
              <small>{check.reason_code.replaceAll("_", " ")}</small>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function ReleaseIdentityPanel({
  overview,
}: Readonly<{
  overview: Awaited<ReturnType<typeof getPlatformOperationsOverview>>;
}>) {
  const release = overview.release;
  return (
    <section className={styles.releasePanel} aria-labelledby="release-title">
      <div className={styles.releaseMark} aria-hidden="true">
        <Fingerprint size={27} />
      </div>
      <div className={styles.releaseIdentity}>
        <span className={styles.eyebrow}>CURRENT RELEASE</span>
        <h2 id="release-title">{release.release_id}</h2>
        <p>
          平台 {release.semantic_version} · Chart {release.chart_version} · Git{" "}
          {release.git_commit.slice(0, 10)}
        </p>
      </div>
      <dl className={styles.releaseFacts}>
        <div>
          <dt>发布清单</dt>
          <dd title={release.release_manifest_digest}>
            {shortDigest(release.release_manifest_digest)}
          </dd>
        </div>
        <div>
          <dt>迁移清单</dt>
          <dd title={release.migration_manifest_digest}>
            {shortDigest(release.migration_manifest_digest)}
          </dd>
        </div>
        <div>
          <dt>观测时间</dt>
          <dd>{formatInstant(overview.observed_at)}</dd>
        </div>
      </dl>
    </section>
  );
}

function NodesPanel({
  nodes,
}: Readonly<{ nodes: readonly PlatformOperationsNode[] }>) {
  return (
    <section className={styles.dataPanel} aria-labelledby="nodes-title">
      <div className={styles.sectionHeading}>
        <div>
          <span className={styles.eyebrow}>RUNTIME FLEET</span>
          <h2 id="nodes-title">节点收敛</h2>
        </div>
        <span className={styles.countBadge}>{nodes.length} 个进程</span>
      </div>
      {nodes.length === 0 ? (
        <PageState
          state="empty"
          label="平台节点"
          description="尚未收到平台进程心跳。"
        />
      ) : (
        <div className={styles.tableViewport}>
          <table className={styles.operationsTable}>
            <thead>
              <tr>
                <th>角色</th>
                <th>匿名节点</th>
                <th>版本</th>
                <th>配置修订</th>
                <th>最后心跳</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map((node) => (
                <tr key={node.node_ref}>
                  <td>
                    <strong>{roleLabels[node.role]}</strong>
                  </td>
                  <td>
                    <code>{shortReference(node.node_ref)}</code>
                  </td>
                  <td title={node.release_manifest_digest}>
                    {node.release_id}
                  </td>
                  <td>r{node.applied_config_revision}</td>
                  <td>{formatInstant(node.last_heartbeat_at)}</td>
                  <td>
                    <StatusTag
                      known
                      label={
                        node.stale
                          ? "心跳过期"
                          : node.readiness === "ready"
                            ? "就绪"
                            : "未就绪"
                      }
                      status={node.stale ? "STALE" : node.readiness}
                      tone={
                        node.stale || node.readiness === "not_ready"
                          ? "danger"
                          : node.readiness === "ready"
                            ? "success"
                            : "warning"
                      }
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

type OrganizationDraft = Readonly<{
  organizationId: string;
  organizationName: string;
}>;

const emptyOrganizationDraft: OrganizationDraft = {
  organizationId: "",
  organizationName: "",
};

const stableScopeId = /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$/u;

function OrganizationInitializationPanel() {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [draft, setDraft] = useState<OrganizationDraft>(emptyOrganizationDraft);
  const [formError, setFormError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const organizations = useQuery({
    queryKey: ["platform-operations", "global", "organizations"],
    queryFn: ({ signal }) => listPlatformOrganizations(signal),
    staleTime: 5_000,
    gcTime: 0,
    retry: 1,
  });

  useEffect(() => {
    if (organizations.data?.count === 0) setExpanded(true);
  }, [organizations.data?.count]);

  const create = useMutation({
    mutationFn: () =>
      createPlatformOrganization({
        organization_id: draft.organizationId.trim(),
        organization_name: draft.organizationName.trim(),
      }),
    onSuccess: async (created) => {
      setFormError(null);
      setSuccessMessage(`组织“${created.organization_name}”已创建。`);
      setDraft(emptyOrganizationDraft);
      setExpanded(false);
      await queryClient.invalidateQueries({
        queryKey: ["platform-operations", "global", "organizations"],
      });
    },
    onError: (reason) => {
      setSuccessMessage(null);
      setFormError(
        isDomainError(reason) &&
          reason.problemCode === "PLATFORM_ORGANIZATION_ALREADY_EXISTS"
          ? "该组织 ID 已存在，请更换组织 ID。"
          : "组织未创建，请检查输入或稍后重试。",
      );
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const organizationId = draft.organizationId.trim();
    const organizationName = draft.organizationName.trim();
    if (!organizationId || !organizationName) {
      setFormError("组织 ID 和组织名称均为必填项。");
      return;
    }
    if (!stableScopeId.test(organizationId)) {
      setFormError("组织 ID 只能使用字母、数字、点、下划线和连字符。");
      return;
    }
    setFormError(null);
    setSuccessMessage(null);
    create.mutate();
  }

  return (
    <section
      className={styles.dataPanel}
      id="organization-initialization"
      aria-labelledby="organization-init-title"
    >
      <div className={styles.sectionHeading}>
        <div>
          <span className={styles.eyebrow}>ORGANIZATION DIRECTORY</span>
          <h2 id="organization-init-title">组织目录</h2>
        </div>
        <Button
          icon={<Building2 aria-hidden="true" size={16} />}
          onClick={() => {
            setFormError(null);
            setSuccessMessage(null);
            setExpanded((value) => !value);
          }}
          type={organizations.data?.count === 0 ? "primary" : "default"}
        >
          {expanded ? "取消创建组织" : "创建组织"}
        </Button>
      </div>

      {organizations.isPending ? (
        <PageState state="loading" label="组织目录" />
      ) : organizations.isError || !organizations.data ? (
        <PageState
          state={errorState(organizations.error)}
          label="组织目录"
          description="组织目录暂时不可用；项目和存储配置不会受影响。"
          onRetry={() => void organizations.refetch()}
        />
      ) : organizations.data.count === 0 ? (
        <Alert
          description="先创建组织，再在项目目录中为该组织创建项目。"
          showIcon
          title="尚未创建组织"
          type="warning"
        />
      ) : (
        <div className={styles.projectDirectory} aria-live="polite">
          <span>{organizations.data.count} 个组织</span>
          <ul>
            {organizations.data.items.map((organization) => (
              <li key={organization.organization_id}>
                <strong>{organization.organization_name}</strong>
                <span>组织</span>
                <code>{organization.organization_id}</code>
              </li>
            ))}
          </ul>
        </div>
      )}

      {expanded ? (
        <form className={styles.projectForm} onSubmit={submit}>
          <div className={styles.projectFields}>
            <label>
              <span>组织 ID</span>
              <Input
                aria-describedby="organization-id-help"
                aria-label="新组织 ID"
                autoComplete="off"
                disabled={create.isPending}
                maxLength={128}
                placeholder="例如 hangcha"
                value={draft.organizationId}
                onChange={(event) =>
                  setDraft({ ...draft, organizationId: event.target.value })
                }
              />
            </label>
            <label>
              <span>组织名称</span>
              <Input
                aria-label="新组织名称"
                autoComplete="organization"
                disabled={create.isPending}
                maxLength={256}
                placeholder="例如 杭叉集团"
                value={draft.organizationName}
                onChange={(event) =>
                  setDraft({ ...draft, organizationName: event.target.value })
                }
              />
            </label>
          </div>
          <p className={styles.configurationNote} id="organization-id-help">
            组织 ID 创建后作为稳定标识使用；日常界面显示组织名称。
          </p>
          {formError ? (
            <p className={styles.filterError} role="alert">
              {formError}
            </p>
          ) : null}
          <Button htmlType="submit" loading={create.isPending} type="primary">
            创建组织
          </Button>
        </form>
      ) : null}

      {successMessage ? (
        <Alert
          className={styles.successAlert}
          showIcon
          title={successMessage}
          type="success"
        />
      ) : null}
    </section>
  );
}

type ProjectDraft = Readonly<{
  organizationId: string;
  projectId: string;
  projectName: string;
}>;

const emptyProjectDraft: ProjectDraft = {
  organizationId: "",
  projectId: "",
  projectName: "",
};

function ProjectInitializationPanel() {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [draft, setDraft] = useState<ProjectDraft>(emptyProjectDraft);
  const [formError, setFormError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const projects = useQuery({
    queryKey: ["platform-operations", "global", "projects"],
    queryFn: ({ signal }) => listPlatformProjects(signal),
    staleTime: 5_000,
    gcTime: 0,
    retry: 1,
  });
  const organizations = useQuery({
    queryKey: ["platform-operations", "global", "organizations"],
    queryFn: ({ signal }) => listPlatformOrganizations(signal),
    staleTime: 5_000,
    gcTime: 0,
    retry: 1,
  });

  useEffect(() => {
    if (projects.data?.count === 0 && (organizations.data?.count ?? 0) > 0)
      setExpanded(true);
  }, [organizations.data?.count, projects.data?.count]);

  useEffect(() => {
    const firstOrganization = organizations.data?.items[0];
    if (!draft.organizationId && firstOrganization) {
      setDraft((current) => ({
        ...current,
        organizationId: firstOrganization.organization_id,
      }));
    }
  }, [draft.organizationId, organizations.data?.items]);

  const create = useMutation({
    mutationFn: () =>
      createPlatformProject({
        organization_id: draft.organizationId.trim(),
        project_id: draft.projectId.trim(),
        project_name: draft.projectName.trim(),
      }),
    onSuccess: async (created) => {
      setFormError(null);
      setSuccessMessage(
        `项目“${created.project_name}”已创建，并已加入管理员作用域。`,
      );
      setDraft((current) => ({
        ...emptyProjectDraft,
        organizationId: current.organizationId.trim(),
      }));
      setExpanded(false);
      await queryClient.invalidateQueries({
        queryKey: ["platform-operations", "global", "projects"],
      });
      try {
        installSessionBootstrap(await getSessionBootstrap());
      } catch {
        setSuccessMessage(
          `项目 ${created.organization_id} / ${created.project_id} 已创建；刷新页面后即可选择该项目。`,
        );
      }
    },
    onError: (reason) => {
      setSuccessMessage(null);
      setFormError(
        isDomainError(reason) &&
          reason.problemCode === "PLATFORM_PROJECT_ALREADY_EXISTS"
          ? "该组织下已存在同名项目，请更换项目 ID。"
          : isDomainError(reason) &&
              reason.problemCode === "PLATFORM_ORGANIZATION_NOT_FOUND"
            ? "所选组织不存在，请刷新组织目录后重试。"
            : "项目未创建，请检查输入或稍后重试。",
      );
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const organizationId = draft.organizationId.trim();
    const projectId = draft.projectId.trim();
    const projectName = draft.projectName.trim();
    if (!organizationId || !projectId || !projectName) {
      setFormError("所属组织、项目 ID 和项目名称均为必填项。");
      return;
    }
    if (!stableScopeId.test(organizationId) || !stableScopeId.test(projectId)) {
      setFormError(
        "组织 ID 和项目 ID 只能使用字母、数字、点、下划线和连字符。",
      );
      return;
    }
    setFormError(null);
    setSuccessMessage(null);
    create.mutate();
  }

  return (
    <section
      className={styles.dataPanel}
      id="project-initialization"
      aria-labelledby="project-init-title"
    >
      <div className={styles.sectionHeading}>
        <div>
          <span className={styles.eyebrow}>TENANT BOOTSTRAP</span>
          <h2 id="project-init-title">项目目录</h2>
        </div>
        <Button
          disabled={(organizations.data?.count ?? 0) === 0}
          icon={<FolderPlus aria-hidden="true" size={16} />}
          onClick={() => {
            setFormError(null);
            setSuccessMessage(null);
            setExpanded((value) => !value);
          }}
          type={projects.data?.count === 0 ? "primary" : "default"}
        >
          {expanded ? "取消创建项目" : "创建项目"}
        </Button>
      </div>

      {projects.isPending || organizations.isPending ? (
        <PageState state="loading" label="项目目录" />
      ) : projects.isError ||
        !projects.data ||
        organizations.isError ||
        !organizations.data ? (
        <PageState
          state={errorState(projects.error ?? organizations.error)}
          label="项目目录"
          description="项目目录暂时不可用；OSS 配置仍可独立完成。"
          onRetry={() =>
            void Promise.all([projects.refetch(), organizations.refetch()])
          }
        />
      ) : organizations.data.count === 0 ? (
        <Alert
          description="请先在上方创建组织，然后再创建项目。"
          showIcon
          title="需要先创建组织"
          type="warning"
        />
      ) : projects.data.count === 0 ? (
        <Alert
          description="新数据库还没有项目。填写下方信息创建第一个项目；创建后会自动刷新管理员可选作用域。"
          showIcon
          title="尚未创建项目"
          type="warning"
        />
      ) : (
        <div className={styles.projectDirectory} aria-live="polite">
          <span>{projects.data.count} 个项目</span>
          <ul>
            {projects.data.items.map((project) => (
              <li key={`${project.organization_id}/${project.project_id}`}>
                <strong>{project.project_name}</strong>
                <span>{project.organization_name}</span>
                <code>
                  {project.organization_id} / {project.project_id}
                </code>
              </li>
            ))}
          </ul>
        </div>
      )}

      {expanded ? (
        <form className={styles.projectForm} onSubmit={submit}>
          <div className={styles.projectFields}>
            <label>
              <span>所属组织</span>
              <Select
                aria-label="项目所属组织"
                disabled={create.isPending}
                options={(organizations.data?.items ?? []).map(
                  (organization) => ({
                    value: organization.organization_id,
                    label: organization.organization_name,
                  }),
                )}
                placeholder="请选择组织"
                value={draft.organizationId}
                onChange={(organizationId) =>
                  setDraft({ ...draft, organizationId })
                }
              />
            </label>
            <label>
              <span>项目 ID</span>
              <Input
                aria-describedby="project-id-help"
                aria-label="新项目 ID"
                autoComplete="off"
                disabled={create.isPending}
                maxLength={128}
                placeholder="例如 robot-data-01"
                value={draft.projectId}
                onChange={(event) =>
                  setDraft({ ...draft, projectId: event.target.value })
                }
              />
            </label>
            <label>
              <span>项目名称</span>
              <Input
                aria-label="新项目名称"
                autoComplete="off"
                disabled={create.isPending}
                maxLength={256}
                placeholder="例如 双臂采集一期"
                value={draft.projectName}
                onChange={(event) =>
                  setDraft({ ...draft, projectName: event.target.value })
                }
              />
            </label>
          </div>
          <p className={styles.configurationNote} id="project-id-help">
            项目 ID 创建后作为稳定作用域标识使用；日常界面显示项目名称。
          </p>
          {formError ? (
            <p className={styles.filterError} role="alert">
              {formError}
            </p>
          ) : null}
          <Button htmlType="submit" loading={create.isPending} type="primary">
            创建项目
          </Button>
        </form>
      ) : null}

      {successMessage ? (
        <Alert
          className={styles.successAlert}
          showIcon
          title={successMessage}
          type="success"
        />
      ) : null}
    </section>
  );
}

type ObjectStoreConfigDraft = Readonly<{
  endpoint: string;
  publicEndpoint: string;
  bucket: string;
  region: string;
  accessKey: string;
  secretKey: string;
}>;

const emptyObjectStoreConfigDraft: ObjectStoreConfigDraft = {
  endpoint: "https://oss-cn-hangzhou.aliyuncs.com",
  publicEndpoint: "https://oss-cn-hangzhou.aliyuncs.com",
  bucket: "",
  region: "cn-hangzhou",
  accessKey: "",
  secretKey: "",
};

function objectStoreStatusTone(config: PlatformObjectStoreConfig) {
  if (!config.configured) return "warning";
  if (config.activation_required) return "info";
  return "success";
}

function ObjectStoreConfigurationPanel() {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [draft, setDraft] = useState<ObjectStoreConfigDraft>(
    emptyObjectStoreConfigDraft,
  );
  const [formError, setFormError] = useState<string | null>(null);
  const config = useQuery({
    queryKey: ["platform-operations", "global", "object-store-config"],
    queryFn: ({ signal }) => getPlatformObjectStoreConfig(signal),
    enabled: expanded,
    staleTime: 5_000,
    gcTime: 0,
    retry: 1,
  });

  useEffect(() => {
    if (!config.data) return;
    setDraft({
      endpoint: config.data.endpoint || emptyObjectStoreConfigDraft.endpoint,
      publicEndpoint:
        config.data.public_endpoint ||
        emptyObjectStoreConfigDraft.publicEndpoint,
      bucket: config.data.bucket,
      region: config.data.region || emptyObjectStoreConfigDraft.region,
      accessKey: "",
      secretKey: "",
    });
  }, [config.data]);

  const save = useMutation({
    mutationFn: () => {
      const current = config.data;
      if (!current)
        throw new Error("object-store configuration was not loaded");
      const accessKey = draft.accessKey.trim();
      const secretKey = draft.secretKey.trim();
      return updatePlatformObjectStoreConfig({
        expected_revision: current.revision,
        provider: "oss",
        endpoint: draft.endpoint.trim(),
        public_endpoint: draft.publicEndpoint.trim(),
        bucket: draft.bucket.trim(),
        region: draft.region.trim(),
        ...(accessKey ? { access_key: accessKey } : {}),
        ...(secretKey ? { secret_key: secretKey } : {}),
      });
    },
    onSuccess: async (saved) => {
      setFormError(null);
      setDraft((current) => ({
        ...current,
        accessKey: "",
        secretKey: "",
      }));
      queryClient.setQueryData(
        ["platform-operations", "global", "object-store-config"],
        saved,
      );
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["platform-operations", "global", "overview"],
        }),
        queryClient.invalidateQueries({
          queryKey: ["platform-operations", "object-store-location"],
        }),
      ]);
    },
    onError: () => {
      setFormError("配置未保存，请刷新配置修订后重试。");
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const credentialsSupplied = Boolean(
      draft.accessKey.trim() && draft.secretKey.trim(),
    );
    if (
      !draft.endpoint.trim() ||
      !draft.publicEndpoint.trim() ||
      !draft.bucket.trim() ||
      !draft.region.trim()
    ) {
      setFormError("Endpoint、Bucket 和地域不能为空。");
      return;
    }
    if (Boolean(draft.accessKey.trim()) !== Boolean(draft.secretKey.trim())) {
      setFormError("Access Key ID 与 Secret 必须同时填写。");
      return;
    }
    if (!config.data?.access_key_configured && !credentialsSupplied) {
      setFormError("首次配置必须填写 Access Key ID 与 Secret。");
      return;
    }
    setFormError(null);
    save.mutate();
  }

  return (
    <section
      className={styles.dataPanel}
      id="object-store-configuration"
      aria-labelledby="object-store-title"
    >
      <div className={styles.sectionHeading}>
        <div>
          <span className={styles.eyebrow}>RUNTIME DEPENDENCY</span>
          <h2 id="object-store-title">OSS 存储地址</h2>
        </div>
        <Button onClick={() => setExpanded((value) => !value)}>
          {expanded
            ? "收起存储配置"
            : config.data?.configured
              ? "编辑存储地址"
              : "创建存储地址"}
        </Button>
      </div>
      {!expanded ? null : config.isPending ? (
        <PageState state="loading" label="OSS 存储地址" />
      ) : config.isError || !config.data ? (
        <PageState
          state={errorState(config.error)}
          label="OSS 存储地址"
          onRetry={() => void config.refetch()}
        />
      ) : (
        <form className={styles.objectStoreForm} onSubmit={submit}>
          <div className={styles.objectStoreStatus}>
            <StatusTag
              known
              label={
                !config.data.configured
                  ? "尚未配置"
                  : config.data.activation_required
                    ? "等待重启生效"
                    : "已生效"
              }
              status={config.data.source}
              tone={objectStoreStatusTone(config.data)}
            />
            <span>
              配置修订 r{config.data.revision}
              {config.data.access_key_hint
                ? ` · Access Key ${config.data.access_key_hint}`
                : ""}
            </span>
          </div>
          <div className={styles.objectStoreFields}>
            <label>
              <span>服务端 Endpoint</span>
              <Input
                aria-label="OSS 服务端 Endpoint"
                value={draft.endpoint}
                onChange={(event) =>
                  setDraft({ ...draft, endpoint: event.target.value })
                }
              />
            </label>
            <label>
              <span>浏览器 Endpoint</span>
              <Input
                aria-label="OSS 浏览器 Endpoint"
                value={draft.publicEndpoint}
                onChange={(event) =>
                  setDraft({ ...draft, publicEndpoint: event.target.value })
                }
              />
            </label>
            <label>
              <span>Bucket</span>
              <Input
                aria-label="OSS Bucket"
                value={draft.bucket}
                onChange={(event) =>
                  setDraft({ ...draft, bucket: event.target.value })
                }
              />
            </label>
            <label>
              <span>地域</span>
              <Input
                aria-label="OSS 地域"
                value={draft.region}
                onChange={(event) =>
                  setDraft({ ...draft, region: event.target.value })
                }
              />
            </label>
            <label>
              <span>Access Key ID</span>
              <Input.Password
                aria-label="OSS Access Key ID"
                autoComplete="new-password"
                placeholder={
                  config.data.access_key_configured ? "留空则保持不变" : "必填"
                }
                value={draft.accessKey}
                onChange={(event) =>
                  setDraft({ ...draft, accessKey: event.target.value })
                }
              />
            </label>
            <label>
              <span>Access Key Secret</span>
              <Input.Password
                aria-label="OSS Access Key Secret"
                autoComplete="new-password"
                placeholder={
                  config.data.access_key_configured ? "留空则保持不变" : "必填"
                }
                value={draft.secretKey}
                onChange={(event) =>
                  setDraft({ ...draft, secretKey: event.target.value })
                }
              />
            </label>
          </div>
          {formError ? (
            <p className={styles.filterError} role="alert">
              {formError}
            </p>
          ) : null}
          <Button htmlType="submit" loading={save.isPending} type="primary">
            加密保存配置
          </Button>
        </form>
      )}
    </section>
  );
}

function BackupsPanel({
  backups,
}: Readonly<{ backups: readonly PlatformOperationsBackup[] }>) {
  return (
    <section className={styles.dataPanel} aria-labelledby="backups-title">
      <div className={styles.sectionHeading}>
        <div>
          <span className={styles.eyebrow}>RECOVERY ANCHORS</span>
          <h2 id="backups-title">备份目录与验证</h2>
        </div>
        <span className={styles.countBadge}>最近 {backups.length} 份</span>
      </div>
      {backups.length === 0 ? (
        <PageState
          state="empty"
          label="备份目录"
          description="当前环境尚无可用备份目录项。"
        />
      ) : (
        <div className={styles.backupList}>
          {backups.map((backup) => (
            <article className={styles.backupItem} key={backup.backup_ref}>
              <div className={styles.backupIcon} aria-hidden="true">
                <ArchiveRestore size={20} />
              </div>
              <div className={styles.backupCopy}>
                <div>
                  <strong>{shortReference(backup.backup_ref)}</strong>
                  <span>
                    {backup.mode === "portable" ? "可移植备份" : "快照备份"}
                  </span>
                </div>
                <small>
                  完成于 {formatInstant(backup.backup_completed_at)} · 发布清单{" "}
                  {shortDigest(backup.release_manifest_sha256)}
                </small>
              </div>
              <StatusTag
                known
                status={backup.status}
                label={backup.status.replaceAll("_", " ")}
                tone={statusTone(backup.status)}
              />
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function LogEventRow({ event }: Readonly<{ event: PlatformLogEvent }>) {
  const correlation =
    event.operation_id ?? event.workflow_id ?? event.request_id;
  return (
    <article className={styles.logRow}>
      <time dateTime={event.timestamp}>{formatInstant(event.timestamp)}</time>
      <StatusTag
        known
        status={event.severity}
        tone={severityTone(event.severity)}
      />
      <span className={styles.logService}>{serviceLabels[event.service]}</span>
      <code className={styles.eventCode}>{event.event_code}</code>
      <span className={styles.logDetail}>
        {event.http_method && event.route
          ? `${event.http_method} ${event.route}`
          : (event.error_type ?? "结构化运行事件")}
      </span>
      <span className={styles.logCorrelation} title={correlation ?? undefined}>
        {correlation ? shortReference(correlation) : "—"}
      </span>
    </article>
  );
}

export function PlatformOperationsPage() {
  const queryClient = useQueryClient();
  const exactPlatformCapabilities = useShellStore(
    (state) => state.platformCapabilities,
  );
  const canApproveRelease = exactPlatformCapabilities.includes(
    "platform.release.operate",
  );
  const canConfigureObjectStore =
    exactPlatformCapabilities.includes("platform.admin");
  const [form, setForm] = useState<LogFilterForm>(defaultLogFilterForm);
  const [filters, setFilters] = useState<PlatformLogFilters>(() =>
    toLogFilters(form),
  );
  const [filterError, setFilterError] = useState<string | null>(null);
  const [approvalDrafts, setApprovalDrafts] = useState<Record<string, string>>(
    {},
  );
  const [approvalError, setApprovalError] = useState<string | null>(null);

  const overview = useQuery({
    queryKey: ["platform-operations", "global", "overview"],
    queryFn: ({ signal }) => getPlatformOperationsOverview(signal),
    staleTime: 15_000,
    gcTime: 0,
    retry: 1,
  });
  const logs = useQuery({
    queryKey: ["platform-operations", "global", "logs", filters],
    queryFn: ({ signal }) => queryPlatformRuntimeLogs(filters, signal),
    staleTime: 10_000,
    gcTime: 0,
    retry: 1,
  });
  const releaseHistory = useQuery({
    queryKey: ["platform-operations", "global", "release-history"],
    queryFn: ({ signal }) => getPlatformReleaseHistory(signal),
    staleTime: 10_000,
    gcTime: 0,
    retry: 1,
  });
  const approval = useMutation({
    mutationFn: ({
      releaseId,
      stateVersion,
      reason,
    }: Readonly<{ releaseId: string; stateVersion: number; reason: string }>) =>
      approvePlatformRelease(releaseId, stateVersion, reason),
    onSuccess: async () => {
      setApprovalError(null);
      await queryClient.invalidateQueries({
        queryKey: ["platform-operations", "global", "release-history"],
      });
    },
    onError: () => {
      setApprovalError(
        "批准未写入；请确认由另一位发布操作员执行，并刷新状态修订。 ",
      );
    },
  });

  function approveRelease(releaseId: string, stateVersion: number) {
    const reason = (approvalDrafts[releaseId] ?? "").trim();
    if (reason.length < 8) {
      setApprovalError("批准理由至少需要 8 个字符。 ");
      return;
    }
    setApprovalError(null);
    approval.mutate({ releaseId, stateVersion, reason });
  }

  function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const from = new Date(form.occurredFrom);
    const to = new Date(form.occurredTo);
    if (
      !Number.isFinite(from.getTime()) ||
      !Number.isFinite(to.getTime()) ||
      from >= to
    ) {
      setFilterError("开始时间必须早于结束时间。");
      return;
    }
    if (to.getTime() - from.getTime() > 7 * 24 * 60 * 60_000) {
      setFilterError("单次日志检索窗口不能超过 7 天。");
      return;
    }
    setFilterError(null);
    setFilters(toLogFilters(form));
  }

  function refreshAll() {
    void Promise.all([
      overview.refetch(),
      logs.refetch(),
      releaseHistory.refetch(),
    ]);
  }

  const pageState = overview.isPending ? (
    <PageState state="loading" label="平台运维状态" layout="dashboard" />
  ) : overview.isError ? (
    <PageState
      state={errorState(overview.error)}
      label="平台运维状态"
      layout="dashboard"
      onRetry={() => void overview.refetch()}
    />
  ) : null;

  return (
    <StandardPageScaffold
      header={{
        title: "平台设置",
        breadcrumbs: [
          { key: "security", label: "安全与审计" },
          { key: "operations", label: "平台设置" },
        ],
        actions: (
          <Button
            icon={<RefreshCw aria-hidden="true" size={16} />}
            loading={
              overview.isFetching ||
              logs.isFetching ||
              releaseHistory.isFetching
            }
            onClick={refreshAll}
          >
            刷新状态
          </Button>
        ),
      }}
    >
      <div className={styles.pageGrid}>
        {canConfigureObjectStore ? (
          <section
            className={styles.initializationPanel}
            id="platform-initialization"
            aria-labelledby="platform-initialization-title"
          >
            <div className={styles.initializationHeading}>
              <div>
                <span className={styles.eyebrow}>FIRST-RUN SETUP</span>
                <h2 id="platform-initialization-title">平台初始化</h2>
              </div>
            </div>
            <div className={styles.initializationGrid}>
              <OrganizationInitializationPanel />
              <ProjectInitializationPanel />
              <ObjectStoreConfigurationPanel />
            </div>
          </section>
        ) : null}

        {pageState}

        {overview.data ? (
          <>
            <ReleaseIdentityPanel overview={overview.data} />
            <ReleaseGateRail preflight={overview.data.upgrade_preflight} />

            <div className={styles.signalStrip} aria-label="升级信号摘要">
              <div>
                <Server aria-hidden="true" />
                <span>活动进程</span>
                <strong>{overview.data.node_count}</strong>
              </div>
              <div>
                <Boxes aria-hidden="true" />
                <span>配置修订</span>
                <strong>
                  {overview.data.nodes[0]?.applied_config_revision ?? "—"}
                </strong>
              </div>
              <div>
                <ArchiveRestore aria-hidden="true" />
                <span>目录备份</span>
                <strong>{overview.data.backup_count}</strong>
              </div>
              <div>
                <Clock3 aria-hidden="true" />
                <span>日志窗口</span>
                <strong>≤ 7 天</strong>
              </div>
            </div>

            <div className={styles.twoColumn}>
              <NodesPanel nodes={overview.data.nodes} />
              <BackupsPanel backups={overview.data.backups} />
            </div>

            {releaseHistory.isPending ? (
              <PageState state="loading" label="升级历史" />
            ) : releaseHistory.isError ? (
              <PageState
                state={errorState(releaseHistory.error)}
                label="升级历史"
                description="当前版本状态仍可使用；升级历史暂时不可用。"
                onRetry={() => void releaseHistory.refetch()}
              />
            ) : (
              <ReleaseHistoryPanel
                approvalDrafts={approvalDrafts}
                approvalError={approvalError}
                approvingReleaseId={
                  approval.isPending ? approval.variables.releaseId : null
                }
                canApprove={canApproveRelease}
                history={releaseHistory.data}
                onApprove={approveRelease}
                onDraftChange={(releaseId, value) =>
                  setApprovalDrafts((current) => ({
                    ...current,
                    [releaseId]: value,
                  }))
                }
              />
            )}

            <section className={styles.logsPanel} aria-labelledby="logs-title">
              <div className={styles.sectionHeading}>
                <div>
                  <span className={styles.eyebrow}>CENTRAL LOG SEARCH</span>
                  <h2 id="logs-title">结构化运行日志</h2>
                </div>
                <span className={styles.safeQueryNote}>
                  <FileSearch aria-hidden="true" size={15} /> 固定过滤器 ·
                  不接受 LogQL
                </span>
              </div>

              <form className={styles.logFilters} onSubmit={submitFilters}>
                <label>
                  <span>开始时间</span>
                  <Input
                    aria-label="日志开始时间"
                    type="datetime-local"
                    value={form.occurredFrom}
                    onChange={(event) =>
                      setForm({ ...form, occurredFrom: event.target.value })
                    }
                  />
                </label>
                <label>
                  <span>结束时间</span>
                  <Input
                    aria-label="日志结束时间"
                    type="datetime-local"
                    value={form.occurredTo}
                    onChange={(event) =>
                      setForm({ ...form, occurredTo: event.target.value })
                    }
                  />
                </label>
                <label>
                  <span>服务</span>
                  <Select
                    aria-label="日志服务"
                    options={[...serviceOptions]}
                    value={form.service}
                    onChange={(value) => setForm({ ...form, service: value })}
                  />
                </label>
                <label>
                  <span>级别</span>
                  <Select
                    aria-label="日志级别"
                    options={[...severityOptions]}
                    value={form.severity}
                    onChange={(value) => setForm({ ...form, severity: value })}
                  />
                </label>
                <label>
                  <span>事件代码</span>
                  <Input
                    aria-label="日志事件代码"
                    maxLength={128}
                    placeholder="例如 PLATFORM.READY"
                    value={form.eventCode}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        eventCode: event.target.value.toUpperCase(),
                      })
                    }
                  />
                </label>
                <label className={styles.correlationField}>
                  <span>关联标识</span>
                  <span className={styles.compoundField}>
                    <Select
                      aria-label="日志关联类型"
                      options={[...correlationOptions]}
                      value={form.correlationKind}
                      onChange={(value) =>
                        setForm({ ...form, correlationKind: value })
                      }
                    />
                    <Input
                      aria-label="日志关联值"
                      maxLength={253}
                      placeholder="输入精确 ID"
                      value={form.correlationValue}
                      onChange={(event) =>
                        setForm({
                          ...form,
                          correlationValue: event.target.value,
                        })
                      }
                    />
                  </span>
                </label>
                <Button htmlType="submit" type="primary">
                  检索日志
                </Button>
              </form>
              {filterError ? (
                <p className={styles.filterError} role="alert">
                  {filterError}
                </p>
              ) : null}

              {logs.isPending ? (
                <PageState state="loading" label="结构化运行日志" />
              ) : logs.isError ? (
                <PageState
                  state={errorState(logs.error)}
                  label="结构化运行日志"
                  description="版本、节点和备份状态仍可使用；中央日志检索当前不可用。"
                  onRetry={() => void logs.refetch()}
                />
              ) : logs.data.items.length === 0 ? (
                <PageState state="filtered-empty" label="结构化运行日志" />
              ) : (
                <div className={styles.logResults} aria-live="polite">
                  <div className={styles.logSummary}>
                    <span>返回 {logs.data.count} 条结构化事件</span>
                    <span>
                      {logs.data.truncated
                        ? "结果已截断，请缩小窗口"
                        : "结果完整"}
                    </span>
                  </div>
                  <div className={styles.logHeader} aria-hidden="true">
                    <span>时间</span>
                    <span>级别</span>
                    <span>服务</span>
                    <span>事件</span>
                    <span>安全详情</span>
                    <span>关联</span>
                  </div>
                  <div className={styles.logList}>
                    {logs.data.items.map((event, index) => (
                      <LogEventRow
                        event={event}
                        key={`${event.timestamp}-${event.event_code}-${index}`}
                      />
                    ))}
                  </div>
                </div>
              )}
            </section>
          </>
        ) : null}
      </div>
    </StandardPageScaffold>
  );
}

export default PlatformOperationsPage;

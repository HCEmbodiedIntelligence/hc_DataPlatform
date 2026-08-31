import {
  Alert,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
} from "antd";
import type { ColumnDef } from "@tanstack/react-table";
import { LockKeyhole, RefreshCw, Search, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  useCreateLifecyclePolicy,
  useDeleteLifecyclePolicy,
  useEnableLifecyclePolicy,
  useLifecycleAudit,
  useLifecyclePolicies,
  usePauseLifecyclePolicy,
  useUpdateLifecyclePolicy,
} from "../../features/lifecycle/api";
import { isDomainError } from "../../shared/api/domain-error";
import type { components } from "../../shared/api/generated/platform";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import {
  ConfirmDialog,
  DataTable,
  PageState,
  StatusTag,
  type CursorPageInfo,
  type PageStateKind,
} from "../../shared/ui";
import {
  lifecycleObjectRoles,
  lifecyclePolicyStates,
  storageLifecycleQueryCodec,
  updateLifecycleSearch,
  type StorageLifecycleSearch,
} from "./query-codec";
import styles from "./styles.module.css";
import { LifecycleExecutionPanel } from "./LifecycleExecutionPanel";

type LifecyclePolicy = components["schemas"]["LifecyclePolicy"];
type LifecycleAuditEvent = components["schemas"]["LifecycleAuditEvent"];
type LifecyclePolicyAction = components["schemas"]["LifecyclePolicyAction"];
type LifecyclePolicyCommand = components["schemas"]["CreateLifecyclePolicy"];
type ObjectRole = components["schemas"]["ObjectRole"];

const categoryLabels = {
  RAW: "Raw",
  ANNOTATION_COMPLETE: "标注完成",
  PENDING_ANNOTATION: "待标注",
  ISSUE_DATA: "问题数据",
} as const;

const roleLabels: Record<ObjectRole, string> = {
  RAW: "Raw",
  MANIFEST: "数据清单",
  PUBLISHED_MANIFEST: "已发布数据清单",
  REBUILDABLE_DERIVATIVE: "可重建衍生物",
  OTHER: "其他",
};

const actionLabels: Record<LifecyclePolicyAction, string> = {
  RETAIN: "保留",
  REVIEW_EXPIRATION: "到期复核",
  ARCHIVE: "归档",
  TRANSITION_TO_COLD: "迁移到冷存储",
  CLEAN_REBUILDABLE_CACHE: "清理可重建缓存",
};

const stateLabels = {
  DRAFT: "草稿",
  ENABLED: "已启用",
  PAUSED: "已暂停",
} as const;
const stateOptions = lifecyclePolicyStates.map((value) => ({
  value,
  label: value === "ALL" ? "全部状态" : stateLabels[value],
}));
const roleOptions = lifecycleObjectRoles.map((value) => ({
  value,
  label: value === "ALL" ? "全部对象" : roleLabels[value],
}));
const categoryOptions = Object.entries(categoryLabels).map(
  ([value, label]) => ({ value, label }),
);
const actionOptions = Object.entries(actionLabels).map(([value, label]) => ({
  value,
  label,
}));
const formRoleOptions = Object.entries(roleLabels).map(([value, label]) => ({
  value,
  label,
}));
const protectedRoles = new Set<ObjectRole>([
  "RAW",
  "MANIFEST",
  "PUBLISHED_MANIFEST",
]);

interface LifecycleCursorPagerProps {
  pageInfo: CursorPageInfo;
  busy: boolean;
  label: string;
  windowLabel: string;
  onChange: (cursor: string) => void;
}

function LifecycleCursorPager({
  pageInfo,
  busy,
  label,
  windowLabel,
  onChange,
}: Readonly<LifecycleCursorPagerProps>) {
  const previousCursor =
    pageInfo.hasPreviousPage && pageInfo.startCursor
      ? pageInfo.startCursor
      : null;
  const nextCursor =
    pageInfo.hasNextPage && pageInfo.endCursor ? pageInfo.endCursor : null;

  return (
    <nav
      className={styles.cursorPager}
      aria-label={label}
      data-pagination-contract="after-before"
    >
      <Button
        size="small"
        disabled={busy || previousCursor === null}
        onClick={() => previousCursor && onChange(previousCursor)}
      >
        上一组
      </Button>
      <span aria-live="polite">{windowLabel}</span>
      <Button
        size="small"
        disabled={busy || nextCursor === null}
        onClick={() => nextCursor && onChange(nextCursor)}
      >
        下一组
      </Button>
    </nav>
  );
}

function stateFromError(error: unknown): PageStateKind {
  if (!isDomainError(error)) return "contract-mismatch";
  switch (String(error.code)) {
    case "AUTHENTICATION_REQUIRED":
    case "CAPABILITY_REQUIRED":
    case "PROJECT_SCOPE_DENIED":
    case "SERVICE_SCOPE_REQUIRED":
    case "FORBIDDEN":
    case "UNAUTHENTICATED":
      return "forbidden";
    case "NOT_FOUND":
      return "not-found";
    case "GONE":
      return "gone";
    case "INVALID_CURSOR":
    case "VERSION_CONFLICT":
    case "PRECONDITION_FAILED":
      return "conflict";
    case "RATE_LIMITED":
      return "rate-limited";
    case "NETWORK_ERROR":
      return "offline";
    case "CONTRACT_MISMATCH":
      return "contract-mismatch";
    default:
      return "error";
  }
}

function requestId(error: unknown): string | null {
  return isDomainError(error) ? error.requestId : null;
}

function mutationErrorMessage(error: unknown): string {
  if (!isDomainError(error)) return "操作未完成，服务端状态没有被乐观推进。";
  if (error.code === "PRECONDITION_FAILED")
    return "策略已被其他操作更新，请刷新后重试。";
  if (error.code === "VERSION_CONFLICT")
    return "策略名称、启用目标或幂等键发生冲突。";
  return error.message;
}

function toPageInfo(
  pageInfo: components["schemas"]["PageInfo"],
): CursorPageInfo {
  return {
    hasNextPage: pageInfo.has_next_page,
    hasPreviousPage: pageInfo.has_previous_page,
    startCursor: pageInfo.start_cursor ?? null,
    endCursor: pageInfo.end_cursor ?? null,
  };
}

type ConfirmAction = Readonly<{
  kind: "enable" | "pause" | "delete";
  policy: LifecyclePolicy;
}>;

interface PolicyFormValues extends LifecyclePolicyCommand {}

export function LifecycleProtectionSummary() {
  const protectedObjects = [
    { key: "raw", label: "Raw", detail: "源数据不可物理清理" },
    { key: "manifest", label: "数据清单", detail: "采集事实永久保留" },
    { key: "published", label: "已发布数据清单", detail: "发布血缘永久保留" },
  ] as const;

  return (
    <section
      className={styles.protectionSummary}
      aria-labelledby="lifecycle-protection-title"
    >
      <div className={styles.protectionHeading}>
        <ShieldCheck aria-hidden="true" size={17} />
        <h3 id="lifecycle-protection-title">保护状态</h3>
        <span>生产执行需独立审批</span>
      </div>
      <div className={styles.protectionItems}>
        {protectedObjects.map((item) => (
          <div className={styles.protectionItem} key={item.key}>
            <LockKeyhole aria-hidden="true" size={15} />
            <span>
              <strong>{item.label}</strong>
              <small>{item.detail}</small>
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function PolicyTarget({ policy }: Readonly<{ policy: LifecyclePolicy }>) {
  return (
    <span className={styles.cellStack}>
      <span>{categoryLabels[policy.business_category]}</span>
      <small>{roleLabels[policy.object_role]}</small>
      <ProtectionState role={policy.object_role} />
    </span>
  );
}

function ProtectionState({ role }: Readonly<{ role: ObjectRole }>) {
  const protectedTarget = protectedRoles.has(role);
  return (
    <span
      className={styles.protectionCell}
      data-protected={protectedTarget || undefined}
    >
      {protectedTarget ? <LockKeyhole aria-hidden="true" size={14} /> : null}
      {protectedTarget ? "永久保护" : "受策略约束"}
    </span>
  );
}

interface PolicyTableProps {
  items: readonly LifecyclePolicy[];
  canManage: boolean;
  refreshing: boolean;
  onEdit: (policy: LifecyclePolicy) => void;
  onConfirm: (action: ConfirmAction) => void;
}

export function LifecyclePolicyTable({
  items,
  canManage,
  refreshing,
  onEdit,
  onConfirm,
}: Readonly<PolicyTableProps>) {
  const columns = useMemo<readonly ColumnDef<LifecyclePolicy, unknown>[]>(
    () => [
      {
        id: "name",
        header: "策略名称 / ID",
        size: 126,
        cell: ({ row }) => (
          <span className={styles.cellStack}>
            <strong>{row.original.name}</strong>
            <code>{row.original.policy_id}</code>
          </span>
        ),
      },
      {
        id: "target",
        header: "目标 / 保护",
        size: 124,
        cell: ({ row }) => <PolicyTarget policy={row.original} />,
      },
      {
        id: "rule",
        header: "动作 / 规则",
        size: 108,
        cell: ({ row }) => (
          <span className={styles.cellStack}>
            <span>{actionLabels[row.original.action]}</span>
            <small>
              ≥ {row.original.minimum_age_days} 天 · P{row.original.priority} ·
              v{row.original.version}
            </small>
          </span>
        ),
      },
      {
        id: "state",
        header: "状态",
        size: 70,
        cell: ({ row }) => (
          <StatusTag
            status={row.original.state}
            label={stateLabels[row.original.state]}
            tone={
              row.original.state === "ENABLED"
                ? "success"
                : row.original.state === "PAUSED"
                  ? "warning"
                  : "neutral"
            }
          />
        ),
      },
      {
        id: "actions",
        header: "操作",
        size: 168,
        cell: ({ row }) => (
          <Space className={styles.policyActions} size={4} wrap={false}>
            <Button
              size="small"
              aria-label="编辑策略"
              disabled={!canManage}
              onClick={() => onEdit(row.original)}
            >
              编辑
            </Button>
            {row.original.state === "ENABLED" ? (
              <Button
                size="small"
                aria-label="暂停策略"
                disabled={!canManage}
                onClick={() =>
                  onConfirm({ kind: "pause", policy: row.original })
                }
              >
                暂停
              </Button>
            ) : (
              <Button
                size="small"
                type="primary"
                aria-label="启用策略"
                disabled={!canManage}
                onClick={() =>
                  onConfirm({ kind: "enable", policy: row.original })
                }
              >
                启用
              </Button>
            )}
            <Button
              size="small"
              danger
              aria-label="删除策略"
              disabled={!canManage || row.original.state === "ENABLED"}
              title={
                row.original.state === "ENABLED"
                  ? "已启用策略必须先暂停"
                  : undefined
              }
              onClick={() =>
                onConfirm({ kind: "delete", policy: row.original })
              }
            >
              删除
            </Button>
          </Space>
        ),
      },
    ],
    [canManage, onConfirm, onEdit],
  );

  return (
    <DataTable
      data={items}
      columns={columns}
      getRowId={(policy) => policy.policy_id}
      caption="生命周期策略列表"
      state={refreshing ? "loading" : undefined}
    />
  );
}

function AuditIdentity({ event }: Readonly<{ event: LifecycleAuditEvent }>) {
  return (
    <span className={styles.cellStack}>
      <code>{event.policy_id}</code>
      <small>操作者 {event.actor_id}</small>
      <small>请求 {event.request_id}</small>
    </span>
  );
}

function AuditChange({ event }: Readonly<{ event: LifecycleAuditEvent }>) {
  return (
    <span className={styles.cellStack}>
      <span>
        {event.before_digest ?? "—"} → {event.after_digest ?? "—"}
      </span>
      <small>
        {String(event.details?.state ?? "状态未投影")} · v
        {String(event.details?.version ?? "—")}
      </small>
    </span>
  );
}

function LifecycleAuditTable({
  items,
  refreshing,
}: Readonly<{
  items: readonly LifecycleAuditEvent[];
  refreshing: boolean;
}>) {
  const columns = useMemo<readonly ColumnDef<LifecycleAuditEvent, unknown>[]>(
    () => [
      {
        id: "time",
        header: "时间",
        size: 116,
        cell: ({ row }) => (
          <time dateTime={row.original.occurred_at ?? ""} tabIndex={0}>
            {row.original.occurred_at
              ? new Date(row.original.occurred_at).toLocaleString("zh-CN")
              : "—"}
          </time>
        ),
      },
      {
        id: "action",
        header: "事件",
        size: 142,
        cell: ({ row }) => <code>{row.original.action}</code>,
      },
      {
        id: "identity",
        header: "策略 / 操作者 / 请求",
        size: 166,
        cell: ({ row }) => <AuditIdentity event={row.original} />,
      },
      {
        id: "change",
        header: "变更指纹",
        size: 176,
        cell: ({ row }) => <AuditChange event={row.original} />,
      },
    ],
    [],
  );

  return (
    <DataTable
      data={items}
      columns={columns}
      getRowId={(event) => event.audit_id}
      caption="生命周期变更审计"
      state={refreshing ? "loading" : undefined}
    />
  );
}

interface LifecycleFilterBarProps {
  search: StorageLifecycleSearch;
  disabled: boolean;
  refreshing: boolean;
  onChange: (patch: Partial<StorageLifecycleSearch>) => void;
  onRefresh: () => void;
}

export function LifecycleFilterBar({
  search,
  disabled,
  refreshing,
  onChange,
  onRefresh,
}: Readonly<LifecycleFilterBarProps>) {
  return (
    <form
      className={styles.filterBar}
      role="search"
      aria-label="生命周期策略筛选"
      onSubmit={(event) => event.preventDefault()}
    >
      <label className={styles.searchField}>
        <span className={styles.srOnly}>搜索策略名称或 ID</span>
        <Input
          autoComplete="off"
          name="lifecycle-policy-search"
          size="small"
          allowClear
          prefix={<Search aria-hidden="true" size={15} />}
          placeholder="搜索策略名称或 ID…"
          value={search.query}
          disabled={disabled}
          onChange={(event) => onChange({ query: event.target.value })}
        />
      </label>
      <Select
        size="small"
        aria-label="策略状态"
        value={search.state}
        options={stateOptions}
        disabled={disabled}
        onChange={(state) => onChange({ state })}
      />
      <Select
        size="small"
        aria-label="策略对象角色"
        value={search.role}
        options={roleOptions}
        disabled={disabled}
        onChange={(role) => onChange({ role })}
      />
      <span className={styles.localFilterNote}>当前窗口筛选</span>
      <Button
        size="small"
        icon={<RefreshCw aria-hidden="true" size={15} />}
        aria-label="刷新策略与审计"
        disabled={disabled}
        loading={refreshing}
        onClick={onRefresh}
      >
        刷新
      </Button>
    </form>
  );
}

export function LifecyclePane() {
  const [params, setParams] = useSearchParams();
  const search = useMemo(
    () => storageLifecycleQueryCodec.parse(params),
    [params],
  );
  const capabilities = useCapabilities();
  const scopeKey = useShellStore((state) => state.scopeKey);
  const projectId = useShellStore((state) => state.scope?.projectId ?? null);
  const canRead = capabilities.has("storage.lifecycle.read");
  const canManage = capabilities.has("storage.lifecycle.manage");
  const policies = useLifecyclePolicies(
    search.policyCursor,
    search.limit,
    canRead,
  );
  const audit = useLifecycleAudit(search.auditCursor, search.limit, canRead);
  const createPolicy = useCreateLifecyclePolicy();
  const updatePolicy = useUpdateLifecyclePolicy();
  const enablePolicy = useEnableLifecyclePolicy();
  const pausePolicy = usePauseLifecyclePolicy();
  const deletePolicy = useDeleteLifecyclePolicy();
  const [form] = Form.useForm<PolicyFormValues>();
  const selectedAction = Form.useWatch("action", form);
  const [editing, setEditing] = useState<LifecyclePolicy | "create" | null>(
    null,
  );
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(
    null,
  );
  const previousScope = useRef(scopeKey);

  useEffect(() => {
    if (previousScope.current === scopeKey) return;
    previousScope.current = scopeKey;
    if (search.policyCursor || search.auditCursor) {
      setParams(
        storageLifecycleQueryCodec.build({
          ...search,
          policyCursor: undefined,
          auditCursor: undefined,
        }),
        { replace: true },
      );
    }
  }, [scopeKey, search, setParams]);

  const setSearch = useCallback(
    (patch: Partial<StorageLifecycleSearch>) => {
      setParams(
        storageLifecycleQueryCodec.build(updateLifecycleSearch(search, patch)),
      );
    },
    [search, setParams],
  );

  const openCreate = useCallback(() => {
    form.resetFields();
    form.setFieldsValue({
      name: "",
      business_category: "RAW",
      object_role: "RAW",
      action: "RETAIN",
      minimum_age_days: 30,
      priority: 100,
    });
    setEditing("create");
  }, [form]);

  const openEdit = useCallback(
    (policy: LifecyclePolicy) => {
      form.resetFields();
      form.setFieldsValue({
        name: policy.name,
        business_category: policy.business_category,
        object_role: policy.object_role,
        action: policy.action,
        minimum_age_days: policy.minimum_age_days,
        priority: policy.priority,
      });
      setEditing(policy);
    },
    [form],
  );

  const submitPolicy = (command: PolicyFormValues) => {
    if (
      command.action === "CLEAN_REBUILDABLE_CACHE" &&
      command.object_role !== "REBUILDABLE_DERIVATIVE"
    ) {
      form.setFields([
        { name: "object_role", errors: ["清理策略只能指向可重建衍生物"] },
      ]);
      return;
    }
    if (editing === "create") {
      createPolicy.mutate(
        { command, idempotencyKey: crypto.randomUUID() },
        {
          onSuccess: () => setEditing(null),
        },
      );
      return;
    }
    if (editing) {
      updatePolicy.mutate(
        {
          policyId: editing.policy_id,
          etag: editing.etag,
          command,
          idempotencyKey: crypto.randomUUID(),
        },
        { onSuccess: () => setEditing(null) },
      );
    }
  };

  const confirmMutation = () => {
    if (!confirmAction) return;
    const intent = {
      policyId: confirmAction.policy.policy_id,
      etag: confirmAction.policy.etag,
      idempotencyKey: crypto.randomUUID(),
    };
    const options = { onSettled: () => setConfirmAction(null) };
    if (confirmAction.kind === "enable") enablePolicy.mutate(intent, options);
    if (confirmAction.kind === "pause") pausePolicy.mutate(intent, options);
    if (confirmAction.kind === "delete") deletePolicy.mutate(intent, options);
  };

  const filteredPolicies = useMemo(() => {
    const query = search.query.toLocaleLowerCase();
    return (policies.data?.items ?? []).filter(
      (policy) =>
        (!query ||
          policy.name.toLocaleLowerCase().includes(query) ||
          policy.policy_id.toLocaleLowerCase().includes(query)) &&
        (search.state === "ALL" || policy.state === search.state) &&
        (search.role === "ALL" || policy.object_role === search.role),
    );
  }, [policies.data?.items, search.query, search.role, search.state]);

  const gateState: PageStateKind | null = capabilities.loading
    ? "loading"
    : capabilities.failed || !canRead
      ? "forbidden"
      : !projectId
        ? "feature-unavailable"
        : null;
  const policyState: PageStateKind | "ready" =
    gateState ??
    (policies.isPending
      ? "loading"
      : policies.isError
        ? stateFromError(policies.error)
        : !policies.data || policies.data.items.length === 0
          ? "empty"
          : filteredPolicies.length === 0
            ? "filtered-empty"
            : "ready");
  const auditState: PageStateKind | "ready" =
    gateState ??
    (audit.isPending
      ? "loading"
      : audit.isError
        ? stateFromError(audit.error)
        : !audit.data || audit.data.items.length === 0
          ? "empty"
          : "ready");
  const partial =
    (policyState === "ready" && audit.isError) ||
    (auditState === "ready" && policies.isError);
  const refreshing = policies.isFetching || audit.isFetching;
  const mutationError =
    createPolicy.error ??
    updatePolicy.error ??
    enablePolicy.error ??
    pausePolicy.error ??
    deletePolicy.error;
  const confirmPending =
    enablePolicy.isPending || pausePolicy.isPending || deletePolicy.isPending;

  const refreshAll = useCallback(() => {
    void Promise.all([policies.refetch(), audit.refetch()]);
  }, [audit, policies]);

  return (
    <section
      className={styles.lifecyclePane}
      aria-labelledby="lifecycle-pane-title"
    >
      <header className={styles.paneHeader}>
        <div>
          <h1 id="lifecycle-pane-title">生命周期策略</h1>
        </div>
        <div className={styles.headerActions}>
          <span id="lifecycle-permission" className={styles.permissionState}>
            <StatusTag
              status={canManage ? "MANAGE" : "READ_ONLY"}
              label={
                canManage
                  ? "可管理"
                  : capabilities.loading
                    ? "权限检查中"
                    : "只读权限"
              }
              tone={canManage ? "success" : "neutral"}
            />
          </span>
          <Button
            type="primary"
            size="small"
            disabled={!canManage || !projectId}
            aria-describedby="lifecycle-permission"
            onClick={openCreate}
          >
            新建策略
          </Button>
        </div>
      </header>

      <div className={styles.lifecycleBody}>
        <LifecycleProtectionSummary />
        <LifecycleFilterBar
          search={search}
          disabled={gateState !== null}
          refreshing={refreshing}
          onChange={setSearch}
          onRefresh={refreshAll}
        />

        {partial ? (
          <Alert
            className={styles.partialAlert}
            type="warning"
            showIcon
            title="生命周期数据部分加载失败"
            description="已保留成功加载的区域，请重试失败区域。"
          />
        ) : null}

        <section
          className={styles.dataSection}
          aria-labelledby="policy-list-title"
        >
          <div className={styles.sectionHeading}>
            <h3 id="policy-list-title">策略列表</h3>
            <span>{policies.data?.items.length ?? 0} 条 · 当前窗口</span>
          </div>
          {policyState === "ready" ? (
            <LifecyclePolicyTable
              items={filteredPolicies}
              canManage={canManage}
              refreshing={policies.isFetching && policies.data !== undefined}
              onEdit={openEdit}
              onConfirm={setConfirmAction}
            />
          ) : (
            <PageState
              state={policyState}
              label="生命周期策略列表"
              requestId={requestId(policies.error)}
              onRetry={
                policies.isError ? () => void policies.refetch() : undefined
              }
            />
          )}
          {policies.data && policies.data.items.length > 0 ? (
            <LifecycleCursorPager
              pageInfo={toPageInfo(policies.data.page_info)}
              busy={policies.isFetching}
              label="策略列表游标分页"
              windowLabel={`策略窗口 ${policies.data.items.length} 条`}
              onChange={(cursor) => setSearch({ policyCursor: cursor })}
            />
          ) : null}
        </section>

        <LifecycleExecutionPanel
          policies={policies.data?.items ?? []}
          enabled={gateState === null}
        />

        <section
          className={styles.dataSection}
          aria-labelledby="audit-list-title"
        >
          <div className={styles.sectionHeading}>
            <h3 id="audit-list-title">变更审计</h3>
          </div>
          {auditState === "ready" ? (
            <LifecycleAuditTable
              items={audit.data?.items ?? []}
              refreshing={audit.isFetching && audit.data !== undefined}
            />
          ) : (
            <PageState
              state={auditState}
              label="生命周期变更审计"
              requestId={requestId(audit.error)}
              onRetry={audit.isError ? () => void audit.refetch() : undefined}
            />
          )}
          {audit.data && audit.data.items.length > 0 ? (
            <LifecycleCursorPager
              pageInfo={toPageInfo(audit.data.page_info)}
              busy={audit.isFetching}
              label="变更审计游标分页"
              windowLabel={`审计窗口 ${audit.data.items.length} 条`}
              onChange={(cursor) => setSearch({ auditCursor: cursor })}
            />
          ) : null}
        </section>

        {mutationError ? (
          <Alert
            className={styles.operationAlert}
            type="error"
            showIcon
            title="策略操作未完成"
            description={mutationErrorMessage(mutationError)}
          />
        ) : null}
      </div>

      <Modal
        open={editing !== null}
        title={editing === "create" ? "新建生命周期策略" : "编辑生命周期策略"}
        footer={null}
        destroyOnHidden
        onCancel={() => setEditing(null)}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={submitPolicy}
          requiredMark="optional"
        >
          <Form.Item
            name="name"
            label="策略名称"
            rules={[
              { required: true, message: "请输入策略名称" },
              { max: 256 },
            ]}
          >
            <Input autoComplete="off" name="lifecycle-policy-name" />
          </Form.Item>
          <div className={styles.formGrid}>
            <Form.Item
              name="business_category"
              label="业务容量分类"
              rules={[{ required: true }]}
            >
              <Select options={categoryOptions} />
            </Form.Item>
            <Form.Item
              name="action"
              label="策略动作"
              rules={[{ required: true }]}
            >
              <Select
                options={actionOptions}
                onChange={(value: LifecyclePolicyAction) => {
                  if (value === "CLEAN_REBUILDABLE_CACHE")
                    form.setFieldValue("object_role", "REBUILDABLE_DERIVATIVE");
                }}
              />
            </Form.Item>
            <Form.Item
              name="object_role"
              label="对象角色"
              rules={[{ required: true }]}
            >
              <Select
                options={formRoleOptions.map((option) => ({
                  ...option,
                  disabled:
                    selectedAction === "CLEAN_REBUILDABLE_CACHE" &&
                    option.value !== "REBUILDABLE_DERIVATIVE",
                }))}
              />
            </Form.Item>
            <Form.Item
              name="minimum_age_days"
              label="最小年龄（天）"
              rules={[{ required: true }]}
            >
              <InputNumber
                min={0}
                max={36_500}
                precision={0}
                className={styles.fullWidth}
              />
            </Form.Item>
            <Form.Item
              name="priority"
              label="优先级"
              rules={[{ required: true }]}
            >
              <InputNumber
                min={0}
                max={10_000}
                precision={0}
                className={styles.fullWidth}
              />
            </Form.Item>
          </div>
          {selectedAction === "CLEAN_REBUILDABLE_CACHE" ? (
            <Alert
              className={styles.formAlert}
              type="warning"
              showIcon
              title="仅可清理已验证可重建的缓存对象"
            />
          ) : null}
          <Space className={styles.formActions}>
            <Button onClick={() => setEditing(null)}>取消</Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={createPolicy.isPending || updatePolicy.isPending}
            >
              保存策略
            </Button>
          </Space>
        </Form>
      </Modal>

      <ConfirmDialog
        open={confirmAction !== null}
        title={
          confirmAction?.kind === "enable"
            ? "确认启用策略"
            : confirmAction?.kind === "pause"
              ? "确认暂停策略"
              : "确认删除策略"
        }
        resourceId={confirmAction?.policy.policy_id ?? "unknown"}
        impact={
          confirmAction?.kind === "enable"
            ? "启用规则调度状态；不会从本页面触发生产物理执行。"
            : confirmAction?.kind === "pause"
              ? "停止后续规则调度；现有审计记录保留。"
              : "删除草稿或已暂停策略；审计记录仍永久保留。"
        }
        confirmLabel={
          confirmAction?.kind === "enable"
            ? "确认启用"
            : confirmAction?.kind === "pause"
              ? "确认暂停"
              : "确认删除"
        }
        pending={confirmPending}
        onCancel={() => setConfirmAction(null)}
        onConfirm={confirmMutation}
      />
    </section>
  );
}

export function LifecyclePage() {
  return (
    <div className={styles.page} data-page-id="P13">
      <LifecyclePane />
    </div>
  );
}

export const Component = LifecyclePage;
export default LifecyclePage;

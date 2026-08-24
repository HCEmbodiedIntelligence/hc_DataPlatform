import {
  Alert,
  Button,
  Dropdown,
  Form,
  Input,
  Modal,
  Pagination,
  Select,
  Space,
  Table,
  Tag,
  type MenuProps,
  type TableColumnsType,
} from "antd";
import {
  MoreHorizontal,
  LockKeyholeOpen,
  MonitorSmartphone,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  UserRoundCheck,
  Users,
} from "lucide-react";
import { useMemo, useState } from "react";
import { isDomainError } from "../../shared/api/domain-error";
import { PageState } from "../../shared/ui";
import type {
  ManagedAccount,
  ManagedAccountCreate,
  ManagedAccountPage,
  PlatformAccountRole,
} from "./contracts";
import type { AccessSearch } from "./query-codec";
import styles from "./styles.module.css";

type ConfirmAction =
  | "disable"
  | "enable"
  | "promote"
  | "demote"
  | "unlock"
  | "delete";

interface ConfirmDialogState {
  readonly action: ConfirmAction;
  readonly account: ManagedAccount;
}

interface ResetDialogState {
  readonly account: ManagedAccount;
}

interface CreateFormValues {
  readonly username: string;
  readonly display_name?: string;
  readonly password: string;
  readonly confirm_password: string;
  readonly recovery_email?: string;
  readonly platform_role: PlatformAccountRole;
}

interface ResetFormValues {
  readonly new_password: string;
  readonly confirm_password: string;
}

export interface UserManagementPanelProps {
  readonly search: AccessSearch;
  readonly page: ManagedAccountPage | undefined;
  readonly loading: boolean;
  readonly fetching: boolean;
  readonly error: unknown;
  readonly mutationPending: boolean;
  readonly mutationError: unknown;
  readonly successMessage?: string;
  readonly canManage: boolean;
  readonly canUnlock: boolean;
  readonly currentPrincipalId: string | null;
  readonly onSearchChange: (patch: Partial<AccessSearch>) => void;
  readonly onRefresh: () => void;
  readonly onCreate: (command: ManagedAccountCreate) => Promise<void>;
  readonly onStatusChange: (
    account: ManagedAccount,
    enabled: boolean,
  ) => Promise<void>;
  readonly onRoleChange: (
    account: ManagedAccount,
    role: PlatformAccountRole,
  ) => Promise<void>;
  readonly onResetPassword: (
    account: ManagedAccount,
    newPassword: string,
    confirmPassword: string,
  ) => Promise<void>;
  readonly onDelete: (account: ManagedAccount) => Promise<void>;
  readonly onUnlock: (account: ManagedAccount) => Promise<void>;
  readonly onDismissStatus: () => void;
}

const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function formatDate(value: string): string {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp)
    ? dateFormatter.format(new Date(timestamp))
    : "—";
}

function stateTag(account: ManagedAccount) {
  if (account.state === "ACTIVE") return <Tag color="success">正常</Tag>;
  if (account.state === "DISABLED") return <Tag color="warning">已禁用</Tag>;
  return <Tag>已删除</Tag>;
}

function roleTag(account: ManagedAccount) {
  return account.platform_role === "PLATFORM_ADMIN" ? (
    <Tag color="processing" icon={<ShieldCheck aria-hidden="true" size={13} />}>
      平台管理员
    </Tag>
  ) : (
    <Tag>普通用户</Tag>
  );
}

function actionCopy(action: ConfirmAction, account: ManagedAccount) {
  const target = account.display_name || account.username;
  switch (action) {
    case "disable":
      return {
        title: `禁用 ${target}`,
        description: "该用户的所有登录会话会立即失效，之后可由管理员恢复。",
        confirm: "确认禁用",
        danger: true,
      };
    case "enable":
      return {
        title: `恢复 ${target}`,
        description: "恢复后该用户可以重新登录，已撤销的旧会话不会恢复。",
        confirm: "确认恢复",
        danger: false,
      };
    case "promote":
      return {
        title: `设为平台管理员`,
        description: `${target} 将能查看和管理全平台用户，请确认这是必要授权。`,
        confirm: "确认授权",
        danger: false,
      };
    case "demote":
      return {
        title: `取消平台管理员`,
        description: `${target} 将失去全平台用户管理能力，项目内权限不受影响。`,
        confirm: "确认取消",
        danger: true,
      };
    case "unlock":
      return {
        title: `解除 ${target} 的临时登录锁定`,
        description:
          "仅清除滥用防护产生的当前临时锁定；不会启用已禁用或已删除的账号，也不会修改密码或恢复会话。重复操作是安全的。",
        confirm: "确认解锁",
        danger: false,
      };
    case "delete":
      return {
        title: `删除 ${target}`,
        description:
          "账号将被软删除且会话立即失效；历史数据、归属关系和审计记录会保留。",
        confirm: "确认删除",
        danger: true,
      };
  }
}

export function UserManagementPanel({
  canManage,
  canUnlock,
  currentPrincipalId,
  error,
  fetching,
  loading,
  mutationError,
  mutationPending,
  onCreate,
  onDelete,
  onDismissStatus,
  onRefresh,
  onResetPassword,
  onRoleChange,
  onSearchChange,
  onStatusChange,
  onUnlock,
  page,
  search,
  successMessage,
}: Readonly<UserManagementPanelProps>) {
  const [createOpen, setCreateOpen] = useState(false);
  const [confirmDialog, setConfirmDialog] = useState<ConfirmDialogState | null>(
    null,
  );
  const [resetDialog, setResetDialog] = useState<ResetDialogState | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [createForm] = Form.useForm<CreateFormValues>();
  const [resetForm] = Form.useForm<ResetFormValues>();

  const items = page?.items ?? [];
  const activeCount = items.filter((item) => item.state === "ACTIVE").length;
  const adminCount = items.filter(
    (item) => item.platform_role === "PLATFORM_ADMIN",
  ).length;
  const sessionCount = items.reduce(
    (count, item) => count + item.active_session_count,
    0,
  );

  const runConfirmedAction = async () => {
    if (confirmDialog === null) return;
    const { account, action } = confirmDialog;
    try {
      if (action === "disable") await onStatusChange(account, false);
      if (action === "enable") await onStatusChange(account, true);
      if (action === "promote") await onRoleChange(account, "PLATFORM_ADMIN");
      if (action === "demote") await onRoleChange(account, "USER");
      if (action === "unlock") await onUnlock(account);
      if (action === "delete") await onDelete(account);
    } catch {
      return;
    }
    setConfirmDialog(null);
    setDeleteConfirmation("");
  };

  const columns = useMemo<TableColumnsType<ManagedAccount>>(
    () => [
      {
        title: "用户",
        key: "identity",
        width: 230,
        render: (_, account) => (
          <div className={styles.userIdentity}>
            <strong>{account.display_name}</strong>
            <span>@{account.username}</span>
          </div>
        ),
      },
      {
        title: "平台角色",
        dataIndex: "platform_role",
        width: 145,
        render: (_, account) => roleTag(account),
      },
      {
        title: "状态",
        dataIndex: "state",
        width: 100,
        render: (_, account) => stateTag(account),
      },
      {
        title: "恢复邮箱",
        dataIndex: "recovery_email_hint",
        width: 190,
        render: (value: string | null) => value ?? "未配置",
      },
      {
        title: "活动会话",
        dataIndex: "active_session_count",
        width: 105,
        align: "right",
      },
      {
        title: "最近更新",
        dataIndex: "updated_at",
        width: 165,
        render: (value: string) => formatDate(value),
      },
      {
        title: "操作",
        key: "actions",
        width: 90,
        fixed: "right",
        render: (_, account) => {
          const isSelf = account.principal_id === currentPrincipalId;
          const menuItems: MenuProps["items"] = [
            {
              key: "reset",
              label: isSelf ? "重置密码（请前往账户设置）" : "重置密码",
              disabled: !canManage || isSelf || account.state === "DELETED",
              onClick: () => {
                resetForm.resetFields();
                setResetDialog({ account });
              },
            },
            {
              key: "status",
              label: account.state === "ACTIVE" ? "禁用用户" : "恢复用户",
              disabled: !canManage || isSelf || account.state === "DELETED",
              onClick: () =>
                setConfirmDialog({
                  account,
                  action: account.state === "ACTIVE" ? "disable" : "enable",
                }),
            },
            {
              key: "role",
              label:
                account.platform_role === "PLATFORM_ADMIN"
                  ? "取消平台管理员"
                  : "设为平台管理员",
              disabled: !canManage || isSelf || account.state === "DELETED",
              onClick: () =>
                setConfirmDialog({
                  account,
                  action:
                    account.platform_role === "PLATFORM_ADMIN"
                      ? "demote"
                      : "promote",
                }),
            },
            {
              key: "unlock",
              label: "解除临时锁定",
              icon: <LockKeyholeOpen aria-hidden="true" size={15} />,
              disabled: !canUnlock || account.state !== "ACTIVE",
              onClick: () => setConfirmDialog({ account, action: "unlock" }),
            },
            { type: "divider" },
            {
              key: "delete",
              label: "删除用户",
              danger: true,
              disabled: !canManage || isSelf || account.state === "DELETED",
              onClick: () => {
                setDeleteConfirmation("");
                setConfirmDialog({ account, action: "delete" });
              },
            },
          ];
          return (
            <Dropdown
              menu={{ items: menuItems }}
              placement="bottomRight"
              trigger={["click"]}
              disabled={!canManage && !canUnlock}
            >
              <Button
                type="text"
                aria-label={`管理用户 ${account.display_name}`}
                icon={<MoreHorizontal aria-hidden="true" size={18} />}
              />
            </Dropdown>
          );
        },
      },
    ],
    [canManage, canUnlock, currentPrincipalId, resetForm],
  );

  if (loading && page === undefined) {
    return <PageState state="loading" label="用户列表" layout="list" />;
  }
  if (error && page === undefined) {
    return (
      <PageState
        state={
          isDomainError(error) && error.code === "FORBIDDEN"
            ? "forbidden"
            : "error"
        }
        label="用户列表"
        description={isDomainError(error) ? error.message : undefined}
        requestId={isDomainError(error) ? error.requestId : null}
        onRetry={onRefresh}
      />
    );
  }

  const copy = confirmDialog
    ? actionCopy(confirmDialog.action, confirmDialog.account)
    : null;
  const deleteConfirmed =
    confirmDialog?.action !== "delete" ||
    deleteConfirmation === confirmDialog.account.username;
  const mutationErrorDescription = mutationError
    ? isDomainError(mutationError)
      ? mutationError.message
      : "服务暂时无法完成该用户操作，请重试。"
    : null;

  return (
    <section className={styles.userManagement} aria-label="平台用户管理">
      <div
        className={styles.securityRail}
        role="group"
        aria-label="当前页用户安全概览"
      >
        <div>
          <Users aria-hidden="true" size={18} />
          <span>当前页用户</span>
          <strong>{items.length}</strong>
        </div>
        <div>
          <UserRoundCheck aria-hidden="true" size={18} />
          <span>正常</span>
          <strong>{activeCount}</strong>
        </div>
        <div>
          <ShieldCheck aria-hidden="true" size={18} />
          <span>平台管理员</span>
          <strong>{adminCount}</strong>
        </div>
        <div>
          <MonitorSmartphone aria-hidden="true" size={18} />
          <span>活动会话</span>
          <strong>{sessionCount}</strong>
        </div>
        <Button
          type="primary"
          icon={<Plus aria-hidden="true" size={17} />}
          disabled={!canManage}
          onClick={() => {
            onDismissStatus();
            createForm.resetFields();
            setCreateOpen(true);
          }}
        >
          创建用户
        </Button>
      </div>

      {successMessage ? (
        <Alert
          type="success"
          showIcon
          closable
          role="status"
          title={successMessage}
          onClose={onDismissStatus}
        />
      ) : null}
      {mutationError ? (
        <Alert
          type="error"
          showIcon
          closable
          role="alert"
          title="操作未完成"
          description={mutationErrorDescription}
          onClose={onDismissStatus}
        />
      ) : null}
      {error && page !== undefined ? (
        <Alert type="warning" showIcon title="刷新失败，正在显示上次结果" />
      ) : null}

      <div className={styles.userToolbar} role="search" aria-label="用户筛选">
        <label className={styles.userSearchField}>
          <span>搜索用户</span>
          <Input
            name="platform-account-search"
            autoComplete="off"
            spellCheck={false}
            allowClear
            prefix={<Search aria-hidden="true" size={15} />}
            placeholder="用户名、显示名称、邮箱或用户 ID…"
            value={search.q ?? ""}
            onChange={(event) =>
              onSearchChange({ q: event.target.value || undefined, page: 1 })
            }
          />
        </label>
        <label className={styles.userFilterField}>
          <span>账号状态</span>
          <Select
            aria-label="账号状态"
            value={search.accountState}
            options={[
              { value: "ALL", label: "全部状态" },
              { value: "ACTIVE", label: "正常" },
              { value: "DISABLED", label: "已禁用" },
              { value: "DELETED", label: "已删除" },
            ]}
            onChange={(accountState) =>
              onSearchChange({ accountState, page: 1 })
            }
          />
        </label>
        <label className={styles.userFilterField}>
          <span>平台角色</span>
          <Select
            aria-label="平台角色"
            value={search.accountRole}
            options={[
              { value: "ALL", label: "全部角色" },
              { value: "USER", label: "普通用户" },
              { value: "PLATFORM_ADMIN", label: "平台管理员" },
            ]}
            onChange={(accountRole) => onSearchChange({ accountRole, page: 1 })}
          />
        </label>
        <Button
          icon={<RefreshCw aria-hidden="true" size={16} />}
          loading={fetching}
          onClick={onRefresh}
        >
          刷新
        </Button>
      </div>

      {items.length === 0 ? (
        <PageState
          state={
            search.q ||
            search.accountState !== "ALL" ||
            search.accountRole !== "ALL"
              ? "filtered-empty"
              : "empty"
          }
          label="用户"
          description="当前筛选没有用户，请调整搜索条件。"
        />
      ) : (
        <div className={styles.userTableBlock}>
          <Table<ManagedAccount>
            rowKey="principal_id"
            columns={columns}
            dataSource={items}
            loading={fetching && page === undefined}
            pagination={false}
            scroll={{ x: 1050 }}
            size="middle"
          />
          <Pagination
            current={page?.page ?? search.page}
            pageSize={page?.page_size ?? search.pageSize}
            total={page?.total ?? 0}
            showSizeChanger
            pageSizeOptions={[10, 20]}
            showTotal={(total) => `共 ${total} 个用户`}
            onChange={(nextPage, nextPageSize) =>
              onSearchChange({
                page: nextPageSize === search.pageSize ? nextPage : 1,
                pageSize: nextPageSize === 10 ? 10 : 20,
              })
            }
          />
        </div>
      )}

      <Modal
        rootClassName={styles.userDialog}
        title="创建用户"
        open={createOpen}
        okText="创建用户"
        cancelText="取消"
        confirmLoading={mutationPending}
        onCancel={() => setCreateOpen(false)}
        onOk={() => createForm.submit()}
        destroyOnHidden
      >
        <p className={styles.dialogIntro}>
          初始密码不会出现在响应或日志中，请通过安全渠道交给用户。
        </p>
        {mutationErrorDescription ? (
          <Alert
            className={styles.dialogError}
            type="error"
            showIcon
            role="alert"
            title="用户未创建"
            description={mutationErrorDescription}
          />
        ) : null}
        <Form<CreateFormValues>
          form={createForm}
          layout="vertical"
          initialValues={{ platform_role: "USER" }}
          onFinish={async (values) => {
            try {
              await onCreate({
                username: values.username,
                password: values.password,
                platform_role: values.platform_role,
                ...(values.display_name
                  ? { display_name: values.display_name }
                  : {}),
                ...(values.recovery_email
                  ? { recovery_email: values.recovery_email }
                  : {}),
              });
            } catch {
              return;
            }
            setCreateOpen(false);
          }}
          onFinishFailed={({ errorFields }) => {
            const first = errorFields[0]?.name;
            if (first) createForm.getFieldInstance(first)?.focus?.();
          }}
        >
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: "请输入用户名" }]}
          >
            <Input
              name="username"
              autoComplete="off"
              spellCheck={false}
              maxLength={128}
            />
          </Form.Item>
          <Form.Item name="display_name" label="显示名称（可选）">
            <Input name="display_name" autoComplete="off" maxLength={128} />
          </Form.Item>
          <Form.Item
            name="recovery_email"
            label="恢复邮箱（可选）"
            rules={[{ type: "email", message: "请输入有效邮箱地址" }]}
          >
            <Input
              name="recovery_email"
              type="email"
              autoComplete="off"
              spellCheck={false}
              maxLength={254}
            />
          </Form.Item>
          <Form.Item
            name="password"
            label="初始密码"
            extra="至少 6 位；常见弱密码和包含用户名的密码会被拒绝。"
            rules={[
              { required: true, message: "请输入初始密码" },
              { min: 6, message: "密码至少需要 6 个字符" },
            ]}
          >
            <Input.Password
              name="password"
              autoComplete="new-password"
              maxLength={128}
            />
          </Form.Item>
          <Form.Item
            name="confirm_password"
            label="确认初始密码"
            dependencies={["password"]}
            rules={[
              { required: true, message: "请再次输入初始密码" },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  return !value || getFieldValue("password") === value
                    ? Promise.resolve()
                    : Promise.reject(new Error("两次输入的密码不一致"));
                },
              }),
            ]}
          >
            <Input.Password
              name="confirm_password"
              autoComplete="new-password"
              maxLength={128}
            />
          </Form.Item>
          <Form.Item name="platform_role" label="平台角色">
            <Select
              options={[
                { value: "USER", label: "普通用户" },
                { value: "PLATFORM_ADMIN", label: "平台管理员" },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        rootClassName={styles.userDialog}
        title={`重置 ${resetDialog?.account.display_name ?? "用户"} 的密码`}
        open={resetDialog !== null}
        okText="确认重置"
        cancelText="取消"
        confirmLoading={mutationPending}
        onCancel={() => setResetDialog(null)}
        onOk={() => resetForm.submit()}
        destroyOnHidden
      >
        <Alert
          type="warning"
          showIcon
          title="无需原密码；完成后该用户所有已登录会话会立即失效。"
        />
        {mutationErrorDescription ? (
          <Alert
            className={styles.dialogError}
            type="error"
            showIcon
            role="alert"
            title="密码未重置"
            description={mutationErrorDescription}
          />
        ) : null}
        <Form<ResetFormValues>
          className={styles.resetForm}
          form={resetForm}
          layout="vertical"
          onFinish={async (values) => {
            if (!resetDialog) return;
            try {
              await onResetPassword(
                resetDialog.account,
                values.new_password,
                values.confirm_password,
              );
            } catch {
              return;
            }
            setResetDialog(null);
          }}
          onFinishFailed={({ errorFields }) => {
            const first = errorFields[0]?.name;
            if (first) resetForm.getFieldInstance(first)?.focus?.();
          }}
        >
          <Form.Item
            name="new_password"
            label="新密码"
            extra="至少 6 位。"
            rules={[
              { required: true, message: "请输入新密码" },
              { min: 6, message: "密码至少需要 6 个字符" },
            ]}
          >
            <Input.Password
              name="new_password"
              autoComplete="new-password"
              maxLength={128}
            />
          </Form.Item>
          <Form.Item
            name="confirm_password"
            label="确认新密码"
            dependencies={["new_password"]}
            rules={[
              { required: true, message: "请再次输入新密码" },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  return !value || getFieldValue("new_password") === value
                    ? Promise.resolve()
                    : Promise.reject(new Error("两次输入的密码不一致"));
                },
              }),
            ]}
          >
            <Input.Password
              name="confirm_password"
              autoComplete="new-password"
              maxLength={128}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        rootClassName={styles.userDialog}
        title={copy?.title}
        open={confirmDialog !== null}
        okText={copy?.confirm}
        cancelText="取消"
        okButtonProps={{
          danger: copy?.danger,
          disabled: !deleteConfirmed,
        }}
        confirmLoading={mutationPending}
        onCancel={() => {
          setConfirmDialog(null);
          setDeleteConfirmation("");
        }}
        onOk={() => void runConfirmedAction()}
        destroyOnHidden
      >
        <p>{copy?.description}</p>
        {mutationErrorDescription ? (
          <Alert
            className={styles.dialogError}
            type="error"
            showIcon
            role="alert"
            title="操作未完成"
            description={mutationErrorDescription}
          />
        ) : null}
        {confirmDialog?.action === "delete" ? (
          <Space orientation="vertical" className={styles.deleteConfirmation}>
            <label htmlFor="delete-account-confirmation">
              输入用户名 <strong>{confirmDialog.account.username}</strong>{" "}
              确认删除
            </label>
            <Input
              id="delete-account-confirmation"
              autoComplete="off"
              value={deleteConfirmation}
              onChange={(event) => setDeleteConfirmation(event.target.value)}
            />
          </Space>
        ) : null}
      </Modal>
    </section>
  );
}

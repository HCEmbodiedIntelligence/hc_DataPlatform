import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Alert, Avatar, Button, Form, Input, Tabs } from "antd";
import { CalendarClock, KeyRound, ShieldCheck, UserRound } from "lucide-react";
import { useForm } from "react-hook-form";
import { useNavigate, useSearchParams } from "react-router-dom";
import { z } from "zod";
import { useToast } from "../../app/providers/ToastProvider";
import {
  accountSettingsKeys,
  changeOwnAccountPassword,
  updateOwnAccountProfile,
  useOwnAccountProfile,
  type AccountSettings,
  type PasswordChangeResult,
  type PasswordPolicyView,
} from "../../features/account/api";
import { isDomainError } from "../../shared/api/domain-error";
import { useShellStore } from "../../shared/scope/shell-store";
import { createZodResolver } from "../../shared/ui/forms/createZodResolver";
import { RHFInput } from "../../shared/ui/forms/controlled-fields";
import { StandardPageScaffold } from "../../shared/ui/layout/StandardPageScaffold";
import { PageState } from "../../shared/ui/state/PageState";
import { StatusTag } from "../../shared/ui/state/StatusTag";
import { ControlledPasswordField } from "./components/ControlledPasswordField";
import { RecoveryEmailForm } from "./components/RecoveryEmailForm";
import {
  CapabilityRequestsPanel,
  MembershipsPanel,
  RequestHistoryPanel,
} from "./components/AccountAccessPanels";
import styles from "./styles.module.css";

const profileFormSchema = z
  .object({
    displayName: z
      .string()
      .trim()
      .min(1, "请输入显示名称。")
      .max(128, "显示名称不能超过 128 个字符。")
      .refine(
        (value) =>
          !Array.from(value).some((character) => {
            const codePoint = character.codePointAt(0) ?? 0;
            return codePoint <= 31 || codePoint === 127;
          }),
        "显示名称不能包含控制字符。",
      ),
  })
  .strict();

type ProfileFormValues = z.infer<typeof profileFormSchema>;

interface PasswordFormValues {
  currentPassword: string;
  newPassword: string;
  confirmPassword: string;
}

function createPasswordFormSchema(
  policy: PasswordPolicyView,
  username: string,
): z.ZodType<PasswordFormValues> {
  const canonicalUsername = username.normalize("NFKC").toLocaleLowerCase();
  const normalizedLength = (value: string) =>
    Array.from(value.normalize("NFKC")).length;

  return z
    .object({
      currentPassword: z
        .string()
        .min(1, "请输入当前密码。")
        .refine(
          (value) => Array.from(value).length <= 1024,
          "当前密码不能超过 1024 个字符。",
        ),
      newPassword: z.string(),
      confirmPassword: z.string().min(1, "请再次输入新密码。"),
    })
    .strict()
    .superRefine((values, context) => {
      const newPasswordLength = normalizedLength(values.newPassword);
      if (newPasswordLength < policy.min_length) {
        context.addIssue({
          code: "custom",
          path: ["newPassword"],
          message: `新密码至少需要 ${policy.min_length} 个字符。`,
        });
      }
      if (newPasswordLength > policy.max_length) {
        context.addIssue({
          code: "custom",
          path: ["newPassword"],
          message: `新密码不能超过 ${policy.max_length} 个字符。`,
        });
      }
      if (values.newPassword === values.currentPassword) {
        context.addIssue({
          code: "custom",
          path: ["newPassword"],
          message: "新密码不能与当前密码相同。",
        });
      }
      if (values.newPassword !== values.confirmPassword) {
        context.addIssue({
          code: "custom",
          path: ["confirmPassword"],
          message: "两次输入的新密码不一致。",
        });
      }
      if (
        policy.disallow_username &&
        canonicalUsername.length >= 3 &&
        values.newPassword
          .normalize("NFKC")
          .toLocaleLowerCase()
          .includes(canonicalUsername)
      ) {
        context.addIssue({
          code: "custom",
          path: ["newPassword"],
          message: "新密码不能包含登录用户名。",
        });
      }
    });
}

function fieldName(path: string): string {
  return path.split(/[/.]/u).filter(Boolean).at(-1) ?? "";
}

function accountErrorMessage(reason: unknown, fallback: string): string {
  if (!isDomainError(reason)) return fallback;
  if (reason.httpStatus === 412 || reason.code === "VERSION_CONFLICT") {
    return "账户资料已在其他窗口更新。请重新加载最新资料后再提交。";
  }
  if (reason.httpStatus === 429) {
    return "操作过于频繁。请稍后再试。";
  }
  if (reason.problemCode === "CURRENT_PASSWORD_INVALID") {
    return "当前密码不正确。";
  }
  if (reason.code === "NETWORK_ERROR") {
    return "网络连接失败。请检查连接后重试。";
  }
  if (reason.code === "CONTRACT_MISMATCH") {
    return "服务响应未通过账户合同校验，页面没有采纳该结果。";
  }
  return fallback;
}

function requestIdSuffix(reason: unknown): string {
  return isDomainError(reason) && reason.requestId
    ? ` 请求 ID：${reason.requestId}`
    : "";
}

function formatInstant(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(date);
}

function ProfileForm({
  account,
  disabled,
  onSave,
  onReload,
}: {
  readonly account: AccountSettings;
  readonly disabled: boolean;
  readonly onSave: (displayName: string) => Promise<AccountSettings>;
  readonly onReload: () => Promise<AccountSettings | undefined>;
}) {
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [success, setSuccess] = useState(false);
  const feedbackRef = useRef<HTMLDivElement>(null);
  const inFlightRef = useRef(false);
  const displayName = account.profile.display_name;
  const revision = account.profile.revision;
  const form = useForm<ProfileFormValues>({
    defaultValues: { displayName },
    resolver: createZodResolver(profileFormSchema),
    mode: "onChange",
  });

  useEffect(() => {
    if (!form.formState.isDirty) form.reset({ displayName });
  }, [displayName, revision, form]);

  useEffect(() => {
    if (failure !== null || success) feedbackRef.current?.focus();
  }, [failure, success]);

  const submit = form.handleSubmit(async ({ displayName: nextDisplayName }) => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setPending(true);
    setFailure(null);
    setSuccess(false);
    form.clearErrors();
    try {
      const next = await onSave(nextDisplayName);
      form.reset({ displayName: next.profile.display_name });
      setSuccess(true);
    } catch (reason) {
      if (isDomainError(reason)) {
        for (const issue of reason.fieldErrors) {
          if (fieldName(issue.path) === "display_name") {
            form.setError("displayName", {
              type: issue.code,
              message: issue.message,
            });
          }
        }
      }
      setFailure(reason);
    } finally {
      inFlightRef.current = false;
      setPending(false);
    }
  });

  const conflict =
    isDomainError(failure) &&
    (failure.httpStatus === 412 || failure.code === "VERSION_CONFLICT");

  return (
    <section className={styles.formSection} aria-labelledby="profile-title">
      <header className={styles.sectionHeader}>
        <span className={styles.sectionIcon} aria-hidden="true">
          <UserRound size={20} />
        </span>
        <div>
          <h2 id="profile-title">基本资料</h2>
          <p>显示名称用于平台导航和协作记录；登录用户名不可修改。</p>
        </div>
      </header>

      <div ref={feedbackRef} tabIndex={-1}>
        {failure !== null ? (
          <Alert
            action={
              conflict ? (
                <Button
                  disabled={pending}
                  size="small"
                  onClick={() => {
                    void onReload().then((fresh) => {
                      if (fresh !== undefined) {
                        form.reset({
                          displayName: fresh.profile.display_name,
                        });
                      }
                      setFailure(null);
                      setSuccess(false);
                    });
                  }}
                >
                  重新加载
                </Button>
              ) : undefined
            }
            className={styles.feedback}
            description={`${accountErrorMessage(failure, "基本资料未保存。请稍后重试。")} ${requestIdSuffix(failure)}`}
            role="alert"
            showIcon
            title="基本资料未保存"
            type="error"
          />
        ) : null}
        {success ? (
          <Alert
            className={styles.feedback}
            role="status"
            showIcon
            title="基本资料已保存"
            type="success"
          />
        ) : null}
      </div>

      <Form
        aria-busy={pending}
        layout="vertical"
        requiredMark={false}
        onSubmitCapture={(event) => void submit(event)}
      >
        <Form.Item label="登录用户名" htmlFor="account-username">
          <Input
            id="account-username"
            disabled
            readOnly
            value={account.profile.username}
          />
        </Form.Item>
        <RHFInput
          control={form.control}
          disabled={disabled || pending}
          id="account-display-name"
          label="显示名称"
          maxLength={128}
          name="displayName"
          autoComplete="name"
        />
        <div className={styles.formActions}>
          <Button
            disabled={
              disabled ||
              pending ||
              !form.formState.isDirty ||
              !form.formState.isValid
            }
            htmlType="submit"
            loading={pending}
            type="primary"
          >
            {pending ? "正在保存…" : "保存基本资料"}
          </Button>
        </div>
      </Form>
    </section>
  );
}

function PasswordChangeForm({
  account,
  disabled,
  onChangePassword,
}: {
  readonly account: AccountSettings;
  readonly disabled: boolean;
  readonly onChangePassword: (
    currentPassword: string,
    newPassword: string,
  ) => Promise<PasswordChangeResult>;
}) {
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const feedbackRef = useRef<HTMLDivElement>(null);
  const inFlightRef = useRef(false);
  const policy = account.password_policy;
  const schema = useMemo(
    () => createPasswordFormSchema(policy, account.profile.username),
    [
      account.profile.username,
      policy.disallow_username,
      policy.max_length,
      policy.min_length,
    ],
  );
  const form = useForm<PasswordFormValues>({
    defaultValues: {
      currentPassword: "",
      newPassword: "",
      confirmPassword: "",
    },
    resolver: createZodResolver(schema),
    mode: "onChange",
  });

  useEffect(() => {
    if (failure !== null || success !== null) feedbackRef.current?.focus();
  }, [failure, success]);

  const submit = form.handleSubmit(async (values) => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setPending(true);
    setFailure(null);
    setSuccess(null);
    form.clearErrors();
    try {
      const result = await onChangePassword(
        values.currentPassword,
        values.newPassword,
      );
      form.reset({
        currentPassword: "",
        newPassword: "",
        confirmPassword: "",
      });
      setSuccess(
        result.other_sessions_revoked > 0
          ? `密码已更新，其他 ${result.other_sessions_revoked} 个登录会话已退出。`
          : "密码已更新。当前会话仍然有效。",
      );
    } catch (reason) {
      if (isDomainError(reason)) {
        for (const issue of reason.fieldErrors) {
          const name = fieldName(issue.path);
          if (name === "current_password") {
            form.setError("currentPassword", {
              type: issue.code,
              message: issue.message,
            });
          }
          if (name === "new_password") {
            form.setError("newPassword", {
              type: issue.code,
              message: issue.message,
            });
          }
        }
        if (reason.problemCode === "CURRENT_PASSWORD_INVALID") {
          form.setError("currentPassword", {
            type: reason.problemCode,
            message: "当前密码不正确。",
          });
        }
      }
      setFailure(reason);
    } finally {
      inFlightRef.current = false;
      setPending(false);
    }
  });

  return (
    <section className={styles.formSection} aria-labelledby="password-title">
      <header className={styles.sectionHeader}>
        <span className={styles.sectionIcon} aria-hidden="true">
          <KeyRound size={20} />
        </span>
        <div>
          <h2 id="password-title">修改密码</h2>
          <p>更新后，服务端会撤销此账户的其他活动会话。</p>
        </div>
      </header>

      <div ref={feedbackRef} tabIndex={-1}>
        {failure !== null ? (
          <Alert
            className={styles.feedback}
            description={`${accountErrorMessage(failure, "密码未更新。请检查输入后重试。")} ${requestIdSuffix(failure)}`}
            role="alert"
            showIcon
            title="密码未更新"
            type="error"
          />
        ) : null}
        {success !== null ? (
          <Alert
            className={styles.feedback}
            description={success}
            role="status"
            showIcon
            title="密码已更新"
            type="success"
          />
        ) : null}
      </div>

      <Alert
        className={styles.policyNotice}
        description={
          <span>
            长度须为 {policy.min_length}–{policy.max_length} 个字符
            {policy.disallow_username ? "，且不能包含登录用户名" : ""}。
            平台还会拦截 {policy.blocked_password_count.toLocaleString("zh-CN")}{" "}
            个常见弱密码。
          </span>
        }
        showIcon
        title="当前密码策略"
        type="info"
      />

      <Form
        aria-busy={pending}
        layout="vertical"
        requiredMark={false}
        onSubmitCapture={(event) => void submit(event)}
      >
        <ControlledPasswordField
          autoComplete="current-password"
          control={form.control}
          description="用于确认是您本人发起本次修改。"
          disabled={disabled || pending}
          id="account-current-password"
          label="当前密码"
          name="currentPassword"
          placeholder="请输入当前密码"
        />
        <ControlledPasswordField
          autoComplete="new-password"
          control={form.control}
          disabled={disabled || pending}
          id="account-new-password"
          label="新密码"
          name="newPassword"
          placeholder="请输入新密码"
        />
        <ControlledPasswordField
          autoComplete="new-password"
          control={form.control}
          disabled={disabled || pending}
          id="account-confirm-password"
          label="确认新密码"
          name="confirmPassword"
          placeholder="请再次输入新密码"
        />
        <div className={styles.formActions}>
          <Button
            disabled={disabled || pending || !form.formState.isValid}
            htmlType="submit"
            loading={pending}
            type="primary"
          >
            {pending ? "正在更新…" : "更新密码"}
          </Button>
        </div>
      </Form>
    </section>
  );
}

function SettingsContent({
  account,
  onSaveProfile,
  onChangePassword,
  onReload,
  onSessionExpired,
  activeTab,
  onTabChange,
}: {
  readonly account: AccountSettings;
  readonly onSaveProfile: (displayName: string) => Promise<AccountSettings>;
  readonly onChangePassword: (
    currentPassword: string,
    newPassword: string,
  ) => Promise<PasswordChangeResult>;
  readonly onReload: () => Promise<AccountSettings | undefined>;
  readonly onSessionExpired: () => void;
  readonly activeTab: string;
  readonly onTabChange: (tab: string) => void;
}) {
  const active = account.profile.status === "ACTIVE";
  const initial = account.profile.display_name.trim().slice(0, 1) || "账";

  return (
    <div className={styles.settingsGrid}>
      <aside className={styles.identityPanel} aria-label="当前账户摘要">
        <Avatar className={styles.avatar} size={56}>
          {initial}
        </Avatar>
        <div className={styles.identityHeading}>
          <strong>{account.profile.display_name}</strong>
          <span translate="no">@{account.profile.username}</span>
        </div>
        <StatusTag
          label={active ? "账户正常" : "账户已停用"}
          status={account.profile.status}
          tone={active ? "success" : "danger"}
        />
        <dl className={styles.identityFacts}>
          <div>
            <dt>账户 ID</dt>
            <dd>
              <code>{account.profile.principal_id}</code>
            </dd>
          </div>
          <div>
            <dt>
              <CalendarClock aria-hidden="true" size={15} /> 创建时间
            </dt>
            <dd>{formatInstant(account.profile.created_at)}</dd>
          </div>
          <div>
            <dt>
              <ShieldCheck aria-hidden="true" size={15} /> 密码更新时间
            </dt>
            <dd>{formatInstant(account.profile.password_changed_at)}</dd>
          </div>
        </dl>
        <p className={styles.identityNote}>
          账户设置属于当前登录身份，不会随项目或区域切换而变化。
        </p>
      </aside>

      <div className={styles.formsColumn}>
        {!active ? (
          <Alert
            description="此账户当前不可修改资料或密码。请联系平台管理员确认账户状态。"
            role="alert"
            showIcon
            title="账户已停用"
            type="warning"
          />
        ) : null}
        <Tabs
          activeKey={activeTab}
          className={styles.settingsTabs}
          items={[
            {
              key: "profile",
              label: "个人资料",
              children: (
                <ProfileForm
                  account={account}
                  disabled={!active}
                  onReload={onReload}
                  onSave={onSaveProfile}
                />
              ),
            },
            {
              key: "security",
              label: "安全设置",
              children: (
                <div className={styles.tabStack}>
                  <PasswordChangeForm
                    account={account}
                    disabled={!active}
                    onChangePassword={onChangePassword}
                  />
                  <RecoveryEmailForm
                    disabled={!active}
                    onSessionExpired={onSessionExpired}
                  />
                </div>
              ),
            },
            {
              key: "memberships",
              label: "我的组织与项目",
              children: <MembershipsPanel />,
            },
            {
              key: "capabilities",
              label: "权限申请",
              children: <CapabilityRequestsPanel />,
            },
            {
              key: "requests",
              label: "申请记录",
              children: <RequestHistoryPanel />,
            },
          ]}
          onChange={onTabChange}
        />
      </div>
    </div>
  );
}

export function AccountSettingsPage() {
  const query = useOwnAccountProfile();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { showToast } = useToast();
  const principal = useShellStore((state) => state.principal);
  const updatePrincipal = useShellStore((state) => state.updatePrincipal);
  const clearSensitiveState = useShellStore(
    (state) => state.clearSensitiveState,
  );
  const setSession = useShellStore((state) => state.setSession);
  const requestedTab = searchParams.get("tab");
  const activeTab = [
    "profile",
    "security",
    "memberships",
    "capabilities",
    "requests",
  ].includes(requestedTab ?? "")
    ? requestedTab!
    : "profile";

  const expireSession = useCallback(() => {
    clearSensitiveState();
    setSession(null, null);
    navigate("/auth/session-expired", { replace: true });
  }, [clearSensitiveState, navigate, setSession]);

  useEffect(() => {
    if (isDomainError(query.error) && query.error.httpStatus === 401) {
      expireSession();
    }
  }, [expireSession, query.error]);

  const acceptAccount = useCallback(
    (account: AccountSettings) => {
      if (principal === null) return;
      queryClient.setQueryData(
        accountSettingsKeys.profile(principal.actorId),
        account,
      );
      updatePrincipal({
        ...principal,
        displayName: account.profile.display_name,
      });
    },
    [principal, queryClient, updatePrincipal],
  );

  const saveProfile = useCallback(
    async (displayName: string) => {
      if (principal === null || query.data === undefined) {
        throw new Error("ACCOUNT_SESSION_UNAVAILABLE");
      }
      try {
        const account = await updateOwnAccountProfile(
          principal.actorId,
          displayName,
          query.data.profile.etag,
          globalThis.crypto.randomUUID(),
        );
        acceptAccount(account);
        showToast({ title: "基本资料已保存", tone: "success" });
        return account;
      } catch (reason) {
        if (isDomainError(reason) && reason.httpStatus === 401) expireSession();
        throw reason;
      }
    },
    [acceptAccount, expireSession, principal, query.data, showToast],
  );

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string) => {
      if (principal === null || query.data === undefined) {
        throw new Error("ACCOUNT_SESSION_UNAVAILABLE");
      }
      try {
        const result = await changeOwnAccountPassword(
          principal.actorId,
          currentPassword,
          newPassword,
          query.data.profile.etag,
          globalThis.crypto.randomUUID(),
        );
        acceptAccount(result.account);
        showToast({
          title: "密码已更新",
          message:
            result.other_sessions_revoked > 0
              ? "其他登录会话已安全退出。"
              : "当前会话仍然有效。",
          tone: "success",
        });
        return result;
      } catch (reason) {
        if (isDomainError(reason) && reason.httpStatus === 401) expireSession();
        throw reason;
      }
    },
    [acceptAccount, expireSession, principal, query.data, showToast],
  );

  let state: React.ReactNode;
  if (query.isPending || query.data === undefined) {
    state = <PageState label="账户设置" layout="list" state="loading" />;
  } else {
    const content = (
      <SettingsContent
        activeTab={activeTab}
        account={query.data}
        onChangePassword={changePassword}
        onReload={async () => (await query.refetch()).data}
        onSaveProfile={saveProfile}
        onSessionExpired={expireSession}
        onTabChange={(tab) =>
          setSearchParams(tab === "profile" ? {} : { tab }, { replace: true })
        }
      />
    );
    state = query.isFetching ? (
      <PageState
        description="当前显示上次成功加载的账户资料。"
        label="账户设置"
        state="refreshing"
      >
        {content}
      </PageState>
    ) : (
      content
    );
  }

  if (query.error !== null && query.data === undefined) {
    const error = query.error;
    const pageState = !isDomainError(error)
      ? "error"
      : error.code === "FORBIDDEN"
        ? "forbidden"
        : error.code === "RATE_LIMITED"
          ? "rate-limited"
          : error.code === "NETWORK_ERROR"
            ? "offline"
            : error.code === "CONTRACT_MISMATCH"
              ? "contract-mismatch"
              : "error";
    state = (
      <PageState
        description={accountErrorMessage(
          error,
          "账户资料暂时无法加载。请稍后重试。",
        )}
        label="账户设置"
        onRetry={() => void query.refetch()}
        requestId={isDomainError(error) ? error.requestId : null}
        state={pageState}
      />
    );
  }

  return (
    <StandardPageScaffold
      header={{
        title: "账户设置",
        description: "管理个人资料、安全设置、组织与项目关系及权限申请。",
      }}
      state={state}
    />
  );
}

export default AccountSettingsPage;

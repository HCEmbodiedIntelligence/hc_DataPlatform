import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Form, Input } from "antd";
import { KeyRound, MailCheck, ShieldCheck } from "lucide-react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { isDomainError } from "../../shared/api/domain-error";
import { AuthLayout } from "./AuthLayout";
import { AuthStatusCard } from "./AuthStatusCard";
import { PasswordField } from "./PasswordField";
import { TurnstileChallenge } from "./TurnstileChallenge";
import {
  confirmPasswordRecovery,
  getPublicAuthConfiguration,
  requestPasswordRecovery,
  type PublicAuthConfiguration,
} from "./api";
import styles from "./styles.module.css";

interface RecoveryRequestValues {
  readonly identifier: string;
}

interface PasswordResetValues {
  readonly token: string;
  readonly newPassword: string;
  readonly confirmPassword: string;
}

function requestIdSuffix(reason: unknown): string {
  return isDomainError(reason) && reason.requestId
    ? ` 请求 ID：${reason.requestId}`
    : "";
}

function recoveryErrorMessage(
  reason: unknown,
  phase: "request" | "confirm",
): string {
  if (!isDomainError(reason)) return "账户恢复暂时无法完成。请稍后重试。";
  if (reason.code === "NETWORK_ERROR")
    return "网络连接失败。请检查连接后重试。";
  if (reason.code === "CONTRACT_MISMATCH") {
    return "服务响应未通过账户恢复合同校验，页面没有采纳该结果。";
  }
  if (reason.httpStatus === 429) {
    return "账户恢复请求过于频繁。请稍后再试；平台不会显示账户是否存在。";
  }
  if (
    reason.problemCode === "AUTH_CHALLENGE_REQUIRED" ||
    reason.problemCode === "AUTH_CHALLENGE_INVALID"
  ) {
    return "请重新完成安全验证后再发送恢复邮件。";
  }
  if (reason.problemCode === "AUTH_CHALLENGE_UNAVAILABLE") {
    return "安全验证服务暂时不可用。请稍后再试。";
  }
  if (phase === "confirm") {
    if (reason.problemCode === "ACCOUNT_RECOVERY_TOKEN_INVALID") {
      return "重置凭据无效、已过期或已经使用。请重新发送恢复邮件。";
    }
    if (reason.problemCode === "PASSWORD_REUSE_FORBIDDEN") {
      return "新密码不能与当前密码相同。";
    }
    if (reason.httpStatus === 422) {
      return "新密码未通过服务端密码策略校验。请调整后重试。";
    }
  }
  if (reason.httpStatus === 422) return "账户标识格式不正确。请检查后重试。";
  return "账户恢复暂时无法完成。请稍后重试。";
}

function usePublicAuthConfiguration() {
  const [generation, setGeneration] = useState(0);
  const [configuration, setConfiguration] = useState<
    PublicAuthConfiguration | null | undefined
  >(undefined);

  useEffect(() => {
    const controller = new AbortController();
    setConfiguration(undefined);
    void getPublicAuthConfiguration(controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) setConfiguration(next);
      })
      .catch(() => {
        if (!controller.signal.aborted) setConfiguration(null);
      });
    return () => controller.abort();
  }, [generation]);

  return {
    configuration,
    retry: () => setGeneration((value) => value + 1),
  } as const;
}

function AccountRecoveryStatus() {
  return (
    <AuthStatusCard
      icon={<ShieldCheck size={30} />}
      title="恢复链路只认已验证邮箱"
      description={
        <ol className={styles.accessSteps}>
          <li>
            <strong>统一受理</strong>
            <span>申请结果不会显示用户名或邮箱是否存在。</span>
          </li>
          <li>
            <strong>一次凭据</strong>
            <span>邮件中的重置凭据有时限，成功后立即失效。</span>
          </li>
          <li>
            <strong>撤销会话</strong>
            <span>密码重置完成后，已有登录会话全部退出。</span>
          </li>
        </ol>
      }
    />
  );
}

export function PasswordRecoveryRequestRoute() {
  const [form] = Form.useForm<RecoveryRequestValues>();
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [accepted, setAccepted] = useState(false);
  const [challengeResponse, setChallengeResponse] = useState<string | null>(
    null,
  );
  const [challengeResetKey, setChallengeResetKey] = useState(0);
  const inFlightRef = useRef(false);
  const feedbackRef = useRef<HTMLDivElement>(null);
  const { configuration, retry } = usePublicAuthConfiguration();

  useEffect(() => {
    if (failure !== null || accepted) feedbackRef.current?.focus();
  }, [accepted, failure]);

  const submit = async ({ identifier }: RecoveryRequestValues) => {
    if (
      inFlightRef.current ||
      configuration === undefined ||
      configuration === null
    )
      return;
    if (configuration.challenge !== null && challengeResponse === null) return;
    inFlightRef.current = true;
    setPending(true);
    setFailure(null);
    try {
      await requestPasswordRecovery(identifier, {
        ...(challengeResponse === null ? {} : { challengeResponse }),
      });
      form.resetFields();
      setAccepted(true);
    } catch (reason) {
      setFailure(reason);
    } finally {
      inFlightRef.current = false;
      setPending(false);
      if (configuration.challenge !== null) {
        setChallengeResponse(null);
        setChallengeResetKey((value) => value + 1);
      }
    }
  };

  return (
    <AuthLayout rail={<AccountRecoveryStatus />}>
      <section
        className={styles.authCard}
        aria-labelledby="recovery-request-title"
      >
        <header className={styles.formHeading}>
          <p className={styles.eyebrow}>账户恢复</p>
          <h1 id="recovery-request-title">找回密码</h1>
        </header>

        <div ref={feedbackRef} tabIndex={-1}>
          {failure !== null ? (
            <Alert
              className={styles.recoveryNotice}
              description={`${recoveryErrorMessage(failure, "request")}${requestIdSuffix(failure)}`}
              role="alert"
              showIcon
              title="恢复邮件未发送"
              type="error"
            />
          ) : null}
          {accepted ? (
            <Alert
              className={styles.recoveryNotice}
              description="如果该账户存在且已配置恢复邮箱，重置链接会发送到该邮箱。请检查收件箱和垃圾邮件；页面不会显示账户是否存在。"
              role="status"
              showIcon
              title="恢复请求已受理"
              type="success"
            />
          ) : null}
        </div>

        {configuration === null ? (
          <Alert
            className={styles.recoveryNotice}
            action={
              <Button size="small" onClick={retry}>
                重新加载
              </Button>
            }
            description="当前无法读取安全验证和密码策略，恢复请求不会继续。"
            role="alert"
            showIcon
            title="恢复配置加载失败"
            type="error"
          />
        ) : null}

        {!accepted ? (
          <Form<RecoveryRequestValues>
            form={form}
            layout="vertical"
            requiredMark={false}
            onFinish={(values) => void submit(values)}
          >
            <Form.Item
              htmlFor="recovery-identifier"
              label="用户名或恢复邮箱"
              name="identifier"
              rules={[
                {
                  required: true,
                  whitespace: true,
                  message: "请输入用户名或恢复邮箱。",
                },
                { max: 254, message: "账户标识不能超过 254 个字符。" },
              ]}
            >
              <Input
                autoComplete="username"
                disabled={
                  pending ||
                  (configuration !== undefined && configuration === null)
                }
                id="recovery-identifier"
                maxLength={254}
                name="identifier"
                placeholder="用户名或已验证的恢复邮箱"
                prefix={<MailCheck aria-hidden="true" size={17} />}
                spellCheck={false}
              />
            </Form.Item>

            {configuration === undefined ? (
              <Alert
                className={styles.recoveryNotice}
                description="请稍候，安全配置加载完成后即可发送恢复邮件。"
                showIcon
                title="正在加载安全配置"
                type="info"
              />
            ) : configuration?.challenge ? (
              <TurnstileChallenge
                action="password_recovery"
                configuration={configuration.challenge}
                onToken={setChallengeResponse}
                resetKey={challengeResetKey}
              />
            ) : null}

            <Button
              block
              className={styles.primaryAction}
              disabled={
                pending ||
                configuration === undefined ||
                configuration === null ||
                (configuration.challenge !== null && challengeResponse === null)
              }
              htmlType="submit"
              loading={pending}
              type="primary"
            >
              {pending ? "正在发送…" : "发送恢复邮件"}
            </Button>
          </Form>
        ) : (
          <Button block onClick={() => setAccepted(false)}>
            再次发送
          </Button>
        )}

        <Link className={styles.authBackLink} to="/auth/login">
          返回登录
        </Link>
      </section>
    </AuthLayout>
  );
}

function tokenFromFragment(fragment: string): string {
  const value = new URLSearchParams(fragment.replace(/^#/u, "")).get("token");
  return value ?? "";
}

export function PasswordResetRoute() {
  const location = useLocation();
  const navigate = useNavigate();
  const initialToken = useMemo(
    () => tokenFromFragment(location.hash),
    [location.hash],
  );
  const [form] = Form.useForm<PasswordResetValues>();
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [sessionsRevoked, setSessionsRevoked] = useState<number | null>(null);
  const inFlightRef = useRef(false);
  const feedbackRef = useRef<HTMLDivElement>(null);
  const { configuration, retry } = usePublicAuthConfiguration();

  useEffect(() => {
    if (!location.hash) return;
    navigate(`${location.pathname}${location.search}`, { replace: true });
  }, [location.hash, location.pathname, location.search, navigate]);

  useEffect(() => {
    if (failure !== null || sessionsRevoked !== null)
      feedbackRef.current?.focus();
  }, [failure, sessionsRevoked]);

  const submit = async ({ token, newPassword }: PasswordResetValues) => {
    if (
      inFlightRef.current ||
      configuration === undefined ||
      configuration === null
    )
      return;
    inFlightRef.current = true;
    setPending(true);
    setFailure(null);
    try {
      const result = await confirmPasswordRecovery(token, newPassword);
      form.setFieldsValue({ token: "", newPassword: "", confirmPassword: "" });
      setSessionsRevoked(result.sessions_revoked);
    } catch (reason) {
      setFailure(reason);
    } finally {
      inFlightRef.current = false;
      setPending(false);
    }
  };

  const policy = configuration?.password_policy;

  return (
    <AuthLayout rail={<AccountRecoveryStatus />}>
      <section
        className={styles.authCard}
        aria-labelledby="password-reset-title"
      >
        <header className={styles.formHeading}>
          <p className={styles.eyebrow}>账户恢复</p>
          <h1 id="password-reset-title">设置新密码</h1>
        </header>

        <div ref={feedbackRef} tabIndex={-1}>
          {failure !== null ? (
            <Alert
              className={styles.recoveryNotice}
              description={`${recoveryErrorMessage(failure, "confirm")}${requestIdSuffix(failure)}`}
              role="alert"
              showIcon
              title="密码未重置"
              type="error"
            />
          ) : null}
          {sessionsRevoked !== null ? (
            <Alert
              className={styles.recoveryNotice}
              description={
                sessionsRevoked > 0
                  ? `密码已更新，${sessionsRevoked} 个已有登录会话已退出。`
                  : "密码已更新。重置凭据已经失效，可使用新密码登录。"
              }
              role="status"
              showIcon
              title="密码已重置"
              type="success"
            />
          ) : null}
        </div>

        {configuration === null ? (
          <Alert
            className={styles.recoveryNotice}
            action={
              <Button size="small" onClick={retry}>
                重新加载
              </Button>
            }
            description="当前无法读取密码策略，页面不会提交新密码。"
            role="alert"
            showIcon
            title="密码策略加载失败"
            type="error"
          />
        ) : null}

        {sessionsRevoked === null ? (
          <Form<PasswordResetValues>
            form={form}
            initialValues={{
              token: initialToken,
              newPassword: "",
              confirmPassword: "",
            }}
            layout="vertical"
            requiredMark={false}
            onFinish={(values) => void submit(values)}
          >
            <Form.Item
              htmlFor="password-reset-token"
              label="重置凭据"
              name="token"
              rules={[
                {
                  required: true,
                  whitespace: true,
                  message: "请输入邮件中的重置凭据。",
                },
                { min: 16, message: "重置凭据格式不正确。" },
                { max: 512, message: "重置凭据格式不正确。" },
              ]}
            >
              <Input
                autoComplete="one-time-code"
                disabled={pending}
                id="password-reset-token"
                maxLength={512}
                name="token"
                placeholder="邮件链接会自动填写，也可手动粘贴"
                prefix={<KeyRound aria-hidden="true" size={17} />}
                spellCheck={false}
              />
            </Form.Item>

            {policy ? (
              <Alert
                className={styles.recoveryNotice}
                description={`新密码长度须为 ${policy.min_length}–${policy.max_length} 个字符；服务端还会校验用户名和常见弱密码。`}
                showIcon
                title="当前密码策略"
                type="info"
              />
            ) : (
              <Alert
                className={styles.recoveryNotice}
                description="请稍候，密码策略加载完成后即可继续。"
                showIcon
                title="正在加载密码策略"
                type="info"
              />
            )}

            <PasswordField
              autoComplete="new-password"
              disabled={pending}
              id="password-reset-new"
              label="新密码"
              name="newPassword"
              placeholder="请输入新密码"
              rules={[
                { required: true, message: "请输入新密码。" },
                ...(policy
                  ? [
                      {
                        min: policy.min_length,
                        message: `新密码至少需要 ${policy.min_length} 个字符。`,
                      },
                      {
                        max: policy.max_length,
                        message: `新密码不能超过 ${policy.max_length} 个字符。`,
                      },
                    ]
                  : []),
              ]}
            />
            <PasswordField
              autoComplete="new-password"
              dependencies={["newPassword"]}
              disabled={pending}
              id="password-reset-confirm"
              label="确认新密码"
              name="confirmPassword"
              placeholder="请再次输入新密码"
              rules={[
                { required: true, message: "请再次输入新密码。" },
                ({ getFieldValue }) => ({
                  validator(_, value: string) {
                    return !value || getFieldValue("newPassword") === value
                      ? Promise.resolve()
                      : Promise.reject(new Error("两次输入的新密码不一致。"));
                  },
                }),
              ]}
            />
            <Button
              block
              className={styles.primaryAction}
              disabled={pending || policy === undefined}
              htmlType="submit"
              loading={pending}
              type="primary"
            >
              {pending ? "正在重置…" : "重置密码"}
            </Button>
          </Form>
        ) : null}

        <Link className={styles.authBackLink} to="/auth/login">
          返回登录
        </Link>
      </section>
    </AuthLayout>
  );
}

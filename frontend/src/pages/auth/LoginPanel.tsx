import { useEffect, useRef } from "react";
import { Alert, Button, Divider, Form, Input } from "antd";
import { KeyRound, ShieldCheck, UserRound } from "lucide-react";
import { Link } from "react-router-dom";
import type { PublicAuthChallengeConfiguration } from "./api";
import type { LoginValues } from "./use-login-flow";
import { PasswordField } from "./PasswordField";
import { TurnstileChallenge } from "./TurnstileChallenge";
import styles from "./styles.module.css";

const ignoreChallengeToken = (_token: string | null): void => undefined;
const retryUnavailableChallenge = (): void => undefined;

interface LoginPanelProps {
  readonly initialUsername?: string;
  readonly submitting: boolean;
  readonly error: string | null;
  readonly challengeRequired?: boolean;
  readonly challengeConfiguration?:
    | PublicAuthChallengeConfiguration
    | null
    | undefined;
  readonly challengeResponse?: string | null;
  readonly challengeResetKey?: number;
  readonly onChallengeResponse?: (token: string | null) => void;
  readonly onRetryChallengeConfiguration?: () => void;
  readonly onSubmit: (values: LoginValues) => Promise<void> | void;
}

export function LoginPanel({
  initialUsername,
  submitting,
  error,
  challengeRequired = false,
  challengeConfiguration = undefined,
  challengeResponse = null,
  challengeResetKey = 0,
  onChallengeResponse = ignoreChallengeToken,
  onRetryChallengeConfiguration = retryUnavailableChallenge,
  onSubmit,
}: LoginPanelProps) {
  const [form] = Form.useForm<LoginValues>();
  const errorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  return (
    <section className={styles.authCard} aria-labelledby="login-title">
      <header className={styles.formHeading}>
        <p className={styles.eyebrow}>账户认证</p>
        <h1 id="login-title">登录</h1>
      </header>

      {error ? (
        <div ref={errorRef} tabIndex={-1} className={styles.errorSummary}>
          <Alert type="error" showIcon title="登录未完成" description={error} />
        </div>
      ) : null}

      <Form<LoginValues>
        form={form}
        layout="vertical"
        requiredMark={false}
        initialValues={{ username: initialUsername ?? "", password: "" }}
        onFinish={(values) => {
          if (challengeRequired && challengeResponse === null) return;
          return onSubmit(values);
        }}
        onFinishFailed={({ errorFields }) => {
          const first = errorFields[0]?.name;
          if (first) form.getFieldInstance(first)?.focus?.();
        }}
      >
        <Form.Item
          name="username"
          label="用户名"
          htmlFor="auth-login-username"
          rules={[
            { required: true, whitespace: true, message: "请输入用户名。" },
          ]}
        >
          <Input
            id="auth-login-username"
            name="username"
            autoComplete="username"
            spellCheck={false}
            disabled={submitting}
            placeholder="例如：wangxiaoming…"
            prefix={<UserRound size={17} aria-hidden="true" />}
          />
        </Form.Item>

        <PasswordField
          id="auth-login-password"
          name="password"
          label="密码"
          autoComplete="current-password"
          disabled={submitting}
          placeholder="请输入密码…"
          rules={[{ required: true, message: "请输入密码。" }]}
        />

        <div className={styles.formUtilities}>
          <Link className={styles.inlineAction} to="/auth/recover-password">
            忘记密码？
          </Link>
        </div>

        {challengeRequired ? (
          challengeConfiguration === undefined ? (
            <Alert
              className={styles.registrationNotice}
              type="info"
              showIcon
              title="正在加载安全验证"
              description="请稍候，验证组件加载完成后即可继续登录。"
            />
          ) : challengeConfiguration === null ? (
            <Alert
              className={styles.registrationNotice}
              type="error"
              showIcon
              title="无法加载安全验证"
              description="当前无法继续登录；请检查网络后重新加载验证组件。"
              action={
                <Button size="small" onClick={onRetryChallengeConfiguration}>
                  重新加载
                </Button>
              }
            />
          ) : (
            <TurnstileChallenge
              configuration={challengeConfiguration}
              resetKey={challengeResetKey}
              onToken={onChallengeResponse}
            />
          )
        ) : null}

        <Button
          className={styles.primaryAction}
          type="primary"
          htmlType="submit"
          block
          loading={submitting}
          disabled={
            submitting || (challengeRequired && challengeResponse === null)
          }
        >
          {submitting ? "正在登录…" : "登录"}
        </Button>

        <Divider plain>或</Divider>

        <Link className={styles.secondaryAction} to="/auth/register">
          创建账户
        </Link>
      </Form>

      <ul className={styles.assuranceList} aria-label="账户安全说明">
        <li>
          <UserRound size={18} aria-hidden="true" />
          <span>
            <strong>注册即可登录</strong>
            <small>新账户默认无项目和业务权限。</small>
          </span>
        </li>
        <li>
          <KeyRound size={18} aria-hidden="true" />
          <span>
            <strong>使用独立强密码</strong>
            <small>请勿复用其他网站的密码。</small>
          </span>
        </li>
        <li>
          <ShieldCheck size={18} aria-hidden="true" />
          <span>
            <strong>统一错误反馈</strong>
            <small>失败信息不会提示账户是否存在。</small>
          </span>
        </li>
      </ul>
    </section>
  );
}

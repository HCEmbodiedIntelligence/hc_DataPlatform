import { useEffect, useRef } from "react";
import { Alert, Button, Form, Input } from "antd";
import { UserRound } from "lucide-react";
import { Link } from "react-router-dom";
import type { PasswordPolicyView } from "./api";
import { PasswordField } from "./PasswordField";
import styles from "./styles.module.css";

export interface RegistrationValues {
  readonly username: string;
  readonly password: string;
  readonly confirmPassword: string;
}

interface RegisterPanelProps {
  readonly submitting: boolean;
  readonly error: string | null;
  readonly passwordPolicy: PasswordPolicyView | null;
  readonly onSubmit: (values: RegistrationValues) => Promise<void> | void;
}

function passwordPolicyGuidance(policy: PasswordPolicyView | null): string {
  if (policy === null) {
    return "密码要求将在提交时由服务器安全校验；请使用未在其他网站复用的密码。";
  }
  const guidance = [`密码需为 ${policy.min_length}–${policy.max_length} 个字符`];
  if (policy.disallow_username) guidance.push("且不能包含用户名");
  if (policy.blocked_password_count > 0) guidance.push("常见弱密码会被拒绝");
  return `${guidance.join("，")}。`;
}

export function RegisterPanel({
  submitting,
  error,
  passwordPolicy,
  onSubmit,
}: RegisterPanelProps) {
  const [form] = Form.useForm<RegistrationValues>();
  const errorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  return (
    <section className={styles.authCard} aria-labelledby="register-title">
      <header className={styles.formHeading}>
        <p className={styles.eyebrow}>公开注册</p>
        <h1 id="register-title">创建账户</h1>
        <p>只需设置用户名和密码。注册后可立即登录。</p>
      </header>

      {error ? (
        <div ref={errorRef} tabIndex={-1} className={styles.errorSummary}>
          <Alert type="error" showIcon title="账户未创建" description={error} />
        </div>
      ) : null}

      <Form<RegistrationValues>
        form={form}
        layout="vertical"
        requiredMark={false}
        onFinish={onSubmit}
        onFinishFailed={({ errorFields }) => {
          const first = errorFields[0]?.name;
          if (first) form.getFieldInstance(first)?.focus?.();
        }}
      >
        <Form.Item
          name="username"
          label="用户名"
          htmlFor="auth-register-username"
          extra="用于登录；请勿填写邮箱或手机号。"
          rules={[
            { required: true, whitespace: true, message: "请输入用户名。" },
            {
              validator: (_, value: string | undefined) =>
                value && value !== value.trim()
                  ? Promise.reject(new Error("用户名首尾不能包含空格。"))
                  : Promise.resolve(),
            },
          ]}
        >
          <Input
            id="auth-register-username"
            name="username"
            autoComplete="username"
            spellCheck={false}
            disabled={submitting}
            placeholder="例如：wangxiaoming…"
            prefix={<UserRound size={17} aria-hidden="true" />}
          />
        </Form.Item>

        <PasswordField
          id="auth-register-password"
          name="password"
          label="密码"
          autoComplete="new-password"
          disabled={submitting}
          placeholder="请设置密码…"
          rules={[
            { required: true, message: "请设置密码。" },
            {
              validator: (_, value: string | undefined) => {
                if (!value || passwordPolicy === null) return Promise.resolve();
                const normalizedPassword = value.normalize("NFKC");
                const length = [...normalizedPassword].length;
                if (length < passwordPolicy.min_length) {
                  return Promise.reject(
                    new Error(`密码至少需要 ${passwordPolicy.min_length} 个字符。`),
                  );
                }
                if (length > passwordPolicy.max_length) {
                  return Promise.reject(
                    new Error(`密码不能超过 ${passwordPolicy.max_length} 个字符。`),
                  );
                }
                return Promise.resolve();
              },
            },
          ]}
        />

        <div
          className={styles.passwordStrength}
          data-strength={passwordPolicy === null ? "pending" : "policy"}
          aria-live="polite"
        >
          {passwordPolicyGuidance(passwordPolicy)}
        </div>

        <PasswordField
          id="auth-register-confirm-password"
          name="confirmPassword"
          label="确认密码"
          autoComplete="new-password"
          disabled={submitting}
          placeholder="请再次输入密码…"
          dependencies={["password"]}
          rules={[
            { required: true, message: "请再次输入密码。" },
            ({ getFieldValue }) => ({
              validator: (_, value: string | undefined) =>
                !value || getFieldValue("password") === value
                  ? Promise.resolve()
                  : Promise.reject(new Error("两次输入的密码不一致。")),
            }),
          ]}
        />

        <Alert
          className={styles.registrationNotice}
          type="info"
          showIcon
          title="账户与项目分开授权"
          description="注册不需要邮箱、手机号、验证码或管理员审批；新账户默认没有项目和业务权限。"
        />

        <Button
          className={styles.primaryAction}
          type="primary"
          htmlType="submit"
          block
          loading={submitting}
          disabled={submitting}
        >
          {submitting ? "正在创建账户…" : "创建账户"}
        </Button>

        <Link className={styles.backLink} to="/auth/login">
          返回登录
        </Link>
      </Form>
    </section>
  );
}

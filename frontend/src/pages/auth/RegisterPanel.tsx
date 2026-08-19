import { useEffect, useRef } from "react";
import { Alert, Button, Form, Input } from "antd";
import { UserRound } from "lucide-react";
import { Link } from "react-router-dom";
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
  readonly onSubmit: (values: RegistrationValues) => Promise<void> | void;
}

function passwordStrength(password: string): "empty" | "weak" | "strong" {
  if (!password) return "empty";
  const groups = [/[a-z]/u, /[A-Z]/u, /\d/u, /[^A-Za-z0-9]/u].filter(
    (pattern) => pattern.test(password),
  ).length;
  return password.length >= 12 && groups >= 3 ? "strong" : "weak";
}

export function RegisterPanel({
  submitting,
  error,
  onSubmit,
}: RegisterPanelProps) {
  const [form] = Form.useForm<RegistrationValues>();
  const password = Form.useWatch("password", form) ?? "";
  const strength = passwordStrength(password);
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
          rules={[{ required: true, message: "请设置密码。" }]}
        />

        <div
          className={styles.passwordStrength}
          data-strength={strength}
          aria-live="polite"
        >
          {strength === "strong"
            ? "密码强度较好。"
            : strength === "weak"
              ? "密码强度较弱：建议至少 12 位，并组合大小写字母、数字或符号。"
              : "建议使用至少 12 位且不与其他网站重复的密码。"}
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

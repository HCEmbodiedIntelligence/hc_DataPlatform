import { useEffect, useRef, useState } from "react";
import { Alert, Button, Checkbox, Divider, Form, Input } from "antd";
import { KeyRound, ShieldCheck, UserRound } from "lucide-react";
import { Link } from "react-router-dom";
import type { LoginValues } from "./use-login-flow";
import { PasswordField } from "./PasswordField";
import styles from "./styles.module.css";

interface LoginPanelProps {
  readonly initialUsername?: string;
  readonly submitting: boolean;
  readonly error: string | null;
  readonly onSubmit: (values: LoginValues) => Promise<void> | void;
}

export function LoginPanel({
  initialUsername,
  submitting,
  error,
  onSubmit,
}: LoginPanelProps) {
  const [form] = Form.useForm<LoginValues>();
  const [recoveryVisible, setRecoveryVisible] = useState(false);
  const errorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  return (
    <section className={styles.authCard} aria-labelledby="login-title">
      <header className={styles.formHeading}>
        <p className={styles.eyebrow}>账户认证</p>
        <h1 id="login-title">登录</h1>
        <p>使用已注册的用户名进入 HC 数据平台。</p>
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
        onFinish={onSubmit}
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
          <span title="当前会话合同不支持持久登录">
            <Checkbox disabled>记住我（暂未开放）</Checkbox>
          </span>
          <Button
            type="link"
            className={styles.inlineAction}
            onClick={() => setRecoveryVisible((current) => !current)}
          >
            忘记密码？
          </Button>
        </div>

        {recoveryVisible ? (
          <Alert
            className={styles.recoveryNotice}
            type="info"
            showIcon
            title="当前未提供自助找回"
            description="请联系平台管理员核验身份。页面不会要求邮箱、手机号或验证码。"
            closable
            onClose={() => setRecoveryVisible(false)}
          />
        ) : null}

        <Button
          className={styles.primaryAction}
          type="primary"
          htmlType="submit"
          block
          loading={submitting}
          disabled={submitting}
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

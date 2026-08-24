import { useEffect, useRef, useState } from "react";
import { Alert, Button, Form, Input } from "antd";
import { KeyRound, MailCheck } from "lucide-react";
import {
  confirmRecoveryEmail,
  requestRecoveryEmailVerification,
} from "../../../features/account/api";
import { isDomainError } from "../../../shared/api/domain-error";
import styles from "../styles.module.css";

interface EmailValues {
  readonly recoveryEmail: string;
}

interface ConfirmationValues {
  readonly token: string;
}

function requestIdSuffix(reason: unknown): string {
  return isDomainError(reason) && reason.requestId
    ? ` 请求 ID：${reason.requestId}`
    : "";
}

function recoveryEmailError(reason: unknown): string {
  if (!isDomainError(reason)) return "恢复邮箱操作暂时无法完成。请稍后重试。";
  if (reason.code === "NETWORK_ERROR")
    return "网络连接失败。请检查连接后重试。";
  if (reason.code === "CONTRACT_MISMATCH") {
    return "服务响应未通过恢复邮箱合同校验，页面没有采纳该结果。";
  }
  if (reason.problemCode === "ACCOUNT_RECOVERY_DELIVERY_UNAVAILABLE") {
    return "恢复邮件服务暂时不可用。请联系平台管理员检查邮件配置。";
  }
  if (reason.problemCode === "ACCOUNT_RECOVERY_TOKEN_INVALID") {
    return "验证码无效、已过期或已经使用。请重新发送验证邮件。";
  }
  if (reason.httpStatus === 429) return "请求过于频繁。请稍后再试。";
  if (reason.httpStatus === 422)
    return "恢复邮箱或验证码格式不正确。请检查后重试。";
  if (reason.httpStatus === 409)
    return "恢复邮箱状态已变化。请重新发送验证邮件。";
  return "恢复邮箱操作暂时无法完成。请稍后重试。";
}

export function RecoveryEmailForm({
  disabled,
  onSessionExpired,
}: {
  readonly disabled: boolean;
  readonly onSessionExpired: () => void;
}) {
  const [emailForm] = Form.useForm<EmailValues>();
  const [confirmationForm] = Form.useForm<ConfirmationValues>();
  const [emailPending, setEmailPending] = useState(false);
  const [confirmationPending, setConfirmationPending] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [verificationSent, setVerificationSent] = useState(false);
  const [configuredHint, setConfiguredHint] = useState<string | null>(null);
  const emailInFlightRef = useRef(false);
  const confirmationInFlightRef = useRef(false);
  const feedbackRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (failure !== null || verificationSent || configuredHint !== null) {
      feedbackRef.current?.focus();
    }
  }, [configuredHint, failure, verificationSent]);

  const requestVerification = async ({ recoveryEmail }: EmailValues) => {
    if (emailInFlightRef.current) return;
    emailInFlightRef.current = true;
    setEmailPending(true);
    setFailure(null);
    setVerificationSent(false);
    setConfiguredHint(null);
    try {
      await requestRecoveryEmailVerification(recoveryEmail);
      setVerificationSent(true);
    } catch (reason) {
      if (isDomainError(reason) && reason.httpStatus === 401) {
        onSessionExpired();
        return;
      }
      setFailure(reason);
    } finally {
      emailInFlightRef.current = false;
      setEmailPending(false);
    }
  };

  const confirmVerification = async ({ token }: ConfirmationValues) => {
    if (confirmationInFlightRef.current) return;
    confirmationInFlightRef.current = true;
    setConfirmationPending(true);
    setFailure(null);
    setConfiguredHint(null);
    try {
      const result = await confirmRecoveryEmail(token);
      confirmationForm.resetFields();
      emailForm.resetFields();
      setVerificationSent(false);
      setConfiguredHint(result.recovery_email_hint);
    } catch (reason) {
      if (isDomainError(reason) && reason.httpStatus === 401) {
        onSessionExpired();
        return;
      }
      setFailure(reason);
    } finally {
      confirmationInFlightRef.current = false;
      setConfirmationPending(false);
    }
  };

  const pending = emailPending || confirmationPending;

  return (
    <section
      className={styles.formSection}
      aria-labelledby="recovery-email-title"
    >
      <header className={styles.sectionHeader}>
        <span className={styles.sectionIcon} aria-hidden="true">
          <MailCheck size={20} />
        </span>
        <div>
          <h2 id="recovery-email-title">恢复邮箱</h2>
          <p>验证后可用于自助找回密码；平台不会把未验证地址用于账户恢复。</p>
        </div>
      </header>

      <div ref={feedbackRef} tabIndex={-1}>
        {failure !== null ? (
          <Alert
            className={styles.feedback}
            description={`${recoveryEmailError(failure)}${requestIdSuffix(failure)}`}
            role="alert"
            showIcon
            title="恢复邮箱未更新"
            type="error"
          />
        ) : null}
        {verificationSent ? (
          <Alert
            className={styles.feedback}
            description="验证码已发送。请从邮件中复制一次性验证码并在下方确认。"
            role="status"
            showIcon
            title="验证邮件已发送"
            type="success"
          />
        ) : null}
        {configuredHint !== null ? (
          <Alert
            className={styles.feedback}
            description={`已验证恢复邮箱：${configuredHint}`}
            role="status"
            showIcon
            title="恢复邮箱已更新"
            type="success"
          />
        ) : null}
      </div>

      <Form<EmailValues>
        form={emailForm}
        layout="vertical"
        requiredMark={false}
        onFinish={(values) => void requestVerification(values)}
      >
        <Form.Item
          htmlFor="account-recovery-email"
          label="恢复邮箱"
          name="recoveryEmail"
          rules={[
            { required: true, whitespace: true, message: "请输入恢复邮箱。" },
            { type: "email", message: "请输入有效的邮箱地址。" },
            { max: 254, message: "邮箱地址不能超过 254 个字符。" },
          ]}
        >
          <Input
            autoComplete="email"
            disabled={disabled || pending}
            id="account-recovery-email"
            maxLength={254}
            name="recoveryEmail"
            placeholder="name@example.com"
            prefix={<MailCheck aria-hidden="true" size={17} />}
            spellCheck={false}
            type="email"
          />
        </Form.Item>
        <div className={styles.formActions}>
          <Button
            disabled={disabled || pending}
            htmlType="submit"
            loading={emailPending}
            type="primary"
          >
            {emailPending ? "正在发送…" : "发送验证邮件"}
          </Button>
        </div>
      </Form>

      <div className={styles.recoveryConfirmation}>
        <Form<ConfirmationValues>
          form={confirmationForm}
          layout="vertical"
          requiredMark={false}
          onFinish={(values) => void confirmVerification(values)}
        >
          <Form.Item
            htmlFor="account-recovery-token"
            label="邮件验证码"
            name="token"
            rules={[
              {
                required: true,
                whitespace: true,
                message: "请输入邮件验证码。",
              },
              { min: 16, message: "验证码格式不正确。" },
              { max: 512, message: "验证码格式不正确。" },
            ]}
          >
            <Input
              autoComplete="one-time-code"
              disabled={disabled || pending}
              id="account-recovery-token"
              maxLength={512}
              name="token"
              placeholder="粘贴邮件中的一次性验证码"
              prefix={<KeyRound aria-hidden="true" size={17} />}
              spellCheck={false}
            />
          </Form.Item>
          <div className={styles.formActions}>
            <Button
              disabled={disabled || pending}
              htmlType="submit"
              loading={confirmationPending}
            >
              {confirmationPending ? "正在确认…" : "确认恢复邮箱"}
            </Button>
          </div>
        </Form>
      </div>
    </section>
  );
}

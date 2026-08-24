import { Alert, Button, Form, Input, Modal, type InputRef } from "antd";
import { Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { requestProjectMembership } from "../../pages/auth/api";
import { isDomainError } from "../../shared/api/domain-error";
import styles from "./PlatformShell.module.css";

interface ProjectMembershipRequestDialogProps {
  readonly afterClose: () => void;
  readonly open: boolean;
  readonly organizationId: string;
  readonly onClose: () => void;
}

function requestErrorDescription(error: unknown): string {
  if (!isDomainError(error)) {
    return error instanceof Error ? error.message : "申请未完成，请稍后重试。";
  }
  return `${error.message}${error.problemCode ? ` 问题代码：${error.problemCode}。` : ""}${error.requestId ? ` 请求 ID：${error.requestId}。` : ""}${error.retryable ? " 服务端允许重试。" : ""}`;
}

function newSubmissionKey(): string {
  return globalThis.crypto.randomUUID();
}

export default function ProjectMembershipRequestDialog({
  afterClose,
  open,
  organizationId,
  onClose,
}: ProjectMembershipRequestDialogProps) {
  const [projectId, setProjectId] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const submissionKey = useRef<string | null>(null);
  const submissionInFlight = useRef(false);
  const projectInputRef = useRef<InputRef>(null);
  const normalizedProjectId = projectId.trim();
  if (submissionKey.current === null)
    submissionKey.current = newSubmissionKey();

  useEffect(() => {
    if (!open) return;
    setProjectId("");
    setReason("");
    setSubmitting(false);
    setError(null);
    setRequestId(null);
    submissionInFlight.current = false;
    submissionKey.current = newSubmissionKey();
    const focusTimer = globalThis.setTimeout(
      () => projectInputRef.current?.focus(),
      0,
    );
    return () => globalThis.clearTimeout(focusTimer);
  }, [open, organizationId]);

  const updateIntent = (update: () => void) => {
    update();
    setError(null);
    setRequestId(null);
    submissionKey.current = newSubmissionKey();
  };

  const close = () => {
    if (submissionInFlight.current) return;
    onClose();
  };

  async function submit(): Promise<void> {
    if (
      submissionInFlight.current ||
      normalizedProjectId.length === 0 ||
      requestId !== null
    ) {
      return;
    }
    submissionInFlight.current = true;
    setSubmitting(true);
    setError(null);
    const idempotencyKey = submissionKey.current ?? newSubmissionKey();
    submissionKey.current = idempotencyKey;
    try {
      const result = await requestProjectMembership(
        organizationId,
        normalizedProjectId,
        reason.trim() || null,
        idempotencyKey,
      );
      setRequestId(result.request_id);
    } catch (caught) {
      setError(caught);
    } finally {
      submissionInFlight.current = false;
      setSubmitting(false);
    }
  }

  return (
    <Modal
      afterClose={afterClose}
      afterOpenChange={(nextOpen) => {
        if (nextOpen) projectInputRef.current?.focus();
      }}
      closable={!submitting}
      destroyOnHidden
      focusable={{ focusTriggerAfterClose: false }}
      footer={null}
      keyboard={!submitting}
      mask={{ closable: false }}
      maskTransitionName=""
      open={open}
      rootClassName={styles.membershipRequestModal}
      title="申请加入项目"
      transitionName=""
      width={560}
      onCancel={close}
    >
      <p className={styles.membershipRequestIntro}>
        填写项目管理员提供的项目 ID。平台不会搜索或展示您尚未加入的项目。
      </p>
      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <Form.Item
          extra="当前会话所在组织，提交时不可更改。"
          htmlFor="shell-membership-organization-id"
          label="当前组织 ID"
        >
          <Input
            id="shell-membership-organization-id"
            readOnly
            value={organizationId}
          />
        </Form.Item>
        <Form.Item
          extra="必填，由项目管理员提供。"
          htmlFor="shell-membership-project-id"
          label="项目 ID"
          required
        >
          <Input
            ref={projectInputRef}
            aria-required="true"
            autoComplete="off"
            disabled={submitting || requestId !== null}
            id="shell-membership-project-id"
            maxLength={256}
            name="project-id"
            placeholder="请输入项目 ID"
            spellCheck={false}
            value={projectId}
            onChange={(event) =>
              updateIntent(() => setProjectId(event.target.value))
            }
          />
        </Form.Item>
        <Form.Item htmlFor="shell-membership-reason" label="申请说明（选填）">
          <Input.TextArea
            disabled={submitting || requestId !== null}
            id="shell-membership-reason"
            maxLength={500}
            name="reason"
            placeholder="说明您的使用场景，便于管理员审批"
            rows={4}
            value={reason}
            onChange={(event) =>
              updateIntent(() => setReason(event.target.value))
            }
          />
        </Form.Item>

        {error ? (
          <Alert
            className={styles.membershipRequestFeedback}
            description={requestErrorDescription(error)}
            role="alert"
            showIcon
            title="加入申请未提交"
            type="error"
          />
        ) : null}
        {requestId ? (
          <Alert
            className={styles.membershipRequestFeedback}
            description={
              <span>
                申请 ID：<code>{requestId}</code>
                。审批通过后，请刷新会话或重新登录，项目才会进入项目列表。
              </span>
            }
            role="status"
            showIcon
            title="项目加入申请已提交"
            type="success"
          />
        ) : null}

        <div className={styles.membershipRequestActions}>
          <Button disabled={submitting} onClick={close}>
            {requestId ? "关闭" : "取消"}
          </Button>
          <Button
            disabled={
              submitting ||
              normalizedProjectId.length === 0 ||
              requestId !== null
            }
            htmlType="submit"
            icon={<Send aria-hidden="true" size={16} />}
            loading={submitting}
            type="primary"
          >
            提交加入申请
          </Button>
        </div>
      </form>
    </Modal>
  );
}

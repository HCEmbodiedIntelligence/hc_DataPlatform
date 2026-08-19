import { Alert, Button, Input } from "antd";
import { RefreshCw, Send } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { isDomainError } from "../../shared/api/domain-error";
import {
  requestProjectCapabilities,
  requestProjectMembership,
} from "./api";
import type { EmptyAccountIntent } from "./EmptyAccountStatus";
import styles from "./styles.module.css";

interface EmptyAccountRequestPanelProps {
  readonly intent: EmptyAccountIntent | null;
  readonly refreshing: boolean;
  readonly onRefresh: () => Promise<void>;
}

function parseCapabilities(value: string): readonly string[] {
  return [
    ...new Set(
      value
        .split(/[\s,，]+/u)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ];
}

function errorDescription(error: unknown): string {
  if (!isDomainError(error)) {
    return error instanceof Error ? error.message : "申请未完成，请稍后重试。";
  }
  return `${error.message}${error.problemCode ? ` 问题代码：${error.problemCode}。` : ""}${error.requestId ? ` 请求 ID：${error.requestId}。` : ""}${error.retryable ? " 服务端允许重试。" : ""}`;
}

export function EmptyAccountRequestPanel({
  intent,
  refreshing,
  onRefresh,
}: EmptyAccountRequestPanelProps) {
  const [projectId, setProjectId] = useState("");
  const [reason, setReason] = useState("");
  const [capabilityText, setCapabilityText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [success, setSuccess] = useState<{
    readonly kind: "membership" | "capability";
    readonly requestId: string;
  } | null>(null);
  const submissionKey = useRef(globalThis.crypto.randomUUID());
  const capabilities = useMemo(
    () => parseCapabilities(capabilityText),
    [capabilityText],
  );
  const normalizedProjectId = projectId.trim();
  const projectError =
    normalizedProjectId && normalizedProjectId.length <= 256
      ? null
      : projectId
        ? "项目 ID 不能超过 256 个字符。"
        : "请输入管理员提供的真实项目 ID。";
  const capabilityError =
    intent !== "capability" || capabilities.length > 0
      ? null
      : "至少填写一个要申请的 capability。";

  async function submit(): Promise<void> {
    if (
      submitting ||
      !intent ||
      intent === "history" ||
      projectError ||
      capabilityError
    ) {
      return;
    }
    setSubmitting(true);
    setError(null);
    setSuccess(null);
    try {
      if (intent === "membership") {
        const result = await requestProjectMembership(
          normalizedProjectId,
          reason.trim() || null,
          submissionKey.current,
        );
        setSuccess({ kind: "membership", requestId: result.request_id });
      } else {
        const result = await requestProjectCapabilities(
          normalizedProjectId,
          capabilities,
          reason.trim() || null,
          submissionKey.current,
        );
        setSuccess({ kind: "capability", requestId: result.request_id });
      }
      submissionKey.current = globalThis.crypto.randomUUID();
    } catch (caught) {
      setError(caught);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className={styles.emptyRequestCard} aria-labelledby="empty-account-request-title">
      <header>
        <p className={styles.eyebrow}>PROJECT ACCESS</p>
        <h2 id="empty-account-request-title">
          {intent === "membership"
            ? "申请加入项目"
            : intent === "capability"
              ? "申请项目权限"
              : intent === "history"
                ? "检查申请状态"
                : "建立项目访问范围"}
        </h2>
        <p>
          所有申请都通过正式 Access API 提交并等待项目管理员决定；账户注册本身不需要审批。
        </p>
      </header>

      {intent === null ? (
        <div className={styles.emptyRequestGuide}>
          <p>从右侧选择“申请加入项目”或“申请权限”。</p>
          <p>项目 ID 必须由项目管理员提供，本页不会展示或猜测可加入项目。</p>
        </div>
      ) : intent === "history" ? (
        <div className={styles.emptyRequestGuide}>
          <p>
            当前正式合同没有不带项目作用域的全局申请历史。审批结果生效后，可刷新会话范围进入项目。
          </p>
          <Button
            icon={<RefreshCw aria-hidden="true" size={16} />}
            loading={refreshing}
            onClick={() => void onRefresh()}
          >
            刷新会话范围
          </Button>
        </div>
      ) : (
        <form
          className={styles.emptyRequestForm}
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label htmlFor="empty-request-project-id">
            <span>项目 ID</span>
            <Input
              autoComplete="off"
              id="empty-request-project-id"
              maxLength={256}
              name="project-id"
              placeholder="由项目管理员提供"
              spellCheck={false}
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
            />
            {projectId && projectError ? <small role="alert">{projectError}</small> : null}
          </label>
          {intent === "capability" ? (
            <label htmlFor="empty-request-capabilities">
              <span>Capability（逗号或换行分隔）</span>
              <Input.TextArea
                autoComplete="off"
                id="empty-request-capabilities"
                maxLength={2_000}
                name="capabilities"
                placeholder="例如 collection.upload, annotation.write"
                rows={4}
                spellCheck={false}
                value={capabilityText}
                onChange={(event) => setCapabilityText(event.target.value)}
              />
              <small>{capabilities.length} 项，将由服务端校验允许范围。</small>
            </label>
          ) : null}
          <label htmlFor="empty-request-reason">
            <span>申请说明（选填）</span>
            <Input.TextArea
              id="empty-request-reason"
              maxLength={500}
              name="reason"
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          {error ? (
            <Alert
              showIcon
              type="error"
              title="申请未完成"
              description={errorDescription(error)}
            />
          ) : null}
          {success ? (
            <Alert
              showIcon
              type="success"
              title={
                success.kind === "membership"
                  ? "项目加入申请已提交"
                  : "权限申请已提交"
              }
              description={`申请 ID：${success.requestId}。请等待另一位项目管理员审批后刷新会话范围。`}
            />
          ) : null}
          <div className={styles.emptyRequestActions}>
            <Button
              htmlType="submit"
              type="primary"
              icon={<Send aria-hidden="true" size={16} />}
              disabled={Boolean(projectError || capabilityError)}
              loading={submitting}
            >
              {intent === "membership" ? "提交加入申请" : "提交权限申请"}
            </Button>
            <Button
              icon={<RefreshCw aria-hidden="true" size={16} />}
              loading={refreshing}
              onClick={() => void onRefresh()}
            >
              审批后刷新
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}

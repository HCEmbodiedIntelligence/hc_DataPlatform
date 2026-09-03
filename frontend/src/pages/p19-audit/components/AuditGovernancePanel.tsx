import { Button, Input, InputNumber, Tag } from "antd";
import {
  Download,
  FileLock2,
  Play,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  useAuditExport,
  useAuditLegalHolds,
  useAuditRetentionPolicy,
  useAuthorizeAuditExportDownload,
  useCancelAuditExport,
  useCreateAuditExport,
  useCreateAuditLegalHold,
  useReleaseAuditLegalHold,
  useRetryAuditExport,
  useUpdateAuditRetentionPolicy,
} from "../../../features/audit/api/queries";
import type { AuditScope } from "../../../features/audit/types";
import { isDomainError } from "../../../shared/api/domain-error";
import { PageState, StatusTag } from "../../../shared/ui";
import styles from "../styles.module.css";

type Props = Readonly<{
  scope: AuditScope;
  occurredFrom: string;
  occurredTo: string;
  canManage: boolean;
}>;

const statusCopy = {
  QUEUED: "排队中",
  RUNNING: "导出中",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
} as const;

function errorText(error: unknown): string | null {
  if (!error) return null;
  return isDomainError(error) ? error.message : "治理操作失败，请重试";
}

function errorRequestId(error: unknown): string | null {
  return isDomainError(error) ? error.requestId : null;
}

export function AuditGovernancePanel({
  scope,
  occurredFrom,
  occurredTo,
  canManage,
}: Props) {
  const policy = useAuditRetentionPolicy(scope, true);
  const holds = useAuditLegalHolds(scope, true);
  const updatePolicy = useUpdateAuditRetentionPolicy(scope);
  const createHold = useCreateAuditLegalHold(scope);
  const releaseHold = useReleaseAuditLegalHold(scope);
  const createExport = useCreateAuditExport(scope);
  const cancelExport = useCancelAuditExport(scope);
  const retryExport = useRetryAuditExport(scope);
  const download = useAuthorizeAuditExportDownload(scope);
  const [standardDays, setStandardDays] = useState(365);
  const [securityDays, setSecurityDays] = useState(2555);
  const [holdReason, setHoldReason] = useState("");
  const [exportJobId, setExportJobId] = useState<string | null>(null);
  const exportJob = useAuditExport(scope, exportJobId, exportJobId !== null);

  useEffect(() => {
    if (!policy.data) return;
    setStandardDays(policy.data.standardDays);
    setSecurityDays(policy.data.securityDays);
  }, [policy.data]);

  useEffect(
    () => setExportJobId(null),
    [scope.organizationId, scope.projectId, scope.regionCode],
  );

  if (policy.isPending || holds.isPending)
    return <PageState state="loading" label="审计治理" />;
  if (policy.error || holds.error)
    return (
      <PageState
        state="error"
        label="审计治理"
        requestId={errorRequestId(policy.error ?? holds.error)}
        onRetry={() => void Promise.all([policy.refetch(), holds.refetch()])}
      />
    );
  if (!policy.data) return null;

  const activeHolds = (holds.data ?? []).filter(
    (hold) => hold.status === "ACTIVE",
  );
  const mutationError =
    updatePolicy.error ??
    createHold.error ??
    releaseHold.error ??
    createExport.error ??
    cancelExport.error ??
    retryExport.error ??
    download.error;
  const job = exportJob.data;

  return (
    <section
      className={styles.governancePanel}
      aria-labelledby="audit-governance-title"
    >
      <header className={styles.governanceHeader}>
        <div>
          <span>治理控制面</span>
          <h2 id="audit-governance-title">留存、冻结与脱敏导出</h2>
        </div>
        <StatusTag
          status={activeHolds.length ? "ACTIVE_HOLDS" : "NO_ACTIVE_HOLDS"}
          label={
            activeHolds.length
              ? `${activeHolds.length} 个有效冻结`
              : "无有效冻结"
          }
          tone={activeHolds.length ? "warning" : "success"}
        />
      </header>

      <div className={styles.governanceGrid}>
        <article className={styles.governanceCard}>
          <div className={styles.governanceCardTitle}>
            <ShieldCheck aria-hidden="true" size={17} />
            <strong>留存策略 v{policy.data.policyVersion}</strong>
          </div>
          <div className={styles.retentionFields}>
            <label>
              <span>标准事件（天）</span>
              <InputNumber
                min={30}
                max={3650}
                value={standardDays}
                disabled={!canManage}
                onChange={(value) => value !== null && setStandardDays(value)}
              />
            </label>
            <label>
              <span>安全事件（天）</span>
              <InputNumber
                min={90}
                max={3650}
                value={securityDays}
                disabled={!canManage}
                onChange={(value) => value !== null && setSecurityDays(value)}
              />
            </label>
          </div>
          <div className={styles.governanceActions}>
            <span>更新于 {policy.data.updatedAt}</span>
            {canManage ? (
              <Button
                size="small"
                loading={updatePolicy.isPending}
                disabled={
                  standardDays === policy.data.standardDays &&
                  securityDays === policy.data.securityDays
                }
                onClick={() =>
                  updatePolicy.mutate({
                    standardDays,
                    securityDays,
                    etag: policy.data.etag,
                  })
                }
              >
                保存策略
              </Button>
            ) : null}
          </div>
        </article>

        <article className={styles.governanceCard}>
          <div className={styles.governanceCardTitle}>
            <FileLock2 aria-hidden="true" size={17} />
            <strong>Legal Hold</strong>
          </div>
          {activeHolds.length ? (
            <ul className={styles.holdList}>
              {activeHolds.slice(0, 3).map((hold) => (
                <li key={hold.holdId}>
                  <div>
                    <strong>{hold.reason}</strong>
                    <span>
                      {hold.occurredFrom} → {hold.occurredTo}
                    </span>
                  </div>
                  {canManage ? (
                    <Button
                      size="small"
                      danger
                      loading={
                        releaseHold.isPending &&
                        releaseHold.variables === hold.holdId
                      }
                      onClick={() => releaseHold.mutate(hold.holdId)}
                    >
                      解除
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <span className={styles.governanceMuted}>
              当前作用域没有有效冻结。
            </span>
          )}
          {canManage ? (
            <div className={styles.holdComposer}>
              <Input
                value={holdReason}
                maxLength={2000}
                placeholder="冻结原因（覆盖当前筛选时间窗）"
                aria-label="Legal Hold 原因"
                onChange={(event) => setHoldReason(event.target.value)}
              />
              <Button
                size="small"
                loading={createHold.isPending}
                disabled={holdReason.trim().length < 3}
                onClick={() =>
                  createHold.mutate(
                    { reason: holdReason.trim(), occurredFrom, occurredTo },
                    { onSuccess: () => setHoldReason("") },
                  )
                }
              >
                冻结当前时间窗
              </Button>
            </div>
          ) : null}
        </article>

        <article className={styles.governanceCard}>
          <div className={styles.governanceCardTitle}>
            <Download aria-hidden="true" size={17} />
            <strong>脱敏 JSONL 导出</strong>
          </div>
          {job ? (
            <div className={styles.exportStatus} role="status">
              <div>
                <Tag>{statusCopy[job.status]}</Tag>
                <span>
                  {job.exportedEventCount.toLocaleString("zh-CN")} 条事件
                </span>
              </div>
              {job.status === "RUNNING" ? (
                <span className={styles.governanceMuted}>
                  已完成 {job.scannedPageCount.toLocaleString("zh-CN")} 个分页；
                  任务仍在服务端流式执行。
                </span>
              ) : null}
              {job.errorMessage ? (
                <span className={styles.governanceError}>
                  {job.errorMessage}
                </span>
              ) : null}
            </div>
          ) : (
            <span className={styles.governanceMuted}>
              导出沿用当前时间窗；服务端先校验审计链，再流式物化脱敏投影。
            </span>
          )}
          {canManage ? (
            <div className={styles.governanceActions}>
              {!job ? (
                <Button
                  size="small"
                  type="primary"
                  icon={<Play aria-hidden="true" size={14} />}
                  loading={createExport.isPending}
                  onClick={() =>
                    createExport.mutate(
                      {
                        occurredFrom,
                        occurredTo,
                        idempotencyKey: crypto.randomUUID(),
                      },
                      { onSuccess: (created) => setExportJobId(created.jobId) },
                    )
                  }
                >
                  创建导出
                </Button>
              ) : null}
              {job?.status === "QUEUED" || job?.status === "RUNNING" ? (
                <Button
                  size="small"
                  onClick={() => cancelExport.mutate(job.jobId)}
                >
                  取消
                </Button>
              ) : null}
              {job?.status === "FAILED" || job?.status === "CANCELLED" ? (
                <Button
                  size="small"
                  icon={<RotateCcw aria-hidden="true" size={14} />}
                  onClick={() => retryExport.mutate(job.jobId)}
                >
                  重试
                </Button>
              ) : null}
              {job?.status === "SUCCEEDED" ? (
                <Button
                  size="small"
                  type="primary"
                  icon={<Download aria-hidden="true" size={14} />}
                  loading={download.isPending}
                  onClick={() =>
                    download.mutate(job.jobId, {
                      onSuccess: (authorization) =>
                        window.location.assign(authorization.downloadUrl),
                    })
                  }
                >
                  安全下载
                </Button>
              ) : null}
            </div>
          ) : null}
        </article>
      </div>
      {mutationError ? (
        <p className={styles.governanceError} role="alert">
          {errorText(mutationError)}
        </p>
      ) : null}
    </section>
  );
}

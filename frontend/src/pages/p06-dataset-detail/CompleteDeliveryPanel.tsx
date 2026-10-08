import { useEffect, useRef } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Progress } from "antd";
import type { Scope } from "../../entities/scope";
import type { ReviewDataRef } from "../../features/datasets/episodes/data-reviews";
import {
  authorizeCompleteDelivery,
  commandCompleteDelivery,
  listCompleteDeliveries,
  requestCompleteDelivery,
  type CompleteDeliveryJob,
} from "../../features/datasets/episodes/complete-deliveries";
import styles from "./EpisodeDetailPanel.module.css";

const phases = {
  queued: "等待打包",
  verifying: "校验来源",
  archiving: "正在归档",
  uploading: "上传归档",
  completed: "完整包就绪",
  failed: "打包失败",
  cancelled: "已取消",
};
export function CompleteDeliveryPanel({
  scope,
  data,
  canDownload,
  canCreate,
}: {
  scope: Scope;
  data: ReviewDataRef;
  canDownload: boolean;
  canCreate: boolean;
}) {
  const lifetime = useRef<AbortController | null>(null);
  const key = useRef<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    return () => controller.abort();
  }, []);
  const query = useQuery({
    queryKey: ["episode-complete-deliveries", scope, data],
    queryFn: ({ signal }) => listCompleteDeliveries(scope, data, signal),
    enabled: canDownload,
    refetchInterval: (q) =>
      q.state.data?.some(
        (job) => job.status === "PENDING" || job.status === "RUNNING",
      )
        ? 1000
        : false,
  });
  const mutation = useMutation({
    mutationFn: async (command: {
      action: "create" | "download" | "cancel" | "retry";
      job?: CompleteDeliveryJob;
    }) => {
      const signal = lifetime.current!.signal;
      if (command.action === "create") {
        key.current ??= crypto.randomUUID();
        return requestCompleteDelivery(scope, data, key.current, signal);
      }
      const job = command.job!;
      if (command.action !== "download")
        return commandCompleteDelivery(
          scope,
          data,
          job,
          command.action,
          signal,
        );
      const grant = await authorizeCompleteDelivery(scope, data, job, signal);
      signal.throwIfAborted();
      const anchor = document.createElement("a");
      anchor.href = grant.url;
      anchor.download = "";
      anchor.rel = "noopener noreferrer";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
    },
    onSuccess: async () => {
      await query.refetch();
    },
  });
  if (!canDownload) return <Alert type="warning" title="缺少完整包下载权限" />;
  return (
    <Card title="下载完整数据（含已采集深度）" size="small">
      <div className={styles.stack}>
        <p>
          包含数值、RGB、深度视频、深度
          profile、有效性掩码和源帧映射。复用原文件，不重复转换；需要带标注版本时进入标注工作区选择修订。
        </p>
        <div className={styles.actions}>
          <Button
            type="primary"
            disabled={!canCreate || mutation.isPending}
            onClick={() => mutation.mutate({ action: "create" })}
          >
            准备完整数据下载
          </Button>
          <Button
            loading={query.isFetching}
            onClick={() => void query.refetch()}
          >
            刷新打包状态
          </Button>
        </div>
        {!canCreate && (
          <p>可以下载已就绪归档；创建、取消和重试打包需要导出创建权限。</p>
        )}
        {query.error && (
          <Alert
            type="error"
            title="打包状态读取失败"
            description={query.error.message}
          />
        )}
        {mutation.error && (
          <Alert
            type="error"
            title="完整包操作失败"
            description={mutation.error.message}
          />
        )}
        {query.isPending && <p>读取打包状态…</p>}
        {query.data && !query.error && query.data.length === 0 && (
          <p>此版本尚无完整包归档。</p>
        )}
        {query.data &&
          !query.error &&
          query.data.map((job) => (
            <Card
              key={job.job_id}
              size="small"
              title={`${job.plan.archive_format === "independent_episode_zip/1" ? "单条 Episode" : `${job.plan.members.length} 条独立标准包批量 ZIP`} · ${phases[job.phase]}`}
            >
              <p>
                归档 {job.processed_files} / {job.total_files} 个文件 ·{" "}
                {job.processed_bytes.toLocaleString()} /{" "}
                {job.total_bytes.toLocaleString()} 字节
              </p>
              <Progress
                percent={Math.floor(
                  (job.processed_bytes / job.total_bytes) * 100,
                )}
                status={
                  job.status === "FAILED"
                    ? "exception"
                    : job.status === "READY"
                      ? "success"
                      : "normal"
                }
              />
              {job.phase === "uploading" && (
                <p>
                  上传 {job.uploaded_bytes.toLocaleString()} /{" "}
                  {job.size_bytes?.toLocaleString()} 字节
                </p>
              )}
              {job.error_code && (
                <Alert
                  type="error"
                  title={
                    job.error_code === "EPISODE_DELIVERY_INTEGRITY_FAILED"
                      ? "文件校验失败"
                      : "打包未完成"
                  }
                  description="重试会保留相同数据版本；若持续失败，请联系项目管理员检查文件和服务。"
                />
              )}
              <div className={styles.actions}>
                <Button
                  disabled={job.status !== "READY" || mutation.isPending}
                  onClick={() => mutation.mutate({ action: "download", job })}
                >
                  下载完整 ZIP
                </Button>
                {(job.status === "PENDING" || job.status === "RUNNING") && (
                  <Button
                    disabled={!canCreate || mutation.isPending}
                    onClick={() => mutation.mutate({ action: "cancel", job })}
                  >
                    取消打包
                  </Button>
                )}
                {(job.status === "FAILED" || job.status === "CANCELLED") && (
                  <Button
                    disabled={!canCreate || mutation.isPending}
                    onClick={() => mutation.mutate({ action: "retry", job })}
                  >
                    重试打包
                  </Button>
                )}
              </div>
              <details>
                <summary>交付清单与哈希</summary>
                <pre className={styles.technical}>
                  {JSON.stringify(
                    {
                      plan: job.plan,
                      artifact_sha256: job.artifact_sha256,
                      size_bytes: job.size_bytes,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
            </Card>
          ))}
      </div>
    </Card>
  );
}

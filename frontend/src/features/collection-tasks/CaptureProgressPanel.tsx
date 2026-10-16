import { ResourceLabel } from "../../shared/lib/resource-labels";
import { RecordingProcessingDetails } from "../quality/RecordingProcessingDetails";
import ResumeProcessingAction from "../ingest/processing/ResumeProcessingAction";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Drawer, Pagination, Tag } from "antd";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { components } from "../../shared/api/generated/platform";
import { request } from "../../shared/api/http-client";
import type { CollectionTaskScope } from "./api";
import styles from "./CaptureProgressPanel.module.css";

type CapturePage = components["schemas"]["CaptureProgressPage"];
const labels: Record<string, string> = {
  UPLOADING: "上传中",
  RECEIVED: "已接收，等待处理",
  PREPARING: "自动校验与预处理",
  AWAITING_SLICE: "已校验，待切片",
  PROCESSING: "逐段质检与入库",
  READY: "已入库",
  FAILED: "处理未通过",
  BLOCKED: "等待处理配置",
  CANCELLED: "已取消",
  DISCARDED: "成片已删除",
};

/** One fact-based view reused by uploads and collection tasks. */
export function CaptureProgressPanel({
  scope,
  taskId,
  robotId,
  datasetId,
}: {
  readonly scope: CollectionTaskScope;
  readonly taskId?: string;
  readonly robotId?: string;
  readonly datasetId?: string;
}) {
  const [page, setPage] = useState(1);
  const [detailsId, setDetailsId] = useState<string | null>(null);
  const query = useQuery({
    queryKey: [
      "capture-progress",
      scope.organizationId,
      scope.projectId,
      scope.regionCode,
      taskId,
      robotId,
      datasetId,
      page,
    ],
    queryFn: ({ signal }) =>
      request<CapturePage>({
        method: "GET",
        scope,
        signal,
        path: `/projects/${encodeURIComponent(scope.projectId)}/capture-progress`,
        query: {
          collection_task_id: taskId,
          robot_id: robotId,
          dataset_id: datasetId,
          limit: 10,
          offset: (page - 1) * 10,
        },
      }),
    retry: false,
    refetchInterval: 5_000,
  });
  const counts = query.data?.counts;
  return (
    <section className={styles.panel} aria-label="采集上传与入库进度">
      <header>
        <div>
          <h2>采集上传与入库</h2>
          <p>
            每次采集独立记录，接收成功后自动校验；连续录制提交切片后逐段质检入库。
          </p>
        </div>
        <Button loading={query.isFetching} onClick={() => void query.refetch()}>
          刷新
        </Button>
      </header>
      {query.isError ? (
        <Alert
          type="error"
          showIcon
          message="采集进度读取失败"
          description={query.error.message}
        />
      ) : null}
      {query.isPending ? <p role="status">正在读取采集记录…</p> : null}
      {counts && (
        <div className={styles.counts}>
          <span>
            上传 <b>{counts.upload_count}</b> 次
          </span>
          <span>
            已接收 <b>{counts.received_count}</b> 条录制
          </span>
          <span>
            待切片 <b>{counts.awaiting_slice_count}</b> 条
          </span>
          <span>
            处理中 <b>{counts.processing_count}</b> 条
          </span>
          <span>
            待配置 <b>{counts.blocked_count}</b> 条
          </span>
          <span>
            成片 <b>{counts.episode_count}</b> 条
          </span>
          <span>
            已入库 <b>{counts.ready_episode_count}</b> 条
          </span>
          <span>
            未通过 <b>{counts.failed_episode_count}</b> 条成片 /{" "}
            <b>{counts.failed_count}</b> 次采集
          </span>
        </div>
      )}
      <div className={styles.rows}>
        {query.data?.items.map((item) => (
          <article
            key={`${item.upload_id}:${item.recording_id ?? "raw"}`}
            className={styles.row}
          >
            <div>
              <strong>
                <ResourceLabel kind="robot" id={item.robot_id} scope={scope} />
              </strong>
              <small>
                {new Date(item.created_at).toLocaleString("zh-CN")} ·{" "}
                {item.duration_seconds?.toFixed(1) ?? "待统计"} 秒
              </small>
              <small>
                <ResourceLabel
                  kind="rollout"
                  id={item.recording_id ?? item.rollout_id}
                  scope={scope}
                />
              </small>
            </div>
            <div className={styles.stages}>
              <Tag color={item.received ? "success" : "processing"}>
                {item.received
                  ? "上传完成"
                  : item.upload_state === "CANCELLED"
                    ? "上传已取消"
                    : item.upload_state === "FAILED"
                      ? "上传失败"
                      : "上传中"}
              </Tag>
              <Tag color={item.received ? "success" : undefined}>
                {item.received ? "平台已接收" : "等待接收"}
              </Tag>
              <Tag
                color={
                  item.stage === "FAILED"
                    ? "error"
                    : item.stage === "READY"
                      ? "success"
                      : "processing"
                }
              >
                {labels[item.stage]}
              </Tag>
              <small>
                {item.episode_count} 条成片 · {item.ready_episode_count}{" "}
                条已入库 · {item.failed_episode_count} 条未通过
              </small>
              {item.error_code && (
                <small role="status">原因：{item.error_code}</small>
              )}
              {item.stage === "PREPARING" && (
                <small role="status">
                  正在校验与预处理；服务中断后会自动检测并重试，无需重复上传。
                </small>
              )}
              {!item.recording_id &&
                item.source_import_id &&
                item.capture_mode === "CONTINUOUS" &&
                ["FAILED", "BLOCKED", "PREPARING"].includes(item.stage) && (
                  <ResumeProcessingAction
                    scope={scope}
                    importId={item.source_import_id}
                    robotId={item.robot_id}
                  />
                )}
            </div>
            <div className={styles.actions}>
              {item.recording_id && (
                <Button
                  size="small"
                  onClick={() => setDetailsId(item.recording_id!)}
                >
                  处理详情
                </Button>
              )}
              {item.recording_id && (
                <Link
                  to={`/recordings/${encodeURIComponent(item.recording_id)}/slice`}
                >
                  {item.stage === "AWAITING_SLICE"
                    ? "查看录制 / 切片"
                    : "查看录制与成片"}
                </Link>
              )}
              {item.ready_episode_count > 0 && (
                <Link to={`/datasets/${encodeURIComponent(item.dataset_id)}`}>
                  查看数据集
                </Link>
              )}
            </div>
          </article>
        ))}
        {query.data?.total === 0 && <p>当前还没有采集上传记录。</p>}
      </div>
      {(query.data?.total ?? 0) > 10 && (
        <Pagination
          current={page}
          pageSize={10}
          total={query.data?.total}
          showSizeChanger={false}
          onChange={setPage}
        />
      )}
      <Drawer
        title="Episode 处理详情"
        open={Boolean(detailsId)}
        onClose={() => setDetailsId(null)}
        size="large"
      >
        {detailsId && (
          <RecordingProcessingDetails scope={scope} recordingId={detailsId} />
        )}
      </Drawer>
    </section>
  );
}

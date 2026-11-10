import { ResourceLabel } from "../../shared/lib/resource-labels";
import { RecordingProcessingDetails } from "../quality/RecordingProcessingDetails";
import ResumeProcessingAction from "../ingest/processing/ResumeProcessingAction";
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Drawer, Pagination, Tag } from "antd";
import { ChevronDown } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router-dom";
import type { components } from "../../shared/api/generated/platform";
import { request } from "../../shared/api/http-client";
import type { CollectionTaskScope } from "./api";
import { idleProgressInterval, progressRefreshInterval } from "./progress-polling";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import {
  UploadCancellationDialog,
  type CancellableUpload,
} from "./UploadCancellationDialog";
import styles from "./CaptureProgressPanel.module.css";

type CapturePage = components["schemas"]["CaptureProgressPage"];
const labels: Record<string, string> = {
  UPLOADING: "上传中",
  RECEIVED: "已接收，等待处理",
  PREPARING: "自动校验与预处理",
  PENDING_QC: "等待整段质检",
  QC_RUNNING: "整段质检中",
  QC_FAILED: "整段质检未通过",
  AWAITING_SLICE: "质检通过，待分片",
  PROCESSING: "片段对齐与入库",
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
  compact = false,
  initialUnfinishedOnly = false,
}: {
  readonly scope: CollectionTaskScope;
  readonly taskId?: string;
  readonly robotId?: string;
  readonly datasetId?: string;
  readonly compact?: boolean;
  readonly initialUnfinishedOnly?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const recordsId = useId();
  const [page, setPage] = useState(1);
  const [detailsId, setDetailsId] = useState<string | null>(null);
  const [unfinishedOnly, setUnfinishedOnly] = useState(initialUnfinishedOnly);
  const [cancelling, setCancelling] = useState<
    readonly CancellableUpload[] | null
  >(null);
  const [cancelledCount, setCancelledCount] = useState(0);
  const canManage = useCapabilities().has("upload.manage");
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
      unfinishedOnly,
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
          unfinished_uploads_only: unfinishedOnly || undefined,
        },
      }),
    retry: false,
    staleTime: idleProgressInterval,
    refetchInterval: (current) =>
      progressRefreshInterval(
        (current.state.data?.counts?.processing_count ?? 0) > 0 ||
          (current.state.data?.items?.some(
            (item) => item.stage === "UPLOADING" && item.upload_state === "UPLOADING",
          ) ?? false),
        current.state.error,
      ),
    refetchIntervalInBackground: false,
  });
  const counts = query.data?.counts;
  const cancellable = (query.data?.items ?? []).filter(
    (item) =>
      !item.received &&
      ["UPLOADING", "PAUSED", "READY_TO_COMMIT", "FAILED"].includes(
        item.upload_state,
      ),
  );
  return (
    <section
      className={`${styles.panel} ${compact ? styles.compact : ""}`}
      aria-label="采集上传与入库进度"
    >
      <header>
        <div className={styles.heading}>
          <h2>采集上传与入库</h2>
          {compact ? (
            <div className={styles.summary}>
              {counts && (
                <>
                  <span>
                    上传 <b>{counts.upload_count}</b> 次
                  </span>
                  <span>
                    已入库 <b>{counts.ready_episode_count}</b> 条
                  </span>
                  {counts.awaiting_slice_count > 0 && (
                    <span>
                      待分片 <b>{counts.awaiting_slice_count}</b>
                    </span>
                  )}
                  {counts.processing_count > 0 && (
                    <span>
                      处理中 <b>{counts.processing_count}</b>
                    </span>
                  )}
                  {counts.blocked_count > 0 && (
                    <span>
                      待配置 <b>{counts.blocked_count}</b>
                    </span>
                  )}
                  {(counts.failed_episode_count > 0 ||
                    counts.failed_count > 0) && (
                    <span className={styles.issue}>
                      未通过 {counts.failed_episode_count} 条成片 /{" "}
                      {counts.failed_count} 次采集
                    </span>
                  )}
                </>
              )}
              {query.isPending && <span role="status">正在读取…</span>}
            </div>
          ) : (
            <p>
              每次采集独立记录，上传完成后自动质检；通过后即可切片，提交后对齐入库。
            </p>
          )}
        </div>
        <div className={styles.headerActions}>
          <Button
            size={compact ? "small" : "middle"}
            type={unfinishedOnly ? "primary" : "default"}
            aria-pressed={unfinishedOnly}
            onClick={() => {
              setUnfinishedOnly(!unfinishedOnly);
              setPage(1);
              setExpanded(true);
            }}
          >
            {unfinishedOnly ? "查看全部记录" : "处理未完成上传"}
          </Button>
          <Button
            size={compact ? "small" : "middle"}
            type={compact ? "text" : "default"}
            loading={query.isFetching}
            onClick={() => void query.refetch()}
          >
            刷新
          </Button>
          {compact && (
            <Button
              size="small"
              type="text"
              aria-expanded={expanded}
              aria-controls={recordsId}
              aria-label={expanded ? "收起采集记录" : "展开采集记录"}
              onClick={() => setExpanded(!expanded)}
              icon={
                <ChevronDown
                  size={14}
                  className={expanded ? styles.chevronOpen : ""}
                />
              }
            >
              {expanded ? "收起" : "展开记录"}
            </Button>
          )}
        </div>
      </header>
      {query.isError ? (
        <Alert
          type="error"
          showIcon
          message="采集进度读取失败"
          description={query.error.message}
        />
      ) : null}
      <div id={recordsId} hidden={compact && !expanded}>
        {cancelledCount > 0 && (
          <Alert
            type="success"
            showIcon
            title={`已取消 ${cancelledCount} 条上传，信号轨道已刷新。`}
            closable
            onClose={() => setCancelledCount(0)}
          />
        )}
        {unfinishedOnly && canManage && cancellable.length > 0 && (
          <Button danger onClick={() => setCancelling(cancellable)}>
            取消本页 {cancellable.length} 条上传
          </Button>
        )}
        {query.isPending && !compact ? (
          <p role="status">正在读取采集记录…</p>
        ) : null}
        {counts && (
          <div className={styles.counts}>
            <span>
              上传 <b>{counts.upload_count}</b> 次
            </span>
            <span>
              已接收 <b>{counts.received_count}</b> 条录制
            </span>
            <span>
              待分片 <b>{counts.awaiting_slice_count}</b> 条
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
              key={`${item.upload_id}:${item.data_package_id}`}
              className={styles.row}
            >
              <div>
                {item.source_path && <strong>{item.source_path}</strong>}
                <strong>
                  <ResourceLabel
                    kind="robot"
                    id={item.robot_id}
                    scope={scope}
                  />
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
                {!compact && (
                  <Tag color={item.received ? "success" : "processing"}>
                    {item.received
                      ? "上传完成"
                      : item.upload_state === "CANCELLED"
                        ? "上传已取消"
                        : item.upload_state === "FAILED"
                          ? "上传失败"
                          : "上传中"}
                  </Tag>
                )}
                {!compact && (
                  <Tag color={item.received ? "success" : undefined}>
                    {item.received ? "平台已接收" : "等待接收"}
                  </Tag>
                )}
                <Tag
                  color={
                    item.canonical_episode_id
                      ? "success"
                      : item.stage === "FAILED"
                        ? "error"
                        : item.stage === "READY"
                          ? "success"
                          : "processing"
                  }
                >
                  {item.canonical_episode_id
                    ? "同次录制 · 已关联成功结果"
                    : labels[item.stage]}
                </Tag>
                <small>
                  {item.episode_count} 条成片 · {item.ready_episode_count}{" "}
                  条已入库 · {item.failed_episode_count} 条未通过
                </small>
                {item.canonical_episode_id ? (
                  <small>
                    原始文件作为同次录制证据保留，不重复计入采集或入库。
                  </small>
                ) : (
                  item.error_code && (
                    <small role="status">原因：{item.error_code}</small>
                  )
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
                {canManage && cancellable.includes(item) && (
                  <Button
                    size="small"
                    danger
                    onClick={() => setCancelling([item])}
                  >
                    取消上传
                  </Button>
                )}
                {item.canonical_episode_id && !datasetId && (
                  <Link to={`/datasets/${encodeURIComponent(item.dataset_id)}`}>
                    查看已有成片
                  </Link>
                )}
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
                      : ["PENDING_QC", "QC_RUNNING", "QC_FAILED", "FAILED"].includes(item.stage)
                        ? "查看录制与质检"
                      : "查看录制与成片"}
                  </Link>
                )}
                {item.ready_episode_count > 0 && !datasetId && (
                  <Link to={`/datasets/${encodeURIComponent(item.dataset_id)}`}>
                    查看数据集
                  </Link>
                )}
              </div>
            </article>
          ))}
          {query.data?.total === 0 && (
            <p>
              {unfinishedOnly
                ? "当前没有未完成上传。"
                : "当前还没有采集上传记录。"}
            </p>
          )}
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
      </div>
      {cancelling && (
        <UploadCancellationDialog
          scope={scope}
          items={cancelling}
          onClose={() => setCancelling(null)}
          onCompleted={setCancelledCount}
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

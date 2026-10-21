import {
  annotationStatusLabel,
  captureProblemLabels,
} from "../../../capture-schemes/presentation";
import { ResourceLabel } from "../../../../shared/lib/resource-labels";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Alert, Button, Modal, Progress, Space, Tag } from "antd";
import { ChevronDown } from "lucide-react";
import { lazy, Suspense, useId, useState } from "react";
const OriginalSourceBrowser = lazy(
  () => import("../../browser/OriginalSourceBrowser"),
);
import { Link } from "react-router-dom";
import type { IngestScope } from "../../../../entities/data-source";
import { request } from "../../../../shared/api/http-client";
import { nativeRoot, type NativeProgress } from "../lerobot-processing";
import styles from "../../upload/upload.module.css";
import recordStyles from "./LeRobotProcessingRecords.module.css";
import {
  McapRobotProfile,
  McapPreparationProgress,
  mcapPreparationQuery,
} from "./McapPreparation";
import { ProcessingTargetFields } from "./ProcessingTargetFields";
import { VideoPreviewModal } from "../../../viewer/VideoPreviewModal";
import { ProcessingLabels } from "./ProcessingLabels";

const labels: Record<string, string> = {
  RAW_COMMITTED: "原始文件已保存",
  PENDING: "等待后台处理",
  RUNNING: "质检 / 对齐处理中",
  SUCCEEDED: "数据处理完成",
  PARTIALLY_FAILED: "部分处理未完成",
  FAILED: "处理失败",
  CANCELLED: "处理已取消",
};

const errorLabels: Record<string, string> = {
  LEROBOT_DATASET_SCHEMA_CONFLICT: "数据集结构已固定，上传数据与已有结构冲突",
  LEROBOT_SOURCE_INVALID: "源数据校验失败",
  ROBOT_PROCESSING_TECHNICAL_FAILURE: "后台处理技术故障",
  ROBOT_PROCESSOR_UNAVAILABLE: "处理器暂不可用",
};

export function LeRobotProcessingRecords({
  scope,
  canManage,
  datasetId,
  compact = false,
}: {
  readonly scope: IngestScope;
  readonly canManage: boolean;
  readonly datasetId?: string;
  readonly compact?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const recordsId = useId();
  const [offset, setOffset] = useState(0);
  const [openedId, setOpenedId] = useState<string | null>(null);
  const [processingItem, setProcessingItem] = useState<NativeProgress | null>(
    null,
  );
  const [taskId, setTaskId] = useState("");
  const [robotId, setRobotId] = useState("");
  const client = useQueryClient();
  const key = [
    "native-processing",
    scope.organizationId,
    scope.projectId,
    scope.regionCode,
  ];
  const records = useQuery({
    queryKey: [...key, datasetId, offset],
    queryFn: ({ signal }) =>
      request<NativeProgress[]>({
        method: "GET",
        path: nativeRoot(scope),
        scope,
        signal,
        query: { limit: 20, offset, dataset_id: datasetId },
      }),
    refetchInterval: 5_000,
    retry: false,
  });
  const mcapItems = (records.data ?? []).filter(
    (item) => item.source_format === "MCAP",
  );
  const preparations = useQueries({
    queries: mcapItems.map((item) =>
      mcapPreparationQuery(scope, item.import_id),
    ),
  });
  const linked = new Set(
    mcapItems
      .filter((_, i) => preparations[i]?.data?.same_recording)
      .map((item) => item.import_id),
  );
  const retry = useMutation({
    mutationFn: (id: string) =>
      request<NativeProgress>({
        method: "POST",
        scope,
        path: `${nativeRoot(scope)}/${encodeURIComponent(id)}:retry`,
      }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: key }),
        client.invalidateQueries({ queryKey: ["dashboard"] }),
      ]);
    },
  });
  const processStored = useMutation({
    mutationFn: () =>
      request({
        method: "POST",
        scope,
        path: `${nativeRoot(scope)}/${encodeURIComponent(processingItem!.import_id)}:${processingItem!.source_format === "MCAP" ? "process-mcap" : "process"}`,
        body: { collection_task_id: taskId, robot_id: robotId },
      }),
    onSuccess: async () => {
      setProcessingItem(null);
      await client.invalidateQueries({ queryKey: key });
    },
  });
  if (!records.isError && !records.data?.length && offset === 0) return null;
  return (
    <section
      className={compact ? recordStyles.compact : styles.uploadPanel}
      aria-label="原始数据"
    >
      {compact ? (
        <header className={recordStyles.header}>
          <div className={recordStyles.heading}>
            <h2>原始数据</h2>
            <span className={recordStyles.summary}>
              {records.data
                ? `${records.data.length} 批记录${offset > 0 || records.data.length === 20 ? "（本页）" : ""}`
                : "正在读取…"}
            </span>
          </div>
          <div className={recordStyles.headerActions}>
            <Button
              size="small"
              type="text"
              loading={records.isFetching}
              onClick={() => void records.refetch()}
            >
              刷新
            </Button>
            <Button
              size="small"
              type="text"
              aria-expanded={expanded}
              aria-controls={recordsId}
              aria-label={expanded ? "收起原始数据记录" : "展开原始数据记录"}
              onClick={() => setExpanded(!expanded)}
              icon={
                <ChevronDown
                  size={14}
                  className={expanded ? recordStyles.chevronOpen : ""}
                />
              }
            >
              {expanded ? "收起" : "展开记录"}
            </Button>
          </div>
        </header>
      ) : (
        <>
          <h2>原始数据</h2>
          <Button onClick={() => void records.refetch()}>刷新记录</Button>
          <p>
            原始文件始终保留。自动质检与对齐完成后，可进入数据集可视化和标注。
          </p>
        </>
      )}
      {records.isError ? (
        <Alert
          type="error"
          title="处理进度读取失败"
          action={<Button onClick={() => void records.refetch()}>刷新</Button>}
        />
      ) : null}
      {retry.isError ? (
        <Alert
          type="error"
          title="重试失败"
          description={
            retry.error instanceof Error
              ? retry.error.message
              : "请刷新后重试。"
          }
        />
      ) : null}
      <div id={recordsId} hidden={compact && !expanded}>
        <div
          className={`${styles.queueList} ${compact ? recordStyles.list : ""}`}
        >
          {records.data?.map((item) => (
            <article
              className={compact ? recordStyles.row : styles.queueItem}
              key={item.import_id}
            >
              <div className={compact ? recordStyles.identity : undefined}>
                {!compact && (
                  <strong>
                    <ResourceLabel
                      kind="dataset"
                      id={item.dataset_id}
                      scope={scope}
                    />
                  </strong>
                )}
                <small title={item.import_id}>
                  {item.source_format.toUpperCase()} ·{" "}
                  {new Date(item.updated_at).toLocaleString("zh-CN", {
                    hour12: false,
                  })}
                </small>
              </div>
              <Tag
                color={
                  linked.has(item.import_id) ||
                  ["SUCCEEDED", "RAW_COMMITTED"].includes(item.status)
                    ? "success"
                    : item.status.includes("FAILED")
                      ? "error"
                      : "processing"
                }
              >
                {linked.has(item.import_id)
                  ? "同次录制 · 已关联成功结果"
                  : (labels[item.status] ?? item.status)}
              </Tag>
              {!linked.has(item.import_id) &&
              item.status !== "RAW_COMMITTED" &&
              (!compact ||
                ["PENDING", "RUNNING", "PARTIALLY_FAILED"].includes(
                  item.status,
                )) ? (
                <Progress
                  className={compact ? recordStyles.progress : undefined}
                  size={compact ? "small" : "middle"}
                  aria-label="LeRobot 处理进度"
                  percent={Math.round(
                    (100 * (item.ready + item.failed + (item.discarded ?? 0))) /
                      Math.max(1, item.episode_count),
                  )}
                  status={
                    item.failed
                      ? "exception"
                      : item.status === "SUCCEEDED"
                        ? "success"
                        : "active"
                  }
                />
              ) : null}
              {linked.has(item.import_id) ? (
                <p className={recordStyles.recordSummary}>
                  原始文件已保留，沿用同次录制的成功成片，不重复入库或计数。
                </p>
              ) : item.status === "RAW_COMMITTED" ? (
                <p className={recordStyles.recordSummary}>
                  {item.source_format} · {item.file_count} 个原始文件
                </p>
              ) : (
                <p className={recordStyles.recordSummary}>
                  共 {item.episode_count} 个 Episode · 已就绪 {item.ready} ·
                  处理未完成 {item.failed}
                  {(item.discarded ?? 0) > 0
                    ? ` · 已移除 ${item.discarded}`
                    : ""}
                </p>
              )}
              {(item.resume_required ?? 0) +
                (item.reprocessing_conflicts ?? 0) >
              0 ? (
                <p className={recordStyles.recordSummary}>
                  未完成明细：待继续处理 {item.resume_required ?? 0} · 处理冲突{" "}
                  {item.reprocessing_conflicts ?? 0}
                  {item.failed >
                  (item.resume_required ?? 0) +
                    (item.reprocessing_conflicts ?? 0)
                    ? ` · 其他未完成 ${item.failed - (item.resume_required ?? 0) - (item.reprocessing_conflicts ?? 0)}`
                    : ""}
                  。质检通过后仍需完成入库，处理冲突可在首页查看详情。
                </p>
              ) : null}
              {item.readable_episodes !== undefined &&
                item.readable_episodes !== null && (
                  <p className={recordStyles.recordSummary}>
                    数据可读取 {item.readable_episodes} 个 Episode
                    {Object.entries(item.annotation_counts ?? {}).map(
                      ([status, count]) => (
                        <span key={status}>
                          {" "}
                          · {annotationStatusLabel(status)} {count}
                        </span>
                      ),
                    )}
                  </p>
                )}
              {!linked.has(item.import_id) && item.last_error_code ? (
                <p className={recordStyles.recordSummary} role="alert">
                  上次处理问题：
                  {captureProblemLabels[item.last_error_code] ??
                    errorLabels[item.last_error_code] ??
                    item.last_error_code}
                  {item.last_error_detail ? (
                    <span className={recordStyles.errorDetail}>
                      {item.last_error_detail}
                    </span>
                  ) : null}
                </p>
              ) : null}
              <Space
                wrap
                size={8}
                className={compact ? recordStyles.actions : undefined}
              >
                {canManage &&
                !linked.has(item.import_id) &&
                (item.status === "RAW_COMMITTED" ||
                  (item.source_format === "MCAP" &&
                    item.status === "FAILED")) &&
                ["LEROBOT_V3", "MCAP"].includes(item.source_format) ? (
                  <Button
                    size={compact ? "small" : "middle"}
                    onClick={() => {
                      setProcessingItem(item);
                      setTaskId("");
                      setRobotId("");
                      processStored.reset();
                    }}
                  >
                    {item.source_format === "MCAP"
                      ? "准备录制 / 重试"
                      : "开始质检与对齐"}
                  </Button>
                ) : null}
                <Button
                  size={compact ? "small" : "middle"}
                  onClick={() => setOpenedId(item.import_id)}
                >
                  查看原文件与视频
                </Button>
                {!datasetId &&
                (linked.has(item.import_id) ||
                  item.ready > 0 ||
                  item.status === "RAW_COMMITTED") ? (
                  <Link to={`/datasets/${encodeURIComponent(item.dataset_id)}`}>
                    查看数据集
                  </Link>
                ) : null}
                {canManage &&
                !linked.has(item.import_id) &&
                ["FAILED", "PARTIALLY_FAILED", "CANCELLED"].includes(
                  item.status,
                ) ? (
                  <Button
                    size={compact ? "small" : "middle"}
                    loading={
                      retry.isPending && retry.variables === item.import_id
                    }
                    onClick={() => retry.mutate(item.import_id)}
                  >
                    重试未完成处理
                  </Button>
                ) : null}
              </Space>
              {item.source_format === "MCAP" &&
                (compact ? (
                  <details className={recordStyles.details}>
                    <summary>处理详情与配置</summary>
                    {expanded && (
                      <McapPreparationProgress
                        scope={scope}
                        importId={item.import_id}
                      />
                    )}
                  </details>
                ) : (
                  <McapPreparationProgress
                    scope={scope}
                    importId={item.import_id}
                  />
                ))}
            </article>
          ))}
        </div>
        {(!compact || offset > 0 || (records.data?.length ?? 0) >= 20) && (
          <Space className={compact ? recordStyles.pager : undefined}>
            <Button
              size={compact ? "small" : "middle"}
              disabled={offset === 0}
              onClick={() => setOffset((value) => Math.max(0, value - 20))}
            >
              上一页
            </Button>
            <Button
              size={compact ? "small" : "middle"}
              disabled={(records.data?.length ?? 0) < 20}
              onClick={() => setOffset((value) => value + 20)}
            >
              下一页
            </Button>
          </Space>
        )}
      </div>
      <Modal
        open={processingItem !== null}
        title="处理已存档数据"
        destroyOnHidden
        onCancel={() => setProcessingItem(null)}
        onOk={() => processStored.mutate()}
        confirmLoading={processStored.isPending}
        okText="开始处理"
        okButtonProps={{
          disabled: !taskId || !robotId,
        }}
      >
        {processingItem ? (
          <>
            <dl className={styles.confirmFacts}>
              <ProcessingTargetFields
                scope={scope}
                datasetId={processingItem.dataset_id}
                fixedDatasetId={processingItem.dataset_id}
                collectionTaskId={taskId}
                robotId={robotId}
                onDatasetIdChange={() => {}}
                onCollectionTaskIdChange={setTaskId}
                onRobotIdChange={setRobotId}
              />
            </dl>
            {processingItem.source_format === "MCAP" && (
              <McapRobotProfile scope={scope} robotId={robotId} />
            )}
            <ProcessingLabels
              scope={scope}
              datasetId={processingItem.dataset_id}
            />
            {processStored.isError ? (
              <Alert
                type="error"
                title="处理启动失败"
                description={processStored.error.message}
              />
            ) : null}
          </>
        ) : null}
      </Modal>
      <VideoPreviewModal
        open={openedId !== null}
        onCancel={() => setOpenedId(null)}
        title="原始文件与视频"
      >
        {openedId ? (
          <Suspense fallback={<p>正在加载原始数据…</p>}>
            <OriginalSourceBrowser
              key={openedId}
              scope={scope}
              importId={openedId}
            />
          </Suspense>
        ) : null}
      </VideoPreviewModal>
    </section>
  );
}

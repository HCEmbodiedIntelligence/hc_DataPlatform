import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Modal, Progress, Space, Tag } from "antd";
import { lazy, Suspense, useState } from "react";
const OriginalSourceBrowser = lazy(() => import("./OriginalSourceBrowser"));
import { Link } from "react-router-dom";
import type { IngestScope } from "../../../entities/data-source";
import { request } from "../../../shared/api/http-client";
import { nativeRoot, type NativeProgress } from "../lerobot-processing";
import styles from "../styles.module.css";
import { ProcessingTargetFields } from "./ProcessingTargetFields";
import {
  ProcessingLabels,
  useProcessingConfiguration,
} from "./ProcessingLabels";

const labels: Record<string, string> = {
  RAW_COMMITTED: "原始文件已保存",
  PENDING: "等待后台处理",
  RUNNING: "质检 / 对齐处理中",
  SUCCEEDED: "处理完成，可人工标注",
  PARTIALLY_FAILED: "部分处理失败",
  FAILED: "处理失败",
  CANCELLED: "处理已取消",
};

export function LeRobotProcessingRecords({
  scope,
  canManage,
  datasetId,
}: {
  readonly scope: IngestScope;
  readonly canManage: boolean;
  readonly datasetId?: string;
}) {
  const [offset, setOffset] = useState(0);
  const [openedId, setOpenedId] = useState<string | null>(null);
  const [processingItem, setProcessingItem] = useState<NativeProgress | null>(
    null,
  );
  const [taskId, setTaskId] = useState("");
  const [robotId, setRobotId] = useState("");
  const configuration = useProcessingConfiguration(
    scope,
    processingItem?.dataset_id ?? "",
    Boolean(processingItem),
  );
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
    refetchInterval: (query) =>
      query.state.data?.some((item) =>
        ["PENDING", "RUNNING"].includes(item.status),
      )
        ? 5_000
        : false,
    retry: false,
  });
  const retry = useMutation({
    mutationFn: (id: string) =>
      request<NativeProgress>({
        method: "POST",
        scope,
        path: `${nativeRoot(scope)}/${encodeURIComponent(id)}:retry`,
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: key }),
  });
  const processStored = useMutation({
    mutationFn: () =>
      request({
        method: "POST",
        scope,
        path: `${nativeRoot(scope)}/${encodeURIComponent(processingItem!.import_id)}:process`,
        body: { collection_task_id: taskId, robot_id: robotId },
      }),
    onSuccess: async () => {
      setProcessingItem(null);
      await client.invalidateQueries({ queryKey: key });
    },
  });
  if (!records.isError && !records.data?.length && offset === 0) return null;
  return (
    <section className={styles.uploadPanel} aria-label="原始数据">
      <h2>原始数据</h2>
      <Button onClick={() => void records.refetch()}>刷新记录</Button>
      <p>原始文件始终保留。自动质检与对齐完成后，可进入数据集可视化和标注。</p>
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
      <div className={styles.queueList}>
        {records.data?.map((item) => (
          <article className={styles.queueItem} key={item.import_id}>
            <strong>Dataset {item.dataset_id}</strong>
            <small>{item.import_id}</small>
            <Tag
              color={
                ["SUCCEEDED", "RAW_COMMITTED"].includes(item.status)
                  ? "success"
                  : item.status.includes("FAILED")
                    ? "error"
                    : "processing"
              }
            >
              {labels[item.status] ?? item.status}
            </Tag>
            {item.status !== "RAW_COMMITTED" ? (
              <Progress
                aria-label="LeRobot 处理进度"
                percent={Math.round(
                  (100 * (item.ready + item.failed)) /
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
            {item.status === "RAW_COMMITTED" ? (
              <p>
                {item.source_format} · {item.file_count} 个原始文件
              </p>
            ) : (
              <p>
                共 {item.episode_count} 个 Episode · 已就绪 {item.ready} · 失败{" "}
                {item.failed}
              </p>
            )}
            {item.last_error_code ? (
              <p role="alert">
                失败原因：<code>{item.last_error_code}</code>
              </p>
            ) : null}
            <Space>
              {canManage &&
              item.status === "RAW_COMMITTED" &&
              item.source_format === "LEROBOT_V3" ? (
                <Button
                  onClick={() => {
                    setProcessingItem(item);
                    setTaskId("");
                    setRobotId("");
                    processStored.reset();
                  }}
                >
                  开始质检与对齐
                </Button>
              ) : null}
              <Button onClick={() => setOpenedId(item.import_id)}>
                查看原文件与视频
              </Button>
              {item.ready > 0 || item.status === "RAW_COMMITTED" ? (
                <Link to={`/datasets/${encodeURIComponent(item.dataset_id)}`}>
                  查看数据集
                </Link>
              ) : null}
              {canManage &&
              ["FAILED", "PARTIALLY_FAILED", "CANCELLED"].includes(
                item.status,
              ) ? (
                <Button
                  loading={
                    retry.isPending && retry.variables === item.import_id
                  }
                  onClick={() => retry.mutate(item.import_id)}
                >
                  重试未完成处理
                </Button>
              ) : null}
            </Space>
          </article>
        ))}
      </div>
      <Space>
        <Button
          disabled={offset === 0}
          onClick={() => setOffset((value) => Math.max(0, value - 20))}
        >
          上一页
        </Button>
        <Button
          disabled={(records.data?.length ?? 0) < 20}
          onClick={() => setOffset((value) => value + 20)}
        >
          下一页
        </Button>
      </Space>
      <Modal
        open={processingItem !== null}
        title="处理已存档数据"
        destroyOnHidden
        onCancel={() => setProcessingItem(null)}
        onOk={() => processStored.mutate()}
        confirmLoading={processStored.isPending}
        okText="开始处理"
        okButtonProps={{
          disabled: !taskId || !robotId || !configuration.data?.configured,
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
      <Modal
        open={openedId !== null}
        onCancel={() => setOpenedId(null)}
        footer={null}
        width="min(1400px, 95vw)"
        title="原始文件与视频"
        destroyOnHidden
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
      </Modal>
    </section>
  );
}

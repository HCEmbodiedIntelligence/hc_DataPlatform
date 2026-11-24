import { saveExportRecord, useExportHistory } from "../../features/exports/export-history";
import { ExportReceipt } from "./ExportHistoryDetails";
import { ExportControls, useExportDestination } from "../../features/exports/ExportControls";
import { exportArchiveName } from "../../features/exports/archive-name";
import type { ExportDestination } from "../../features/exports/oss-export";
import { runExportTask } from "../../features/exports/export-tasks";
import { ExportTaskList } from "../../features/exports/ExportTaskList";
import { MAX_EXPORT_ITEMS, transferLabel } from "../../shared/lib/ordered-transfer";
import { ResourceLabel } from "../../shared/lib/resource-labels";
import { lazy, Suspense, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { Alert, Button, Card, Space, Table } from "antd";
import type { Scope } from "../../shared/scope/model";
import { request } from "../../shared/api/http-client";
import { PageState } from "../../shared/ui";
import {
  downloadRawSources,
} from "../../features/exports/raw-download";
import { formatBytes } from "../../features/ingest/upload/upload-contract";
import { useScopeQueryRevision } from "../../shared/api/use-scoped-query";
import {
  nativeRoot,
  type NativeProgress,
} from "../../features/ingest/processing/lerobot-processing";

const OriginalSourceBrowser = lazy(
  () => import("../../features/ingest/browser/OriginalSourceBrowser"),
);

type Props = {
  scope: Scope;
  datasetNumbers?: ReadonlyMap<string, string>;
  datasetIds: readonly string[];
  taskIds: readonly string[];
  batchNumbers?: readonly number[];
  allBatches?: boolean;
  uploadedFrom?: string;
  uploadedBefore?: string;
  hasFilter: boolean;
  canRead: boolean;
  canCreate: boolean;
  canDownload: boolean;
  canDownloadDataset?: (datasetId?: string | null) => boolean;
};

export default function RawDataExportPanel(props: Props) {
  const revision = useScopeQueryRevision();
  const identity = JSON.stringify([
    revision,
    props.scope,
    [...props.datasetIds].sort(),
    [...props.taskIds].sort(),
    props.batchNumbers, props.allBatches, props.uploadedFrom, props.uploadedBefore,
    props.hasFilter,
    props.canRead,
    props.canDownload,
  ]);
  const history = useExportHistory(props.scope).filter(record => record.source === "raw");
  return (
    <>
      <RawDataSelection key={identity} {...props} />
      {props.canRead && props.canDownload && <ExportTaskList scope={props.scope} />}
      {props.canRead && props.canDownload && history.length > 0 && (
        <Card size="small" title="导出记录">
          <Space orientation="vertical">
            {history.map(record => <ExportReceipt key={record.id} record={record} scope={props.scope} />)}
          </Space>
        </Card>
      )}
    </>
  );
}

function RawDataSelection({
  scope,
  datasetNumbers,
  datasetIds,
  taskIds,
  hasFilter,
  batchNumbers = [], allBatches = false, uploadedFrom, uploadedBefore,
  canRead,
  canCreate,
  canDownload,
  canDownloadDataset = () => canDownload,
}: Props) {
  const [openedId, setOpenedId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const exportDestination = useExportDestination();
  const ingestScope =
    scope?.projectId && scope.regionCode
      ? {
          organizationId: scope.organizationId,
          projectId: scope.projectId,
          regionCode: scope.regionCode,
        }
      : null;
  const datasetsToRead = (uploadedFrom || uploadedBefore || allBatches || batchNumbers.length > 0) && !datasetIds.length ? [undefined] : datasetIds;
  const queries = useQueries({
    queries: datasetsToRead.map((datasetId) => ({
      queryKey: [
        "raw-export-sources",
        scope.organizationId,
        scope.projectId,
        scope.regionCode,
        datasetId,
        batchNumbers, allBatches, uploadedFrom, uploadedBefore,
      ],
      queryFn: async ({ signal }: { signal: AbortSignal }) => {
        const items: NativeProgress[] = [];
        for (let offset = 0; ; offset += 100) {
          const page = await request<NativeProgress[]>({
            method: "GET",
            scope: ingestScope!,
            path: nativeRoot(ingestScope!),
            query: {
              dataset_id: datasetId,
              uploaded_from: uploadedFrom, uploaded_before: uploadedBefore,
              batch_number: batchNumbers, all_batches: allBatches || undefined,
              limit: 100,
              offset,
              include_all_sources: true,
            },
            signal,
          });
          items.push(...page);
          if (page.length < 100) return items;
        }
      },
      enabled: canRead && hasFilter && Boolean(ingestScope),
      staleTime: 0,
    })),
  });
  if (!canRead)
    return <PageState state="forbidden" description="需要原始数据读取权限。" />;
  if (!hasFilter || !ingestScope)
    return <PageState state="empty" description="请先选择上传时间、上传批次、任务或数据集" />;
  if (queries.some((query) => query.isPending))
    return <PageState state="loading" label="原始数据" />;
  if (queries.some((query) => query.isError))
    return (
      <PageState
        state="error"
        description="原始数据读取失败"
        onRetry={() => queries.forEach((query) => void query.refetch())}
      />
    );
  const records = queries
    .flatMap((query) => query.data ?? [])
    .filter(
      (item) =>
        taskIds.length === 0 ||
        (item.collection_task_id && taskIds.includes(item.collection_task_id)),
    );
  const opened = records.find((item) => item.import_id === openedId);
  const selected = records.filter((item) =>
    selectedIds.includes(item.import_id),
  );
  function select(ids: string[]) {
    setSelectedIds(ids);

  }
  async function download(target: ExportDestination) {
    if (!ingestScope || !canDownload || !selected.length || !selected.every(item => canDownloadDataset(item.dataset_id))) return;
    await runExportTask(scope, transferLabel(`原始文件 · ${selected.length} 个批次`, selected.length), target, async control => {
      const result = await downloadRawSources(
        ingestScope,
        selected.map((item) => item.import_id),
        control.signal,
        bytes => control.report(`${target === "oss" ? "已上传到 OSS" : "已接收"} ${formatBytes(bytes)}`),
        batchNumbers.length || allBatches || uploadedFrom || uploadedBefore
          ? { stage: "dataset", batch_numbers: batchNumbers, ...(allBatches && {all_batches: true}), uploaded_from: uploadedFrom, uploaded_before: uploadedBefore }
          : undefined,
        target,
        exportArchiveName("original", selected.map(item => datasetNumbers?.get(item.dataset_id) ?? item.dataset_id)),
        control.checkpoint, control.saving,
      );
      control.signal.throwIfAborted();
      if (result.kind === "cancelled") return result;
        saveExportRecord(scope, {
          source: "raw", format: "原始文件", selectedBatches: [...batchNumbers],
          ...(result.kind === "oss" ? { uri: result.uri } : { filename: result.filename }),
          items: selected.map(item => ({
            id: item.import_id, datasetId: item.dataset_id, episodes: item.episode_count,
            files: item.file_count, sourceFormat: item.source_format, taskId: item.collection_task_id,
          })),
        });
      return result.kind === "oss" ? result : { kind: "saved", message: `ZIP 已保存到所选位置 · ${result.filename}` };
    });
  }
  return (
    <section aria-label="原始数据导出">
      <Alert
        showIcon
        type="info"
        title="原始文件"
        description="可选择一个或多个批次，导出时保留原文件内容和目录结构。"
      />
      <p role="status">
        共 {records.length} 个原始批次 ·{" "}
        {records.reduce((count, item) => count + item.episode_count, 0)} 条原始
        Episode · {records.reduce((count, item) => count + item.file_count, 0)}{" "}
        个文件
      </p>
      <Space wrap style={{ marginBottom: 16 }}>
        <Button
          disabled={!canDownload || records.length === 0}
          onClick={() => select(records.filter(item => canDownloadDataset(item.dataset_id)).map(item => item.import_id))}
        >
          全选筛选结果
        </Button>
        <Button
          disabled={selected.length === 0}
          onClick={() => select([])}
        >
          清空选择
        </Button>
        <span aria-live="polite">
          已选 {selected.length} 个批次 ·{" "}
          {selected.reduce((n, item) => n + item.file_count, 0)} 个文件
          {selected.length > 0
            ? ` · ${formatBytes(selected.reduce((n, item) => n + item.total_bytes, 0))}`
            : ""}
        </span>
        <ExportControls selection={exportDestination} canPublish={canCreate} busy={false}
          disabled={!selected.length || !selected.every(item => canDownloadDataset(item.dataset_id)) || selected.length > MAX_EXPORT_ITEMS}
          onExport={target => void download(target)} />
      </Space>
      {selected.length > MAX_EXPORT_ITEMS ? (
        <Alert
          type="warning"
          showIcon
          title={`一次最多下载 ${MAX_EXPORT_ITEMS} 个批次，请减少选择。`}
        />
      ) : null}
      <Table<NativeProgress>
        aria-label="原始数据批次"
        rowKey="import_id"
        dataSource={records}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        rowSelection={{
          selectedRowKeys: selected.map((item) => item.import_id),
          onChange: (keys) => select(keys.map(String)),
          getCheckboxProps: item => ({ disabled: !canDownloadDataset(item.dataset_id) }),
        }}
        columns={[
          {
            title: "原始批次",
            key: "source",
            render: (_, record) => (
              <span title={record.import_id}>
                {record.source_format.toUpperCase()} ·{" "}
                {new Date(record.updated_at).toLocaleString("zh-CN", {
                  hour12: false,
                })}
              </span>
            ),
          },
          {
            title: "采集任务",
            key: "task",
            render: (_, record) => (
              <ResourceLabel
                kind="collection_task"
                id={record.collection_task_id}
                scope={scope}
              />
            ),
          },
          { title: "源格式", dataIndex: "source_format" },
          { title: "原始 Episode 数", dataIndex: "episode_count" },
          { title: "文件数", dataIndex: "file_count" },
          {
            title: "操作",
            key: "open",
            render: (_, record) => (
              <Button onClick={() => setOpenedId(record.import_id)}>
                查看文件
              </Button>
            ),
          },
        ]}
      />
      {opened ? (
        <Suspense fallback={<PageState state="loading" label="原始文件" />}>
          <OriginalSourceBrowser
            key={`${scope.organizationId}/${scope.projectId}/${scope.regionCode}/${opened.import_id}`}
            scope={ingestScope}
            importId={opened.import_id}
            filesOnly
            canDownload={canDownloadDataset(opened.dataset_id)}
          />
        </Suspense>
      ) : null}
    </section>
  );
}

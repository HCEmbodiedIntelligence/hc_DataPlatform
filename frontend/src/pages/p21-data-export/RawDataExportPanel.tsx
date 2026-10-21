import { ResourceLabel } from "../../shared/lib/resource-labels";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { Alert, Button, Space, Table } from "antd";
import type { Scope } from "../../shared/scope/model";
import { request } from "../../shared/api/http-client";
import { PageState } from "../../shared/ui";
import { BrowserDownloadHelp } from "../../features/exports/BrowserDownloadHelp";
import {
  downloadRawSources,
  type RawDownloadResult,
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
  datasetIds: readonly string[];
  taskIds: readonly string[];
  hasFilter: boolean;
  canRead: boolean;
  canDownload: boolean;
};

export default function RawDataExportPanel(props: Props) {
  const revision = useScopeQueryRevision();
  const identity = JSON.stringify([
    revision,
    props.scope,
    [...props.datasetIds].sort(),
    [...props.taskIds].sort(),
    props.hasFilter,
    props.canRead,
    props.canDownload,
  ]);
  return <RawDataSelection key={identity} {...props} />;
}

function RawDataSelection({
  scope,
  datasetIds,
  taskIds,
  hasFilter,
  canRead,
  canDownload,
}: Props) {
  const [openedId, setOpenedId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [downloading, setDownloading] = useState(false);
  const [downloadedBytes, setDownloadedBytes] = useState(0);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [downloadResult, setDownloadResult] =
    useState<RawDownloadResult | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    if (downloadResult?.kind !== "browser") return;
    // Allow a just-started browser download to consume the URL before cleanup.
    return () => {
      window.setTimeout(() => URL.revokeObjectURL(downloadResult.url), 60_000);
    };
  }, [downloadResult]);
  const ingestScope =
    scope?.projectId && scope.regionCode
      ? {
          organizationId: scope.organizationId,
          projectId: scope.projectId,
          regionCode: scope.regionCode,
        }
      : null;
  const queries = useQueries({
    queries: datasetIds.map((datasetId) => ({
      queryKey: [
        "raw-export-sources",
        scope.organizationId,
        scope.projectId,
        scope.regionCode,
        datasetId,
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
    return <PageState state="empty" description="请先选择任务或数据集" />;
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
    setDownloadResult(null);
    setDownloadError(null);
  }
  async function download() {
    if (!ingestScope || !canDownload || !selected.length || controller.current)
      return;
    const current = new AbortController();
    controller.current = current;
    setDownloading(true);
    setDownloadedBytes(0);
    setDownloadError(null);
    setDownloadResult(null);
    try {
      const result = await downloadRawSources(
        ingestScope,
        selected.map((item) => item.import_id),
        current.signal,
        setDownloadedBytes,
      );
      if (!current.signal.aborted) setDownloadResult(result);
      else if (result.kind === "browser") URL.revokeObjectURL(result.url);
    } catch (error) {
      if (
        !current.signal.aborted &&
        !(error instanceof DOMException && error.name === "AbortError")
      )
        setDownloadError(
          error instanceof Error ? error.message : "原始数据下载失败，请重试。",
        );
    } finally {
      if (controller.current === current) controller.current = null;
      setDownloading(false);
    }
  }
  return (
    <section aria-label="原始数据导出">
      <Alert
        showIcon
        type="info"
        title="按原始文件下载"
        description="勾选一个或多个原始批次，一次下载为 ZIP。保留原文件内容和目录结构，不要求处理或标注完成。"
      />
      <p role="status">
        共 {records.length} 个原始批次 ·{" "}
        {records.reduce((count, item) => count + item.episode_count, 0)} 条原始
        Episode · {records.reduce((count, item) => count + item.file_count, 0)}{" "}
        个文件
      </p>
      <Space wrap style={{ marginBottom: 16 }}>
        <Button
          disabled={!canDownload || downloading || records.length === 0}
          onClick={() => select(records.map((item) => item.import_id))}
        >
          全选筛选结果
        </Button>
        <Button
          disabled={downloading || selected.length === 0}
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
        <Button
          type="primary"
          disabled={
            !canDownload || selected.length === 0 || selected.length > 1000
          }
          loading={downloading}
          onClick={() => void download()}
        >
          下载所选批次
        </Button>
        {downloading ? (
          <>
            <span role="status">已接收 {formatBytes(downloadedBytes)}</span>
            <Button onClick={() => controller.current?.abort()}>
              取消下载
            </Button>
          </>
        ) : null}
      </Space>
      {selected.length > 1000 ? (
        <Alert
          type="warning"
          showIcon
          title="一次最多下载 1000 个批次，请减少选择。"
        />
      ) : null}
      {downloadError ? (
        <Alert
          type="error"
          showIcon
          title="原始数据下载失败"
          description={downloadError}
        />
      ) : null}
      {downloadResult ? (
        <Alert
          type={downloadResult.kind === "saved" ? "success" : "info"}
          showIcon
          title={
            downloadResult.kind === "saved"
              ? "ZIP 已保存到所选位置"
              : "ZIP 已准备好，已向浏览器请求下载"
          }
          description={
            <Space orientation="vertical" size={4}>
              <span>
                {downloadResult.filename} · {formatBytes(downloadResult.bytes)}
              </span>
              {downloadResult.kind === "saved" ? (
                <span>
                  请到你选择的文件夹查看；这次保存不会出现在浏览器下载列表中。
                </span>
              ) : (
                <Space orientation="vertical" size={4}>
                  <Space wrap>
                    <span>若下载列表中没有记录，请点击下载：</span>
                    <Button
                      size="small"
                      type="primary"
                      href={downloadResult.url}
                      download={downloadResult.filename}
                      rel="noopener noreferrer"
                    >
                      点击下载 ZIP
                    </Button>
                  </Space>
                  {window.isSecureContext === false && <BrowserDownloadHelp />}
                </Space>
              )}
            </Space>
          }
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
          getCheckboxProps: () => ({ disabled: !canDownload || downloading }),
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
            canDownload={canDownload}
          />
        </Suspense>
      ) : null}
    </section>
  );
}

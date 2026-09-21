import { lazy, Suspense, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { Alert, Button, Table } from "antd";
import type { Scope } from "../../entities/scope";
import { request } from "../../shared/api/http-client";
import { PageState } from "../../shared/ui";
import {
  nativeRoot,
  type NativeProgress,
} from "../p03-upload-jobs/lerobot-processing";

const OriginalSourceBrowser = lazy(
  () => import("../p03-upload-jobs/components/OriginalSourceBrowser"),
);

export default function RawDataExportPanel({
  scope,
  datasetIds,
  taskIds,
  hasFilter,
  canRead,
  canDownload,
}: {
  scope: Scope;
  datasetIds: readonly string[];
  taskIds: readonly string[];
  hasFilter: boolean;
  canRead: boolean;
  canDownload: boolean;
}) {
  const [openedId, setOpenedId] = useState<string | null>(null);
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
  return (
    <section aria-label="原始数据导出">
      <Alert
        showIcon
        type="info"
        title="按原始文件下载"
        description="保留上传时的文件内容，不要求处理或标注完成。展开原始批次后，选择要下载的原文件。"
      />
      <p role="status">
        共 {records.length} 个原始批次 ·{" "}
        {records.reduce((count, item) => count + item.episode_count, 0)} 条原始
        Episode · {records.reduce((count, item) => count + item.file_count, 0)}{" "}
        个文件
      </p>
      <Table<NativeProgress>
        aria-label="原始数据批次"
        rowKey="import_id"
        dataSource={records}
        pagination={{ pageSize: 20, hideOnSinglePage: true }}
        columns={[
          { title: "原始批次", dataIndex: "import_id" },
          { title: "源格式", dataIndex: "source_format" },
          { title: "原始 Episode 数", dataIndex: "episode_count" },
          { title: "文件数", dataIndex: "file_count" },
          {
            title: "操作",
            key: "open",
            render: (_, record) => (
              <Button onClick={() => setOpenedId(record.import_id)}>
                选择原文件
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

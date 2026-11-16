import { lazy, Suspense, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Card, Checkbox, DatePicker, Radio, Select } from "antd";
import dayjs from "dayjs";
import { RefreshCcw } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { taskNumber } from "../../shared/lib/resource-labels";
import {
  fetchDatasets,
  type DatasetListItemVm,
} from "../../features/datasets/api";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import { PageState, StandardPageScaffold } from "../../shared/ui";
import {
  collectionTaskGateway,
  type CollectionTask,
} from "../../features/collection-tasks/api";
import { loadUploadBatches } from "../../features/exports/package-deliveries";
import { PackageBatchPanel } from "./PackageBatchPanel";
import styles from "./styles.module.css";

const RawDataExportPanel = lazy(() => import("./RawDataExportPanel"));
type DataStage = "annotated" | "dataset" | "raw" | "qc_passed";
const stageLabels: Record<DataStage, string> = {
  annotated: "标注完成数据",
  qc_passed: "质检通过且已入库的数据",
  dataset: "数据集数据",
  raw: "原始数据",
};

function listParam(params: URLSearchParams, key: string): string[] {
  return [
    ...new Set(
      params
        .getAll(key)
        .map((value) => value.trim())
        .filter(Boolean),
    ),
  ];
}

async function allPages<T>(
  fetchPage: (
    cursor?: string,
  ) => Promise<{ items: readonly T[]; next?: string | null }>,
): Promise<T[]> {
  const items: T[] = [];
  const seen = new Set<string>();
  let cursor: string | undefined;
  do {
    const page = await fetchPage(cursor);
    items.push(...page.items);
    cursor = page.next ?? undefined;
    if (cursor && seen.has(cursor))
      throw new Error("分页游标重复，请刷新后重试。");
    if (cursor) seen.add(cursor);
  } while (cursor);
  return items;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

function toSource(item: DatasetListItemVm) {
  return {
    datasetId: item.datasetId,
    datasetName: item.name,
    versionId: item.currentVersion?.versionId ?? null,
    versionLabel: item.currentVersion?.displayVersion ?? "待选择数据",
  };
}

export function DataExportPage() {
  const [params, setParams] = useSearchParams();
  const capabilities = useCapabilities();
  const scope = useShellStore((state) => state.scope);
  const scopeKey = useShellStore((state) => state.scopeKey);
  const projectId = scope?.projectId;
  const appliedDatasetIds = listParam(params, "datasetId");
  const appliedTasks = listParam(params, "task");
  const requestedStage = params.get("dataStage");
  // Old bookmarks without an explicit stage opened annotated data.
  const dataStage: DataStage =
    requestedStage === "raw" ||
    requestedStage === "dataset" ||
    requestedStage === "annotated"
      ? requestedStage
      : !requestedStage && params.get("delivery") === "legacy"
        ? "annotated"
        : "qc_passed";
  const batchTags = listParam(params, "uploadBatch");
  const allBatches = batchTags.includes("all");
  const batchNumbers = batchTags
    .map(Number)
    .filter((n) => Number.isSafeInteger(n) && n > 0);
  const [from, through] = params
    .getAll("uploadTime")
    .map((value) => dayjs(value));
  const uploadRange =
    from?.isValid() && through?.isValid() && !through.isBefore(from)
      ? ([from, through] as const)
      : null;
  const uploadedFrom = uploadRange?.[0].startOf("minute").toISOString();
  const uploadedBefore = uploadRange?.[1]
    .startOf("minute")
    .add(1, "minute")
    .toISOString();
  const hasUploadFilter =
    allBatches || batchNumbers.length > 0 || Boolean(uploadRange);
  const canRead =
    capabilities.has("export.read") &&
    capabilities.has("dataset.read") &&
    capabilities.has("dataset_version.read") &&
    capabilities.has("episode.read");
  const canCreate = capabilities.has("export.create");
  const canDownload = capabilities.has("export.download");
  const batches = useQuery({
    queryKey: ["export-upload-batches", scopeKey],
    queryFn: ({ signal }) => loadUploadBatches(scope!, signal),
    enabled: canRead && canDownload && Boolean(scope && projectId),
  });
  const batchOptions = (batches.data ?? []).map((b) => ({
    value: String(b.batch_number),
    label: `第 ${b.batch_number} 次上传 · ${b.channel === "historical" ? "历史上传来源" : b.channel === "oss" ? "OSS" : "网页"} · ${formatDate(b.created_at)}`,
  }));
  const filteredMode = dataStage === "qc_passed" || hasUploadFilter;
  const datasets = useQuery({
    queryKey: ["export-datasets", scopeKey],
    queryFn: ({ signal }) =>
      allPages<DatasetListItemVm>(async (after) => {
        const page = await fetchDatasets(
          { sort: "activityDesc", limit: 100, after },
          signal,
        );
        if (page.pageInfo.hasNextPage && !page.pageInfo.after)
          throw new Error("数据集分页不完整。");
        return {
          items: page.items,
          next: page.pageInfo.hasNextPage ? page.pageInfo.after : null,
        };
      }),
    enabled: canRead,
    staleTime: 30_000,
  });
  const collectionTasks = useQuery({
    queryKey: ["export-collection-tasks", scopeKey],
    queryFn: ({ signal }) =>
      allPages<CollectionTask>(async (cursor) => {
        const page = await collectionTaskGateway.list(
          { ...scope!, projectId: projectId!, regionCode: scope!.regionCode! },
          { limit: 100, ...(cursor ? { cursor } : {}) },
          signal,
        );
        return { items: page.items, next: page.next_cursor };
      }),
    enabled: canRead && Boolean(scope && projectId),
    staleTime: 30_000,
  });
  const allSources = useMemo(
    () => (datasets.data ?? []).map(toSource),
    [datasets.data],
  );
  const taskById = new Map(
    (collectionTasks.data ?? []).map((task) => [task.collection_task_id, task]),
  );
  const activeSourceFilterCount =
    appliedDatasetIds.length + appliedTasks.length;
  const requestedSources = allSources.filter(
    (source) =>
      (appliedDatasetIds.length === 0 ||
        appliedDatasetIds.includes(source.datasetId)) &&
      (appliedTasks.length === 0 ||
        appliedTasks.some(
          (id) => taskById.get(id)?.dataset_id === source.datasetId,
        )),
  );
  const updateSourceFilter = (key: string, values: readonly string[]) => {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete(key);
        values.forEach((value) => next.append(key, value));
        return next;
      },
      { replace: true },
    );
  };
  const resetSourceSearch = () => {
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        ["datasetId", "task", "tag", "robotId", "q", "uploadBatch", "uploadTime"].forEach(
          (key) => next.delete(key),
        );
        return next;
      },
      { replace: true },
    );
  };
  return (
    <main className={styles.page} data-page-id="P21">
      <StandardPageScaffold
        header={{
          title: "数据导出",
          breadcrumbs: [
            { key: "production", label: "数据生产" },
            { key: "exports", label: "数据导出" },
          ],
        }}
        state={
          <div className={styles.workspace}>
            <Card className={styles.sourceCard}>
              {!canRead && !capabilities.loading ? (
                <PageState
                  state="forbidden"
                  description="需要数据集读取和导出查看权限。"
                />
              ) : datasets.isPending || capabilities.loading ? (
                <PageState state="loading" label="可导出数据" />
              ) : datasets.isError ? (
                <PageState
                  state="error"
                  description={datasets.error.message}
                  onRetry={() => void datasets.refetch()}
                />
              ) : (
                <>
                  <div
                    className={`${styles.filterSection} ${styles.stageSelector}`}
                  >
                    <strong>类型</strong>
                    <Radio.Group
                      aria-label="导出数据类型"
                      optionType="button"
                      buttonStyle="solid"
                      value={dataStage}
                      options={(
                        ["annotated", "qc_passed", "raw", "dataset"] as const
                      ).map((value) => ({ value, label: stageLabels[value] }))}
                      onChange={(event) => {
                        setParams(
                          (current) => {
                            const next = new URLSearchParams(current);
                            next.delete("delivery");
                            next.set("dataStage", event.target.value);
                            return next;
                          },
                          { replace: true },
                        );
                      }}
                    />
                  </div>
                  <section
                    aria-label="选择数据范围"
                    className={styles.filterSection}
                  >
                    <div className={styles.filterGrid}>
                      <div className={styles.filterField}>
                        <label htmlFor="export-dataset-filter">数据集</label>
                        <Select
                          allowClear
                          showSearch
                          id="export-dataset-filter"
                          maxTagCount="responsive"
                          mode="multiple"
                          optionFilterProp="label"
                          options={allSources.map((source) => ({
                            value: source.datasetId,
                            label: source.versionId
                              ? `${source.datasetName} · ${source.versionLabel}`
                              : source.datasetName,
                          }))}
                          placeholder="全部数据集"
                          value={appliedDatasetIds}
                          onChange={(values) =>
                            updateSourceFilter("datasetId", values)
                          }
                        />
                      </div>
                      <div className={styles.filterField}>
                        <label htmlFor="export-task-filter">任务</label>
                        <Select
                          allowClear
                          showSearch
                          id="export-task-filter"
                          loading={collectionTasks.isPending}
                          maxTagCount="responsive"
                          mode="multiple"
                          optionFilterProp="label"
                          options={(collectionTasks.data ?? [])
                            .filter(
                              (task) =>
                                appliedDatasetIds.length === 0 ||
                                appliedDatasetIds.includes(task.dataset_id),
                            )
                            .map((task) => ({
                              value: task.collection_task_id,
                              label: taskNumber(task.task_code),
                            }))}
                          placeholder="全部任务"
                          value={appliedTasks}
                          onChange={(values) =>
                            updateSourceFilter("task", values)
                          }
                        />
                      </div>
                      <div className={styles.filterField}>
                        <label htmlFor="export-upload-start">上传时间</label>
                        <DatePicker.RangePicker
                          id={{
                            start: "export-upload-start",
                            end: "export-upload-end",
                          }}
                          placeholder={["开始时间", "结束时间"]}
                          format="YYYY-MM-DD HH:mm"
                          showTime={{
                            format: "HH:mm",
                            defaultOpenValue: [
                              dayjs().startOf("day"),
                              dayjs().endOf("day"),
                            ],
                          }}
                          value={uploadRange ? [...uploadRange] : null}
                          onChange={(range) =>
                            updateSourceFilter(
                              "uploadTime",
                              range?.[0] && range[1]
                                ? [range[0], range[1]].map((value) =>
                                    value.startOf("minute").toISOString(),
                                  )
                                : [],
                            )
                          }
                        />
                      </div>
                      <div className={styles.filterField}>
                        <label htmlFor="export-upload-batch">上传批次</label>
                        <Select
                          allowClear
                          showSearch
                          optionFilterProp="label"
                          id="export-upload-batch"
                          placeholder="全部上传批次"
                          loading={batches.isFetching}
                          mode="multiple"
                          maxTagCount="responsive"
                          value={
                            allBatches
                              ? batchOptions.map((b) => b.value)
                              : batchNumbers.map(String)
                          }
                          options={batchOptions}
                          onChange={(values: string[]) =>
                            updateSourceFilter(
                              "uploadBatch",
                              batchOptions.length &&
                                values.length === batchOptions.length
                                ? ["all"]
                                : values,
                            )
                          }
                        />
                        <Checkbox
                          checked={allBatches}
                          indeterminate={!allBatches && batchNumbers.length > 0}
                          disabled={!batchOptions.length}
                          onChange={(e) =>
                            updateSourceFilter(
                              "uploadBatch",
                              e.target.checked ? ["all"] : [],
                            )
                          }
                        >
                          全选上传批次
                        </Checkbox>
                        {batches.isError && (
                          <Button
                            size="small"
                            onClick={() => void batches.refetch()}
                          >
                            批次读取失败，重试
                          </Button>
                        )}
                      </div>
                      <Button
                        disabled={
                          activeSourceFilterCount === 0 && !hasUploadFilter
                        }
                        icon={<RefreshCcw aria-hidden="true" size={14} />}
                        size="small"
                        type="text"
                        onClick={resetSourceSearch}
                      >
                        清空筛选
                      </Button>
                    </div>
                    {dataStage !== "raw" &&
                      !filteredMode &&
                      appliedTasks.length > 0 && (
                        <span className={styles.filterHint}>
                          显示任务所属数据集内的数据
                        </span>
                      )}
                  </section>

                  {dataStage === "raw" ? (
                    <Suspense
                      fallback={<PageState state="loading" label="原始数据" />}
                    >
                      <RawDataExportPanel
                        scope={scope!}
                        datasetIds={
                          hasUploadFilter
                            ? appliedDatasetIds
                            : requestedSources.map((source) => source.datasetId)
                        }
                        taskIds={appliedTasks}
                        uploadedFrom={uploadedFrom}
                        uploadedBefore={uploadedBefore}
                        batchNumbers={batchNumbers}
                        allBatches={allBatches}
                        hasFilter={
                          activeSourceFilterCount > 0 || hasUploadFilter
                        }
                        canRead={capabilities.has("upload.read")}
                        canDownload={canDownload}
                      />
                    </Suspense>
                  ) : (
                    <PackageBatchPanel
                      key={`${scopeKey}:${dataStage}:${uploadedFrom ?? ""}:${uploadedBefore ?? ""}:${batchTags.join(",")}:${appliedDatasetIds.join(",")}:${requestedSources.map((s) => s.datasetId).join(",")}:${appliedTasks.join(",")}:${activeSourceFilterCount}`}
                      scope={scope!}
                      datasets={
                        activeSourceFilterCount > 0
                          ? requestedSources.map((s) => ({
                              id: s.datasetId,
                              name: s.datasetName,
                            }))
                          : []
                      }
                      filter={
                        filteredMode
                          ? {
                              stage: dataStage,
                              uploaded_from: uploadedFrom,
                              uploaded_before: uploadedBefore,
                              ...(allBatches
                                ? { all_batches: true }
                                : batchNumbers.length
                                  ? { batch_numbers: batchNumbers }
                                  : {}),
                            }
                          : undefined
                      }
                      datasetIds={appliedDatasetIds}
                      taskIds={appliedTasks}
                      annotated={dataStage === "annotated"}
                      canReadAnnotations={capabilities.has(
                        "annotation_task.read",
                      )}
                      canCreate={canCreate}
                      canDownload={canDownload}
                    />
                  )}
                </>
              )}
            </Card>
          </div>
        }
      />
    </main>
  );
}

export default DataExportPage;

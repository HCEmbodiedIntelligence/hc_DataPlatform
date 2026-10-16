import { ResourceLabel, taskNumber } from "../../shared/lib/resource-labels";
import { downloadExport } from "../../features/exports/download";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Input,
  Progress,
  Radio,
  Select,
  Table,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  Download,
  FileArchive,
  Info,
  PackageCheck,
  RefreshCcw,
  Search,
} from "lucide-react";
import { useSearchParams } from "react-router-dom";
import type { DatasetId } from "../../entities/dataset";
import type { DatasetVersionId } from "../../entities/dataset-version";
import {
  fetchDatasetBootstrap,
  fetchDatasets,
  fetchVersionEpisodes,
  type EpisodeListItemVm,
  type DatasetListItemVm,
} from "../../features/datasets/api";
import {
  authorizePublishedExportDownload,
  cancelPublishedExport,
  createPublishedExport,
  fetchExportEligibility,
  exportFormatSchema,
  fetchPublishedExport,
  retryPublishedExport,
  type PublishedExportFormat,
  type ExportDataStage,
  type PublishedExportJob,
} from "../../features/exports/api";
import { useExportFormats } from "../../features/exports/use-export-formats";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import { PageState, StandardPageScaffold, StatusTag } from "../../shared/ui";
import {
  collectionTaskGateway,
  type CollectionTask,
} from "../../features/collection-tasks/api";
import { PackageBatchPanel } from "./PackageBatchPanel";
import styles from "./styles.module.css";

const RawDataExportPanel = lazy(() => import("./RawDataExportPanel"));
type DataStage = ExportDataStage | "raw";
const stageLabels: Record<DataStage, string> = {
  annotated: "标注完成数据",
  dataset: "数据集数据",
  raw: "原始数据",
};

const MAX_BATCH_TASKS = 50;
const CREATE_CONCURRENCY = 4;
const exportJobIdPattern =
  /^export:v1:[A-Za-z0-9._~%-]{1,256}:[A-Za-z0-9._~%-]{1,1024}$/u;

const statusLabels = {
  PENDING: "等待中",
  RUNNING: "生成中",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
} as const;

type ExportSource = Readonly<{
  datasetId: DatasetId;
  datasetName: string;
  versionId: DatasetVersionId | null;
  versionLabel: string;
}>;

type ExportableSource = ExportSource &
  Readonly<{ versionId: DatasetVersionId }>;

type ExportEpisode = EpisodeListItemVm &
  Readonly<{
    key: string;
    source: ExportableSource;
  }>;

type ExportTaskRef = Readonly<{
  jobId: string;
  datasetId: string;
  versionId: string;
  datasetName: string;
  versionLabel: string;
  format: PublishedExportFormat;
  createdAt: string;
  episodeCount?: number;
  dataStage?: ExportDataStage;
}>;

type TaskFilter = "all" | "active" | "succeeded" | "failed";
type TaskRow = Readonly<{
  key: string;
  ref: ExportTaskRef;
  job: PublishedExportJob | null;
  error: unknown;
  queryIndex: number;
}>;
type BatchRequest = Readonly<{
  implementationVersion: string;
  source: ExportableSource;
  format: PublishedExportFormat;
  episodeIds: readonly string[];
  dataStage: ExportDataStage;
}>;
type CreatedTask = Readonly<{ ref: ExportTaskRef; job: PublishedExportJob }>;
type BatchResult = Readonly<{
  created: readonly CreatedTask[];
  failed: readonly Readonly<{ request: BatchRequest; reason: string }>[];
}>;
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

function idempotencyKey(prefix: string): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`
  );
}

function downloadFileName(ref: ExportTaskRef): string {
  const stem = `${ref.datasetName}-${ref.versionLabel}-${ref.format}`
    .trim()
    .replace(/[<>:"/\\|?*]/gu, "-")
    .replace(/\s+/gu, "-")
    .slice(0, 120);
  return stem || "data-export";
}

function readableError(error: unknown): string {
  if (isDomainError(error)) {
    if (
      error.problemCode === "EXPORT_EPISODE_NOT_ELIGIBLE" ||
      error.problemCode === "NO_ELIGIBLE_ROLLOUTS"
    ) {
      return "所选数据尚未全部通过质量检查和标注审核，请先完成审核后导出。";
    }
    if (error.problemCode === "DATASET_VERSION_NOT_FOUND") {
      return "当前 READY 版本尚未完成导出准备，请刷新数据后重试。";
    }
    if (error.problemCode === "EXPORT_SOURCE_NOT_FOUND") {
      return "该版本的源数据已不可用，无法生成导出文件。";
    }
  }
  return error instanceof Error ? error.message : "操作未完成，请稍后重试。";
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

function progressOf(job: PublishedExportJob | null): number {
  if (!job) return 0;
  return Math.round(
    (job.progress.completed_phases / job.progress.total_phases) * 100,
  );
}

function statusTone(status: PublishedExportJob["status"]) {
  if (status === "SUCCEEDED") return "success" as const;
  if (status === "FAILED") return "danger" as const;
  if (status === "CANCELLED") return "neutral" as const;
  return "info" as const;
}

function taskQueryKey(scopeKey: string, ref: ExportTaskRef) {
  return [
    "published-export",
    scopeKey,
    ref.datasetId,
    ref.versionId,
    ref.jobId,
  ] as const;
}

function taskStorageKey(scopeKey: string): string {
  return `hc-data-export-tasks:${scopeKey}`;
}

function isTaskRef(value: unknown): value is ExportTaskRef {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Partial<ExportTaskRef>;
  return (
    typeof item.jobId === "string" &&
    exportJobIdPattern.test(item.jobId) &&
    typeof item.datasetId === "string" &&
    typeof item.versionId === "string" &&
    typeof item.datasetName === "string" &&
    typeof item.versionLabel === "string" &&
    exportFormatSchema.safeParse(item.format).success &&
    typeof item.createdAt === "string"
  );
}

function loadTaskRefs(scopeKey: string): ExportTaskRef[] {
  try {
    const raw = globalThis.sessionStorage?.getItem(taskStorageKey(scopeKey));
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isTaskRef).slice(0, 200) : [];
  } catch {
    return [];
  }
}

function persistTaskRefs(
  scopeKey: string,
  refs: readonly ExportTaskRef[],
): void {
  try {
    globalThis.sessionStorage?.setItem(
      taskStorageKey(scopeKey),
      JSON.stringify(refs.slice(0, 200)),
    );
  } catch {
    // Session persistence is optional; in-memory polling still works.
  }
}

function mergeTaskRefs(
  current: readonly ExportTaskRef[],
  additions: readonly ExportTaskRef[],
): ExportTaskRef[] {
  const seen = new Set<string>();
  return [...additions, ...current].filter((item) => {
    if (seen.has(item.jobId)) return false;
    seen.add(item.jobId);
    return true;
  });
}

function toSource(item: DatasetListItemVm): ExportSource {
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
  const queryClient = useQueryClient();
  const appliedDatasetIds = listParam(params, "datasetId");
  const appliedTasks = listParam(params, "task");
  const dataStage: DataStage =
    params.get("dataStage") === "raw"
      ? "raw"
      : params.get("dataStage") === "dataset"
        ? "dataset"
        : "annotated";
  const standardMode =
    dataStage !== "raw" && params.get("delivery") !== "legacy";
  const [selectedSourceKeys, setSelectedSourceKeys] = useState<string[]>([]);
  const [formatSelection, setSelectedFormats] = useState<
    PublishedExportFormat[] | null
  >(null);
  const [taskQuery, setTaskQuery] = useState("");
  const [taskFilter, setTaskFilter] = useState<TaskFilter>("all");
  const [batchFeedback, setBatchFeedback] = useState<{
    type: "success" | "warning" | "error";
    title: string;
    description: string;
  } | null>(null);
  const [downloadFeedback, setDownloadFeedback] = useState<string | null>(null);
  const [taskStore, setTaskStore] = useState(() => ({
    scopeKey,
    refs: loadTaskRefs(scopeKey),
  }));

  const canRead =
    capabilities.has("export.read") &&
    capabilities.has("dataset.read") &&
    capabilities.has("dataset_version.read") &&
    capabilities.has("episode.read");
  const canCreate = capabilities.has("export.create");
  const canDownload = capabilities.has("export.download");
  const formats = useExportFormats(
    scopeKey,
    projectId,
    canRead && !standardMode && dataStage !== "raw",
  );
  const formatOptions = formats.data ?? [];
  const availableFormats = formatOptions.filter((item) => item.available);
  const selectedFormats = (
    formatSelection ?? availableFormats.slice(0, 1).map((item) => item.format)
  ).filter((format) => availableFormats.some((item) => item.format === format));
  const formatLabel = (format: PublishedExportFormat) =>
    formatOptions.find((item) => item.format === format)?.display_name ??
    format;

  useEffect(() => {
    setSelectedSourceKeys([]);
    setSelectedFormats(null);
    setTaskStore((current) =>
      current.scopeKey === scopeKey
        ? current
        : { scopeKey, refs: loadTaskRefs(scopeKey) },
    );
  }, [scopeKey]);

  const taskRefs = taskStore.scopeKey === scopeKey ? taskStore.refs : [];
  useEffect(() => {
    if (taskStore.scopeKey === scopeKey) {
      persistTaskRefs(scopeKey, taskStore.refs);
    }
  }, [scopeKey, taskStore]);

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
  const sourceQueries = useQueries({
    queries: requestedSources.map((source) => ({
      queryKey: ["export-source", scopeKey, source.datasetId],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchDatasetBootstrap(source.datasetId, signal),
      enabled:
        canRead &&
        !standardMode &&
        activeSourceFilterCount > 0 &&
        dataStage !== "raw",
      staleTime: 0,
    })),
  });
  const sources: ExportableSource[] = requestedSources.flatMap(
    (source, index) => {
      const data = sourceQueries[index]?.data;
      // Dataset list currentVersion describes a business release. Annotation and
      // first-time exports use the current working snapshot before any release.
      const versionId =
        data?.workingVersionId ?? data?.currentReadyVersion?.versionId;
      if (!versionId) return [];
      return [
        {
          ...source,
          versionId,
          versionLabel: data?.workingVersionId
            ? "当前工作数据"
            : data!.currentReadyVersion!.displayVersion,
        },
      ];
    },
  );
  const episodeQueries = useQueries({
    queries: sources.map((source) => ({
      queryKey: [
        "export-eligible-episodes",
        scopeKey,
        source.datasetId,
        source.versionId,
        dataStage,
      ],
      queryFn: async ({ signal }: { signal: AbortSignal }) => {
        const [eligibility, items] = await Promise.all([
          fetchExportEligibility({
            projectId: projectId!,
            datasetId: source.datasetId,
            datasetVersion: source.versionId,
            signal,
            dataStage: dataStage === "dataset" ? "dataset" : "annotated",
          }),
          allPages<EpisodeListItemVm>(async (after) => {
            const page = await fetchVersionEpisodes(
              source.datasetId,
              source.versionId,
              {
                limit: 100,
                included: true,
                after,
              },
              signal,
            );
            if (page.pageInfo.hasNextPage && !page.pageInfo.after)
              throw new Error("Episode 分页不完整。");
            return {
              items: page.items,
              next: page.pageInfo.hasNextPage ? page.pageInfo.after : null,
            };
          }),
        ]);
        const eligibleIds = new Set(eligibility.eligible_episode_ids);
        return items.filter((episode) => eligibleIds.has(episode.episodeId));
      },
      enabled:
        canRead &&
        !standardMode &&
        Boolean(projectId) &&
        activeSourceFilterCount > 0 &&
        dataStage !== "raw",
      staleTime: 0,
    })),
  });
  const episodes: ExportEpisode[] = sources.flatMap((source, index) =>
    (episodeQueries[index]?.isError || episodeQueries[index]?.isFetching
      ? []
      : (episodeQueries[index]?.data ?? [])
    )
      .filter(
        (episode) =>
          appliedTasks.length === 0 ||
          (episode.task !== null && appliedTasks.includes(episode.task)),
      )
      .map((episode) => ({
        ...episode,
        source,
        key: `${scopeKey}/${source.datasetId}/${source.versionId}/${episode.episodeId}`,
      })),
  );
  const selectedKeys = new Set(selectedSourceKeys);
  const selectedEpisodes = episodes.filter((episode) =>
    selectedKeys.has(episode.key),
  );
  const selectedSources = sources.filter((source) =>
    selectedEpisodes.some((episode) => episode.datasetId === source.datasetId),
  );
  const batchTaskCount = selectedSources.length * selectedFormats.length;
  const selectedEpisodeCount = selectedEpisodes.length;
  const sourceFiltersPending =
    activeSourceFilterCount > 0 &&
    (sourceQueries.some((query) => query.isPending || query.isFetching) ||
      episodeQueries.some((query) => query.isPending || query.isFetching));
  const sourceFilterError =
    sourceQueries.find((query) => query.isError)?.error ??
    episodeQueries.find((query) => query.isError)?.error;

  const taskQueries = useQueries({
    queries: taskRefs.map((ref) => ({
      queryKey: taskQueryKey(scopeKey, ref),
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchPublishedExport({
          projectId: projectId!,
          datasetId: ref.datasetId,
          datasetVersion: ref.versionId,
          jobId: ref.jobId,
          signal,
        }),
      enabled: canRead && !standardMode && Boolean(projectId),
      staleTime: 0,
      refetchInterval: (query: { state: { data?: PublishedExportJob } }) => {
        const status = query.state.data?.status;
        return !status || status === "PENDING" || status === "RUNNING"
          ? 2_000
          : false;
      },
    })),
  });

  const taskRows: TaskRow[] = taskRefs.map((ref, queryIndex) => ({
    key: ref.jobId,
    ref,
    job: taskQueries[queryIndex]?.data ?? null,
    error: taskQueries[queryIndex]?.error ?? null,
    queryIndex,
  }));
  const visibleTaskRows = useMemo(() => {
    const needle = taskQuery.trim().toLocaleLowerCase("zh-CN");
    return taskRows.filter((row) => {
      const status = row.job?.status ?? "PENDING";
      const matchesStatus =
        taskFilter === "all" ||
        (taskFilter === "active" &&
          (status === "PENDING" || status === "RUNNING")) ||
        (taskFilter === "succeeded" && status === "SUCCEEDED") ||
        (taskFilter === "failed" &&
          (status === "FAILED" || status === "CANCELLED"));
      const matchesText =
        !needle ||
        `${row.ref.datasetName} ${row.ref.versionLabel} ${row.ref.jobId}`
          .toLocaleLowerCase("zh-CN")
          .includes(needle);
      return matchesStatus && matchesText;
    });
  }, [taskFilter, taskQuery, taskRows]);

  const updateTaskRefs = (
    updater: (current: readonly ExportTaskRef[]) => ExportTaskRef[],
  ) => {
    setTaskStore((current) => {
      const base =
        current.scopeKey === scopeKey ? current.refs : loadTaskRefs(scopeKey);
      return { scopeKey, refs: updater(base) };
    });
  };

  const createBatch = useMutation({
    mutationFn: async (
      requests: readonly BatchRequest[],
    ): Promise<BatchResult> => {
      const created: CreatedTask[] = [];
      const failed: { request: BatchRequest; reason: string }[] = [];
      for (
        let index = 0;
        index < requests.length;
        index += CREATE_CONCURRENCY
      ) {
        const chunk = requests.slice(index, index + CREATE_CONCURRENCY);
        const settled = await Promise.allSettled(
          chunk.map((request) =>
            createPublishedExport({
              projectId: projectId!,
              datasetId: request.source.datasetId,
              datasetVersion: request.source.versionId,
              format: request.format,
              implementationVersion: request.implementationVersion,
              episodeIds: request.episodeIds,
              dataStage: request.dataStage,
              idempotencyKey: idempotencyKey("published-export"),
            }),
          ),
        );
        settled.forEach((result, resultIndex) => {
          const request = chunk[resultIndex]!;
          if (result.status === "fulfilled") {
            const job = result.value;
            created.push({
              job,
              ref: {
                jobId: job.job_id,
                datasetId: request.source.datasetId,
                versionId: request.source.versionId,
                datasetName: request.source.datasetName,
                versionLabel: request.source.versionLabel,
                format: request.format,
                createdAt: job.created_at,
                episodeCount: request.episodeIds.length,
                dataStage: request.dataStage,
              },
            });
          } else {
            failed.push({ request, reason: readableError(result.reason) });
          }
        });
      }
      return { created, failed };
    },
    onSuccess: (result) => {
      result.created.forEach(({ ref, job }) => {
        queryClient.setQueryData(taskQueryKey(scopeKey, ref), job);
      });
      updateTaskRefs((current) =>
        mergeTaskRefs(
          current,
          result.created.map((item) => item.ref),
        ),
      );
      setDownloadFeedback(null);
      if (result.failed.length === 0) {
        setBatchFeedback({
          type: "success",
          title: `已创建 ${result.created.length} 个导出任务`,
          description:
            "各任务将独立生成 导出文件，可在下方查看进度并分别下载。",
        });
      } else if (result.created.length > 0) {
        setBatchFeedback({
          type: "warning",
          title: `已创建 ${result.created.length} 个，${result.failed.length} 个未创建`,
          description: "已成功的任务继续运行；请检查失败项后重新提交。",
        });
      } else {
        setBatchFeedback({
          type: "error",
          title: "批量导出任务创建失败",
          description: result.failed[0]?.reason ?? "请稍后重试。",
        });
      }
    },
  });

  const cancelTask = useMutation({
    mutationFn: (ref: ExportTaskRef) =>
      cancelPublishedExport({
        projectId: projectId!,
        datasetId: ref.datasetId,
        datasetVersion: ref.versionId,
        jobId: ref.jobId,
      }),
    onSuccess: (job, ref) =>
      queryClient.setQueryData(taskQueryKey(scopeKey, ref), job),
  });
  const retryTask = useMutation({
    mutationFn: (ref: ExportTaskRef) =>
      retryPublishedExport({
        projectId: projectId!,
        datasetId: ref.datasetId,
        datasetVersion: ref.versionId,
        jobId: ref.jobId,
        idempotencyKey: idempotencyKey("published-export-retry"),
      }),
    onSuccess: (job, previousRef) => {
      const ref: ExportTaskRef = {
        ...previousRef,
        jobId: job.job_id,
        format: job.format,
        createdAt: job.created_at,
      };
      queryClient.setQueryData(taskQueryKey(scopeKey, ref), job);
      updateTaskRefs((current) => mergeTaskRefs(current, [ref]));
    },
  });
  const downloadTask = useMutation({
    mutationFn: (ref: ExportTaskRef) =>
      authorizePublishedExportDownload({
        projectId: projectId!,
        datasetId: ref.datasetId,
        datasetVersion: ref.versionId,
        jobId: ref.jobId,
      }),
    onSuccess: (authorization, ref) => {
      downloadExport(authorization, downloadFileName(ref));
      setDownloadFeedback(
        `${ref.datasetName} · ${formatLabel(ref.format)} 文件已开始下载，授权有效期至 ${formatDate(authorization.expires_at)}。`,
      );
    },
  });

  const updateSourceFilter = (key: string, values: readonly string[]) => {
    setSelectedSourceKeys([]);
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
        ["datasetId", "task", "tag", "robotId", "q"].forEach((key) =>
          next.delete(key),
        );
        return next;
      },
      { replace: true },
    );
    setSelectedSourceKeys([]);
  };
  const startBatch = () => {
    if (!projectId || dataStage === "raw" || !formats.isSuccess) return;
    setBatchFeedback(null);
    createBatch.mutate(
      selectedSources.flatMap((source) =>
        selectedFormats.map((format) => ({
          source,
          format,
          implementationVersion: availableFormats.find(
            (item) => item.format === format,
          )!.implementation_version,
          dataStage,
          episodeIds: selectedEpisodes
            .filter((episode) => episode.datasetId === source.datasetId)
            .map((episode) => episode.episodeId),
        })),
      ),
    );
  };

  const sourceColumns: ColumnsType<ExportEpisode> = [
    {
      title: "Episode",
      key: "episode",
      render: (_, episode) => (
        <div className={styles.primaryCell}>
          <strong title={episode.episodeId}>Episode {episode.ordinal}</strong>
        </div>
      ),
    },
    {
      title: "数据集",
      key: "dataset",
      render: (_, episode) => episode.source.datasetName,
    },
    {
      title: "来源任务",
      key: "task",
      render: (_, episode) =>
        episode.task ? (
          <ResourceLabel
            kind="collection_task"
            id={episode.task}
            fallback={
              taskById.get(episode.task)
                ? taskNumber(taskById.get(episode.task)!.task_code)
                : undefined
            }
          />
        ) : (
          "—"
        ),
    },
    {
      title: "版本",
      key: "version",
      render: (_, episode) => episode.source.versionLabel,
    },
    {
      title: dataStage === "annotated" ? "标注状态" : "处理状态",
      render: () => (
        <StatusTag
          known
          status={dataStage === "annotated" ? "标注完成" : "处理完成"}
          tone="success"
        />
      ),
    },
  ];

  const taskColumns: ColumnsType<TaskRow> = [
    {
      title: "导出对象",
      key: "target",
      render: (_, row) => (
        <div className={styles.primaryCell}>
          <strong>{row.ref.datasetName}</strong>
          <span>
            {stageLabels[row.ref.dataStage ?? "annotated"]} ·{" "}
            {row.ref.versionLabel}
            {row.ref.episodeCount !== undefined
              ? ` · ${row.ref.episodeCount} 个 Episode`
              : ""}
          </span>
        </div>
      ),
    },
    {
      title: "格式",
      key: "format",
      render: (_, row) => formatLabel(row.ref.format),
    },
    {
      title: "状态",
      key: "status",
      render: (_, row) =>
        row.error ? (
          <StatusTag known status="同步失败" tone="danger" />
        ) : row.job ? (
          <StatusTag
            known
            status={statusLabels[row.job.status]}
            tone={statusTone(row.job.status)}
          />
        ) : (
          <StatusTag known status="同步中" tone="info" />
        ),
    },
    {
      title: "进度",
      key: "progress",
      responsive: ["lg"],
      render: (_, row) => (
        <div className={styles.compactProgress}>
          <Progress
            percent={progressOf(row.job)}
            showInfo={false}
            size="small"
            status={
              row.job?.status === "FAILED"
                ? "exception"
                : row.job?.status === "SUCCEEDED"
                  ? "success"
                  : "active"
            }
          />
          <span>{row.job?.progress.phase ?? "读取任务"}</span>
        </div>
      ),
    },
    {
      title: "技术信息",
      key: "jobId",
      responsive: ["xl"],
      render: (_, row) => (
        <details>
          <summary>导出记录</summary>
          <code className={styles.jobId}>{row.ref.jobId}</code>
        </details>
      ),
    },
    {
      title: "操作",
      key: "actions",
      align: "right",
      render: (_, row) => {
        const status = row.job?.status;
        const cancelling =
          cancelTask.isPending && cancelTask.variables?.jobId === row.ref.jobId;
        const retrying =
          retryTask.isPending && retryTask.variables?.jobId === row.ref.jobId;
        const downloading =
          downloadTask.isPending &&
          downloadTask.variables?.jobId === row.ref.jobId;
        if (row.error) {
          return (
            <Button
              aria-label={`刷新 ${row.ref.datasetName} 导出任务`}
              icon={<RefreshCcw aria-hidden="true" size={15} />}
              size="small"
              onClick={() => void taskQueries[row.queryIndex]?.refetch()}
            >
              刷新
            </Button>
          );
        }
        if (status === "PENDING" || status === "RUNNING") {
          return (
            <Button
              danger
              aria-label={`取消 ${row.ref.datasetName} 导出任务`}
              disabled={cancelTask.isPending}
              loading={cancelling}
              size="small"
              onClick={() => cancelTask.mutate(row.ref)}
            >
              取消
            </Button>
          );
        }
        if (status === "FAILED" || status === "CANCELLED") {
          return (
            <Button
              aria-label={`重试 ${row.ref.datasetName} 导出任务`}
              disabled={!canCreate || retryTask.isPending}
              icon={<RefreshCcw aria-hidden="true" size={15} />}
              loading={retrying}
              size="small"
              onClick={() => retryTask.mutate(row.ref)}
            >
              重试
            </Button>
          );
        }
        if (status === "SUCCEEDED") {
          return (
            <Button
              aria-label={`下载 ${row.ref.datasetName} ${formatLabel(row.ref.format)} 文件`}
              disabled={!canDownload || downloadTask.isPending}
              icon={<Download aria-hidden="true" size={15} />}
              loading={downloading}
              size="small"
              type="primary"
              onClick={() => downloadTask.mutate(row.ref)}
            >
              下载文件
            </Button>
          );
        }
        return <Typography.Text type="secondary">同步中</Typography.Text>;
      },
    },
  ];

  const taskCounts = taskRows.reduce(
    (counts, row) => {
      const status = row.job?.status ?? "PENDING";
      counts.total += 1;
      if (status === "PENDING" || status === "RUNNING") counts.active += 1;
      if (status === "SUCCEEDED") counts.succeeded += 1;
      if (status === "FAILED" || status === "CANCELLED") counts.failed += 1;
      return counts;
    },
    { total: 0, active: 0, succeeded: 0, failed: 0 },
  );
  const batchBlockedReason =
    batchTaskCount > MAX_BATCH_TASKS
      ? `一次最多创建 ${MAX_BATCH_TASKS} 个任务，请减少数据条目或导出格式。`
      : null;
  const creationReady =
    canCreate &&
    Boolean(projectId) &&
    formats.isSuccess &&
    batchTaskCount > 0 &&
    batchTaskCount <= MAX_BATCH_TASKS &&
    !sourceFiltersPending &&
    !sourceFilterError;

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
            <Card
              className={styles.sourceCard}
              title={
                <span className={styles.cardTitle}>
                  <FileArchive aria-hidden="true" size={18} />
                  {dataStage === "raw" ? "导出原始数据" : "创建导出任务"}
                </span>
              }
              extra={
                <span className={styles.selectionCount} aria-live="polite">
                  {dataStage === "raw" ? (
                    "保留上传时的原文件"
                  ) : standardMode ? (
                    "使用所选数据与当前标注"
                  ) : (
                    <>
                      已选 {selectedSources.length} 个数据集 ·{" "}
                      {selectedEpisodeCount.toLocaleString("zh-CN")} 个 Episode
                    </>
                  )}
                </span>
              }
            >
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
                  description={readableError(datasets.error)}
                  onRetry={() => void datasets.refetch()}
                />
              ) : (
                <>
                  <div
                    className={`${styles.filterSection} ${styles.stageSelector}`}
                  >
                    <strong>导出数据类型</strong>
                    <Radio.Group
                      aria-label="导出数据类型"
                      optionType="button"
                      buttonStyle="solid"
                      value={dataStage}
                      options={(["annotated", "raw", "dataset"] as const).map(
                        (value) => ({ value, label: stageLabels[value] }),
                      )}
                      onChange={(event) => {
                        setSelectedSourceKeys([]);
                        setBatchFeedback(null);
                        setParams(
                          (current) => {
                            const next = new URLSearchParams(current);
                            next.set("dataStage", event.target.value);
                            return next;
                          },
                          { replace: true },
                        );
                      }}
                    />
                  </div>
                  {dataStage !== "raw" && (
                    <Radio.Group
                      className={styles.deliverySwitch}
                      optionType="button"
                      aria-label="导出交付方式"
                      value={standardMode ? "standard" : "legacy"}
                      options={[
                        {
                          value: "standard",
                          label: "完整标准包 / LeRobot 集合",
                        },
                        { value: "legacy", label: "历史格式" },
                      ]}
                      onChange={(event) => {
                        setSelectedSourceKeys([]);
                        setBatchFeedback(null);
                        setParams(
                          (current) => {
                            const next = new URLSearchParams(current);
                            next.set("delivery", event.target.value);
                            return next;
                          },
                          { replace: true },
                        );
                      }}
                    />
                  )}
                  <section
                    aria-labelledby="export-scope-title"
                    className={styles.filterSection}
                  >
                    <div className={styles.sectionHeader}>
                      <div>
                        <span className={styles.stepBadge}>1</span>
                        <span>
                          <strong id="export-scope-title">选择数据范围</strong>
                          <small>
                            {dataStage === "raw"
                              ? "按任务或数据集查找，勾选原始批次后批量下载"
                              : "先按任务或数据集查找，再勾选 Episode"}
                          </small>
                        </span>
                      </div>
                      <Button
                        disabled={activeSourceFilterCount === 0}
                        icon={<RefreshCcw aria-hidden="true" size={14} />}
                        size="small"
                        type="text"
                        onClick={resetSourceSearch}
                      >
                        清空筛选
                      </Button>
                    </div>
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
                        <label htmlFor="export-task-filter">
                          {standardMode ? "任务所属数据集" : "任务"}
                        </label>
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
                    </div>
                  </section>

                  {dataStage === "raw" ? (
                    <Suspense
                      fallback={<PageState state="loading" label="原始数据" />}
                    >
                      <RawDataExportPanel
                        scope={scope!}
                        datasetIds={requestedSources.map(
                          (source) => source.datasetId,
                        )}
                        taskIds={appliedTasks}
                        hasFilter={activeSourceFilterCount > 0}
                        canRead={capabilities.has("upload.read")}
                        canDownload={canDownload}
                      />
                    </Suspense>
                  ) : standardMode ? (
                    <>
                      {appliedTasks.length > 0 && (
                        <Alert
                          type="info"
                          title="以下列出所选任务所属数据集的标准包，请逐条选择要交付的 Episode。"
                        />
                      )}
                      <PackageBatchPanel
                        key={`${scopeKey}:${dataStage}:${requestedSources.map((s) => s.datasetId).join(",")}:${appliedTasks.join(",")}:${activeSourceFilterCount}`}
                        scope={scope!}
                        datasets={
                          activeSourceFilterCount > 0
                            ? requestedSources.map((s) => ({
                                id: s.datasetId,
                                name: s.datasetName,
                              }))
                            : []
                        }
                        annotated={dataStage === "annotated"}
                        canReadAnnotations={capabilities.has(
                          "annotation_task.read",
                        )}
                        canCreate={canCreate}
                        canDownload={canDownload}
                      />
                    </>
                  ) : (
                    <>
                      <div className={styles.scopeNote} role="note">
                        <Info aria-hidden="true" size={17} />
                        <span>
                          <strong>按 Episode 选择导出内容</strong>
                          <small>
                            {dataStage === "annotated"
                              ? "仅列出已通过标注审核且符合导出条件的数据，无需先手动发布版本。"
                              : "列出已经处理入库的数据，不要求完成标注；导出不包含标注审核结果。"}
                            同一数据集的 Episode 按格式分别打包。
                          </small>
                        </span>
                      </div>

                      {collectionTasks.isError || sourceFilterError ? (
                        <Alert
                          showIcon
                          className={styles.sourceAlert}
                          title="部分筛选选项加载失败"
                          description={readableError(
                            collectionTasks.error ?? sourceFilterError,
                          )}
                          type="warning"
                        />
                      ) : null}

                      <div className={styles.sectionHeader}>
                        <strong>选择 Episode</strong>
                        {activeSourceFilterCount > 0 &&
                        !sourceFiltersPending &&
                        !sourceFilterError ? (
                          <span role="status">
                            可导出 {episodes.length} 条
                            {dataStage === "annotated"
                              ? "已标注数据"
                              : "数据集数据"}
                          </span>
                        ) : null}
                        <Button
                          disabled={
                            episodes.length === 0 ||
                            sourceFiltersPending ||
                            Boolean(sourceFilterError)
                          }
                          onClick={() =>
                            setSelectedSourceKeys(
                              episodes.map((episode) => episode.key),
                            )
                          }
                        >
                          全选可导出数据
                        </Button>
                        <Button
                          disabled={selectedEpisodeCount === 0}
                          onClick={() => setSelectedSourceKeys([])}
                        >
                          清空选择
                        </Button>
                      </div>
                      <Table<ExportEpisode>
                        aria-label="可导出 Episode 列表"
                        className={styles.sourceTable}
                        columns={sourceColumns}
                        dataSource={episodes}
                        locale={{
                          emptyText:
                            activeSourceFilterCount > 0
                              ? `所选范围暂无可导出的${dataStage === "annotated" ? "已标注数据" : "数据集数据"}`
                              : "请先选择任务或数据集",
                        }}
                        loading={sourceFiltersPending}
                        pagination={{
                          pageSize: 20,
                          showSizeChanger: false,
                          hideOnSinglePage: true,
                        }}
                        rowSelection={{
                          selectedRowKeys: selectedSourceKeys,
                          onChange: (keys) =>
                            setSelectedSourceKeys(keys.map(String)),
                          getCheckboxProps: (episode) => ({
                            "aria-label": `选择 Episode ${episode.ordinal} · ${episode.source.datasetName}`,
                          }),
                        }}
                        size="middle"
                      />
                      <section
                        aria-labelledby="export-format-title"
                        className={styles.exportSettings}
                      >
                        <div className={styles.sectionHeader}>
                          <div>
                            <span className={styles.stepBadge}>2</span>
                            <span>
                              <strong id="export-format-title">
                                选择导出格式
                              </strong>
                              <small>
                                格式可多选，每个格式独立生成导出文件
                              </small>
                            </span>
                          </div>
                        </div>

                        <div className={styles.exportComposer}>
                          <fieldset className={styles.formatFieldset}>
                            <legend>导出格式（可多选）</legend>
                            {formats.isPending ? (
                              <p role="status">正在读取导出格式…</p>
                            ) : null}
                            {formats.isError ? (
                              <p role="alert">
                                无法读取导出格式，请重试。
                                <Button onClick={() => void formats.refetch()}>
                                  重试
                                </Button>
                              </p>
                            ) : null}
                            {formats.isSuccess &&
                            availableFormats.length === 0 ? (
                              <p role="status">当前没有可用的导出格式。</p>
                            ) : null}
                            <div className={styles.formatOptions}>
                              {formatOptions.map((option) => {
                                const checked = selectedFormats.includes(
                                  option.format,
                                );
                                return (
                                  <label
                                    key={option.format}
                                    data-selected={checked}
                                  >
                                    <Checkbox
                                      aria-label={option.display_name}
                                      checked={checked}
                                      disabled={!option.available}
                                      onChange={(event) =>
                                        setSelectedFormats(
                                          event.target.checked
                                            ? [
                                                ...selectedFormats,
                                                option.format,
                                              ]
                                            : selectedFormats.filter(
                                                (format) =>
                                                  format !== option.format,
                                              ),
                                        )
                                      }
                                    />
                                    <span>
                                      <strong>{option.display_name}</strong>
                                      <small>
                                        {option.description}
                                        {!option.available
                                          ? "（当前输入能力不支持）"
                                          : ""}
                                      </small>
                                    </span>
                                  </label>
                                );
                              })}
                            </div>
                          </fieldset>

                          <div
                            className={styles.batchSummary}
                            aria-live="polite"
                          >
                            <div className={styles.summaryStats}>
                              <span>
                                <small>数据集</small>
                                <strong>{selectedSources.length}</strong>
                              </span>
                              <span>
                                <small>Episode 数</small>
                                <strong>
                                  {selectedEpisodeCount.toLocaleString("zh-CN")}
                                </strong>
                              </span>
                              <span>
                                <small>格式</small>
                                <strong>{selectedFormats.length}</strong>
                              </span>
                              <span>
                                <small>任务</small>
                                <strong>{batchTaskCount}</strong>
                              </span>
                            </div>
                            <div className={styles.batchAction}>
                              <small>
                                所选 Episode
                                按数据集和格式分别打包；不会导出未勾选的数据
                              </small>
                              <Button
                                aria-label={`创建 ${batchTaskCount} 个导出任务`}
                                className={styles.primaryAction}
                                disabled={
                                  !creationReady || createBatch.isPending
                                }
                                icon={
                                  <PackageCheck aria-hidden="true" size={17} />
                                }
                                loading={createBatch.isPending}
                                type="primary"
                                onClick={startBatch}
                              >
                                {createBatch.isPending
                                  ? "正在创建…"
                                  : `创建 ${batchTaskCount} 个导出任务`}
                              </Button>
                            </div>
                          </div>
                        </div>

                        {batchBlockedReason ? (
                          <Alert
                            showIcon
                            className={styles.composerAlert}
                            type="warning"
                            title={batchBlockedReason}
                          />
                        ) : null}
                        {!canCreate && !capabilities.loading ? (
                          <Alert
                            showIcon
                            className={styles.composerAlert}
                            type="warning"
                            title="当前账户不能创建导出任务"
                            description="需要 export.create 权限；已有任务仍可查看。"
                          />
                        ) : null}
                        {batchFeedback ? (
                          <Alert
                            showIcon
                            className={styles.composerAlert}
                            description={batchFeedback.description}
                            title={batchFeedback.title}
                            type={batchFeedback.type}
                          />
                        ) : null}
                      </section>
                    </>
                  )}
                </>
              )}
            </Card>

            {!standardMode && (
              <Card
                className={styles.tasksCard}
                title={
                  <span className={styles.cardTitle}>
                    <Download aria-hidden="true" size={18} />
                    导出任务
                  </span>
                }
              >
                <div className={styles.metrics} aria-label="导出任务汇总">
                  <div>
                    <span>全部</span>
                    <strong>{taskCounts.total}</strong>
                  </div>
                  <div>
                    <span>进行中</span>
                    <strong>{taskCounts.active}</strong>
                  </div>
                  <div>
                    <span>已完成</span>
                    <strong>{taskCounts.succeeded}</strong>
                  </div>
                  <div>
                    <span>失败/取消</span>
                    <strong>{taskCounts.failed}</strong>
                  </div>
                </div>

                <div className={styles.taskToolbar}>
                  <div>
                    <label htmlFor="export-task-search">检索任务</label>
                    <Input
                      allowClear
                      id="export-task-search"
                      prefix={<Search aria-hidden="true" size={16} />}
                      placeholder="数据集、版本或任务 ID"
                      value={taskQuery}
                      onChange={(event) => setTaskQuery(event.target.value)}
                    />
                  </div>
                  <div>
                    <label htmlFor="export-task-status">任务状态</label>
                    <Select<TaskFilter>
                      id="export-task-status"
                      options={[
                        { value: "all", label: "全部状态" },
                        { value: "active", label: "进行中" },
                        { value: "succeeded", label: "已完成" },
                        { value: "failed", label: "失败/取消" },
                      ]}
                      value={taskFilter}
                      onChange={setTaskFilter}
                    />
                  </div>
                </div>

                {downloadFeedback ? (
                  <Alert
                    closable
                    showIcon
                    className={styles.taskAlert}
                    description={downloadFeedback}
                    title="文件下载已开始"
                    type="success"
                    onClose={() => setDownloadFeedback(null)}
                  />
                ) : null}
                {[cancelTask.error, retryTask.error, downloadTask.error].find(
                  Boolean,
                ) ? (
                  <Alert
                    showIcon
                    className={styles.taskAlert}
                    description={readableError(
                      [
                        cancelTask.error,
                        retryTask.error,
                        downloadTask.error,
                      ].find(Boolean),
                    )}
                    title="任务操作失败"
                    type="error"
                  />
                ) : null}

                {taskRows.length === 0 ? (
                  <div className={styles.taskEmpty}>
                    <span>
                      <FileArchive aria-hidden="true" size={27} />
                    </span>
                    <Typography.Title level={3}>
                      还没有导出任务
                    </Typography.Title>
                    <Typography.Paragraph>
                      选择一条或多条数据以及目标格式，创建后可在这里统一查看进度。
                    </Typography.Paragraph>
                  </div>
                ) : (
                  <Table<TaskRow>
                    aria-label="导出任务列表"
                    className={styles.taskTable}
                    columns={taskColumns}
                    dataSource={visibleTaskRows}
                    locale={{ emptyText: "没有符合当前条件的任务" }}
                    pagination={{ pageSize: 20, showSizeChanger: false }}
                    size="middle"
                  />
                )}
              </Card>
            )}
          </div>
        }
      />
    </main>
  );
}

export default DataExportPage;

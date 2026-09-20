import { useEffect, useMemo, useState } from "react";
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
  fetchDatasets,
  fetchVersionEpisodes,
  type EpisodeListItemVm,
  type DatasetListItemVm,
} from "../../features/datasets/api";
import {
  authorizePublishedExportDownload,
  cancelPublishedExport,
  createPublishedExport,
  fetchPublishedExport,
  retryPublishedExport,
  type PublishedExportFormat,
  type PublishedExportJob,
} from "../../features/exports/api";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import { useShellStore } from "../../shared/scope/shell-store";
import { PageState, StandardPageScaffold, StatusTag } from "../../shared/ui";
import {
  collectionTaskGateway,
  type CollectionTask,
} from "../p20-collection-tasks/api";
import styles from "./styles.module.css";

const MAX_BATCH_TASKS = 50;
const CREATE_CONCURRENCY = 4;
const exportJobIdPattern =
  /^export:v1:[A-Za-z0-9._~%-]{1,256}:[A-Za-z0-9._~%-]{1,1024}$/u;

const formatOptions: readonly {
  value: PublishedExportFormat;
  label: string;
  description: string;
}[] = [
  {
    value: "lance_snapshot",
    label: "Lance 数据文件",
    description: "导出所选 Episode 的对齐 Step，并保留 rollout_id。",
  },
  {
    value: "lerobot_v3",
    label: "LeRobot v3",
    description: "所选 Episode 的数值数据与媒体引用，生成 LeRobot v3 目录。",
  },
] as const;

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
  source: ExportableSource;
  format: PublishedExportFormat;
  episodeIds: readonly string[];
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
  return `${stem || "data-export"}.zip`;
}

function triggerDownload(url: string, fileName: string): void {
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.rel = "noreferrer";
  anchor.style.display = "none";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
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
      return "该版本的源数据已不可用，无法生成导出压缩包。";
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

function formatLabel(format: PublishedExportFormat): string {
  return formatOptions.find((item) => item.value === format)?.label ?? format;
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
    (item.format === "lance_snapshot" || item.format === "lerobot_v3") &&
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
    versionLabel: item.currentVersion?.displayVersion ?? "暂无 READY 版本",
  };
}

function isExportable(source: ExportSource): source is ExportableSource {
  return source.versionId !== null;
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
  const [selectedSourceKeys, setSelectedSourceKeys] = useState<string[]>([]);
  const [selectedFormats, setSelectedFormats] = useState<
    PublishedExportFormat[]
  >(["lance_snapshot"]);
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

  useEffect(() => {
    setSelectedSourceKeys([]);
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
  const sources = allSources.filter(
    (source): source is ExportableSource =>
      isExportable(source) &&
      (appliedDatasetIds.length === 0 ||
        appliedDatasetIds.includes(source.datasetId)) &&
      (appliedTasks.length === 0 ||
        appliedTasks.some(
          (id) => taskById.get(id)?.dataset_id === source.datasetId,
        )),
  );
  const activeSourceFilterCount =
    appliedDatasetIds.length + appliedTasks.length;
  const episodeQueries = useQueries({
    queries: sources.map((source) => ({
      queryKey: [
        "export-episodes",
        scopeKey,
        source.datasetId,
        source.versionId,
      ],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
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
      enabled: canRead && activeSourceFilterCount > 0,
      staleTime: 30_000,
    })),
  });
  const episodes: ExportEpisode[] = sources.flatMap((source, index) =>
    (episodeQueries[index]?.data ?? [])
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
    episodeQueries.some((query) => query.isPending);
  const sourceFilterError = episodeQueries.find(
    (query) => query.isError,
  )?.error;

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
      enabled: canRead && Boolean(projectId),
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
              episodeIds: request.episodeIds,
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
            "各任务将独立生成 ZIP 压缩包，可在下方查看进度并分别下载。",
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
      triggerDownload(authorization.download_url, downloadFileName(ref));
      setDownloadFeedback(
        `${ref.datasetName} · ${formatLabel(ref.format)} 压缩包已开始下载，授权有效期至 ${formatDate(authorization.expires_at)}。`,
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
    if (!projectId) return;
    setBatchFeedback(null);
    createBatch.mutate(
      selectedSources.flatMap((source) =>
        selectedFormats.map((format) => ({
          source,
          format,
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
          <strong>Episode {episode.ordinal}</strong>
          <code>{episode.episodeId}</code>
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
        episode.task ? (taskById.get(episode.task)?.name ?? episode.task) : "—",
    },
    {
      title: "版本",
      key: "version",
      render: (_, episode) => episode.source.versionLabel,
    },
    {
      title: "处理状态",
      dataIndex: "successState",
      render: (value: string) =>
        value === "SUCCEEDED" ? "已完成" : value === "FAILED" ? "失败" : "未知",
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
      title: "任务 ID",
      key: "jobId",
      responsive: ["xl"],
      render: (_, row) => <code className={styles.jobId}>{row.ref.jobId}</code>,
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
              aria-label={`下载 ${row.ref.datasetName} ${formatLabel(row.ref.format)} 压缩包`}
              disabled={!canDownload || downloadTask.isPending}
              icon={<Download aria-hidden="true" size={15} />}
              loading={downloading}
              size="small"
              type="primary"
              onClick={() => downloadTask.mutate(row.ref)}
            >
              下载压缩包
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
                  创建导出任务
                </span>
              }
              extra={
                <span className={styles.selectionCount} aria-live="polite">
                  已选 {selectedSources.length} 个数据集 ·{" "}
                  {selectedEpisodeCount.toLocaleString("zh-CN")} 个 Episode
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
                  <section
                    aria-labelledby="export-scope-title"
                    className={styles.filterSection}
                  >
                    <div className={styles.sectionHeader}>
                      <div>
                        <span className={styles.stepBadge}>1</span>
                        <span>
                          <strong id="export-scope-title">选择数据范围</strong>
                          <small>先按任务或数据集查找，再勾选 Episode</small>
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
                            label: `${source.datasetName} · ${source.versionLabel}`,
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
                              label: task.name,
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

                  <div className={styles.scopeNote} role="note">
                    <Info aria-hidden="true" size={17} />
                    <span>
                      <strong>按 Episode 选择导出内容</strong>
                      <small>
                        列出当前 READY 版本中的 Episode，仅导出勾选的数据。
                        所选数据需要通过质量检查和标注审核；同一数据集的 Episode
                        按格式分别打包。
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
                      {appliedTasks.length > 0
                        ? "全选所选任务的 Episode"
                        : "全选所选数据集的 Episode"}
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
                          ? "所选范围暂无 Episode"
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
                          <strong id="export-format-title">选择导出格式</strong>
                          <small>格式可多选，每个格式独立生成 ZIP</small>
                        </span>
                      </div>
                    </div>

                    <div className={styles.exportComposer}>
                      <fieldset className={styles.formatFieldset}>
                        <legend>导出格式（可多选）</legend>
                        <div className={styles.formatOptions}>
                          {formatOptions.map((option) => {
                            const checked = selectedFormats.includes(
                              option.value,
                            );
                            return (
                              <label key={option.value} data-selected={checked}>
                                <Checkbox
                                  aria-label={option.label}
                                  checked={checked}
                                  onChange={(event) =>
                                    setSelectedFormats((current) =>
                                      event.target.checked
                                        ? [...current, option.value]
                                        : current.filter(
                                            (format) => format !== option.value,
                                          ),
                                    )
                                  }
                                />
                                <span>
                                  <strong>{option.label}</strong>
                                  <small>{option.description}</small>
                                </span>
                              </label>
                            );
                          })}
                        </div>
                      </fieldset>

                      <div className={styles.batchSummary} aria-live="polite">
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
                            disabled={!creationReady || createBatch.isPending}
                            icon={<PackageCheck aria-hidden="true" size={17} />}
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
            </Card>

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
                  title="压缩包下载已开始"
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
                  <Typography.Title level={3}>还没有导出任务</Typography.Title>
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
          </div>
        }
      />
    </main>
  );
}

export default DataExportPage;

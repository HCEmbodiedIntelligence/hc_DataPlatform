import { ResourceLabel } from "../../shared/lib/resource-labels";
import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Empty,
  Progress,
  Radio,
  Select,
  Skeleton,
  Space,
} from "antd";
import { PackageCheck, RefreshCw, X } from "lucide-react";
import styles from "./PackageBatchPanel.module.css";
import type { Scope } from "../../shared/scope/model";
import { formatBytes } from "../../features/ingest/upload/upload-contract";
import { BrowserDownloadHelp } from "../../features/exports/BrowserDownloadHelp";
import { CollectionAnnotationChooser } from "./CollectionAnnotationChooser";
import {
  commandPackageJob,
  createPackageBatch,
  downloadPackageJob,
  listPackageJobs,
  loadExportFormats,
  loadPackageCandidates,
  loadPackageJob,
  type AnnotationSelection,
  type BatchMode,
  type PackageData,
  type PackageJob,
} from "../../features/exports/package-deliveries";

const phases = {
  queued: "等待处理",
  verifying: "校验固定来源",
  projecting: "生成集合",
  archiving: "归档文件",
  uploading: "上传归档",
  completed: "已完成",
  failed: "导出失败",
  cancelled: "已取消",
};

function formatName(job: PackageJob) {
  const { plan } = job;
  if (plan.archive_format === "original_collection_zip/1") return "原始文件";
  if (plan.archive_format === "mcap_collection_zip/1") return "MCAP 集合";
  if (plan.archive_format === "lerobot_collection_zip/1")
    return plan.modalities === "rgb"
      ? "LeRobot RGB（不含深度）"
      : "LeRobot RGB-D（含深度）";
  return "完整标准包";
}

function depthSummary(job: PackageJob) {
  if (job.plan.format !== "hc-collection-delivery-plan/1")
    return "含已采集深度";
  const excluded = job.plan.modalities === "rgb";
  const count = job.plan.members.reduce(
    (total, member) =>
      total +
      (excluded
        ? member.excluded_depth_features.length
        : Object.keys(member.selected_depth_profiles).length),
    0,
  );
  if (!count) return "无深度数据";
  return excluded ? `已排除 ${count} 项深度数据` : `含 ${count} 项深度数据`;
}

const builtInFormats = [
  {
    value: "original",
    title: "原始文件",
    label: "原始文件及标注",
  },
  {
    value: "mcap",
    title: "MCAP",
    label: "MCAP 数据包集合",
  },
  {
    value: "independent",
    title: "完整标准包",
    label: "独立标准包批量 ZIP（含已采集深度）",
  },
  {
    value: "rgbd",
    title: "LeRobot（含深度）",
    label: "LeRobot RGB-D 集合",
  },
  {
    value: "rgb",
    title: "LeRobot（不含深度）",
    label: "LeRobot RGB 子集（排除深度）",
  },
] as const;
export function PackageBatchPanel({
  scope,
  datasets,
  annotated,
  canReadAnnotations,
  canCreate,
  canDownload,
}: {
  scope: Scope;
  datasets: { id: string; name: string }[];
  annotated: boolean;
  canReadAnnotations: boolean;
  canCreate: boolean;
  canDownload: boolean;
}) {
  const formats = useQuery({
    queryKey: ["export-formats", scope],
    queryFn: ({ signal }) => loadExportFormats(scope, signal),
    enabled: canDownload,
  });
  const deliveryFormats = [
    ...builtInFormats,
    ...(formats.data ?? [])
      .filter(
        (f) =>
          !["lerobot", "mcap", "original"].includes(f.id) &&
          (!annotated || f.supports_annotations),
      )
      .map((f) => ({
        value: f.id,
        title: f.title,
        label: f.title,
      })),
  ];
  const [datasetId, setDatasetId] = useState(datasets[0]?.id);
  const [selected, setSelected] = useState<Record<string, PackageData>>({});
  const [annotations, setAnnotations] = useState<
    Record<string, AnnotationSelection | null>
  >({});
  const [mode, setMode] = useState<BatchMode>(
    annotated ? "rgbd" : "independent",
  );
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [autoJob, setAutoJob] = useState<PackageJob | null>(null);
  const [downloadNotice, setDownloadNotice] = useState<string | null>(null);
  const [downloadLink, setDownloadLink] = useState<{
    url: string;
    expiresAt: number;
  } | null>(null);
  const automaticDownloads = useRef(new Set<string>());
  const jobsRef = useRef<HTMLDivElement>(null);
  const lifetime = useRef<AbortController | null>(null);
  const pending = useRef<{ signature: string; key: string } | null>(null);
  useEffect(() => {
    const c = new AbortController();
    lifetime.current = c;
    return () => c.abort();
  }, []);
  useEffect(() => {
    if (!downloadLink) return;
    const timer = window.setTimeout(
      () => setDownloadLink(null),
      Math.max(0, downloadLink.expiresAt - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [downloadLink]);
  const candidates = useInfiniteQuery({
    queryKey: ["package-batch-candidates", scope, datasetId],
    initialPageParam: undefined as
      | { collectionId: string; offset: number }
      | undefined,
    queryFn: ({ signal, pageParam }) =>
      loadPackageCandidates(scope, datasetId!, signal, pageParam),
    enabled: canDownload && Boolean(datasetId),
    getNextPageParam: (page) =>
      page.next_offset !== null && page.collection_version_id
        ? { collectionId: page.collection_version_id, offset: page.next_offset }
        : undefined,
  });
  const jobs = useInfiniteQuery({
    queryKey: ["package-batch-jobs", scope, canReadAnnotations],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ signal, pageParam }) =>
      listPackageJobs(scope, signal, pageParam),
    enabled: canDownload,
    getNextPageParam: (page) =>
      page.length === 25 ? page.at(-1)?.job_id : undefined,
    refetchInterval: (q) =>
      q.state.data?.pages
        .flat()
        .some((j) => j.status === "PENDING" || j.status === "RUNNING")
        ? 1500
        : false,
  });
  const activeJob = useQuery({
    queryKey: [
      "package-active-export",
      scope,
      autoJob?.job_id,
      autoJob?.generation,
    ],
    queryFn: ({ signal }) => loadPackageJob(scope, autoJob!, signal),
    enabled: canDownload && Boolean(autoJob),
    initialData: autoJob ?? undefined,
    refetchInterval: (q) =>
      ["PENDING", "RUNNING"].includes(q.state.data?.status ?? "")
        ? 1500
        : false,
  });
  const history = jobs.data?.pages.flat() ?? [];
  const visibleJobs =
    autoJob && activeJob.data
      ? [
          activeJob.data,
          ...history.filter((job) => job.job_id !== autoJob.job_id),
        ]
      : history;
  const items = candidates.data?.pages.flatMap((p) => p.items) ?? [];
  const selections = Object.values(selected).toSorted((a, b) =>
    a.data_version_id.localeCompare(b.data_version_id),
  );
  const missingAnnotations =
    annotated && selections.some((d) => !annotations[d.data_version_id]);
  const mutation = useMutation({
    mutationFn: async (action: {
      kind: "create" | "download" | "retry" | "cancel";
      job?: PackageJob;
      automatic?: boolean;
    }) => {
      const signal = lifetime.current!.signal;
      if (action.kind === "create") {
        const members = selections.map((data) => ({
          data,
          annotation: annotated ? annotations[data.data_version_id] : null,
        }));
        const signature = JSON.stringify({ mode, members });
        if (pending.current?.signature !== signature)
          pending.current = { signature, key: crypto.randomUUID() };
        return createPackageBatch(
          scope,
          members,
          mode,
          pending.current.key,
          signal,
        );
      }
      if (action.kind !== "download")
        return commandPackageJob(scope, action.job!, action.kind, signal);
      const grant = await downloadPackageJob(scope, action.job!, signal);
      signal.throwIfAborted();
      setDownloadLink({
        url: grant.url,
        expiresAt: Date.now() + grant.expires_seconds * 1000,
      });
      const a = document.createElement("a");
      a.href = grant.url;
      a.download = "";
      a.rel = "noopener noreferrer";
      document.body.append(a);
      a.click();
      a.remove();
    },
    onMutate: (action) => {
      if (action.kind !== "cancel") {
        setDownloadNotice(null);
        setDownloadLink(null);
      }
    },
    onSuccess: async (result, action) => {
      if ((action.kind === "create" || action.kind === "retry") && result) {
        pending.current = null;
        automaticDownloads.current.delete(
          `${result.job_id}:${result.generation}`,
        );
        setAutoJob(result);
        jobsRef.current?.scrollIntoView?.({ block: "start" });
      }
      if (action.kind === "download") {
        if (action.automatic || autoJob?.job_id === action.job?.job_id)
          setAutoJob(null);
        setDownloadNotice(
          `${formatName(action.job!)}已生成，已向浏览器请求下载。`,
        );
      }
      if (action.kind === "cancel" && autoJob?.job_id === action.job?.job_id)
        setAutoJob(null);
      await jobs.refetch();
    },
    onError: (_, action) => {
      if (action.automatic) setAutoJob(null);
    },
  });
  const runAction = mutation.mutate;
  useEffect(() => {
    const job = activeJob.data;
    if (!canDownload || !autoJob || !job || job.job_id !== autoJob.job_id)
      return;
    if (job.status === "FAILED" || job.status === "CANCELLED") {
      setAutoJob(null);
      void jobs.refetch();
      return;
    }
    if (job.status !== "READY") return;
    const key = `${job.job_id}:${job.generation}`;
    if (automaticDownloads.current.has(key)) return;
    automaticDownloads.current.add(key);
    runAction({ kind: "download", job, automatic: true });
  }, [canDownload, autoJob, activeJob.data, runAction, jobs.refetch]);
  function select(data: PackageData, enabled: boolean) {
    setSelectionError(null);
    if (
      enabled &&
      !selected[data.data_version_id] &&
      selections.length >= 100
    ) {
      setSelectionError("一次最多选择 100 条 Episode。");
      return;
    }
    if (
      enabled &&
      selections.some(
        (d) =>
          d.identity.dataset_id === data.identity.dataset_id &&
          d.identity.episode_id === data.identity.episode_id &&
          d.data_version_id !== data.data_version_id,
      )
    ) {
      setSelectionError("同一 Episode 只能选择一个数据版本，请先移除旧选择。");
      return;
    }
    if (!enabled)
      setAnnotations((current) => {
        const next = { ...current };
        delete next[data.data_version_id];
        return next;
      });
    setSelected((current) => {
      const next = { ...current };
      if (enabled) next[data.data_version_id] = data;
      else delete next[data.data_version_id];
      return next;
    });
  }
  if (!canDownload) return <Alert type="warning" title="缺少完整包下载权限" />;
  return (
    <div className={styles.panel}>
      <section
        className={styles.composer}
        aria-label="完整标准包与 LeRobot 集合"
      >
        <section
          className={styles.section}
          aria-labelledby="package-source-title"
        >
          <div className={styles.sectionHeading}>
            <h3 id="package-source-title">数据</h3>
            {datasets.length > 1 && (
              <Select
                className={styles.datasetSelect}
                aria-label="标准包数据集"
                size="small"
                value={datasetId}
                showSearch
                optionFilterProp="label"
                options={datasets.map((d) => ({ value: d.id, label: d.name }))}
                onChange={setDatasetId}
              />
            )}
            <span className={styles.count} aria-live="polite">
              共 {candidates.data?.pages[0]?.total ?? 0} 条 · 已选{" "}
              {selections.length} 条
            </span>
          </div>
          {candidates.error && (
            <Alert
              showIcon
              type="error"
              title="标准包候选读取失败"
              description={candidates.error.message}
              action={
                <Button onClick={() => void candidates.refetch()}>
                  重试读取
                </Button>
              }
            />
          )}
          {!datasets.length && (
            <div className={styles.empty}>请选择任务或数据集</div>
          )}
          {candidates.isFetching && !candidates.data && datasetId && (
            <div
              className={styles.loading}
              role="status"
              aria-label="读取固定集合快照"
            >
              <Skeleton active title={false} paragraph={{ rows: 2 }} />
            </div>
          )}
          {candidates.data && !candidates.error && (
            <div className={styles.candidates}>
              {(candidates.data.pages[0]?.legacy_episode_count ?? 0) > 0 && (
                <Alert
                  type="info"
                  showIcon
                  title={`此集合另含 ${candidates.data.pages[0]?.legacy_episode_count} 条历史存储数据`}
                  description="如需导出这些数据，请切换到历史格式。"
                />
              )}
              <div className={styles.candidateList}>
                {items.map((data) => (
                  <Checkbox
                    className={styles.candidate}
                    key={`candidate-${data.data_version_id}`}
                    checked={Boolean(selected[data.data_version_id])}
                    onChange={(e) => select(data, e.target.checked)}
                  >
                    <span className={styles.candidateContent}>
                      <span
                        className={styles.episodeName}
                        title={data.identity.episode_id}
                      >
                        <ResourceLabel
                          kind="episode"
                          id={data.identity.episode_id}
                          datasetId={data.identity.dataset_id}
                        />
                      </span>
                      <span className={styles.episodeMeta}>
                        数据 v{data.version} ·{" "}
                        {data.frame_count.toLocaleString("zh-CN")} 帧
                      </span>
                    </span>
                  </Checkbox>
                ))}
              </div>
              {items.length === 0 && (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="此数据集暂无独立标准包；可查看历史格式或原始数据。"
                />
              )}
              {candidates.hasNextPage && (
                <Button
                  loading={candidates.isFetchingNextPage}
                  onClick={() => void candidates.fetchNextPage()}
                >
                  加载更多数据
                </Button>
              )}
            </div>
          )}
          {selectionError && (
            <Alert showIcon type="warning" title={selectionError} />
          )}
          {selections.length > 0 && (
            <details className={styles.selectionDetails}>
              <summary>查看已选数据（{selections.length}）</summary>
              <div className={styles.selectedList} aria-label="已选 Episode">
                {selections.map((data) => (
                  <span
                    className={styles.selectedItem}
                    key={`selected-${data.data_version_id}`}
                  >
                    <span title={data.identity.episode_id}>
                      <ResourceLabel
                        kind="episode"
                        id={data.identity.episode_id}
                        datasetId={data.identity.dataset_id}
                      />{" "}
                      · 数据 v{data.version}
                    </span>
                    <Button
                      type="text"
                      size="small"
                      aria-label={`移除 ${data.identity.episode_id}`}
                      icon={<X size={13} aria-hidden="true" />}
                      onClick={() => select(data, false)}
                    />
                  </span>
                ))}
              </div>
            </details>
          )}
        </section>
        <section
          className={styles.section}
          aria-labelledby="package-format-title"
        >
          <div className={styles.formatRow}>
            <h3 id="package-format-title">格式</h3>
            <Radio.Group
              className={styles.formatOptions}
              aria-label="标准包交付格式"
              name="package-delivery-format"
              value={mode}
              onChange={(e) => setMode(e.target.value)}
            >
              {deliveryFormats.map((format) => {
                const disabled = annotated && format.value === "independent";
                return (
                  <Radio.Button
                    key={format.value}
                    value={format.value}
                    aria-label={format.label}
                    disabled={disabled}
                    title={disabled ? "带标注数据请选择 LeRobot" : undefined}
                  >
                    {format.title}
                  </Radio.Button>
                );
              })}
            </Radio.Group>
            <Button
              className={styles.createButton}
              type="primary"
              icon={<PackageCheck size={16} aria-hidden="true" />}
              title="导出完成后自动下载"
              loading={mutation.isPending || Boolean(autoJob)}
              disabled={
                !canCreate ||
                Boolean(autoJob) ||
                !selections.length ||
                missingAnnotations ||
                (annotated && !canReadAnnotations)
              }
              onClick={() => mutation.mutate({ kind: "create" })}
            >
              导出
            </Button>
          </div>
          {annotated && !canReadAnnotations && (
            <Alert type="warning" title="带标注集合需要标注读取权限" />
          )}
          {annotated && canReadAnnotations && selections.length > 0 && (
            <p className={styles.annotationHint}>
              附带当前已保存标注（含未审核内容）
            </p>
          )}
          {annotated &&
            canReadAnnotations &&
            selections.map((data) => (
              <div
                className={styles.annotationRow}
                key={`annotation-${data.data_version_id}`}
              >
                <CollectionAnnotationChooser
                  scope={scope}
                  data={data}
                  onChange={(value) =>
                    setAnnotations((old) => ({
                      ...old,
                      [data.data_version_id]: value,
                    }))
                  }
                />
              </div>
            ))}
          {missingAnnotations && (
            <span className={styles.annotationHint}>
              标注尚未就绪，请查看上方读取状态或分段提示。
            </span>
          )}
        </section>
        {mutation.error && (
          <Alert
            showIcon
            className={styles.operationError}
            type="error"
            title={
              mutation.variables?.kind === "download"
                ? "文件未能开始下载，请在导出记录中重新下载"
                : "导出操作失败"
            }
            description={mutation.error.message}
          />
        )}
        {downloadNotice && (
          <Alert
            showIcon
            className={styles.operationError}
            type="info"
            title={downloadNotice}
            description={
              downloadLink ? (
                <Space orientation="vertical" size={4}>
                  <Space wrap>
                    <span>若下载列表中没有记录，请点击下载：</span>
                    <Button
                      size="small"
                      type="primary"
                      href={downloadLink.url}
                      download=""
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      点击下载 ZIP
                    </Button>
                  </Space>
                  {downloadLink.url.startsWith("http:") && (
                    <BrowserDownloadHelp />
                  )}
                </Space>
              ) : (
                "下载链接已过期，请在导出记录中重新下载。"
              )
            }
          />
        )}
        {autoJob && activeJob.error && (
          <Alert
            showIcon
            className={styles.operationError}
            type="warning"
            title="暂时无法读取导出进度，正在重试"
            description={activeJob.error.message}
          />
        )}
      </section>
      <Card
        ref={jobsRef}
        className={styles.jobsCard}
        title="导出记录"
        size="small"
        extra={
          <Button
            size="small"
            icon={<RefreshCw size={14} aria-hidden="true" />}
            onClick={() => void jobs.refetch()}
            loading={jobs.isFetching}
          >
            刷新状态
          </Button>
        }
      >
        <div className={styles.jobsList}>
          {jobs.error && (
            <Alert
              type="error"
              title="导出任务读取失败"
              description={jobs.error.message}
            />
          )}
          {jobs.isPending && (
            <Skeleton active title={false} paragraph={{ rows: 2 }} />
          )}
          {!jobs.error && jobs.data && !visibleJobs.length && (
            <div className={styles.jobsEmpty}>暂无导出记录</div>
          )}
          {!jobs.error &&
            visibleJobs.map((job) => (
              <div className={styles.jobRow} key={job.job_id}>
                <div className={styles.jobInfo}>
                  <strong>
                    {formatName(job)} · {job.plan.members.length} 条数据
                  </strong>
                  <span className={styles.jobMeta}>
                    <span>{depthSummary(job)}</span>
                    {job.size_bytes !== null && (
                      <span> · {formatBytes(job.size_bytes)}</span>
                    )}
                  </span>
                </div>
                <div
                  className={styles.jobStatus}
                  role="status"
                  data-status={job.status}
                >
                  <span>
                    {phases[job.phase]}
                    {autoJob?.job_id === job.job_id && " · 完成后自动下载"}
                  </span>
                  {["PENDING", "RUNNING"].includes(job.status) &&
                    Boolean(job.total_bytes) && (
                      <Progress
                        size="small"
                        percent={Math.min(
                          100,
                          Math.floor(
                            (job.processed_bytes / job.total_bytes!) * 100,
                          ),
                        )}
                      />
                    )}
                </div>
                <Space className={styles.jobActions} size={0}>
                  {job.status === "READY" && autoJob?.job_id !== job.job_id && (
                    <Button
                      type="link"
                      size="small"
                      disabled={mutation.isPending}
                      onClick={() => mutation.mutate({ kind: "download", job })}
                    >
                      重新下载
                    </Button>
                  )}
                  {["PENDING", "RUNNING"].includes(job.status) && (
                    <Button
                      type="link"
                      size="small"
                      disabled={!canCreate || mutation.isPending}
                      onClick={() => mutation.mutate({ kind: "cancel", job })}
                    >
                      取消导出
                    </Button>
                  )}
                  {["FAILED", "CANCELLED"].includes(job.status) && (
                    <Button
                      type="link"
                      size="small"
                      disabled={!canCreate || mutation.isPending}
                      onClick={() => mutation.mutate({ kind: "retry", job })}
                    >
                      重试导出
                    </Button>
                  )}
                </Space>
                {job.error_code && (
                  <Alert
                    className={styles.jobError}
                    type="error"
                    title={`导出失败：${job.error_code}`}
                  />
                )}
              </div>
            ))}
          {jobs.hasNextPage && (
            <Button
              loading={jobs.isFetchingNextPage}
              onClick={() => void jobs.fetchNextPage()}
            >
              加载更多导出任务
            </Button>
          )}
        </div>
      </Card>
    </div>
  );
}

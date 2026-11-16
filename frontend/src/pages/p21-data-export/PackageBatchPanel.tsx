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
  downloadEpisodeBundle,
  listPackageJobs,
  loadExportFormats,
  loadPackageCandidates,
  loadUploadCandidates,
  type PackageExportFilter,
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
  if (plan.archive_format.startsWith("mcap_collection_zip/")) return "MCAP 集合";
  if (["holobrain_collection_zip/5", "holobrain_collection_zip/6"].includes(plan.archive_format))
    return "HoloBrain（16维，双臂和夹爪）";
  if (plan.archive_format.startsWith("holobrain_collection_zip/"))
    return "HoloBrain（19维，含腰部）";
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
  filter,
  datasetIds = [],
  taskIds = [],
  canReadAnnotations,
  canCreate,
  canDownload,
}: {
  scope: Scope;
  datasets: { id: string; name: string }[];
  annotated: boolean;
  filter?: PackageExportFilter;
  datasetIds?: readonly string[];
  taskIds?: readonly string[];
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
  const qcBundle = filter?.stage === "qc_passed";
  const useEpisodeBundle = qcBundle && mode !== "holobrain";
  const selectionLimit = mode === "holobrain" ? undefined : qcBundle ? 1000 : 100;
  const [selectingAll, setSelectingAll] = useState(false);
  const [bundleProgress, setBundleProgress] = useState("");
  const [bundleUrl, setBundleUrl] = useState<string>();
  useEffect(() => () => { if (bundleUrl) URL.revokeObjectURL(bundleUrl); }, [bundleUrl]);
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
  const candidateDatasetIds = annotated
    ? datasets.map((dataset) => dataset.id)
    : datasetId ? [datasetId] : [];
  const datasetNames = new Map(datasets.map((dataset) => [dataset.id, dataset.name]));
  const showDatasetName = !filter && annotated && datasets.length > 1;
  const candidates = useInfiniteQuery({
    queryKey: ["package-batch-candidates", scope, candidateDatasetIds, annotated],
    initialPageParam: candidateDatasetIds.map((id) => ({
      id,
      cursor: undefined as { collectionId: string; offset: number } | undefined,
    })),
    queryFn: async ({ signal, pageParam }) => {
      const pages = await Promise.all(pageParam.map(({ id, cursor }) =>
        loadPackageCandidates(scope, id, signal, cursor, annotated ? "annotated" : "dataset"),
      ));
      return {
        items: pages.flatMap((page) => page.items),
        total: pages.reduce((sum, page) => sum + page.total, 0),
        legacy_episode_count: pages.reduce((sum, page) => sum + page.legacy_episode_count, 0),
        next: pages.flatMap((page) =>
          page.next_offset !== null && page.collection_version_id
            ? [{ id: page.dataset_id, cursor: { collectionId: page.collection_version_id, offset: page.next_offset } }]
            : [],
        ),
      };
    },
    enabled:
      !filter && canDownload && candidateDatasetIds.length > 0 && (!annotated || canReadAnnotations),
    staleTime: 0,
    getNextPageParam: (page) => page.next.length ? page.next : undefined,
  });
  const uploadCandidates = useInfiniteQuery({
    queryKey: ["export-upload-candidates", scope, filter, datasetIds, taskIds],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ signal, pageParam }) => loadUploadCandidates(scope, filter!, datasetIds, taskIds, signal, pageParam),
    enabled: Boolean(filter) && canDownload && (!annotated || canReadAnnotations),
    getNextPageParam: (page) => page.next_after ?? undefined,
  });
  const list = filter ? uploadCandidates : candidates;
  const uploaded = uploadCandidates.data?.pages.flatMap(p => p.items) ?? [];
  const batchLabels = new Map(uploaded.map(row => [row.data.data_version_id,
    row.batch_numbers.length ? row.batch_numbers.map(n => `第 ${n} 次上传`).join("、") : "历史数据 · 无批次编号"]));
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
  const items = filter ? uploaded.map(row => row.data) : candidates.data?.pages.flatMap((p) => p.items) ?? [];
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
        if (useEpisodeBundle && filter) {
          setBundleUrl(undefined);
          setBundleProgress("");
          const result = await downloadEpisodeBundle(scope, selections, mode, filter, signal, setBundleProgress);
          if (result.kind === "cancelled") {
            setBundleProgress("尚未选择保存位置。已保留所选数据，点击“导出”可重新选择。");
            return;
          }
          if (result.kind === "browser") setBundleUrl(result.url);
          setBundleProgress(`已完成 ${selections.length} 条 Episode 的打包下载`);
          return;
        }
        const members = selections.map((data) => ({
          data,
          annotation: annotated ? annotations[data.data_version_id] : null,
        }));
        const signature = JSON.stringify({ mode, members, filter });
        if (pending.current?.signature !== signature)
          pending.current = { signature, key: crypto.randomUUID() };
        return createPackageBatch(
          scope,
          members,
          mode,
          pending.current.key,
          signal,
          filter,
        );
      }
      if (action.kind !== "download")
        return commandPackageJob(scope, action.job!, action.kind, signal);
      const grant = await downloadPackageJob(scope, action.job!, signal, qcBundle ? filter : undefined);
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
  async function selectFiltered() {
    if (!filter) return;
    setSelectionError(null);
    setSelectingAll(true);
    try {
      const next: Record<string, PackageData> = {};
      let page = uploadCandidates.data?.pages[0];
      const cursors = new Set<string>();
      while (page) {
        if (selectionLimit !== undefined && page.total > selectionLimit) throw new Error(`本次筛选有 ${page.total} 条，每个压缩包最多 ${selectionLimit} 条，请缩小筛选范围。`);
        for (const { data } of page.items) next[data.data_version_id] = data;
        if (!page.next_after) break;
        if (cursors.has(page.next_after)) throw new Error("候选数据已变化，请刷新后重试。");
        cursors.add(page.next_after);
        page = await loadUploadCandidates(scope, filter, datasetIds, taskIds, lifetime.current!.signal, page.next_after);
      }
      lifetime.current!.signal.throwIfAborted();
      setSelected(next);
    } catch (error) {
      setSelectionError(error instanceof Error ? error.message : "全选失败，请重试。");
    } finally {
      setSelectingAll(false);
    }
  }
  function select(data: PackageData, enabled: boolean) {
    setSelectionError(null);
    if (
      enabled &&
      !selected[data.data_version_id] &&
      selectionLimit !== undefined && selections.length >= selectionLimit
    ) {
      setSelectionError(`每个压缩包最多选择 ${selectionLimit} 条 Episode。`);
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
            {!filter && !annotated && datasets.length > 1 && (
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
              {annotated ? "标注完成" : "共"}{" "}
              {list.data?.pages[0]?.total ?? 0} 条 · 已选{" "}
              {selections.length} 条
            </span>
          </div>
          {list.error && (
            <Alert
              showIcon
              type="error"
              title="标准包候选读取失败"
              description={list.error.message}
              action={
                <Button onClick={() => void list.refetch()}>
                  重试读取
                </Button>
              }
            />
          )}
          {!filter && !datasets.length && (
            <div className={styles.empty}>请选择任务或数据集</div>
          )}
          {list.isFetching && !list.data && (filter || candidateDatasetIds.length > 0) && (
            <div
              className={styles.loading}
              role="status"
              aria-label="读取固定集合快照"
            >
              <Skeleton active title={false} paragraph={{ rows: 2 }} />
            </div>
          )}
          {list.data && !list.error && (
            <div className={styles.candidates}>
              {!filter && candidates.data && (candidates.data.pages[0]?.legacy_episode_count ?? 0) > 0 && (
                <Alert
                  type="info"
                  showIcon
                  title={`此集合另含 ${candidates.data.pages[0]?.legacy_episode_count} 条历史存储数据`}
                  description="标准包不包含这些历史存储数据。"
                />
              )}
              {filter && items.length > 0 && (
                <Checkbox checked={selections.length === uploadCandidates.data?.pages[0]?.total}
                  indeterminate={selections.length > 0 && selections.length !== uploadCandidates.data?.pages[0]?.total}
                  disabled={selectingAll || mutation.isPending}
                  onChange={event => event.target.checked ? void selectFiltered() : setSelected({})}>
                  {selectingAll ? "正在读取全部 Episode…" : "全选筛选结果"}
                </Checkbox>
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
                        {showDatasetName && <>{datasetNames.get(data.identity.dataset_id)} · </>}
                        {filter && <>{batchLabels.get(data.data_version_id)} · </>}数据 v{data.version} ·{" "}
                        {data.frame_count.toLocaleString("zh-CN")} 帧
                      </span>
                    </span>
                  </Checkbox>
                ))}
              </div>
              {items.length === 0 && (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description={
                    filter ? "所选范围暂无符合条件的数据。" : annotated
                      ? "所选范围暂无标注完成的数据，请先完成标注审核。"
                      : "此数据集暂无独立标准包，可查看原始数据。"
                  }
                />
              )}
              {list.hasNextPage && (
                <Button
                  loading={list.isFetchingNextPage}
                  onClick={() => void list.fetchNextPage()}
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
                      {showDatasetName && <>· {datasetNames.get(data.identity.dataset_id)} </>}
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
              onChange={(e) => {
                setMode(e.target.value);
                setSelectionError(null);
              }}
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
                !canCreate || selectingAll ||
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
              仅导出当前已通过审核的标注
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
        {useEpisodeBundle && bundleProgress && !mutation.error && <Alert type="info" title={bundleProgress}
          action={bundleUrl && <a href={bundleUrl} download="Episodes.zip">下载 ZIP</a>} />}
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

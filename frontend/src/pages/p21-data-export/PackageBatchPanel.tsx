import { MAX_EXPORT_ITEMS } from "../../shared/lib/ordered-transfer";
import { ResourceLabel } from "../../shared/lib/resource-labels";
import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
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
import { episodeItem, useExportHistory, type ExportRecord } from "../../features/exports/export-history";
import { ExportContents, ExportReceipt } from "./ExportHistoryDetails";
import { ExportControls, useExportDestination } from "../../features/exports/ExportControls";
import type { ExportDestination } from "../../features/exports/oss-export";
import { RefreshCw, X } from "lucide-react";
import styles from "./PackageBatchPanel.module.css";
import type { Scope } from "../../shared/scope/model";
import { formatBytes } from "../../features/ingest/upload/upload-contract";
import { startPackageExport } from "../../features/exports/package-export-task";
import { ExportTaskList } from "../../features/exports/ExportTaskList";
import { startEpisodeBundleExport } from "../../features/exports/episode-bundle-export";
import { CollectionAnnotationChooser } from "./CollectionAnnotationChooser";
import {
  commandPackageJob,
  listPackageJobs,
  loadExportFormats,
  loadPackageCandidates,
  loadUploadCandidates,
  type PackageExportFilter,
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
  if (["holobrain_collection_zip/5", "holobrain_collection_zip/6", "holobrain_collection_zip/7", "holobrain_collection_zip/8"].includes(plan.archive_format))
    return "HoloBrain（16维，双臂和夹爪）";
  if (plan.archive_format.startsWith("holobrain_collection_zip/"))
    return "HoloBrain（19维，含腰部）";
  if (plan.archive_format === "lerobot_collection_zip/1")
    return plan.modalities === "rgb"
      ? "LeRobot RGB（不含深度）"
      : "LeRobot RGB-D（含深度）";
  return "Episode 归档";
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
  datasetNumbers,
  datasetIds = [],
  taskIds = [],
  canReadAnnotations,
  canReadRaw = false,
  canCreate,
  canDownload,
  canDownloadData = () => canDownload,
  historyOnly = false,
}: {
  scope: Scope | null;
  datasets: { id: string; name: string }[];
  annotated: boolean;
  filter?: PackageExportFilter;
  datasetNumbers?: ReadonlyMap<string, string>;
  datasetIds?: readonly string[];
  taskIds?: readonly string[];
  canReadAnnotations: boolean;
  canReadRaw?: boolean;
  canCreate: boolean;
  canDownload: boolean;
  canDownloadData?: (data: PackageData) => boolean;
  historyOnly?: boolean;
}) {
  const formats = useQuery({
    queryKey: ["export-formats", scope],
    queryFn: ({ signal }) => loadExportFormats(scope!, signal),
    enabled: Boolean(scope) && canDownload,
  });
  const deliveryFormats = [
    ...builtInFormats,
    ...(formats.data ?? [])
      .filter(
        (f) =>
          !["lerobot", "mcap", "original", "independent"].includes(f.id) &&
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
  const [mode, setMode] = useState<BatchMode>("rgbd");
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const qcBundle = filter?.stage === "qc_passed";
  const useEpisodeBundle = qcBundle;
  const selectionLimit = mode === "holobrain" ? undefined : mode === "independent" && !qcBundle ? 100 : MAX_EXPORT_ITEMS;
  const selectedPageBatches = useRef(new Map<string, number[]>());
  const [selectingAll, setSelectingAll] = useState(false);
  const queryClient = useQueryClient();
  const exportDestination = useExportDestination();
  const receipts = useExportHistory(scope);
  const lifetime = useRef<AbortController | null>(null);
  const pending = useRef<{ signature: string; key: string } | null>(null);
  useEffect(() => {
    const c = new AbortController();
    lifetime.current = c;
    return () => c.abort();
  }, []);
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
        loadPackageCandidates(scope!, id, signal, cursor, annotated ? "annotated" : "dataset"),
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
      Boolean(scope) && !historyOnly && !filter && candidateDatasetIds.length > 0 && (!annotated || canReadAnnotations),
    staleTime: 0,
    getNextPageParam: (page) => page.next.length ? page.next : undefined,
  });
  const uploadCandidates = useInfiniteQuery({
    queryKey: ["export-upload-candidates", scope, filter, datasetIds, taskIds],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ signal, pageParam }) => loadUploadCandidates(scope!, filter!, datasetIds, taskIds, signal, pageParam),
    enabled: Boolean(scope && filter) && !historyOnly && (!annotated || canReadAnnotations),
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
      listPackageJobs(scope!, signal, pageParam),
    enabled: Boolean(scope),
    getNextPageParam: (page) =>
      page.length === 25 ? page.at(-1)?.job_id : undefined,
    refetchInterval: (q) =>
      q.state.data?.pages
        .flat()
        .some((j) => j.status === "PENDING" || j.status === "RUNNING")
        ? 1500
        : false,
  });
  const visibleJobs = jobs.data?.pages.flat() ?? [];
  const jobReceipts = new Map<string, ExportRecord[]>();
  const historyRows: Array<{ time: number; job: PackageJob } | { time: number; receipt: ExportRecord }> = [];
  const loadedJobs = new Set(jobs.error ? [] : visibleJobs.map(job => job.job_id));
  for (const receipt of receipts) {
    if ((receipt.source === "bundle" && !(receipt.jobId && loadedJobs.has(receipt.jobId))) || (receipt.source === "raw" && canReadRaw))
      historyRows.push({ time: receipt.created, receipt });
    if (receipt.jobId) {
      const previous = jobReceipts.get(receipt.jobId);
      if (previous) previous.push(receipt);
      else jobReceipts.set(receipt.jobId, [receipt]);
    }
  }
  if (!jobs.error) {
    for (const job of visibleJobs) historyRows.push({
      time: Math.max(Date.parse(job.created_at), jobReceipts.get(job.job_id)?.[0]?.created ?? 0), job,
    });
  }
  historyRows.sort((a, b) => b.time - a.time);
  const items = filter ? uploaded.map(row => row.data) : candidates.data?.pages.flatMap((p) => p.items) ?? [];
  const selections = Object.values(selected).toSorted((a, b) =>
    a.data_version_id.localeCompare(b.data_version_id),
  );
  const missingAnnotations =
    annotated && selections.some((d) => !annotations[d.data_version_id]);
  const mutation = useMutation({
    mutationFn: (action: { kind: "cancel"; job: PackageJob }) =>
      commandPackageJob(scope!, action.job, action.kind, lifetime.current!.signal),
    onSuccess: () => jobs.refetch(),
  });

  function startJob(action: "create" | "retry" | "download", destination: ExportDestination, job?: PackageJob) {
    if (!scope) return;
    const members = selections.map(data => ({ data, annotation: annotated ? annotations[data.data_version_id] : null }));
    const signature = JSON.stringify({ mode, members, filter });
    if (pending.current?.signature !== signature)
      pending.current = { signature, key: crypto.randomUUID() };
    const key = pending.current.key;
    const title = job ? formatName(job) : deliveryFormats.find(format => format.value === mode)?.title ?? mode;
    void startPackageExport({
      scope, mode, selections: members, filter, datasetNumbers, destination, action, job, key,
      label: `${title} · ${job?.plan.members.length ?? members.length} 条`,
      onJob: updated => {
        if (pending.current?.key === key) pending.current = null;
        queryClient.setQueryData<InfiniteData<PackageJob[]>>(["package-batch-jobs", scope, canReadAnnotations], cache => {
          if (!cache) return { pages: [[updated]], pageParams: [undefined] };
          const exists = cache.pages.some(page => page.some(item => item.job_id === updated.job_id));
          return { ...cache, pages: cache.pages.map((page, i) => !i && !exists ? [updated, ...page]
            : page.map(item => item.job_id === updated.job_id ? updated : item)) };
        });
      },
    }).then(() => queryClient.invalidateQueries({ queryKey: ["package-batch-jobs", scope] }));
  }
  async function selectFiltered() {
    if (!scope || !filter) return;
    setSelectionError(null);
    setSelectingAll(true);
    try {
      const next: Record<string, PackageData> = {};
      const allBatches = new Map<string, number[]>();
      let page = uploadCandidates.data?.pages[0];
      const cursors = new Set<string>();
      while (page) {
        for (const { data, batch_numbers } of page.items) {
          if (!canDownloadData(data)) continue;
          next[data.data_version_id] = data;
          allBatches.set(data.data_version_id, batch_numbers);
          if (selectionLimit !== undefined && allBatches.size > selectionLimit) {
            throw new Error(`可下载的筛选结果超过每包 ${selectionLimit} 条的上限，请缩小筛选范围。`);
          }
        }
        if (!page.next_after) break;
        if (cursors.has(page.next_after)) throw new Error("候选数据已变化，请刷新后重试。");
        cursors.add(page.next_after);
        page = await loadUploadCandidates(scope, filter, datasetIds, taskIds, lifetime.current!.signal, page.next_after);
      }
      lifetime.current!.signal.throwIfAborted();
      selectedPageBatches.current = allBatches;
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
  return (
    <div className={styles.panel}>
      {!historyOnly && <section
        className={styles.composer}
        aria-label="导出数据和格式"
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
            {list.isSuccess && (
              <span className={styles.count} aria-live="polite">
                {annotated ? "标注完成" : "共"} {list.data.pages[0]?.total ?? 0} 条 · 已选 {selections.length} 条
              </span>
            )}
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
          {!scope && <div className={styles.empty}>暂无可导出的数据</div>}
          {scope && !filter && !datasets.length && (
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
                  disabled={!canDownload || selectingAll || mutation.isPending}
                  onChange={event => event.target.checked ? void selectFiltered() : setSelected({})}>
                  {selectingAll ? "正在读取全部 Episode…" : "全选筛选结果"}
                </Checkbox>
              )}
              <div className={styles.candidateList}>
                {items.map((data) => (
                  <Checkbox
                    className={styles.candidate}
                    key={`candidate-${data.data_version_id}`}
                    disabled={!canDownloadData(data)}
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
              {deliveryFormats.map((format) => (
                <Radio.Button
                  key={format.value}
                  value={format.value}
                  aria-label={format.label}
                >
                  {format.title}
                </Radio.Button>
              ))}
            </Radio.Group>
            <ExportControls
              selection={exportDestination}
              canPublish={canCreate}
              busy={false}
              disabled={
                !scope || !canCreate || selectingAll ||
                !selections.length || !selections.every(canDownloadData) ||
                missingAnnotations ||
                (annotated && !canReadAnnotations)
              }
              onExport={destination => {
                if (!scope) return;
                if (useEpisodeBundle && filter) {
                  mutation.reset();
                  void startEpisodeBundleExport(scope, selections, mode, filter, destination, datasetNumbers,
                    {
                      format: deliveryFormats.find(format => format.value === mode)?.title ?? mode,
                      batchesByVersion: new Map([...selectedPageBatches.current,
                        ...uploaded.map(row => [row.data.data_version_id, row.batch_numbers] as const)]),
                    }).then(() => { if (mode === "holobrain") void jobs.refetch(); });
                } else {
                  void startJob("create", destination);
                }
              }}
            />
          </div>
          {scope && annotated && !canReadAnnotations && (
            <Alert type="warning" title="带标注集合需要标注读取权限" />
          )}
          {annotated && canReadAnnotations && selections.length > 0 && (
            <p className={styles.annotationHint}>
              仅导出当前已通过审核的标注
            </p>
          )}
          {scope && annotated &&
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
        {mutation.error && <Alert type="error" title="取消导出失败" description={mutation.error.message} />}
      </section>}
      <ExportTaskList scope={scope} />
      <Card
        className={styles.jobsCard}
        title="导出记录"
        size="small"
        extra={
          <Button
            size="small"
            icon={<RefreshCw size={14} aria-hidden="true" />}
            onClick={() => void jobs.refetch()}
            disabled={!scope}
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
          {jobs.isLoading && (
            <Skeleton active title={false} paragraph={{ rows: 2 }} />
          )}
          {!jobs.error && (!scope || jobs.data) && !historyRows.length && (
            <div className={styles.jobsEmpty}>暂无导出记录</div>
          )}
          {scope && historyRows.map(row => {
            if ("receipt" in row) return (
              <div className={styles.jobRow} key={row.receipt.id}>
                <div className={styles.jobInfo}><ExportReceipt record={row.receipt} scope={scope} /></div>
              </div>
            );
            const { job } = row;
            return (
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
                  <time dateTime={job.created_at}>创建于 {new Date(job.created_at).toLocaleString("zh-CN")}</time>
                  <ExportContents scope={scope} items={job.plan.members.map(member => ({
                    ...episodeItem(member.data),
                    taskId: member.collection_record?.collection_task_id,
                    batches: [...new Set(member.collection_record?.uploads.flatMap(upload => upload.batch_numbers))],
                    annotationRevision: "annotation" in member ? member.annotation?.binding.revision : undefined,
                  }))} />
                  {(jobReceipts.get(job.job_id) ?? []).map(record => (
                    <ExportReceipt key={record.id} record={record} scope={scope} showContents={false} />
                  ))}
                </div>
                <div
                  className={styles.jobStatus}
                  role="status"
                  data-status={job.status}
                >
                  <span>
                    {phases[job.phase]}
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
                  {job.status === "READY" && job.plan.members.every(member => canDownloadData(member.data)) && (
                    <Button
                      type="link"
                      size="small"
                      onClick={() => void startJob("download", "zip", job)}
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
                      onClick={() => void startJob("retry", exportDestination[0], job)}
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
            );
          })}
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

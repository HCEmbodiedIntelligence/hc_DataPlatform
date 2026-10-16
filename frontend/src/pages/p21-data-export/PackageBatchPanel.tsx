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
import {
  Archive,
  Check,
  FileArchive,
  Image,
  Layers3,
  PackageCheck,
  RefreshCw,
  X,
} from "lucide-react";
import styles from "./PackageBatchPanel.module.css";
import type { Scope } from "../../shared/scope/model";
import { CollectionAnnotationChooser } from "./CollectionAnnotationChooser";
import {
  commandPackageJob,
  createPackageBatch,
  downloadPackageJob,
  listPackageJobs,
  loadExportFormats,
  loadPackageCandidates,
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
  completed: "交付就绪",
  failed: "交付失败",
  cancelled: "已取消",
};

const builtInFormats = [
  {
    value: "original",
    title: "原始文件",
    label: "原始文件及标注",
    description: "保留上传文件；带标注导出附带当前标注、切片范围及源帧映射。",
    detail: "原始字节不变 · 标注附带交付",
    badge: "保留来源",
    icon: Archive,
  },
  {
    value: "mcap",
    title: "MCAP",
    label: "MCAP 数据包集合",
    description:
      "仅支持保留原始 MCAP 的数据，按切片保留消息结构；LeRobot 不支持转 MCAP。",
    detail: "保留原 topic、schema 和消息内容",
    badge: "需要原始 MCAP",
    icon: FileArchive,
  },
  {
    value: "independent",
    title: "完整标准包",
    label: "独立标准包批量 ZIP（含已采集深度）",
    description: "每条 Episode 独立保存，适合完整归档与交付。",
    detail: "独立 Episode · 批量 ZIP",
    badge: "保留深度",
    icon: Archive,
  },
  {
    value: "rgbd",
    title: "LeRobot RGB-D",
    label: "LeRobot RGB-D 集合",
    description: "合并为一个 LeRobot 集合，保留 RGB 与已采集深度。",
    detail: "统一索引 · 跨 Episode 读取",
    badge: "保留深度",
    icon: Layers3,
  },
  {
    value: "rgb",
    title: "LeRobot RGB",
    label: "LeRobot RGB 子集（排除深度）",
    description: "导出 RGB 与数值数据，适用于不需要深度的任务。",
    detail: "RGB 子集 · 不含深度数据",
    badge: "不含深度",
    icon: Image,
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
        description: "使用平台已注册的导出格式。",
        detail: f.requires_originals
          ? "保留原始来源"
          : "包含所选数据与当前标注",
        badge: "扩展格式",
        icon: FileArchive,
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
  const lifetime = useRef<AbortController | null>(null);
  const pending = useRef<{ signature: string; key: string } | null>(null);
  useEffect(() => {
    const c = new AbortController();
    lifetime.current = c;
    return () => c.abort();
  }, []);
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
      const a = document.createElement("a");
      a.href = grant.url;
      a.download = "";
      a.rel = "noopener noreferrer";
      document.body.append(a);
      a.click();
      a.remove();
    },
    onSuccess: async () => {
      await jobs.refetch();
    },
  });
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
            <span className={styles.step}>2</span>
            <div>
              <h3 id="package-source-title">选择 Episode</h3>
              <p>按数据版本选择导出内容，可从多个数据集添加，最多 100 条。</p>
            </div>
            <span className={styles.count} aria-live="polite">
              已选 {selections.length} 条
            </span>
          </div>
          {datasets.length > 0 && (
            <div className={styles.datasetField}>
              <label htmlFor="package-dataset">当前数据集</label>
              <Select
                id="package-dataset"
                aria-label="标准包数据集"
                value={datasetId}
                showSearch
                optionFilterProp="label"
                options={datasets.map((d) => ({ value: d.id, label: d.name }))}
                onChange={setDatasetId}
              />
            </div>
          )}
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
            <div className={styles.empty}>
              <span className={styles.emptyIcon}>
                <FileArchive size={23} aria-hidden="true" />
              </span>
              <strong>请先选择数据集或采集任务</strong>
              <p>选择上方的数据范围后，即可在这里勾选要导出的 Episode。</p>
            </div>
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
              <div className={styles.listHeading}>
                <span>
                  可选 Episode{" "}
                  <strong>{candidates.data.pages[0]?.total ?? 0}</strong>
                </span>
                <span>
                  集合版本 {candidates.data.pages[0]?.version ?? "尚未建立"}
                </span>
              </div>
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
                  加载更多 Episode
                </Button>
              )}
            </div>
          )}
          {selectionError && (
            <Alert showIcon type="warning" title={selectionError} />
          )}
          {selections.length > 0 && (
            <div className={styles.selectedList} aria-label="已选 Episode">
              <span>已选 {selections.length} 条 Episode</span>
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
          )}
        </section>
        <section
          className={styles.section}
          aria-labelledby="package-format-title"
        >
          <div className={styles.sectionHeading}>
            <span className={styles.step}>3</span>
            <div>
              <h3 id="package-format-title">选择交付格式</h3>
              <p>根据归档或训练用途选择，导出范围以已选 Episode 为准。</p>
            </div>
          </div>
          <Radio.Group
            className={styles.formatGrid}
            aria-label="标准包交付格式"
            name="package-delivery-format"
            value={mode}
            onChange={(e) => setMode(e.target.value)}
          >
            {deliveryFormats.map((format) => {
              const Icon = format.icon;
              const disabled = annotated && format.value === "independent";
              return (
                <Radio
                  key={format.value}
                  value={format.value}
                  aria-label={format.label}
                  disabled={disabled}
                  className={styles.formatCard}
                >
                  <span className={styles.formatTop}>
                    <span className={styles.formatIcon}>
                      <Icon size={21} aria-hidden="true" />
                    </span>
                    <span
                      className={styles.formatBadge}
                      data-depth={format.value !== "rgb"}
                    >
                      {format.badge}
                    </span>
                    <span
                      className={styles.selectionIndicator}
                      aria-hidden="true"
                    >
                      {mode === format.value && (
                        <Check size={12} strokeWidth={3} />
                      )}
                    </span>
                  </span>
                  <strong className={styles.formatTitle}>{format.title}</strong>
                  <span className={styles.formatDescription}>
                    {format.description}
                  </span>
                  <span className={styles.formatDetail}>
                    {disabled ? "带标注数据请选择 LeRobot 集合" : format.detail}
                  </span>
                </Radio>
              );
            })}
          </Radio.Group>
          {mode === "rgb" ? (
            <Alert
              className={styles.formatNotice}
              showIcon
              type="warning"
              title="RGB 子集将排除深度视频、深度像素掩码及相关数据特征"
              description="数值、RGB 和来源说明保留。需要深度时选择完整标准包或 RGB-D 集合。"
            />
          ) : (
            <Alert
              className={styles.formatNotice}
              showIcon
              type="info"
              title="保留已采集深度视频、profile、有效掩码和源帧映射"
              description={
                mode === "original"
                  ? "原文件不改写；切片范围和当前标注保存为附带文件。"
                  : mode === "mcap"
                    ? "有原始 MCAP 时保留其 topic、schema 和消息字段；其他来源采用平台 MCAP 格式，内含视频文件与帧引用。"
                    : mode === "independent"
                      ? "ZIP 内每条是可独立读取的标准包。"
                      : "输出为一个可跨 Episode 读取的 LeRobot 集合；不兼容的频率、单位或相机契约会明确拒绝合并。"
              }
            />
          )}
          {annotated && !canReadAnnotations && (
            <Alert type="warning" title="带标注集合需要标注读取权限" />
          )}
          {annotated &&
            canReadAnnotations &&
            selections.map((data) => (
              <Card size="small" key={`annotation-${data.data_version_id}`}>
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
                {annotations[data.data_version_id] && (
                  <span>已确认当前标注</span>
                )}
              </Card>
            ))}
          {missingAnnotations && (
            <span className={styles.annotationHint}>
              请确认每条 Episode 的当前标注和分段。
            </span>
          )}
        </section>
        <div className={styles.footer}>
          <div className={styles.summary} aria-live="polite">
            <PackageCheck size={20} aria-hidden="true" />
            <div>
              <strong>
                {deliveryFormats.find((format) => format.value === mode)?.title}
                <span> · {selections.length} 条 Episode</span>
              </strong>
              <p>
                {selections.length
                  ? "任务在后台打包，完成后可在下方下载。"
                  : "选择 Episode 后即可创建交付任务。"}
              </p>
            </div>
          </div>
          <Button
            className={styles.createButton}
            type="primary"
            icon={<PackageCheck size={17} aria-hidden="true" />}
            loading={mutation.isPending}
            disabled={
              !canCreate ||
              !selections.length ||
              missingAnnotations ||
              (annotated && !canReadAnnotations)
            }
            onClick={() => mutation.mutate({ kind: "create" })}
          >
            创建标准包交付任务
          </Button>
        </div>
        {mutation.error && (
          <Alert
            showIcon
            className={styles.operationError}
            type="error"
            title="交付操作失败"
            description={mutation.error.message}
          />
        )}
      </section>
      <Card
        className={styles.jobsCard}
        title="标准包交付任务"
        size="small"
        extra={
          <Button
            size="small"
            icon={<RefreshCw size={14} aria-hidden="true" />}
            onClick={() => void jobs.refetch()}
            loading={jobs.isFetching}
          >
            刷新交付状态
          </Button>
        }
      >
        <div className={styles.jobsList}>
          {jobs.error && (
            <Alert
              type="error"
              title="交付任务读取失败"
              description={jobs.error.message}
            />
          )}
          {jobs.isPending && (
            <Skeleton active title={false} paragraph={{ rows: 2 }} />
          )}
          {!jobs.error && jobs.data && !jobs.data.pages.flat().length && (
            <div className={styles.jobsEmpty}>
              <FileArchive size={25} aria-hidden="true" />
              <div>
                <strong>暂无交付任务</strong>
                <p>创建任务后，在这里查看打包进度并下载文件。</p>
              </div>
            </div>
          )}
          {!jobs.error &&
            jobs.data?.pages.flat().map((job) => (
              <Card
                className={styles.jobCard}
                key={job.job_id}
                size="small"
                title={`${job.plan.archive_format === "original_collection_zip/1" ? "原始文件" : job.plan.archive_format === "mcap_collection_zip/1" ? "MCAP 集合" : job.plan.archive_format === "lerobot_collection_zip/1" ? `LeRobot ${job.plan.modalities.toUpperCase()} 集合` : "独立标准包 ZIP"} · ${job.plan.members.length} 条 · ${phases[job.phase]}`}
              >
                {job.total_files === null || job.total_bytes === null ? (
                  <p>正在生成集合，归档文件数和大小尚未确定。</p>
                ) : (
                  <>
                    <p>
                      归档 {job.processed_files} / {job.total_files} 个文件 ·{" "}
                      {job.processed_bytes.toLocaleString()} /{" "}
                      {job.total_bytes.toLocaleString()} 字节
                    </p>
                    <Progress
                      percent={Math.floor(
                        (job.processed_bytes / job.total_bytes) * 100,
                      )}
                      status={
                        job.status === "FAILED"
                          ? "exception"
                          : job.status === "READY"
                            ? "success"
                            : "normal"
                      }
                    />
                  </>
                )}
                {job.phase === "uploading" && (
                  <p>
                    上传 {job.uploaded_bytes.toLocaleString()} /{" "}
                    {job.size_bytes?.toLocaleString()} 字节
                  </p>
                )}
                {job.error_code && (
                  <Alert
                    type="error"
                    title="交付未完成"
                    description="重试沿用固定的数据版本和标注选择；持续失败时请检查来源文件和服务。"
                  />
                )}
                <Space wrap>
                  <Button
                    disabled={job.status !== "READY" || mutation.isPending}
                    onClick={() => mutation.mutate({ kind: "download", job })}
                  >
                    下载交付 ZIP
                  </Button>
                  {["PENDING", "RUNNING"].includes(job.status) && (
                    <Button
                      disabled={!canCreate || mutation.isPending}
                      onClick={() => mutation.mutate({ kind: "cancel", job })}
                    >
                      取消交付
                    </Button>
                  )}
                  {["FAILED", "CANCELLED"].includes(job.status) && (
                    <Button
                      disabled={!canCreate || mutation.isPending}
                      onClick={() => mutation.mutate({ kind: "retry", job })}
                    >
                      重试交付
                    </Button>
                  )}
                </Space>
                <details>
                  <summary>固定选择与文件哈希</summary>
                  <pre
                    style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                  >
                    {JSON.stringify(
                      { plan: job.plan, artifact_sha256: job.artifact_sha256 },
                      null,
                      2,
                    )}
                  </pre>
                </details>
              </Card>
            ))}
          {jobs.hasNextPage && (
            <Button
              loading={jobs.isFetchingNextPage}
              onClick={() => void jobs.fetchNextPage()}
            >
              加载更多交付任务
            </Button>
          )}
        </div>
      </Card>
    </div>
  );
}

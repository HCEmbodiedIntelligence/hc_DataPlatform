import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useMutation } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Progress,
  Radio,
  Select,
  Space,
} from "antd";
import type { Scope } from "../../entities/scope";
import { CollectionAnnotationChooser } from "./CollectionAnnotationChooser";
import {
  commandPackageJob,
  createPackageBatch,
  downloadPackageJob,
  listPackageJobs,
  loadPackageCandidates,
  type AnnotationSelection,
  type BatchMode,
  type PackageData,
  type PackageJob,
} from "./package-deliveries";

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
    <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      <Card title="完整标准包与 LeRobot 集合" size="small">
        <Space orientation="vertical" style={{ width: "100%" }}>
          <p>
            按固定数据版本选择。完整包包含全部已采集
            RGB、深度视频、profile、掩码和源帧；集合会重建索引、任务与统计并复用媒体。
          </p>
          {!datasets.length ? (
            <Alert type="info" title="请先选择数据集或采集任务" />
          ) : (
            <Select
              aria-label="标准包数据集"
              value={datasetId}
              style={{ width: "100%" }}
              options={datasets.map((d) => ({ value: d.id, label: d.name }))}
              onChange={setDatasetId}
            />
          )}
          {candidates.error && (
            <Alert
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
          {candidates.isFetching && <span>读取固定集合快照…</span>}
          {candidates.data && !candidates.error && (
            <>
              <span>
                集合版本 {candidates.data.pages[0]?.version ?? "尚未建立"} ·{" "}
                {candidates.data.pages[0]?.total ?? 0} 条标准包
              </span>
              {(candidates.data.pages[0]?.legacy_episode_count ?? 0) > 0 && (
                <Alert
                  type="info"
                  title={`此集合另含 ${candidates.data.pages[0]?.legacy_episode_count} 条历史存储数据`}
                  description="历史数据可在历史格式入口导出；这里不会把它们伪装成标准包。"
                />
              )}
              {items.map((data) => (
                <Checkbox
                  key={`candidate-${data.data_version_id}`}
                  checked={Boolean(selected[data.data_version_id])}
                  onChange={(e) => select(data, e.target.checked)}
                >
                  {data.identity.episode_id} · 数据 v{data.version} ·{" "}
                  {data.frame_count} 帧
                </Checkbox>
              ))}
              {items.length === 0 && (
                <span>此数据集暂无独立标准包；可查看历史格式或原始数据。</span>
              )}
              {candidates.hasNextPage && (
                <Button
                  loading={candidates.isFetchingNextPage}
                  onClick={() => void candidates.fetchNextPage()}
                >
                  加载更多 Episode
                </Button>
              )}
            </>
          )}
          {selectionError && <Alert type="warning" title={selectionError} />}
          <strong>已选 {selections.length} 条 Episode</strong>
          {selections.map((data) => (
            <div key={`selected-${data.data_version_id}`}>
              <span>
                {data.identity.episode_id} · 数据 v{data.version}
              </span>{" "}
              <Button size="small" onClick={() => select(data, false)}>
                移除 {data.identity.episode_id}
              </Button>
            </div>
          ))}
          <Radio.Group
            aria-label="标准包交付格式"
            value={mode}
            onChange={(e) => setMode(e.target.value)}
            options={[
              {
                value: "independent",
                label: "独立标准包批量 ZIP（含已采集深度）",
                disabled: annotated,
              },
              { value: "rgbd", label: "LeRobot RGB-D 集合" },
              { value: "rgb", label: "LeRobot RGB 子集（排除深度）" },
            ]}
          />
          {mode === "rgb" ? (
            <Alert
              type="warning"
              title="RGB 子集将排除深度视频、深度像素掩码及相关数据特征"
              description="数值、RGB 和来源说明保留。需要深度时选择完整标准包或 RGB-D 集合。"
            />
          ) : (
            <Alert
              type="info"
              title="保留已采集深度视频、profile、有效掩码和源帧映射"
              description={
                mode === "independent"
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
                  <span>
                    已固定选择 r{annotations[data.data_version_id]!.revision}
                  </span>
                )}
              </Card>
            ))}
          {missingAnnotations && (
            <span>请为每条 Episode 明确选择标注修订和分段轨道。</span>
          )}
          <Button
            type="primary"
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
          {mutation.error && (
            <Alert
              type="error"
              title="交付操作失败"
              description={mutation.error.message}
            />
          )}
        </Space>
      </Card>
      <Card
        title="标准包交付任务"
        size="small"
        extra={
          <Button onClick={() => void jobs.refetch()} loading={jobs.isFetching}>
            刷新交付状态
          </Button>
        }
      >
        <Space orientation="vertical" style={{ width: "100%" }}>
          {jobs.error && (
            <Alert
              type="error"
              title="交付任务读取失败"
              description={jobs.error.message}
            />
          )}
          {!jobs.error &&
            jobs.data?.pages.flat().map((job) => (
              <Card
                key={job.job_id}
                size="small"
                title={`${job.plan.archive_format === "lerobot_collection_zip/1" ? `LeRobot ${job.plan.modalities.toUpperCase()} 集合` : "独立标准包 ZIP"} · ${job.plan.members.length} 条 · ${phases[job.phase]}`}
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
        </Space>
      </Card>
    </Space>
  );
}

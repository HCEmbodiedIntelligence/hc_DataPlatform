import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Tabs, Typography } from "antd";
import { Link, useLocation } from "react-router-dom";
import type { Scope } from "../../shared/scope/model";
import { annotationRoutes } from "../../features/annotation/routing";
import { DataReviewPanel } from "./DataReviewPanel";
import { DepthPixelPanel } from "./DepthPixelPanel";
import { EpisodeQualityPanel } from "./EpisodeQualityPanel";
import { CompleteDeliveryPanel } from "./CompleteDeliveryPanel";
import type { ReviewDataRef } from "../../features/datasets/episodes/data-reviews";
import {
  authorizeEpisodeFile,
  downloadAnnotationMigration,
  loadEpisodeFiles,
  loadEpisodeOverview,
  secondsFromNs,
  type DetailSelection,
  type EpisodeFile,
  type EpisodeOverview,
  type EpisodeAnnotationMigration,
} from "../../features/datasets/episodes/episode-detail";
import styles from "./EpisodeDetailPanel.module.css";

const DatasetSourceFiles = lazy(
  () => import("../../features/ingest/browser/DatasetSourceFiles"),
);

const reviews = { NOT_REVIEWED: "未审核", APPROVED: "通过", RETURNED: "退回" };
const previews = {
  NOT_GENERATED: "尚未生成",
  PREPARING: "生成中",
  READY: "可播放",
  FAILED: "生成失败",
  INTEGRITY_FAILED: "文件校验失败",
};
const categories = {
  NUMERIC: "数值",
  RGB: "RGB",
  DEPTH: "深度",
  ANNOTATION: "标注",
  METADATA: "说明",
  REPORT: "报告",
};
const unavailable = {
  MISSING_CAPABILITY: "缺少下载权限",
  MISSING: "文件缺失",
  SIZE_MISMATCH: "文件大小不符",
};
const annotationStates: Record<string, string> = {
  APPROVED: "通过",
  APPROVE: "通过",
  NEEDS_REVISION: "需返修",
  REJECTED: "拒绝",
  SUBMITTED: "待审核",
  DRAFT: "草稿",
};
function currentAnnotationReview(value: EpisodeOverview) {
  const ann = value.annotation;
  if (ann.availability === "FORBIDDEN") return "无读取权限";
  if (ann.availability === "SELECTION_REQUIRED") return "待选择标注任务";
  if (ann.availability === "NOT_CREATED") return "尚无标注任务";
  if (ann.current_approved_review_id) return "通过";
  if (ann.status === "SUBMITTED") return "待审核";
  if (ann.latest_review?.revision !== ann.current_revision?.revision)
    return "当前修订未审核";
  if (ann.latest_review?.decision === "APPROVE")
    return "当前未批准（保留历史批准记录）";
  return ann.latest_review
    ? (annotationStates[ann.latest_review.decision] ??
        ann.latest_review.decision)
    : "未审核";
}
function Technical({ title, value }: { title: string; value: unknown }) {
  return (
    <details>
      <summary>{title}</summary>
      <pre className={styles.technical}>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
function EpisodeFiles({ scope, data }: { scope: Scope; data: ReviewDataRef }) {
  const [cursors, setCursors] = useState<(string | undefined)[]>([undefined]);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    controller.current = new AbortController();
    return () => controller.current?.abort();
  }, []);
  const query = useQuery({
    queryKey: ["episode-files", scope, data, cursors.at(-1)],
    queryFn: ({ signal }) =>
      loadEpisodeFiles(scope, data, cursors.at(-1), signal),
  });
  const download = useMutation({
    mutationFn: async (file: EpisodeFile) => {
      const signal = controller.current!.signal;
      const grant = await authorizeEpisodeFile(scope, data, file, signal);
      signal.throwIfAborted();
      const anchor = document.createElement("a");
      anchor.href = grant.url;
      anchor.download = "";
      anchor.rel = "noopener noreferrer";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
    },
  });
  return (
    <section aria-label="已提交数据文件" className={styles.stack}>
      <Button loading={query.isFetching} onClick={() => void query.refetch()}>
        刷新文件清单
      </Button>
      <Typography.Paragraph>
        以下为已提交的数据文件；完整数据包含数值、RGB、已采集深度视频、深度
        profile 和有效性掩码。
      </Typography.Paragraph>
      {query.isPending && <p>加载文件清单…</p>}
      {query.error && (
        <Alert
          type="error"
          title="文件清单读取失败"
          description={query.error.message}
        />
      )}
      {download.error && (
        <Alert
          type="error"
          title="文件下载授权失败"
          description={download.error.message}
        />
      )}
      {query.data && !query.error && (
        <>
          <p>
            共 {query.data.total} 个文件 · 第 {cursors.length} 页
          </p>
          {query.data.items.map((file) => (
            <Card
              key={file.file_id}
              size="small"
              title={`${categories[file.category]} · ${file.path.split("/").at(-1)}`}
            >
              <p className={styles.wrap}>{file.path}</p>
              <p>
                {file.size.toLocaleString()} 字节 ·{" "}
                {file.availability === "AVAILABLE"
                  ? "可读取"
                  : unavailable[file.availability]}
              </p>
              <Technical
                title="文件哈希"
                value={{ sha256: file.sha256, media_type: file.media_type }}
              />
              <Button
                disabled={!file.download.allowed || download.isPending}
                loading={
                  download.isPending &&
                  download.variables?.file_id === file.file_id
                }
                onClick={() => download.mutate(file)}
              >
                下载文件
              </Button>
              {!file.download.allowed && (
                <p>
                  {unavailable[
                    file.download.denied_reason as keyof typeof unavailable
                  ] ?? "当前不可下载"}
                </p>
              )}
            </Card>
          ))}
          <div className={styles.actions}>
            <Button
              disabled={cursors.length === 1}
              onClick={() => setCursors((old) => old.slice(0, -1))}
            >
              上一页
            </Button>
            <Button
              disabled={!query.data.next_after}
              onClick={() =>
                setCursors((old) => [...old, query.data!.next_after!])
              }
            >
              下一页
            </Button>
          </div>
        </>
      )}
    </section>
  );
}

function HistoricalAnnotationMappings({
  scope,
  data,
  mappings,
}: {
  scope: Scope;
  data: ReviewDataRef;
  mappings: EpisodeAnnotationMigration[];
}) {
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    controller.current = new AbortController();
    return () => controller.current?.abort();
  }, []);
  const download = useMutation({
    mutationFn: async (mapping: EpisodeAnnotationMigration) => {
      const signal = controller.current!.signal;
      const blob = await downloadAnnotationMigration(
        scope,
        data,
        mapping,
        signal,
      );
      signal.throwIfAborted();
      const url = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `${mapping.mapping_id}.json`;
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
      } finally {
        // Give the browser time to begin consuming the object URL.
        window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
      }
    },
  });
  return (
    <section aria-label="历史标注迁移" className={styles.stack}>
      {mappings.map((mapping) => (
        <Card key={mapping.mapping_id} size="small" title="历史标注迁移">
          <p>
            来源：历史数据版本 {mapping.source_data.dataset_version} · 标注修订{" "}
            {mapping.source_revision} ·{" "}
            {annotationStates[mapping.source_status] ?? mapping.source_status}
          </p>
          <p>
            保留 {mapping.source_submission_count} 次历史提交、{" "}
            {mapping.source_review_count} 次历史审核，映射至当前任务的修订{" "}
            {mapping.target_revision}。
          </p>
          <p>历史审核仅作来源记录；当前版本标注需独立提交和审核。</p>
          <Button
            disabled={!mapping.download.allowed || download.isPending}
            loading={
              download.isPending &&
              download.variables?.mapping_id === mapping.mapping_id
            }
            onClick={() => download.mutate(mapping)}
          >
            下载历史标注映射 JSON
          </Button>
          {!mapping.download.allowed && <p>缺少标注读取或下载权限。</p>}
          <Technical title="映射身份与文件校验" value={mapping} />
        </Card>
      ))}
      {download.error && (
        <Alert
          type="error"
          title="历史标注映射下载失败"
          description={download.error.message}
        />
      )}
    </section>
  );
}

function AnnotationSummary({
  scope,
  value,
  select,
}: {
  scope: Scope;
  value: EpisodeOverview;
  select: (selection: DetailSelection) => void;
}) {
  const ann = value.annotation;
  const location = useLocation();
  if (ann.availability === "FORBIDDEN")
    return <Alert type="warning" title="缺少标注读取权限" />;
  if (ann.availability === "NOT_CREATED")
    return <Alert type="info" title="此数据版本尚未创建标注任务" />;
  if (ann.availability === "SELECTION_REQUIRED")
    return (
      <section className={styles.stack}>
        <Alert type="info" title="此数据版本关联多个标注任务，请选择任务" />
        {ann.task_options.map((option) => (
          <Button
            key={option.task_id}
            onClick={() => select({ annotationTaskId: option.task_id })}
          >
            集合版本 {option.dataset_version} · 修订 {option.current_revision} ·{" "}
            {option.task_id}
          </Button>
        ))}
        {ann.task_options_has_more && (
          <Link to={annotationRoutes.annotate.build()}>查看全部标注任务</Link>
        )}
      </section>
    );
  const taskId = ann.task_id!;
  return (
    <section className={styles.stack} aria-label="固定数据版本标注状态">
      <dl className={styles.facts}>
        <dt>标注任务状态</dt>
        <dd>{annotationStates[ann.status ?? ""] ?? ann.status}</dd>
        <dt>当前草稿</dt>
        <dd>
          修订 {ann.current_revision!.revision}
          {ann.has_unsubmitted_changes ? " · 有未提交内容" : ""}
        </dd>
        <dt>最近提交</dt>
        <dd>
          {ann.latest_submission
            ? `修订 ${ann.latest_submission.revision}`
            : "尚未提交"}
        </dd>
        <dt>最近批准</dt>
        <dd>
          {ann.latest_approval
            ? `修订 ${ann.latest_approval.revision}${ann.current_approved_review_id ? " · 当前有效" : " · 历史记录"}`
            : "尚未批准"}
        </dd>
        <dt>最近审核</dt>
        <dd>
          {ann.latest_review
            ? (annotationStates[ann.latest_review.decision] ??
              ann.latest_review.decision)
            : "未审核"}
        </dd>
      </dl>
      <div className={styles.actions}>
        <Link
          to={annotationRoutes.task.build(
            { taskId },
            { returnTo: location.pathname + location.search },
          )}
        >
          进入标注与提交
        </Link>
        <Link to={annotationRoutes.tagReviewTask.build({ taskId })}>
          进入标注审核
        </Link>
        <Button onClick={() => select({})}>重新选择标注任务</Button>
      </div>
      <p>带标注下载在标注工作区选择修订和分段轨道，保留已采集深度。</p>
      {ann.historical_migrations.length > 0 && (
        <HistoricalAnnotationMappings
          scope={scope}
          data={value.data}
          mappings={ann.historical_migrations}
        />
      )}
      <strong>标注 JSON 文件</strong>
      {ann.revision_files.map((file) => (
        <p key={file.revision}>
          修订 {file.revision} ·{" "}
          {(
            {
              READY: "可下载",
              PENDING: "等待生成",
              GENERATING: "生成中",
              FAILED: "生成失败",
              NOT_REQUESTED: "尚未请求",
            } as Record<string, string>
          )[file.status] ?? file.status}
          {file.error_code ? ` · ${file.error_code}` : ""}
        </p>
      ))}
      <strong>带标注交付与分段轨道</strong>
      {!value.deliveries.length && <p>尚无此任务的交付记录。</p>}
      {value.deliveries.map((job) => (
        <Card
          size="small"
          key={job.job_id}
          title={`${job.track.label} · 修订 ${job.revision}`}
        >
          <p>
            {job.segment_count} 个任务分段 ·{" "}
            {job.review.status === "APPROVED" ? "已审核" : "未审核"} ·{" "}
            {job.status}
          </p>
          <Button
            onClick={() =>
              select({ annotationTaskId: taskId, deliveryId: job.job_id })
            }
          >
            查看此分段轨道
          </Button>
        </Card>
      ))}
      {value.deliveries_has_more && <p>更多交付记录见标注工作区。</p>}
      <Technical title="标注修订与审核绑定" value={ann} />
    </section>
  );
}

export function EpisodeDetailPanel({
  scope,
  data,
  canRead,
  canReview,
  canDownload = false,
  canCreate = false,
  canReadOriginal = false,
  initialTab = "playback",
  returnToPlayback,
}: {
  scope: Scope;
  data: ReviewDataRef;
  canRead: boolean;
  canReview: boolean;
  canDownload?: boolean;
  canCreate?: boolean;
  canReadOriginal?: boolean;
  initialTab?: string;
  returnToPlayback: () => void;
}) {
  const [selection, setSelection] = useState<DetailSelection>({});
  const [activeTab, setActiveTab] = useState(initialTab);
  const query = useQuery({
    queryKey: ["episode-overview", scope, data, selection],
    queryFn: ({ signal }) =>
      loadEpisodeOverview(scope, data, selection, signal),
    enabled: canRead,
  });
  if (!canRead) return <Alert type="warning" title="缺少数据版本读取权限" />;
  const value = query.data;
  return (
    <section aria-label="Episode 统一详情" className={styles.stack}>
      <div className={styles.actions}>
        <Button onClick={returnToPlayback}>返回同步回放</Button>
        <Button loading={query.isFetching} onClick={() => void query.refetch()}>
          刷新详情
        </Button>
      </div>
      {query.isPending && <p>加载 Episode 详情…</p>}
      {query.error && (
        <Alert
          type="error"
          title="Episode 详情读取失败"
          description={query.error.message}
        />
      )}
      {value && !query.error && (
        <>
          <Typography.Title level={4} className={styles.wrap}>
            {value.display_name}
          </Typography.Title>
          <p>
            {value.task.instructions.join(" / ") || value.task.task_id} ·{" "}
            {value.robot_id} · {value.recording_date}
          </p>
          <dl className={styles.facts}>
            <dt>数据版本</dt>
            <dd>{data.version}</dd>
            <dt>标注修订</dt>
            <dd>{value.annotation.current_revision?.revision ?? "未选定"}</dd>
            <dt>自动 QC</dt>
            <dd>
              {value.quality.status === "UNKNOWN"
                ? "尚无报告"
                : value.quality.status}
            </dd>
            <dt>人工数据审核</dt>
            <dd>{reviews[value.data_review_status]}</dd>
            <dt>标注审核</dt>
            <dd>{currentAnnotationReview(value)}</dd>
            <dt>当前 Episode</dt>
            <dd>
              {value.duration_seconds.toLocaleString(undefined, {
                maximumFractionDigits: 6,
              })}{" "}
              秒 · {data.frame_count} 帧 · {value.frequency_hz} Hz
            </dd>
            <dt>相机</dt>
            <dd>
              {value.physical_camera_count ?? "物理相机数未记录"} ·{" "}
              {value.camera_stream_count} 路视频
            </dd>
            <dt>任务分段</dt>
            <dd>
              {value.subtasks.segment_count ?? "未指定"}
              {value.subtasks.track ? ` · ${value.subtasks.track.label}` : ""}
            </dd>
          </dl>
          <CompleteDeliveryPanel
            scope={scope}
            data={data}
            canDownload={canDownload}
            canCreate={canCreate}
          />
          <Tabs
            activeKey={activeTab}
            onChange={setActiveTab}
            items={[
              {
                key: "playback",
                label: "回放与分段",
                children: (
                  <div className={styles.stack}>
                    <p>
                      同步回放保留当前时间位置；分段按所选标注交付轨道显示。
                    </p>
                    {value.cameras.map((camera) => (
                      <Card
                        size="small"
                        key={camera.feature}
                        title={`${camera.kind} · ${camera.feature}`}
                      >
                        <p>
                          {camera.shape.join(" × ")} · 预览
                          {previews[camera.preview_status]}
                        </p>
                        {camera.depth?.measurement_status ===
                          "NO_SOURCE_FRAMES" && (
                          <Alert
                            type="warning"
                            title="本区间没有源深度帧"
                            description="深度视频保留缺帧占位和掩码；物理值不可用，未生成深度统计。"
                          />
                        )}
                        {camera.depth?.measurement_status ===
                          "NO_VALID_PIXELS" && (
                          <Alert
                            type="warning"
                            title="本区间没有有效深度像素"
                            description="源帧与无效掩码已保留；物理值不可用，未生成深度统计。"
                          />
                        )}
                        {camera.depth && (
                          <Technical
                            title="深度单位、量化、有效性与误差"
                            value={camera.depth}
                          />
                        )}
                      </Card>
                    ))}
                    <DepthPixelPanel
                      scope={scope}
                      data={data}
                      canRead={canRead}
                      cameras={value.cameras
                        .filter((c) => c.kind === "DEPTH")
                        .map((c) => ({
                          feature: c.feature,
                          label: c.feature,
                          start: 0,
                          end: data.frame_count,
                        }))}
                    />
                  </div>
                ),
              },
              {
                key: "files",
                label: "数据文件",
                children: <EpisodeFiles scope={scope} data={data} />,
              },
              ...(canReadOriginal && scope.projectId && scope.regionCode
                ? [
                    {
                      key: "original-files",
                      label: "原始文件",
                      children: (
                        <Suspense fallback={<p>加载原始文件…</p>}>
                          <DatasetSourceFiles
                            scope={{
                              organizationId: scope.organizationId,
                              projectId: scope.projectId,
                              regionCode: scope.regionCode,
                            }}
                            datasetId={data.identity.dataset_id}
                            recordingId={
                              value.source.parent_recording_id ?? undefined
                            }
                            initialImportId={
                              typeof value.source.document.raw_upload_id ===
                              "string"
                                ? value.source.document.raw_upload_id
                                : undefined
                            }
                          />
                        </Suspense>
                      ),
                    },
                  ]
                : []),
              {
                key: "quality",
                label: "数据质检",
                children: (
                  <div className={styles.stack}>
                    <EpisodeQualityPanel
                      scope={scope}
                      data={data}
                      canRead={canRead}
                    />
                    <DataReviewPanel
                      scope={scope}
                      data={data}
                      canRead={canRead}
                      canReview={canReview}
                    />
                  </div>
                ),
              },
              {
                key: "annotation",
                label: "标注与审核",
                children: (
                  <AnnotationSummary
                    scope={scope}
                    value={value}
                    select={setSelection}
                  />
                ),
              },
              {
                key: "source",
                label: "版本与来源",
                children: (
                  <div className={styles.stack}>
                    <dl className={styles.facts}>
                      <dt>父录制</dt>
                      <dd>{value.source.parent_recording_id ?? "未记录"}</dd>
                      <dt>父录制时长</dt>
                      <dd>{secondsFromNs(value.source.parent_duration_ns)}</dd>
                      <dt>源窗口</dt>
                      <dd>
                        {secondsFromNs(
                          value.source.window.start_offset_ns ??
                            value.source.window.start_ns,
                        )}{" "}
                        →{" "}
                        {secondsFromNs(
                          value.source.window.end_offset_ns ??
                            value.source.window.end_ns,
                        )}
                      </dd>
                      <dt>窗口时间基准</dt>
                      <dd>
                        {value.source.window_clock === "recording_relative"
                          ? "相对父录制起点"
                          : value.source.window_clock === "NOT_RECORDED"
                            ? "未记录"
                            : value.source.window_clock}
                      </dd>
                      <dt>任务要求</dt>
                      <dd>
                        {value.task.requirements_status === "RECORDED" ? (
                          <Technical
                            title="查看任务要求"
                            value={value.task.requirements}
                          />
                        ) : (
                          "此版本未记录"
                        )}
                      </dd>
                    </dl>
                    <Technical title="精确来源与帧映射" value={value.source} />
                    <Technical
                      title="存储格式、路径与哈希"
                      value={value.data}
                    />
                  </div>
                ),
              },
            ]}
          />
        </>
      )}
    </section>
  );
}

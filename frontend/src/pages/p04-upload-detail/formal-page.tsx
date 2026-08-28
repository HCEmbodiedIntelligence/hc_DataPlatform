import { useQuery } from "@tanstack/react-query";
import { Button, Space, Typography } from "antd";
import { ArrowLeft, Camera, Clock3, FileJson2, RadioTower } from "lucide-react";
import { useEffect, useMemo } from "react";
import {
  Link,
  useLocation,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { useIngestScope } from "../../features/ingest/use-ingest-scope";
import {
  createPlaybackClock,
  RawDiagnosticWorkbench,
  type ViewerTimelineTrack,
  type WorkbenchFinding,
} from "../../features/viewer";
import type { RuntimeManifestDiscoveryProjection } from "../../features/viewer/raw-diagnostic-adapter";
import type { StreamDescriptor } from "../../features/viewer/types";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import {
  formatEffectiveDuration,
  formatStorageSize,
} from "../../shared/lib/metric-presentation";
import { safeReturnTo } from "../../shared/routing/route-registry";
import { routes as datasetRoutes } from "../../features/datasets/routing";
import { createDatasetPreviewMediaSource } from "../p06-dataset-detail/preview-media-source";
import {
  PageState,
  StatusTag,
  UiMetricCard,
  UiPageHeader,
  type PageStateKind,
} from "../../shared/ui";
import {
  getFormalUploadProcessingStatus,
  getFormalUploadRawMedia,
  loadFormalUploadDetail,
  resolveFormalUploadPreviewTarget,
  resolveFormalUploadViewerTarget,
  type FormalUploadProcessingStatus,
  type FormalQcReport,
  type FormalUploadDetail,
  type FormalUploadPreviewTarget,
  type FormalUploadViewerTarget,
  type FormalRawMediaSource,
} from "./formal-detail-client";
import styles from "./formal-page.module.css";

const uploadRecordsPath = "/ingest/uploads/records";
const problemDataPath = "/manual/issues";

type ResolvedFormalUploadDetail = FormalUploadDetail & {
  readonly quality: FormalQcReport;
};

const findingTitles: Readonly<Record<string, string>> = {
  QC_REQUIRED_TOPIC_MISSING: "必需 Topic 缺失",
  QC_TIMESTAMP_DUPLICATE: "时间戳重复",
  QC_TIMESTAMP_BACKWARD: "时间戳回退",
  QC_FREQUENCY_LOW: "采样频率偏低",
  QC_GAP_EXCESSIVE: "时间间隔过大",
  QC_CONSECUTIVE_FRAMES_MISSING: "连续帧缺失",
  QC_COVERAGE_LOW: "时间覆盖不足",
  QC_IMAGE_BLACK: "图像持续黑屏",
  QC_IMAGE_REPEATED: "图像重复",
  QC_IMAGE_CORRUPT: "图像损坏",
  QC_JOINT_OUT_OF_RANGE: "关节值超出范围",
  QC_ACTION_MISSING: "动作信号缺失",
  QC_ACTION_JUMP: "动作信号跳变",
  QC_POINT_CLOUD_EMPTY: "点云为空",
  QC_POINT_COUNT_ABNORMAL: "点数异常",
  QC_MODALITY_OFFSET: "模态时间偏移",
  QC_COMPLETE_STEP_RATIO_LOW: "完整步比例不足",
};

function pageStateFromError(error: unknown): PageStateKind {
  if (!isDomainError(error)) return "error";
  switch (error.code) {
    case "UNAUTHENTICATED":
    case "FORBIDDEN":
      return "forbidden";
    case "NOT_FOUND":
      return "not-found";
    case "GONE":
      return "gone";
    case "VERSION_CONFLICT":
    case "PRECONDITION_FAILED":
      return "conflict";
    case "RATE_LIMITED":
      return "rate-limited";
    case "NETWORK_ERROR":
      return "offline";
    case "CONTRACT_MISMATCH":
      return "contract-mismatch";
    default:
      return "error";
  }
}

function problemDescription(error: unknown): string {
  if (!isDomainError(error)) return "上传详情加载失败，请检查网络后重试。";
  const problemCode = error.problemCode
    ? ` 问题代码：${error.problemCode}。`
    : "";
  const retry = error.retryable
    ? " 服务端允许重试。"
    : " 请核对当前作用域或联系项目管理员。";
  return `${error.message}${problemCode}${retry}`;
}

function formatDate(value: string | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(date);
}

function statusTone(
  status: FormalQcReport["status"],
): "success" | "warning" | "danger" {
  if (status === "PASS") return "success";
  return status === "RISK" ? "warning" : "danger";
}

function diagnosticBounds(quality: FormalQcReport): {
  readonly startNs: string;
  readonly endNs: string;
} {
  const start = BigInt(Math.max(0, Math.trunc(quality.start_ns)));
  const suppliedEnd = BigInt(Math.max(0, Math.trunc(quality.end_ns)));
  const end = suppliedEnd > start ? suppliedEnd : start + 1n;
  return { startNs: start.toString(), endNs: end.toString() };
}

function diagnosticFindings(
  quality: FormalQcReport,
): readonly WorkbenchFinding[] {
  return quality.findings.map((finding, index) => ({
    id: `${finding.code}:${finding.topic}:${finding.start_ns}:${index}`,
    title: findingTitles[finding.code] ?? finding.code,
    severity: finding.severity,
    streamLabel: finding.topic,
    topic: finding.topic,
    startNs: String(Math.max(0, Math.trunc(finding.start_ns))),
    endNs: String(Math.max(0, Math.trunc(finding.end_ns))),
    message: finding.message,
    observed: String(finding.observed),
    threshold: String(finding.threshold),
  }));
}

function diagnosticTracks(
  quality: FormalQcReport,
): readonly ViewerTimelineTrack[] {
  const { startNs, endNs } = diagnosticBounds(quality);
  return quality.topic_metrics.map((metric) => ({
    id: `topic:${metric.topic}`,
    label: metric.topic,
    segments: [
      {
        id: `coverage:${metric.topic}`,
        label: `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(metric.actual_frequency_hz)} Hz · ${new Intl.NumberFormat("zh-CN", { style: "percent", maximumFractionDigits: 1 }).format(metric.coverage_ratio)}`,
        startNs,
        endNs,
        tone: "signal",
      },
    ],
  }));
}

function rawCameraStreams(
  manifest: RuntimeManifestDiscoveryProjection,
  clock: ReturnType<typeof createPlaybackClock>,
  rawSourceAvailable: boolean,
  detail: FormalUploadDetail,
  scope: NonNullable<ReturnType<typeof useIngestScope>>,
  workflow: FormalUploadProcessingStatus | undefined,
  previewTarget: FormalUploadPreviewTarget | null | undefined,
): Readonly<Record<string, StreamDescriptor>> {
  const streams: Record<string, StreamDescriptor> = {};
  for (const camera of manifest.cameras) {
    const modality = camera.encoding?.toLowerCase().includes("depth")
      ? "depth"
      : "rgb";
    const previewReady = Boolean(previewTarget);
    const previewPending =
      !workflow ||
      workflow.status === "PENDING" ||
      workflow.status === "RUNNING";
    streams[camera.topic] = {
      id: `raw-mcap:${camera.camera_id}`,
      canonicalPath: camera.topic,
      displayName: camera.camera_id,
      modality,
      semanticRole: "raw-mcap-camera",
      schema: {
        id: "raw-mcap",
        version: "raw-media-source/v1",
        ...(camera.encoding ? { encoding: camera.encoding } : {}),
      },
      startNs: clock.startNs,
      endNs: clock.endNs,
      ...(camera.frame_id
        ? { frame: { id: camera.frame_id, name: camera.frame_id } }
        : {}),
      availability: previewReady
        ? "ready"
        : previewPending
          ? "preview-generating"
          : "missing",
      accessibleSummary: previewReady
        ? `${camera.camera_id} 已从固定 Lance 版本生成受权 HLS；面板可见时才会按需签发播放地址。`
        : previewPending
          ? `${camera.camera_id} 正在完成校验、对齐和 Lance 持久化，完成后会自动显示。`
          : rawSourceAvailable
            ? `${camera.camera_id} 的 Raw MCAP 仍可下载，但本次工作流未生成可播放的 Lance 版本。`
            : `${camera.camera_id} 尚无可用的 Raw 证据源或可播放版本。`,
      ...(previewTarget
        ? {
            mediaSource: createDatasetPreviewMediaSource({
              scope: {
                organizationId: scope.organizationId,
                projectId: scope.projectId,
                regionCode: scope.regionCode,
              },
              datasetId: previewTarget.datasetId,
              binding: {
                rollout_id: detail.session.rollout_id,
                lance_version: previewTarget.lanceVersion,
                annotation_revision: 0,
                // Lance fields from ingest use the immutable Manifest topic,
                // so the preview request must use that exact key.
                camera_id: camera.topic,
                frequency_hz: previewTarget.frequencyHz,
                start_step: previewTarget.startStep,
                end_step: previewTarget.endStep,
              },
              modality,
            }),
          }
        : {}),
    };
  }
  return streams;
}

function ProcessingPreviewState({
  session,
  workflow,
  previewTarget,
  viewerTarget,
  pending,
  error,
  onRefresh,
}: {
  readonly session: FormalUploadDetail["session"];
  readonly workflow: FormalUploadProcessingStatus | undefined;
  readonly previewTarget: FormalUploadPreviewTarget | null | undefined;
  readonly viewerTarget: FormalUploadViewerTarget | null | undefined;
  readonly pending: boolean;
  readonly error: unknown;
  readonly onRefresh: () => void;
}) {
  if (!session.workflow) {
    return (
      <div className={styles.processingState} role="alert">
        已提交上传缺少持久化处理工作流，无法生成真实预览。
      </div>
    );
  }
  if (error) {
    return (
      <div className={styles.processingState} role="alert">
        <span>{problemDescription(error)}</span>
        <Button type="default" onClick={onRefresh}>
          重新读取处理状态
        </Button>
      </div>
    );
  }
  if (pending || !workflow) {
    return (
      <output className={styles.processingState} aria-live="polite">
        正在读取上传处理状态…
      </output>
    );
  }
  if (previewTarget) {
    const viewerPath = viewerTarget
      ? datasetRoutes.episodeViewer.build({
          datasetId: viewerTarget.datasetId,
          versionId: viewerTarget.versionId,
          episodeId: viewerTarget.episodeId,
        })
      : null;
    return (
      <div className={styles.processingState} role="status" aria-live="polite">
        <span>
          可视化数据已生成 · 数据集版本 {previewTarget.datasetVersion} · Lance
          版本 {previewTarget.lanceVersion}
        </span>
        {viewerPath ? (
          <Link className={styles.viewerLink} to={viewerPath}>
            打开完整数据视图
          </Link>
        ) : null}
      </div>
    );
  }
  if (workflow.status === "PENDING" || workflow.status === "RUNNING") {
    return (
      <output className={styles.processingState} aria-live="polite">
        后台处理中 · {workflow.stage} · 第 {workflow.attempt} 次尝试
      </output>
    );
  }
  return (
    <div className={styles.processingState} role="alert">
      <span>
        处理结果：{workflow.status}
        {workflow.error_code ? ` · 问题代码 ${workflow.error_code}` : ""}
      </span>
      <Button type="default" onClick={onRefresh}>
        刷新处理状态
      </Button>
    </div>
  );
}

function RawMediaEvidence({
  source,
  pending,
  error,
  onRefresh,
}: {
  readonly source: FormalRawMediaSource | undefined;
  readonly pending: boolean;
  readonly error: unknown;
  readonly onRefresh: () => void;
}) {
  return (
    <section
      className={styles.rawSource}
      aria-labelledby="raw-media-source-title"
    >
      <div>
        <span>RAW EVIDENCE</span>
        <h2 id="raw-media-source-title">原始 MCAP 证据源</h2>
        <p>
          浏览器不会把 MCAP 采集包误播为视频；相机话题与 Raw 文件保持一对一的
          数据清单关联。
        </p>
      </div>
      {pending ? (
        <output aria-live="polite">正在取得受权 Raw 源…</output>
      ) : null}
      {error ? (
        <div className={styles.rawSourceError} role="alert">
          <span>{problemDescription(error)}</span>
          <Button type="default" onClick={onRefresh}>
            重新获取受权链接
          </Button>
        </div>
      ) : null}
      {source ? (
        <div className={styles.rawSourceAction}>
          <span>
            已授权 · {formatStorageSize(source.byte_length)} · 至
            {formatDate(source.expires_at)} 有效
          </span>
          <a
            className={styles.rawSourceLink}
            href={source.download_url}
            target="_blank"
            rel="noopener noreferrer"
            referrerPolicy="no-referrer"
          >
            下载 Raw MCAP
          </a>
          <Button type="text" onClick={onRefresh}>
            刷新链接
          </Button>
        </div>
      ) : null}
    </section>
  );
}

interface WorkflowPreviewView {
  readonly job: FormalUploadProcessingStatus | undefined;
  readonly target: FormalUploadPreviewTarget | null | undefined;
  readonly viewer: FormalUploadViewerTarget | null | undefined;
  readonly pending: boolean;
  readonly error: unknown;
  readonly onRefresh: () => void;
}

function UploadDiagnostic({
  detail,
  rawSourceAvailable,
  scope,
  workflowPreview,
  returnTo = problemDataPath,
  problemDataContext = false,
  showContextBar = true,
}: {
  readonly detail: ResolvedFormalUploadDetail;
  readonly rawSourceAvailable: boolean;
  readonly scope: NonNullable<ReturnType<typeof useIngestScope>>;
  readonly workflowPreview: WorkflowPreviewView;
  readonly returnTo?: string;
  readonly problemDataContext?: boolean;
  readonly showContextBar?: boolean;
}) {
  const bounds = useMemo(
    () => diagnosticBounds(detail.quality),
    [detail.quality.end_ns, detail.quality.start_ns],
  );
  const clock = useMemo(() => createPlaybackClock(bounds), [bounds]);
  useEffect(() => () => clock.dispose(), [clock]);

  const manifest = detail.manifest
    .discovery as RuntimeManifestDiscoveryProjection;
  const findings = useMemo(
    () => diagnosticFindings(detail.quality),
    [detail.quality],
  );
  const tracks = useMemo(
    () => diagnosticTracks(detail.quality),
    [detail.quality],
  );
  const mediaStreamsByTopic = useMemo(
    () =>
      rawCameraStreams(
        manifest,
        clock,
        rawSourceAvailable,
        detail,
        scope,
        workflowPreview.job,
        workflowPreview.target,
      ),
    [
      clock,
      detail,
      manifest,
      rawSourceAvailable,
      scope,
      workflowPreview.job,
      workflowPreview.target,
    ],
  );

  return (
    <>
      {showContextBar ? (
        <div className={styles.contextBar}>
          <Link to={returnTo}>
            <ArrowLeft aria-hidden="true" size={16} />
            返回问题数据
          </Link>
          <Space wrap>
            <StatusTag
              status={detail.session.status}
              label={`上传：${detail.session.status}`}
              tone="info"
            />
            <StatusTag
              status={detail.quality.status}
              label={`自动质检：${detail.quality.status}`}
              tone={statusTone(detail.quality.status)}
            />
          </Space>
        </div>
      ) : null}
      <ProcessingPreviewState
        session={detail.session}
        workflow={workflowPreview.job}
        previewTarget={workflowPreview.target}
        viewerTarget={workflowPreview.viewer}
        pending={workflowPreview.pending}
        error={workflowPreview.error}
        onRefresh={workflowPreview.onRefresh}
      />
      <RawDiagnosticWorkbench
        id={`upload-diagnostic:${detail.session.session_id ?? detail.session.rollout_id}`}
        title="Raw 诊断"
        description={
          problemDataContext
            ? "问题数据 / 自动质检异常 / Raw 证据诊断"
            : "采集记录 / 数据包诊断 / 自动质检证据"
        }
        clock={clock}
        manifest={manifest}
        mediaStreamsByTopic={mediaStreamsByTopic}
        collectionItems={[
          {
            id: detail.session.data_package_id,
            label: detail.session.data_package_id,
            description: `机器人 ${detail.manifest.identifiers.robot_id}`,
            status: `自动质检 ${detail.quality.status}`,
            statusTone: detail.quality.status === "RISK" ? "warning" : "error",
            facts: [
              {
                label: "开始",
                value: formatDate(detail.manifest.time_range.start_time),
              },
              {
                label: "结束",
                value: formatDate(detail.manifest.time_range.end_time),
              },
              {
                label: "时长",
                value: formatEffectiveDuration(detail.quality.duration_ns),
              },
              { label: "相机", value: `${manifest.cameras.length} 路` },
              { label: "来源", value: "数据清单" },
            ],
          },
        ]}
        selectedCollectionItemId={detail.session.data_package_id}
        findings={findings}
        signalTracks={tracks}
        commands={{
          preserveEvidence: {
            invoke:
              typeof navigator !== "undefined" && navigator.clipboard
                ? () => navigator.clipboard.writeText(window.location.href)
                : undefined,
            disabledReason:
              typeof navigator !== "undefined" && navigator.clipboard
                ? undefined
                : "当前浏览器不允许复制页面证据链接。",
          },
        }}
      />
    </>
  );
}

function ManifestDiscoveryPanel({
  manifest,
}: {
  readonly manifest: FormalUploadDetail["manifest"];
}) {
  const discovery = manifest.discovery;
  return (
    <section
      className={styles.factPanel}
      aria-labelledby="manifest-facts-title"
    >
      <header>
        <Typography.Title id="manifest-facts-title" level={2}>
          数据清单发现
        </Typography.Title>
        <span>只读</span>
      </header>
      {discovery.cameras.length ? (
        <ul className={styles.discoveryList}>
          {discovery.cameras.map((camera) => (
            <li key={`${camera.camera_id}:${camera.topic}`}>
              <strong>{camera.camera_id}</strong>
              <code title={camera.topic}>{camera.topic}</code>
              <span>{camera.encoding ?? "编码未声明"}</span>
            </li>
          ))}
        </ul>
      ) : (
        <PageState
          state="empty"
          title="数据清单未声明相机"
          description="这是有效的 0 相机发现结果；Topic 清单仍按原始事实保留。"
        />
      )}
      <details className={styles.topicDetails}>
        <summary>查看 {discovery.topics.length} 个 Topic</summary>
        <ul>
          {discovery.topics.map((topic) => (
            <li key={topic.name}>
              <code>{topic.name}</code>
              <span>{topic.required ? "必需" : "可选"}</span>
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}

function PassedUploadDetail({
  detail,
  rawMedia,
  scope,
  workflowPreview,
}: {
  readonly detail: ResolvedFormalUploadDetail;
  readonly scope: NonNullable<ReturnType<typeof useIngestScope>>;
  readonly workflowPreview: WorkflowPreviewView;
  readonly rawMedia: {
    readonly source: FormalRawMediaSource | undefined;
    readonly pending: boolean;
    readonly error: unknown;
    readonly onRefresh: () => void;
  };
}) {
  const discovery = detail.manifest.discovery;
  return (
    <section className={styles.passedPage}>
      <UiPageHeader
        title="上传详情"
        description="数据清单与自动质检结果均来自当前项目和区域的正式运行时接口。"
        breadcrumbs={[
          { key: "uploads", label: "上传记录", to: uploadRecordsPath },
          { key: "detail", label: detail.session.data_package_id },
        ]}
        metadata={
          <Space wrap>
            <StatusTag status={detail.session.status} tone="info" />
            <StatusTag
              status={detail.quality.status}
              label="自动质检通过"
              tone="success"
            />
          </Space>
        }
      />
      <div className={styles.metrics}>
        <UiMetricCard
          label="数据包大小"
          icon={<FileJson2 />}
          value={formatStorageSize(detail.manifest.total_file_size)}
          description={detail.session.data_package_id}
        />
        <UiMetricCard
          label="相机"
          icon={<Camera />}
          value={discovery.cameras.length}
          unit="路"
          description="由数据清单自动发现"
        />
        <UiMetricCard
          label="Topic"
          icon={<RadioTower />}
          value={discovery.topics.length}
          unit="个"
          description="只读采集事实"
        />
        <UiMetricCard
          label="记录时长"
          icon={<Clock3 />}
          value={formatEffectiveDuration(detail.quality.duration_ns)}
          description={`${formatDate(detail.manifest.time_range.start_time)} 开始`}
        />
      </div>
      <RawMediaEvidence {...rawMedia} />
      <UploadDiagnostic
        detail={detail}
        rawSourceAvailable={Boolean(rawMedia.source)}
        scope={scope}
        workflowPreview={workflowPreview}
        showContextBar={false}
      />
      <div className={styles.passedGrid}>
        <ManifestDiscoveryPanel manifest={detail.manifest} />
        <section
          className={styles.qualityPanel}
          aria-labelledby="quality-result-title"
        >
          <Typography.Title id="quality-result-title" level={2}>
            自动质检
          </Typography.Title>
          <PageState
            state="empty"
            title="未发现需要诊断的异常"
            description="服务端自动质检结果为 PASS；本页不会提供人工改写结论的入口。"
          />
        </section>
      </div>
    </section>
  );
}

function PendingQualityUploadDetail({
  detail,
  rawMedia,
  workflowPreview,
  onRefreshQuality,
}: {
  readonly detail: FormalUploadDetail;
  readonly workflowPreview: WorkflowPreviewView;
  readonly onRefreshQuality: () => void;
  readonly rawMedia: {
    readonly source: FormalRawMediaSource | undefined;
    readonly pending: boolean;
    readonly error: unknown;
    readonly onRefresh: () => void;
  };
}) {
  const discovery = detail.manifest.discovery;
  return (
    <section className={styles.passedPage}>
      <UiPageHeader
        title="上传详情"
        description="上传内容已接收，后台摄取和自动质检仍在进行。"
        breadcrumbs={[
          { key: "uploads", label: "上传记录", to: uploadRecordsPath },
          { key: "detail", label: detail.session.data_package_id },
        ]}
        metadata={
          <Space wrap>
            <StatusTag status={detail.session.status} tone="info" />
            <StatusTag status="PENDING" label="自动质检处理中" tone="info" />
          </Space>
        }
      />
      <div className={styles.metrics}>
        <UiMetricCard
          label="数据包大小"
          icon={<FileJson2 />}
          value={formatStorageSize(detail.manifest.total_file_size)}
          description={detail.session.data_package_id}
        />
        <UiMetricCard
          label="相机"
          icon={<Camera />}
          value={discovery.cameras.length}
          unit="路"
          description="由数据清单自动发现"
        />
        <UiMetricCard
          label="Topic"
          icon={<RadioTower />}
          value={discovery.topics.length}
          unit="个"
          description="只读采集事实"
        />
        <UiMetricCard
          label="记录开始"
          icon={<Clock3 />}
          value={formatDate(detail.manifest.time_range.start_time)}
          description="等待自动质检计算时长"
        />
      </div>
      <ProcessingPreviewState
        session={detail.session}
        workflow={workflowPreview.job}
        previewTarget={workflowPreview.target}
        viewerTarget={workflowPreview.viewer}
        pending={workflowPreview.pending}
        error={workflowPreview.error}
        onRefresh={workflowPreview.onRefresh}
      />
      <RawMediaEvidence {...rawMedia} />
      <div className={styles.passedGrid}>
        <ManifestDiscoveryPanel manifest={detail.manifest} />
        <section
          className={styles.qualityPanel}
          aria-labelledby="quality-result-title"
        >
          <Typography.Title id="quality-result-title" level={2}>
            自动质检
          </Typography.Title>
          <PageState
            state="empty"
            label="自动质检"
            title="自动质检报告尚未生成"
            description="上传详情已可查看；报告生成后本页会自动刷新。"
            onRetry={onRefreshQuality}
            retryLabel="刷新质检状态"
          />
        </section>
      </div>
    </section>
  );
}

export default function FormalUploadDetailPage() {
  const { uploadId = "" } = useParams();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const stableId =
    uploadId.trim() && !["latest", "current"].includes(uploadId)
      ? uploadId
      : null;
  const requestedReturnTo = safeReturnTo(searchParams.get("returnTo"));
  const problemDataReturnTo =
    requestedReturnTo &&
    new URL(requestedReturnTo, "https://application.invalid").pathname ===
      problemDataPath
      ? requestedReturnTo
      : problemDataPath;
  const problemDataContext =
    location.pathname.startsWith(`${problemDataPath}/raw-diagnostic/`) ||
    requestedReturnTo === problemDataReturnTo;
  const scopeSnapshot = useIngestScope();
  const scope = useMemo(
    () => (scopeSnapshot ? { ...scopeSnapshot } : null),
    [
      scopeSnapshot?.organizationId,
      scopeSnapshot?.projectId,
      scopeSnapshot?.regionCode,
    ],
  );
  const capabilities = useCapabilities();
  const detail = useQuery<FormalUploadDetail>({
    queryKey: [
      "p04-formal-upload-detail",
      scope?.projectId,
      scope?.regionCode,
      stableId,
    ],
    enabled: Boolean(scope && stableId && capabilities.has("upload.read")),
    staleTime: 10_000,
    refetchInterval: (query) =>
      query.state.data?.quality === null ? 3_000 : false,
    queryFn: ({ signal }) => {
      if (!scope || !stableId) throw new Error("UPLOAD_SCOPE_UNAVAILABLE");
      return loadFormalUploadDetail(scope, stableId, signal);
    },
  });
  const rawMediaSessionId = detail.data?.session.session_id;
  const rawMedia = useQuery({
    queryKey: [
      "p04-raw-media-source",
      scope?.projectId,
      scope?.regionCode,
      rawMediaSessionId,
    ],
    enabled:
      Boolean(scope) &&
      detail.data?.session.status === "RAW_COMMITTED" &&
      Boolean(rawMediaSessionId),
    staleTime: 0,
    queryFn: ({ signal }) => {
      if (!scope || !rawMediaSessionId) {
        throw new Error("UPLOAD_RAW_MEDIA_SCOPE_OR_SESSION_UNAVAILABLE");
      }
      return getFormalUploadRawMedia(scope, rawMediaSessionId, signal);
    },
  });
  const workflowSession = detail.data?.session;
  const workflowId = workflowSession?.workflow?.workflow_id;
  const workflow = useQuery({
    queryKey: [
      "p04-ingest-workflow",
      scope?.organizationId,
      scope?.projectId,
      scope?.regionCode,
      workflowId,
    ],
    enabled: Boolean(scope && workflowSession && workflowId),
    staleTime: 0,
    queryFn: async ({ signal }) => {
      if (!scope || !workflowSession || !workflowId) {
        throw new Error("UPLOAD_WORKFLOW_SCOPE_OR_SESSION_UNAVAILABLE");
      }
      const job = await getFormalUploadProcessingStatus(
        scope,
        workflowSession,
        signal,
      );
      return {
        job,
        target: resolveFormalUploadPreviewTarget(workflowSession, job),
        viewer: resolveFormalUploadViewerTarget(workflowSession, job),
      };
    },
    refetchInterval: (query) => {
      const status = query.state.data?.job.status;
      return status === "PENDING" || status === "RUNNING" ? 1_000 : false;
    },
  });

  if (!stableId) {
    return (
      <main className={styles.statePage}>
        <PageState
          state="not-found"
          label="上传详情"
          description="请从上传记录打开一个确定的会话 ID。"
          action={<Button href={uploadRecordsPath}>返回上传记录</Button>}
        />
      </main>
    );
  }
  if (!scope) {
    return (
      <main className={styles.statePage}>
        <PageState
          state="feature-unavailable"
          label="上传详情"
          description="请先在顶部选择项目和区域。"
        />
      </main>
    );
  }
  if (capabilities.loading || detail.isPending) {
    return (
      <main className={styles.statePage}>
        <PageState state="loading" label="上传详情" layout="workbench" />
      </main>
    );
  }
  if (!capabilities.has("upload.read")) {
    return (
      <main className={styles.statePage}>
        <PageState
          state="forbidden"
          label="上传详情"
          description="当前会话没有读取上传记录的 capability。"
        />
      </main>
    );
  }
  if (detail.isError) {
    const error = detail.error;
    return (
      <main className={styles.statePage}>
        <PageState
          state={pageStateFromError(error)}
          label="上传详情"
          description={problemDescription(error)}
          requestId={isDomainError(error) ? error.requestId : null}
          onRetry={() => void detail.refetch()}
          retryLabel={
            isDomainError(error) && error.retryable
              ? "按服务端提示重试"
              : "重新加载"
          }
          action={<Button href={uploadRecordsPath}>返回上传记录</Button>}
        />
      </main>
    );
  }

  const workflowPreview: WorkflowPreviewView = {
    job: workflow.data?.job,
    target: workflow.data?.target,
    viewer: workflow.data?.viewer,
    pending: Boolean(workflowId) && workflow.isPending,
    error: workflow.error,
    onRefresh: () => void workflow.refetch(),
  };
  const rawMediaView = {
    source: rawMedia.data,
    pending: rawMedia.isPending,
    error: rawMedia.error,
    onRefresh: () => void rawMedia.refetch(),
  };

  if (detail.data.quality === null) {
    return (
      <main className={styles.page}>
        <PendingQualityUploadDetail
          detail={detail.data}
          workflowPreview={workflowPreview}
          rawMedia={rawMediaView}
          onRefreshQuality={() => void detail.refetch()}
        />
      </main>
    );
  }

  const resolvedDetail: ResolvedFormalUploadDetail = {
    ...detail.data,
    quality: detail.data.quality,
  };

  return (
    <main className={styles.page}>
      {resolvedDetail.quality.status === "PASS" ? (
        <PassedUploadDetail
          detail={resolvedDetail}
          scope={scope}
          workflowPreview={workflowPreview}
          rawMedia={rawMediaView}
        />
      ) : (
        <>
          <RawMediaEvidence
            source={rawMedia.data}
            pending={rawMedia.isPending}
            error={rawMedia.error}
            onRefresh={() => void rawMedia.refetch()}
          />
          <UploadDiagnostic
            detail={resolvedDetail}
            rawSourceAvailable={Boolean(rawMedia.data)}
            scope={scope}
            workflowPreview={workflowPreview}
            returnTo={problemDataReturnTo}
            problemDataContext={problemDataContext}
          />
        </>
      )}
    </main>
  );
}

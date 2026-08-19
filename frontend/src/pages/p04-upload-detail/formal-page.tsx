import { useQuery } from "@tanstack/react-query";
import { Button, Space, Typography } from "antd";
import {
  ArrowLeft,
  Camera,
  Clock3,
  FileJson2,
  RadioTower,
} from "lucide-react";
import { useEffect, useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { useIngestScope } from "../../features/ingest/use-ingest-scope";
import {
  createPlaybackClock,
  RawDiagnosticWorkbench,
  type ViewerTimelineTrack,
  type WorkbenchFinding,
} from "../../features/viewer";
import type { RuntimeManifestDiscoveryProjection } from "../../features/viewer/raw-diagnostic-adapter";
import { isDomainError } from "../../shared/api/domain-error";
import { useCapabilities } from "../../shared/auth/use-capabilities";
import {
  PageState,
  StatusTag,
  UiMetricCard,
  UiPageHeader,
  type PageStateKind,
} from "../../shared/ui";
import {
  loadFormalUploadDetail,
  type FormalQcReport,
  type FormalUploadDetail,
} from "./formal-detail-client";
import styles from "./formal-page.module.css";

const uploadRecordsPath = "/ingest/uploads/records";

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

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"] as const;
  let amount = value;
  let unitIndex = 0;
  while (amount >= 1024 && unitIndex < units.length - 1) {
    amount /= 1024;
    unitIndex += 1;
  }
  return `${new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: unitIndex === 0 ? 0 : 2,
  }).format(amount)} ${units[unitIndex]}`;
}

function formatDuration(durationNs: number): string {
  return `${new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: 3,
  }).format(durationNs / 1_000_000_000)} 秒`;
}

function statusTone(status: FormalQcReport["status"]):
  | "success"
  | "warning"
  | "danger" {
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

function UploadDiagnostic({ detail }: { readonly detail: FormalUploadDetail }) {
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

  return (
    <>
      <div className={styles.contextBar}>
        <Link to={uploadRecordsPath}>
          <ArrowLeft aria-hidden="true" size={16} />
          返回上传记录
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
      <RawDiagnosticWorkbench
        id={`upload-diagnostic:${detail.session.session_id ?? detail.session.rollout_id}`}
        title="Raw 诊断"
        description="采集记录 / 数据包诊断 / 自动质检证据"
        clock={clock}
        manifest={manifest}
        mediaStreamsByTopic={{}}
        collectionItems={[
          {
            id: detail.session.data_package_id,
            label: detail.session.data_package_id,
            description: `机器人 ${detail.manifest.identifiers.robot_id}`,
            status: `自动质检 ${detail.quality.status}`,
            statusTone:
              detail.quality.status === "RISK" ? "warning" : "error",
            facts: [
              { label: "开始", value: formatDate(detail.manifest.time_range.start_time) },
              { label: "结束", value: formatDate(detail.manifest.time_range.end_time) },
              { label: "时长", value: formatDuration(detail.quality.duration_ns) },
              { label: "相机", value: `${manifest.cameras.length} 路` },
              { label: "来源", value: "Manifest" },
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

function PassedUploadDetail({ detail }: { readonly detail: FormalUploadDetail }) {
  const discovery = detail.manifest.discovery;
  return (
    <section className={styles.passedPage}>
      <UiPageHeader
        title="上传详情"
        description="Manifest 与自动质检结果均来自当前项目和区域的正式运行时接口。"
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
          value={formatBytes(detail.manifest.total_file_size)}
          description={detail.session.data_package_id}
        />
        <UiMetricCard
          label="相机"
          icon={<Camera />}
          value={discovery.cameras.length}
          unit="路"
          description="由 Manifest 自动发现"
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
          value={formatDuration(detail.quality.duration_ns)}
          description={`${formatDate(detail.manifest.time_range.start_time)} 开始`}
        />
      </div>
      <div className={styles.passedGrid}>
        <section className={styles.factPanel} aria-labelledby="manifest-facts-title">
          <header>
            <Typography.Title id="manifest-facts-title" level={2}>
              Manifest 发现
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
              title="Manifest 未声明相机"
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
        <section className={styles.qualityPanel} aria-labelledby="quality-result-title">
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

export default function FormalUploadDetailPage() {
  const { uploadId = "" } = useParams();
  const stableId =
    uploadId.trim() && !["latest", "current"].includes(uploadId)
      ? uploadId
      : null;
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
  const detail = useQuery({
    queryKey: [
      "p04-formal-upload-detail",
      scope?.projectId,
      scope?.regionCode,
      stableId,
    ],
    enabled: Boolean(scope && stableId && capabilities.has("upload.read")),
    staleTime: 10_000,
    queryFn: ({ signal }) => {
      if (!scope || !stableId) throw new Error("UPLOAD_SCOPE_UNAVAILABLE");
      return loadFormalUploadDetail(scope, stableId, signal);
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
            isDomainError(error) && error.retryable ? "按服务端提示重试" : "重新加载"
          }
          action={<Button href={uploadRecordsPath}>返回上传记录</Button>}
        />
      </main>
    );
  }

  return (
    <main className={styles.page}>
      {detail.data.quality.status === "PASS" ? (
        <PassedUploadDetail detail={detail.data} />
      ) : (
        <UploadDiagnostic detail={detail.data} />
      )}
    </main>
  );
}

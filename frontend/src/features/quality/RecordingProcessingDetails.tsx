import { recordingPreviewPath } from "../../shared/routing/navigation-routes";
import { captureProblemLabels } from "../capture-schemes/presentation";
import { Alert, Card, Space, Spin, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { request } from "../../shared/api/http-client";
import type { components } from "../../shared/api/generated/platform";
import type { CollectionTaskScope } from "../collection-tasks/api";
import { QualityEvidenceDetails } from "./QualityEvidenceDetails";
import { collectionTaskGateway } from "../collection-tasks/api";
import { readQualityRecording } from "./recording-quality-api";
import { RecordingQualityReview } from "./RecordingQualityReview";
import { RecordingEpisodeActions } from "./RecordingEpisodeActions";

function seconds(value: string) {
  const number = Number(value) / 1e9;
  return Number.isFinite(number) ? number.toFixed(3) : "未提供";
}

const dispatchErrors: Record<string, string> = {
  ...captureProblemLabels,
  RECORDING_DATASET_SCHEMA_MISMATCH:
    "本次采集的相机 / 深度通道与当前数据集已固定的结构不同，无法加入同一个数据集。请使用通道配置匹配的采集任务；原始录制与切片已保留。",
  INGEST_WORKFLOW_PLAN_BLOCKED:
    "处理所需的质检或数据集配置未满足，任务尚未开始质检。",
};
const labels: Record<string, string> = {
  PENDING_QC: "等待质检",
  QC_RUNNING: "质检中",
  QC_FAILED: "质检未通过",
  PENDING_ALIGNMENT: "等待对齐入库",
  ALIGNING: "正在对齐入库",
  READY: "已入库",
  FAILED: "处理失败",
  DISCARDED: "已删除",
};
/** Shared by upload records, dashboard issues and the problem-data center. */
export function RecordingProcessingDetails({
  scope,
  recordingId,
  episodeId,
  showActions = true,
  diagnosticCode,
  reportSha256,
}: {
  readonly scope: CollectionTaskScope;
  readonly recordingId: string;
  readonly episodeId?: string;
  readonly showActions?: boolean;
  readonly diagnosticCode?: string;
  readonly reportSha256?: string;
}) {
  const diagnosticUrl = (episode?: string) => {
    const url = new URLSearchParams({ view: "preview" });
    if (episode) url.set("episode", episode);
    if (reportSha256 && diagnosticCode) {
      url.set("report", reportSha256);
      url.set("finding", diagnosticCode);
    }
    return `/recordings/${encodeURIComponent(recordingId)}/slice?${url}`;
  };
  const query = useQuery({
    queryKey: [
      "continuous-recording-processing",
      `${scope.organizationId}:${scope.projectId}:${scope.regionCode}`,
      recordingId,
    ],
    queryFn: ({ signal }) =>
      request<components["schemas"]["EpisodeProcessingPage"]>({
        method: "GET",
        scope,
        signal,
        path: `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/continuous-recordings/${encodeURIComponent(recordingId)}/episodes`,
      }),
    refetchInterval: 5000,
  });
  const source = useQuery({
    queryKey: ["qc-recording-source", scope, recordingId],
    queryFn: ({ signal }) => readQualityRecording(scope, recordingId, signal),
    retry: false,
  });
  const recording = source.data?.data;
  const taskId = recording?.collection_task_id;
  const task = useQuery({
    queryKey: ["qc-recording-task", scope, taskId],
    queryFn: ({ signal }) =>
      collectionTaskGateway.detail(scope, taskId!, signal),
    enabled: Boolean(taskId),
    retry: false,
  });
  const episodes = query.data?.items.filter(
    (item) => !episodeId || item.episode_id === episodeId,
  ) ?? [];
  return (
    <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
      {source.data && !episodeId && <RecordingQualityReview key={recordingId + recording?.quality_summary?.report_id} scope={scope} detail={source.data} actions={showActions} />}
      {query.isPending && <Spin />}
      {query.isError && <Alert type="error" title={query.error.message} />}
      <Card size="small" title="数据来源">
        <Typography.Paragraph>
          <strong>采集任务：</strong>
          {task.data
            ? `${task.data.task.task_code} · ${task.data.task.name}`
            : (taskId ?? (source.isPending ? "正在读取…" : "来源读取失败"))}
          {task.isError && "（任务名称读取失败，已保留任务 ID）"}
        </Typography.Paragraph>
        {recording?.source_file_name && (
          <p>
            <strong>原始文件：</strong>
            {recording.source_file_name}
          </p>
        )}
        {recording && (
          <Typography.Paragraph>
            <strong>录制：</strong>
            {new Date(recording.capture_started_at).toLocaleString("zh-CN", {
              hour12: false,
            })}
            {" · "}
            {recording.device_id}
          </Typography.Paragraph>
        )}
        <Typography.Paragraph style={{ overflowWrap: "anywhere" }}>
          <strong>录制 ID：</strong>
          <Typography.Text copyable>{recordingId}</Typography.Text>
        </Typography.Paragraph>
        {source.isError && (
          <Typography.Paragraph type="danger">
            录制来源读取失败，仍可通过录制 ID 追溯。
          </Typography.Paragraph>
        )}
        <Link
          to={
            reportSha256
              ? diagnosticUrl(episodeId)
              : recordingPreviewPath(recordingId)
          }
        >
          {reportSha256 ? "可视化定位问题" : "查看原始录制与文件"}
        </Link>
      </Card>
      {query.isSuccess && episodes.length === 0 && recording?.status !== "QC_FAILED" && (
        <Alert
          type="info"
          title={
            "尚未生成 Episode"
          }
          description={
            recording?.ingest_mode === "WHOLE_RECORDING"
                ? "上传完成后自动检查并整段入库，完成后即可切片与标注。"
                : "上传完成后自动检查整段录制，通过质检后即可切片，提交后自动对齐入库。"
          }
        />
      )}
      {episodes.map((episode) => {
        const qc = query.data?.qc_reports.find(
          (item) => item.episode_id === episode.episode_id,
        );
        const status = episode.status === "READY" && qc?.status === "REJECT" ? "QC_FAILED" : episode.status;
        const slice = source.data?.current_slice_revision?.slices.find(
          (item) => item.episode_id === episode.episode_id,
        );
        return (
          <Card
            key={episode.episode_id}
            size="small"
            title={slice?.title || episode.episode_id}
            extra={
              <Tag
                color={
                  status === "READY"
                    ? "success"
                    : status === "DISCARDED"
                      ? "default"
                      : ["FAILED", "QC_FAILED"].includes(status)
                        ? "error"
                        : "processing"
                }
              >
                {status !== "DISCARDED" &&
                status !== "QC_FAILED" &&
                episode.dispatch_error_code
                  ? "等待处理配置"
                  : labels[status]}
              </Tag>
            }
          >
            <Typography.Paragraph style={{ overflowWrap: "anywhere" }}>
              <strong>
                {recording?.ingest_mode === "WHOLE_RECORDING"
                  ? "完整录制："
                  : "切片："}
              </strong>
              {episode.episode_id}
              {" · "}
              {seconds(episode.start_offset_ns)}～
              {seconds(episode.end_offset_ns)} 秒
            </Typography.Paragraph>
            {status !== "DISCARDED" &&
              status !== "QC_FAILED" &&
              episode.dispatch_error_code && (
                <Alert
                  type="error"
                  title="处理尚未启动"
                  description={
                    dispatchErrors[episode.dispatch_error_code] ??
                    episode.dispatch_error_code
                  }
                />
              )}
            {status !== "DISCARDED" &&
              episode.failure_code &&
              !(episode.failure_code === "QC_REJECTED" && qc) && (
                <Alert
                  type="warning"
                  title={labels[status]}
                  description={episode.failure_code}
                />
              )}
            {qc && status !== "DISCARDED" && (
              <QualityEvidenceDetails
                rules={qc.rules}
                findingCodes={qc.finding_codes}
                findings={qc.quality_report.findings}
                verdict={qc.status}
              />
            )}
            <Space wrap>
              <Link
                to={
                  reportSha256
                    ? diagnosticUrl(episode.episode_id)
                    : `/recordings/${encodeURIComponent(recordingId)}/slice?episode=${encodeURIComponent(episode.episode_id)}`
                }
              >
                {recording?.video_asset_count === 0
                  ? "查看关节片段"
                  : "查看片段视频"}
              </Link>
              {status === "READY" && episode.dataset_id && (
                <Link
                  to={`/datasets/${encodeURIComponent(episode.dataset_id)}`}
                >
                  查看已入库数据
                </Link>
              )}
            </Space>
            {showActions &&
              (status !== "QC_FAILED" || episode.status === "QC_FAILED") && (
                <RecordingEpisodeActions scope={scope} episode={episode} />
              )}
          </Card>
        );
      })}
    </Space>
  );
}

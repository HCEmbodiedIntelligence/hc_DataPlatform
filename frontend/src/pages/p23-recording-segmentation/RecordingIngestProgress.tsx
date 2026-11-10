import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Space } from "antd";
import { Link, Navigate } from "react-router-dom";
import { annotationRoutes } from "../../features/annotation/routing";
import { QualityEvidenceDetails } from "../../features/quality/QualityEvidenceDetails";
import { RecordingEpisodeActions } from "../../features/quality/RecordingEpisodeActions";
import { PageState } from "../../shared/ui/state/PageState";
import type { ContinuousRecording, RecordingGateway, RecordingScope } from "./api";
import { recordingQualityLabel, wholeRecordingLabel } from "./quality";

type Props = {
  recording: ContinuousRecording;
  scope: RecordingScope;
  gateway: RecordingGateway;
};

export function progressLocation(recording: ContinuousRecording) {
  const stage = recordingQualityLabel(recording) ? "QUALITY"
    : recording.whole_episode_status === "READY" ? "PENDING" : "INGESTING";
  return `/recordings?${new URLSearchParams({ stage, progress: recording.recording_id })}`;
}

function useEpisode({ recording, scope, gateway }: Props) {
  const result = useQuery({
    queryKey: ["continuous-recording-processing",
      `${scope.organizationId}:${scope.projectId}:${scope.regionCode}`, recording.recording_id],
    queryFn: ({ signal }) => gateway.processing(scope, recording.recording_id, signal),
    refetchInterval: 5000,
    retry: false,
  });
  const episode = result.data?.items.find(item => item.episode_id === "episode_full_recording");
  return { result, episode };
}

export function RecordingSliceTarget(props: Props) {
  const { result, episode } = useEpisode(props);
  if (result.isPending) {
    return <PageState state="loading" layout="workbench" label="切片工作台" />;
  }
  if (!result.isError && episode?.status === "READY" && episode.annotation_task_id) {
    return <Navigate replace to={annotationRoutes.task.build({ taskId: episode.annotation_task_id })} />;
  }
  return <Navigate replace to={progressLocation(props.recording)} />;
}

export function RecordingIngestProgress({ canManage, ...props }: Props & { canManage: boolean }) {
  const { recording, scope, gateway } = props;
  const { result, episode } = useEpisode(props);
  const client = useQueryClient();
  const retry = useMutation({
    mutationFn: () => gateway.retryQuality!(scope, recording.recording_id, crypto.randomUUID()),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["continuous-recordings"] });
      void client.invalidateQueries({ queryKey: ["continuous-recording"] });
      void client.invalidateQueries({ queryKey: ["continuous-recording-processing"] });
    },
  });
  const qc = result.data?.qc_reports.find(item => item.episode_id === episode?.episode_id);
  const failedQuality = ["QC_FAILED", "QC_ERROR"].includes(recording.status);
  const ready = !result.isError && episode?.status === "READY";
  return (
    <Space orientation="vertical" size="middle">
      <strong>{ready && !episode.annotation_task_id ? "数据已入库，标注任务待准备"
        : wholeRecordingLabel({ ...recording, whole_episode_status: episode?.status ?? recording.whole_episode_status })}</strong>
      {recording.quality_error_code && <span role="alert">{recording.quality_error_code}</span>}
      {recording.quality_summary?.finding_codes.join("、")}
      {failedQuality && canManage && gateway.retryQuality && (
        <Button loading={retry.isPending} onClick={() => retry.mutate()}>重新质检并入库</Button>
      )}
      {retry.isError && <Alert type="error" title="重新质检失败" description={retry.error.message} />}
      {result.isError && <Alert type="error" title="入库进度读取失败" description={result.error.message}
        action={<Button onClick={() => void result.refetch()}>重试</Button>} />}
      {episode?.failure_code && <span role="alert">{episode.failure_code}</span>}
      {episode && <RecordingEpisodeActions scope={scope} episode={episode} />}
      {qc && <QualityEvidenceDetails rules={qc.rules} findingCodes={qc.finding_codes}
        findings={qc.quality_report.findings} verdict={qc.status} />}
      {(failedQuality || episode?.status === "FAILED" || episode?.status === "QC_FAILED" ||
        (ready && !episode.annotation_task_id)) && <Link to="/collection-tasks">检查采集任务配置</Link>}
      {ready && episode.annotation_task_id && <Link to={annotationRoutes.task.build({ taskId: episode.annotation_task_id })}>进入切片与标注</Link>}
      {ready && episode.dataset_id && <Link to={`/datasets/${encodeURIComponent(episode.dataset_id)}`}>查看已入库数据</Link>}
    </Space>
  );
}

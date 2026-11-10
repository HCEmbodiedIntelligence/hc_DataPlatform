import type { ContinuousRecording } from "./api";

export function recordingQualityLabel(
  recording: ContinuousRecording,
): string | null {
  switch (recording.status) {
    case "PENDING_QC":
      return "等待整段质检";
    case "QC_RUNNING":
      return "整段质检中";
    case "QC_FAILED":
      return "整段质检未通过";
    case "QC_ERROR":
      return "质检执行失败";
    default:
      return null;
  }
}

export function wholeRecordingLabel(
  recording: ContinuousRecording,
): string | null {
  if (recording.ingest_mode !== "WHOLE_RECORDING") return null;
  if (recordingQualityLabel(recording)) return recordingQualityLabel(recording);
  const labels: Record<string, string> = {
    PENDING_QC: "等待整段入库",
    QC_RUNNING: "入库质检中",
    QC_FAILED: "入库质检未通过",
    PENDING_ALIGNMENT: "等待对齐入库",
    ALIGNING: "正在对齐入库",
    READY: "已入库 · 可切片标注",
    FAILED: "整段入库失败",
    DISCARDED: "已退出入库",
  };
  return labels[recording.whole_episode_status ?? ""] ?? "等待整段入库";
}

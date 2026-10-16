import type { ContinuousRecording } from "./api";

export function recordingTaskNumber(recording: ContinuousRecording): string {
  const code = recording.task_code;
  return code && /^\d+$/u.test(code) ? code.replace(/^0+(?=\d)/u, "") : "—";
}

export function recordingRobotName(recording: ContinuousRecording): string {
  if (recording.robot_name) return recording.robot_name;
  const device = recording.device_id;
  return device && !/[0-9a-f]{24,}|[0-9a-f]{8}-[0-9a-f-]{27}/iu.test(device)
    ? device
    : "未命名机器人";
}

export function recordingFileName(recording: ContinuousRecording): string {
  if (recording.file_name) return recording.file_name;
  if (recording.source_file_name) return recording.source_file_name;
  const timestamp = Date.parse(recording.capture_started_at);
  if (!Number.isFinite(timestamp)) return "未命名录制";
  const local = new Date(timestamp + 8 * 60 * 60 * 1_000).toISOString();
  const task = recordingTaskNumber(recording);
  return `${task === "—" ? "recording" : `T${task}`}_${local.slice(0, 10).replaceAll("-", "")}_${local.slice(11, 19).replaceAll(":", "")}_${local.slice(20, 23)}`;
}

export function recordingSearchValues(
  recording: ContinuousRecording,
): string[] {
  return [
    recordingFileName(recording),
    recordingRobotName(recording),
    recordingTaskNumber(recording),
    recording.source_file_name ?? "",
    recording.task_code ?? "",
    recording.recording_id,
    recording.rollout_id,
    recording.robot_id,
    recording.collection_task_id,
    recording.data_package_id,
  ];
}

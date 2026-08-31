import type { components } from "../../shared/api/generated/platform";

export type EpisodeSlice = components["schemas"]["EpisodeSlice"];
export type EpisodeSliceInput = components["schemas"]["EpisodeSliceInput"];

export interface EditableSlice {
  readonly episodeId: string;
  readonly startNs: number;
  readonly endNs: number;
  readonly title: string;
  readonly taskLabel: string;
  readonly notes: string;
}

const nanosecondsPerSecond = 1_000_000_000;

export function nsToSeconds(value: number): number {
  return value / nanosecondsPerSecond;
}

export function secondsToNs(value: number): number {
  return Math.round(value * nanosecondsPerSecond);
}

export function parseNanoseconds(value: string): number {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 0) {
    throw new Error("录制时长超出浏览器安全编辑范围");
  }
  return parsed;
}

export function formatTimecode(
  valueNs: number,
  showMilliseconds = true,
): string {
  const totalMilliseconds = Math.max(0, Math.round(valueNs / 1_000_000));
  const milliseconds = totalMilliseconds % 1000;
  const totalSeconds = Math.floor(totalMilliseconds / 1000);
  const seconds = totalSeconds % 60;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const minutes = totalMinutes % 60;
  const hours = Math.floor(totalMinutes / 60);
  const base = [hours, minutes, seconds]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
  return showMilliseconds
    ? `${base}.${String(milliseconds).padStart(3, "0")}`
    : base;
}

export function formatDuration(valueNs: number): string {
  const totalSeconds = Math.max(0, Math.round(valueNs / nanosecondsPerSecond));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  if (minutes > 0) return `${minutes} 分 ${seconds} 秒`;
  return `${seconds} 秒`;
}

export function fromServerSlices(
  slices: readonly EpisodeSlice[],
): EditableSlice[] {
  return slices.map((slice) => ({
    episodeId: slice.episode_id,
    startNs: parseNanoseconds(slice.start_offset_ns),
    endNs: parseNanoseconds(slice.end_offset_ns),
    title: slice.title ?? "",
    taskLabel: slice.task_label ?? "",
    notes: slice.notes ?? "",
  }));
}

export function toSliceInputs(
  slices: readonly EditableSlice[],
): EpisodeSliceInput[] {
  return [...slices]
    .sort((left, right) => left.startNs - right.startNs)
    .map((slice) => ({
      episode_id: slice.episodeId,
      start_offset_ns: String(slice.startNs),
      end_offset_ns: String(slice.endNs),
      ...(slice.title.trim() ? { title: slice.title.trim() } : {}),
      ...(slice.taskLabel.trim() ? { task_label: slice.taskLabel.trim() } : {}),
      ...(slice.notes.trim() ? { notes: slice.notes.trim() } : {}),
    }));
}

export function nextEpisodeId(slices: readonly EditableSlice[]): string {
  const used = new Set(slices.map((slice) => slice.episodeId));
  for (let ordinal = 1; ordinal <= 10_000; ordinal += 1) {
    const candidate = `episode_${String(ordinal).padStart(4, "0")}`;
    if (!used.has(candidate)) return candidate;
  }
  return `episode_${Date.now().toString(36)}`;
}

export interface SliceValidationIssue {
  readonly code: "EMPTY" | "OUT_OF_RANGE" | "OVERLAP" | "TOO_SHORT";
  readonly episodeId: string;
  readonly message: string;
}

export function validateSlices(
  slices: readonly EditableSlice[],
  durationNs: number,
  minimumDurationNs = 1,
): SliceValidationIssue[] {
  const ordered = [...slices].sort(
    (left, right) => left.startNs - right.startNs,
  );
  const issues: SliceValidationIssue[] = [];
  for (let index = 0; index < ordered.length; index += 1) {
    const slice = ordered[index];
    if (!slice) continue;
    if (slice.endNs <= slice.startNs) {
      issues.push({
        code: "EMPTY",
        episodeId: slice.episodeId,
        message: "结束时间必须晚于开始时间",
      });
    } else if (slice.endNs - slice.startNs < minimumDurationNs) {
      issues.push({
        code: "TOO_SHORT",
        episodeId: slice.episodeId,
        message: "Episode 短于一帧",
      });
    }
    if (slice.startNs < 0 || slice.endNs > durationNs) {
      issues.push({
        code: "OUT_OF_RANGE",
        episodeId: slice.episodeId,
        message: "时间范围超出录制时长",
      });
    }
    const previous = ordered[index - 1];
    if (previous && slice.startNs < previous.endNs) {
      issues.push({
        code: "OVERLAP",
        episodeId: slice.episodeId,
        message: `与 ${previous.episodeId} 重叠`,
      });
    }
  }
  return issues;
}

export function clampBoundary(
  slices: readonly EditableSlice[],
  episodeId: string,
  edge: "start" | "end",
  requestedNs: number,
  durationNs: number,
  minimumDurationNs: number,
): EditableSlice[] {
  const ordered = [...slices].sort(
    (left, right) => left.startNs - right.startNs,
  );
  const index = ordered.findIndex((slice) => slice.episodeId === episodeId);
  const selected = ordered[index];
  if (!selected) return [...slices];
  const previousEnd = ordered[index - 1]?.endNs ?? 0;
  const nextStart = ordered[index + 1]?.startNs ?? durationNs;
  const updated =
    edge === "start"
      ? {
          ...selected,
          startNs: Math.min(
            selected.endNs - minimumDurationNs,
            Math.max(previousEnd, Math.round(requestedNs)),
          ),
        }
      : {
          ...selected,
          endNs: Math.max(
            selected.startNs + minimumDurationNs,
            Math.min(nextStart, durationNs, Math.round(requestedNs)),
          ),
        };
  return ordered.map((slice) =>
    slice.episodeId === episodeId ? updated : slice,
  );
}

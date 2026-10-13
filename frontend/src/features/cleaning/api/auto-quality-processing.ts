import type { IngestScope } from "../../../entities/data-source";
import type { components } from "../../../shared/api/generated/platform";
import { request } from "../../../shared/api/http-client";
import {
  nativeRoot,
  type NativeProgress,
} from "../../ingest/processing/lerobot-processing";
import type { AutoQualityProblem } from "./auto-quality-problems.queries";

type Episode = components["schemas"]["EpisodeProcessing"];
type EpisodePage = components["schemas"]["EpisodeProcessingPage"];
type Resolution = components["schemas"]["EpisodeResolution"];
export interface AutoQualityProcessing {
  status: string;
  episode_count: number;
  ready: number;
  failed: number;
  dataset_id: string | null;
  updated_at: string;
  targetLabel: string;
  retryable: boolean;
  workflowId?: string;
  unavailableReason?: string;
  quality?: { status: string; findings: { code: string; topic: string }[] };
}

function recordingRoot(scope: IngestScope, problem: AutoQualityProblem) {
  return `/projects/${encodeURIComponent(scope.projectId)}/regions/${encodeURIComponent(scope.regionCode)}/continuous-recordings/${encodeURIComponent(problem.recordingId!)}`;
}

export function episodeProgress(episode: Episode): AutoQualityProcessing {
  const status =
    episode.status === "READY"
      ? "SUCCEEDED"
      : episode.status === "DISCARDED"
        ? "DISCARDED"
        : episode.dispatch_error_code ||
            ["QC_FAILED", "FAILED"].includes(episode.status)
          ? "FAILED"
          : episode.status === "PENDING_QC"
            ? "PENDING"
            : "RUNNING";
  return {
    status,
    episode_count: 1,
    ready: status === "SUCCEEDED" ? 1 : 0,
    failed: status === "FAILED" ? 1 : 0,
    dataset_id: episode.dataset_id ?? null,
    updated_at: episode.updated_at ?? episode.created_at ?? "",
    targetLabel: "当前 Episode",
    retryable:
      ["QC_FAILED", "FAILED"].includes(episode.status) &&
      Boolean(episode.workflow_id),
    workflowId: episode.workflow_id ?? undefined,
    unavailableReason: episode.dispatch_error_code
      ? "处理配置尚未满足，请先修正配置。"
      : undefined,
  };
}

export async function getAutoQualityProcessing(
  scope: IngestScope,
  problem: AutoQualityProblem,
  signal?: AbortSignal,
): Promise<AutoQualityProcessing> {
  if (problem.recordingId) {
    const page = await request<EpisodePage>({
      method: "GET",
      scope,
      signal,
      path: `${recordingRoot(scope, problem)}/episodes`,
    });
    const episode = page.items.find(
      (item) => item.episode_id === problem.recordingEpisodeId,
    );
    if (!episode)
      throw new Error("当前 Episode 的处理记录不存在，请刷新问题列表。");
    const qc = page.qc_reports.find(
      (item) => item.episode_id === episode.episode_id,
    );
    const progress = episodeProgress(episode);
    if (qc)
      progress.quality = {
        status: qc.status,
        findings: [
          ...qc.quality_report.findings.map((f) => ({
            code: f.code,
            topic: f.topic,
          })),
          ...qc.finding_codes
            .filter(
              (code) =>
                !qc.quality_report.findings.some((f) => f.code === code),
            )
            .map((code) => ({ code, topic: "/recording" })),
        ],
      };
    return progress;
  }
  const progress = await request<NativeProgress>({
    method: "GET",
    scope,
    signal,
    path: `${nativeRoot(scope)}/${encodeURIComponent(problem.sourceImportId!)}/processing`,
  });
  return {
    ...progress,
    targetLabel: "当前上传批次",
    retryable: ["FAILED", "PARTIALLY_FAILED", "CANCELLED"].includes(
      progress.status,
    ),
  };
}

export async function retryAutoQualityProcessing(
  scope: IngestScope,
  problem: AutoQualityProblem,
  progress: AutoQualityProcessing,
  requestId: string,
): Promise<AutoQualityProcessing> {
  if (problem.recordingId) {
    const episode = await request<Episode>({
      method: "POST",
      scope,
      path: `${recordingRoot(scope, problem)}/episodes/${encodeURIComponent(problem.recordingEpisodeId!)}:retry`,
      body: {
        request_id: requestId,
        expected_workflow_id: progress.workflowId,
      },
    });
    return { ...episodeProgress(episode), quality: progress.quality };
  }
  const next = await request<NativeProgress>({
    method: "POST",
    scope,
    path: `${nativeRoot(scope)}/${encodeURIComponent(problem.sourceImportId!)}:retry`,
  });
  return { ...next, targetLabel: "当前上传批次", retryable: false };
}

export async function discardAutoQualityProblem(
  scope: IngestScope,
  problem: AutoQualityProblem,
  requestId: string,
) {
  if (problem.recordingId) {
    const progress = await getAutoQualityProcessing(scope, problem);
    if (progress.status === "DISCARDED") return;
    await request<Episode>({
      method: "POST",
      scope,
      path: `${recordingRoot(scope, problem)}/episodes/${encodeURIComponent(problem.recordingEpisodeId!)}:discard`,
      body: {
        request_id: requestId,
        expected_workflow_id: progress.workflowId,
      },
    });
    return;
  }
  const path = `${nativeRoot(scope)}/${encodeURIComponent(problem.sourceImportId!)}/episodes/${problem.sourceEpisodeIndex}/resolution`;
  const state = await request<Resolution>({ method: "GET", path, scope });
  if (state.status === "DISCARDED") return;
  if (!state.discard_available)
    throw new Error(
      state.discard_unavailable_reason ?? "当前数据不可删除，请刷新处理状态。",
    );
  await request<Resolution>({
    method: "POST",
    path,
    scope,
    body: {
      action: "DISCARD",
      request_id: requestId,
      expected_attempt_id: state.expected_attempt_id,
    },
  });
}

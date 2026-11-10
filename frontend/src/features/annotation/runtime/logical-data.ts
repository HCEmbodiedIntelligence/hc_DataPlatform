import { z } from "zod";
import type { components } from "../../../shared/api/generated/platform";
import { getShellState } from "../../../shared/scope/shell-store";
import { request } from "../../../shared/api/http-client";
import { parseWire } from "../../../shared/api/validate";
import { createDomainError } from "../../../shared/api/domain-error";
import { createSessionScopeLease } from "../../../shared/api/session-scope-lease";
import {
  logicalSnapshotWire,
  type LogicalSelection,
  type LogicalSnapshot,
} from "../../datasets/api/logical-data";
import { adaptLogicalViewerStreams } from "../../datasets/episodes/logical-viewer";
import type { StreamDescriptor } from "../../viewer";
import type {
  RuntimeAnnotationScope,
  RuntimeAnnotationTask,
} from "./runtime-annotation-adapter";

import { sceneViewWire, assetGrantWire, validateLogicalRobotAssets } from "../../datasets/api/logical-robot-scene";

const annotationSnapshotWire = logicalSnapshotWire.extend({
  task_id: z.string().min(1),
  snapshot_token: z.string().min(1),
  robot: sceneViewWire.nullish(),
  robot_assets: z.array(assetGrantWire).default([]),
}) satisfies z.ZodType<components["schemas"]["AnnotationLogicalSnapshot"]>;

export interface AnnotationLogicalData {
  readonly selection: LogicalSelection;
  readonly view: LogicalSnapshot;
  readonly streams: readonly StreamDescriptor[];
  readonly robot?: import("../../datasets/api/logical-robot-scene").LogicalRobotScene;
  readonly robotAssets?: ReturnType<typeof validateLogicalRobotAssets>;
}

export function requestAnnotationLogicalSnapshot(scope: RuntimeAnnotationScope, taskId: string, signal?: AbortSignal) {
  const part = encodeURIComponent;
  return request<unknown>({
    method: "GET",
    path: `/projects/${part(scope.projectId)}/regions/${part(scope.regionCode)}/annotation-tasks/${part(taskId)}/logical`,
    scope, scopeMode: "explicit", signal, cache: "no-store", query: { include_model: true },
  });
}

/** Resolve a task through its authorized frozen target; never guess a current collection. */
export async function loadAnnotationLogicalData(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
  frameTime: (index: number) => string,
  signal?: AbortSignal,
  prefetched?: Promise<unknown>,
): Promise<AnnotationLogicalData> {
  const part = encodeURIComponent;
  const path = `/projects/${part(scope.projectId)}/regions/${part(scope.regionCode)}/annotation-tasks/${part(task.task_id)}/logical`;
  const capabilityRevision = getShellState().capabilityRevision;
  const lease = createSessionScopeLease(scope, signal);
  try {
    lease.signal.throwIfAborted();
    const raw = await (prefetched ?? request<unknown>({
      method: "GET",
      path,
      scope,
      scopeMode: "explicit",
      signal: lease.signal,
      cache: "no-store",
      query: { include_model: true },
    }));
    lease.signal.throwIfAborted();
    if (getShellState().capabilityRevision !== capabilityRevision)
      throw new DOMException("Annotation authorization changed", "AbortError");
    const view = parseWire(annotationSnapshotWire, raw, { endpoint: path });
    const fixed = view.snapshot.selection;
    if (
      (view.robot != null && view.robot.snapshot_id !== view.snapshot_id) ||
      view.task_id !== task.task_id ||
      fixed.organization_id !== scope.organizationId ||
      fixed.project_id !== scope.projectId ||
      fixed.region_code !== scope.regionCode ||
      fixed.dataset_id !== task.dataset_id ||
      task.project_id !== scope.projectId ||
      task.region_code !== scope.regionCode ||
      (!task.data_ref &&
        fixed.version_id !== `version_lance_${task.base_lance_version}`) ||
      (task.data_ref &&
        (view.snapshot.episode_id !== task.data_ref.identity.episode_id ||
          view.snapshot.content_sha256 !== task.data_ref.content_hash)) ||
      new Set(view.snapshot.streams.map((s) => s.stream_id)).size !==
        view.snapshot.streams.length ||
      view.snapshot.streams.some((s) => s.sample_count !== task.base_step_count)
    )
      throw createDomainError({
        code: "CONTRACT_MISMATCH",
        message: "标注任务与固定逻辑快照的身份或样本范围不一致。",
        retryable: false,
        fieldErrors: [],
        operationErrors: [],
        blockedReasons: [],
        requestId: null,
        httpStatus: null,
      });
    const selection: LogicalSelection = {
      scope: { ...scope },
      datasetId: fixed.dataset_id,
      versionId: fixed.version_id,
      revisionId: fixed.revision_id,
      episodeId: view.snapshot.episode_id,
      contentSha256: view.snapshot.content_sha256,
      snapshotToken: view.snapshot_token,
    };
    return {
      selection,
      view,
      ...(view.robot ? {
        robot: view.robot,
        ...(view.robot.provenance === "FROZEN" ? { robotAssets: validateLogicalRobotAssets(view.robot_assets ?? [], view.robot) } : {}),
      } : {}),
      streams: adaptLogicalViewerStreams(selection, view, frameTime),
    };
  } finally {
    lease.release();
  }
}

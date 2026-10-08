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

const annotationSnapshotWire = logicalSnapshotWire.extend({
  task_id: z.string().min(1),
  snapshot_token: z.string().min(1),
}) satisfies z.ZodType<components["schemas"]["AnnotationLogicalSnapshot"]>;

export interface AnnotationLogicalData {
  readonly selection: LogicalSelection;
  readonly view: LogicalSnapshot;
  readonly streams: readonly StreamDescriptor[];
}

/** Resolve a task through its authorized frozen target; never guess a current collection. */
export async function loadAnnotationLogicalData(
  scope: RuntimeAnnotationScope,
  task: RuntimeAnnotationTask,
  signal?: AbortSignal,
): Promise<AnnotationLogicalData> {
  const part = encodeURIComponent;
  const path = `/projects/${part(scope.projectId)}/regions/${part(scope.regionCode)}/annotation-tasks/${part(task.task_id)}/logical`;
  const capabilityRevision = getShellState().capabilityRevision;
  const lease = createSessionScopeLease(scope, signal);
  try {
    lease.signal.throwIfAborted();
    const raw = await request<unknown>({
      method: "GET",
      path,
      scope,
      scopeMode: "explicit",
      signal: lease.signal,
      cache: "no-store",
    });
    lease.signal.throwIfAborted();
    if (getShellState().capabilityRevision !== capabilityRevision)
      throw new DOMException("Annotation authorization changed", "AbortError");
    const view = parseWire(annotationSnapshotWire, raw, { endpoint: path });
    const fixed = view.snapshot.selection;
    if (
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
      streams: adaptLogicalViewerStreams(selection, view).map((stream) => {
        // Annotation ranges are zero-based steps. Preserve the source clock in
        // requests and translate it only at this workbench boundary.
        const origin = BigInt(stream.startNs);
        const source = stream.windowSource;
        return {
          ...stream,
          startNs: "0",
          endNs: String(BigInt(stream.endNs) - origin),
          ...(source
            ? {
                windowSource: {
                  async loadWindow(window, signal) {
                    const payload = await source.loadWindow(
                      {
                        ...window,
                        startNs: String(BigInt(window.startNs) + origin),
                        endNs: String(BigInt(window.endNs) + origin),
                      },
                      signal,
                    );
                    return {
                      ...payload,
                      timestampsNs: payload.timestampsNs.map((ns) =>
                        String(BigInt(ns) - origin),
                      ),
                    };
                  },
                },
              }
            : {}),
        };
      }),
    };
  } finally {
    lease.release();
  }
}

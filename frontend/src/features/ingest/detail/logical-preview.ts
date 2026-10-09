import type { IngestScope } from "../../../entities/data-source";
import { request } from "../../../shared/api/http-client";
import { parseWire } from "../../../shared/api/validate";
import { createDomainError } from "../../../shared/api/domain-error";
import { createSessionScopeLease } from "../../../shared/api/session-scope-lease";
import { getShellState } from "../../../shared/scope/shell-store";
import { versionBootstrapWireSchema } from "../../datasets/api/wire-schemas";
import {
  fetchLogicalSnapshot,
  type LogicalSelection,
} from "../../datasets/api/logical-data";
import { adaptLogicalViewerStreams } from "../../datasets/episodes/logical-viewer";
import type { FormalUploadProcessingStatus } from "./client";

function mismatch(): never {
  throw createDomainError({
    code: "CONTRACT_MISMATCH",
    message: "上传处理结果与固定逻辑快照的身份或样本范围不一致。",
    retryable: false,
    fieldErrors: [],
    operationErrors: [],
    blockedReasons: [],
    requestId: null,
    httpStatus: null,
  });
}

/** The persisted viewer target resolves the fixed collection; never choose latest
 * or infer a physical package path from an upload workflow result. */
export async function loadUploadLogicalPreview(
  scope: IngestScope,
  workflow: FormalUploadProcessingStatus,
  signal?: AbortSignal,
) {
  const episode = workflow.episode;
  const viewer = workflow.viewer;
  if (
    !episode ||
    !viewer ||
    workflow.status !== "SUCCEEDED" ||
    workflow.aligned_media ||
    episode.project_id !== scope.projectId ||
    episode.region_code !== scope.regionCode ||
    episode.dataset_id !== viewer.dataset_id ||
    episode.episode_id !== viewer.episode_id
  )
    mismatch();
  const lease = createSessionScopeLease(scope, signal);
  const revision = getShellState().capabilityRevision;
  const assertCurrent = () => {
    lease.signal.throwIfAborted();
    if (getShellState().capabilityRevision !== revision)
      throw new DOMException(
        "Upload preview authorization changed",
        "AbortError",
      );
  };
  try {
    assertCurrent();
    const part = encodeURIComponent;
    const endpoint = `/projects/${part(scope.projectId)}/datasets/${part(viewer.dataset_id)}/versions/${part(viewer.version_id)}/bootstrap`;
    const raw = await request<unknown>({
      method: "GET",
      path: endpoint,
      scope,
      scopeMode: "explicit",
      signal: lease.signal,
      cache: "no-store",
    });
    assertCurrent();
    const bootstrap = parseWire(versionBootstrapWireSchema, raw, {
      endpoint,
    }).data;
    if (
      bootstrap.dataset_id !== viewer.dataset_id ||
      bootstrap.version_id !== viewer.version_id ||
      bootstrap.scope.organization_id !== scope.organizationId ||
      bootstrap.scope.project_id !== scope.projectId ||
      bootstrap.scope.region_code !== scope.regionCode
    )
      mismatch();
    const selection: LogicalSelection = {
      scope: { ...scope },
      datasetId: viewer.dataset_id,
      versionId: viewer.version_id,
      episodeId: viewer.episode_id,
      revisionId: viewer.revision_id,
      contentSha256: episode.content_hash,
      snapshotToken: bootstrap.snapshot_token,
    };
    const view = await fetchLogicalSnapshot(selection, lease.signal);
    assertCurrent();
    if (
      view.snapshot.streams.some(
        (stream) => stream.sample_count !== episode.frame_count,
      ) ||
      new Set(view.snapshot.streams.map((stream) => stream.name)).size !==
        view.snapshot.streams.length
    )
      mismatch();
    return {
      selection,
      view,
      streams: adaptLogicalViewerStreams(selection, view),
    };
  } finally {
    lease.release();
  }
}

import type { components } from "../../../shared/api/generated/platform";
import { useShellStore } from "../../../shared/scope/shell-store";
import type {
  RuntimeAnnotationBundle,
  RuntimeAnnotationScope,
  RuntimeAnnotationTask,
} from "../../../features/annotation/runtime/runtime-annotation-adapter";

export function installAnnotationSession(scope: RuntimeAnnotationScope) {
  useShellStore
    .getState()
    .setSession(
      { actorId: "reader", displayName: "Reader", roleIds: [] },
      "test-session",
    );
  useShellStore.getState().setScope(scope);
  useShellStore
    .getState()
    .setSessionScopes(
      [
        {
          ...scope,
          regionCodes: [scope.regionCode],
          projectWide: false,
          capabilities: [],
        },
      ],
      1,
    );
}

export function annotationLogicalFixture(
  task: RuntimeAnnotationTask,
  scope: RuntimeAnnotationScope,
  cameras: readonly { camera_id: string; topic: string }[] = [],
): components["schemas"]["AnnotationLogicalSnapshot"] {
  const stream = {
    sample_count: task.base_step_count!,
    temporal: true,
    start_ns: "0",
    end_ns: String((BigInt(task.base_step_count!) * 1_000_000_000n) / 30n),
  };
  return {
    task_id: task.task_id,
    snapshot_token: "fixed-task-token",
    snapshot_id: `snapshot_${"a".repeat(64)}`,
    snapshot: {
      format: "hc-logical-snapshot/1",
      selection: {
        organization_id: scope.organizationId,
        project_id: scope.projectId,
        region_code: scope.regionCode,
        dataset_id: task.dataset_id,
        version_id: `version_lance_${task.base_lance_version ?? 1}`,
        revision_id: "revision-fixed",
      },
      episode_id: task.data_ref?.identity.episode_id ?? "episode-fixed",
      content_sha256: task.data_ref?.content_hash ?? "b".repeat(64),
      streams: [
        ...cameras.map((c) => ({
          ...stream,
          stream_id: c.camera_id,
          name: c.topic,
          kind: "RGB_VIDEO",
          value_kind: null,
        })),
        {
          ...stream,
          stream_id: "joint-state",
          name: "/robot/joint_states",
          kind: "JOINT_STATE",
          value_kind: "VECTOR",
        },
      ],
    },
  };
}

export function bundleLogicalFixture(
  bundle: RuntimeAnnotationBundle,
  scope: RuntimeAnnotationScope,
) {
  return annotationLogicalFixture(bundle.task, scope, bundle.manifest?.cameras);
}

export function logicalMediaFixture(
  view: components["schemas"]["AnnotationLogicalSnapshot"],
  streamId: string,
  url: string,
): components["schemas"]["LogicalMediaGrant"] {
  return {
    snapshot_id: view.snapshot_id,
    stream_id: streamId,
    kind: "video",
    content_type: "video/mp4",
    media_url: url,
    expires_at: new Date(Date.now() + 60_000).toISOString(),
    sample_count: view.snapshot.streams[0]!.sample_count,
    sample_rate_hz: 30,
    media_start_seconds: 0,
    width: 1280,
    height: 720,
  };
}

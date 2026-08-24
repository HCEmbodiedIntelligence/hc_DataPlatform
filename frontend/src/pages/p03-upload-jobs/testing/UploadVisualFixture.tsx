import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { ProviderHarness } from "../../../app/providers";
import { useShellStore } from "../../../shared/scope/shell-store";
import UploadJobsPage from "../page";
import {
  resetUploadQueueStoreForTests,
  seedUploadRuntimeForTests,
  useUploadQueueStore,
  type UploadQueueItem,
} from "../upload-queue-store";
import type { ManifestPreflight, UploadManifest } from "../formal-client";

const visualScope = Object.freeze({
  organizationId: "org_e02_visual",
  projectId: "project_e02_visual",
  regionCode: "cn-east-01",
});

const mountedRoots = new WeakMap<HTMLElement, Root>();

/**
 * Mounts the real P03 page inside the already-rendered PlatformShell.
 * This is deliberately page-owned test infrastructure: it does not change
 * the shared runtime router just to collect E05 evidence.
 */
export function mountUploadVisualFixture(
  host: HTMLElement,
  options: { readonly scenario?: "reference" | "timeout" } = {},
): void {
  mountedRoots.get(host)?.unmount();
  resetUploadQueueStoreForTests();
  const shell = useShellStore.getState();
  shell.setSession(
    {
      actorId: "actor_e05_visual",
      displayName: "陈晨",
      roleIds: ["PROJECT_ADMIN"],
    },
    "session-e05-visual",
  );
  shell.setScope(visualScope);
  shell.setAuthorization({
    scopeKey: useShellStore.getState().scopeKey,
    roleVersion: "role-e05-visual",
    capabilities: ["storage.overview.read", "upload.read", "upload.manage"],
    fetchedAt: "2026-08-18T05:00:00Z",
  });
  shell.setSessionScopes(
    [
      {
        organizationId: visualScope.organizationId,
        projectId: visualScope.projectId,
        regionCodes: [visualScope.regionCode],
        projectWide: false,
        capabilities: ["storage.overview.read", "upload.read", "upload.manage"],
      },
    ],
    1,
  );
  const timeoutBytes = 5 * 1024 * 1024;
  const timeoutManifest = {
    schema_version: 1,
    project_id: visualScope.projectId,
    task_id: "task_e05_timeout",
    collection_job_id: "collection_job_e05_timeout",
    rollout_id: "rollout_e05_timeout",
    collection_session_id: "collection_session_e05_timeout",
    recording_request_id: "recording_request_e05_timeout",
    data_package_id: "pkg_hc_e05_timeout",
    sequence_no: 15,
    robot_id: "robot_hc_timeout",
    start_time: "2026-08-18T04:12:20Z",
    end_time: "2026-08-18T04:14:27Z",
    cameras: [],
    topics: [],
    expected_topics: [],
    actual_topics: [],
    files: [
      {
        path: "recording-timeout.mcap",
        size: timeoutBytes,
        sha256: "7c".repeat(32),
        crc64: "42",
        media_type: "application/octet-stream",
        role: "RAW_MCAP",
      },
    ],
    file_size: timeoutBytes,
    sha256: "7c".repeat(32),
    crc64: "42",
    compression: "none",
    recorder_version: "hc-recorder/3.8.2",
  } as const satisfies UploadManifest;
  const timeoutPreflight = {
    schema_version: "manifest-preflight/v1",
    manifest_fingerprint: "8d".repeat(32),
    identifiers: {
      collection_session_id: timeoutManifest.collection_session_id,
      recording_request_id: timeoutManifest.recording_request_id,
      data_package_id: timeoutManifest.data_package_id,
      robot_id: timeoutManifest.robot_id,
      pico_instance_id: null,
    },
    time_range: {
      start_time: timeoutManifest.start_time,
      end_time: timeoutManifest.end_time,
    },
    files: timeoutManifest.files,
    total_file_size: timeoutBytes,
    discovery: {
      source: "MANIFEST",
      read_only: true,
      cameras: [],
      topics: [],
      missing_expected_topics: [],
    },
    manifest: timeoutManifest,
  } as const satisfies ManifestPreflight;
  const timeoutUpload: UploadQueueItem = {
    id: "upload_session_e05_timeout",
    scopeKey: `${visualScope.organizationId}/${visualScope.projectId}/${visualScope.regionCode}`,
    sessionId: "upload_session_e05_timeout",
    sourceType: "BROWSER_MULTIPART",
    fileName: "recording-timeout.mcap",
    dataPackageId: timeoutManifest.data_package_id,
    totalBytes: timeoutBytes,
    uploadedBytes: 0,
    completedParts: 0,
    totalParts: 1,
    speedBytesPerSecond: null,
    remainingSeconds: null,
    transferStatus: "failed",
    serverStatus: "FAILED",
    failedParts: [1],
    failedPartTransfers: [{ partNumber: 1, failureCode: "PART_TIMEOUT" }],
    failureCode: "PART_TIMEOUT",
    failureMessage:
      "分片 #1 在 120 秒内未完成传输，本批其余请求已停止。可“重试失败分片”；服务端已确认分片不会重复上传。",
    requestId: null,
    createdAt: "2026-08-18T04:12:20Z",
  };
  const visualItem = options.scenario === "timeout" ? timeoutUpload : null;
  if (visualItem) {
    useUploadQueueStore.setState({
      scopeKey: visualItem.scopeKey,
      recovering: false,
      items: [visualItem],
    });
  }
  if (options.scenario === "timeout") {
    seedUploadRuntimeForTests(timeoutUpload.id, {
      scope: visualScope,
      manifest: timeoutManifest,
      preflight: timeoutPreflight,
      file: new File([new Uint8Array(timeoutBytes)], timeoutUpload.fileName, {
        type: "application/octet-stream",
      }),
    });
  }

  const root = createRoot(host);
  mountedRoots.set(host, root);
  root.render(
    <ProviderHarness>
      <MemoryRouter initialEntries={["/ingest/uploads/new"]}>
        <UploadJobsPage />
      </MemoryRouter>
    </ProviderHarness>,
  );
}
